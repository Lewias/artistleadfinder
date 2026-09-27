#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod proxy_relay;

use serde_json::{json, Value};
use std::{
    collections::HashMap,
    io::{BufRead, BufReader, Write},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{
        mpsc::{self, Receiver},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};
use tauri::Manager;

type ProxyRelays = Arc<Mutex<HashMap<String, Arc<std::sync::atomic::AtomicBool>>>>;

const CORE_TIMEOUT: &str = "Локальное ядро не ответило вовремя";
const HUNG_AFTER_TIMEOUTS: u32 = 3;

#[derive(Debug, PartialEq)]
enum ReplyOrder {
    Stale,
    Current,
    Invalid,
}

/// A late answer to a request that already timed out must not be taken for the
/// answer to the current one.
fn reply_order(response: &Value, expected: u64) -> ReplyOrder {
    match response["id"].as_u64() {
        Some(id) if id < expected => ReplyOrder::Stale,
        Some(id) if id == expected => ReplyOrder::Current,
        _ => ReplyOrder::Invalid,
    }
}

struct Backend {
    child: Child,
    stdin: ChildStdin,
    stdout: Receiver<Result<String, String>>,
    next_id: u64,
    timeouts: u32,
}
impl Backend {
    fn start() -> Result<Self, String> {
        #[cfg(debug_assertions)]
        let mut command = {
            let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .unwrap();
            let mut c = Command::new(root.join(".venv/Scripts/python.exe"));
            c.arg(root.join("backend/run_backend.py"));
            c
        };
        #[cfg(not(debug_assertions))]
        let mut command = Command::new(
            std::env::current_exe()
                .map_err(|_| "Нет пути приложения")?
                .parent()
                .ok_or("Нет каталога приложения")?
                .join("artist-core.exe"),
        );
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let mut child = command
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|_| "Не удалось запустить локальное ядро")?;
        let stdin = child.stdin.take().ok_or("Нет IPC input")?;
        let mut reader = BufReader::new(child.stdout.take().ok_or("Нет IPC output")?);
        let (sender, stdout) = mpsc::sync_channel(1);
        std::thread::spawn(move || loop {
            let mut line = String::new();
            let response = match reader.read_line(&mut line) {
                Ok(0) => Err("Локальное ядро завершило работу".to_owned()),
                Ok(_) if line.len() <= 8_000_000 => Ok(line),
                _ => Err("Ошибка чтения ответа ядра".to_owned()),
            };
            let failed = response.is_err();
            if sender.send(response).is_err() || failed {
                break;
            }
        });
        Ok(Self {
            child,
            stdin,
            stdout,
            next_id: 0,
            timeouts: 0,
        })
    }
    fn request(&mut self, method: String, params: Value) -> Result<Value, String> {
        self.next_id += 1;
        let payload = json!({"id": self.next_id, "method": method, "params": params});
        if payload.to_string().len() > 2_000_000 {
            return Err("Запрос слишком большой".into());
        }
        writeln!(self.stdin, "{}", payload)
            .and_then(|_| self.stdin.flush())
            .map_err(|_| "Связь с ядром потеряна")?;
        // File import and export scale with user data; everything else is interactive.
        let limit = if matches!(method.as_str(), "providers.import" | "leads.export") {
            Duration::from_secs(600)
        } else {
            Duration::from_secs(45)
        };
        let deadline = Instant::now() + limit;
        let response = loop {
            let line = self
                .stdout
                .recv_timeout(deadline.saturating_duration_since(Instant::now()))
                .map_err(|_| {
                    self.timeouts += 1;
                    CORE_TIMEOUT
                })??;
            let response: Value =
                serde_json::from_str(&line).map_err(|_| "Некорректный ответ ядра")?;
            match reply_order(&response, self.next_id) {
                ReplyOrder::Stale => continue,
                ReplyOrder::Current => break response,
                ReplyOrder::Invalid => return Err("Нарушен порядок ответов ядра".into()),
            }
        };
        self.timeouts = 0;
        if let Some(error) = response["error"].as_str() {
            return Err(error.to_owned());
        }
        Ok(response["result"].clone())
    }
}
impl Drop for Backend {
    fn drop(&mut self) {
        let _ = writeln!(
            self.stdin,
            "{}",
            json!({"id":0,"method":"system.shutdown","params":{}})
        );
        let _ = self.stdin.flush();
        let deadline = Instant::now() + Duration::from_secs(3);
        while Instant::now() < deadline {
            if self.child.try_wait().ok().flatten().is_some() {
                return;
            }
            std::thread::sleep(Duration::from_millis(30));
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}
type Core = Arc<Mutex<Option<Backend>>>;
#[tauri::command]
async fn core_request(
    window: tauri::WebviewWindow,
    core: tauri::State<'_, Core>,
    method: String,
    params: Value,
) -> Result<Value, String> {
    if window.label() != "main" || method.starts_with("browser.") || method.ends_with("_internal") {
        return Err("Недоступный метод".into());
    }
    backend_request(core.inner().clone(), method, params).await
}
async fn backend_request(core: Core, method: String, params: Value) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || {
        if method == "system.shutdown" {
            return Err("Недоступный метод".into());
        }
        let mut lock = core.lock().map_err(|_| "Ядро недоступно")?;
        if lock.is_none() {
            *lock = Some(Backend::start()?);
        }
        let backend = lock.as_mut().unwrap();
        let result = backend.request(method, params);
        // A single slow call must not kill running jobs; restart only a hung or exited core.
        if backend.timeouts >= HUNG_AFTER_TIMEOUTS
            || backend.child.try_wait().ok().flatten().is_some()
        {
            lock.take();
        }
        result
    })
    .await
    .map_err(|_| "Ошибка IPC")?
}
#[tauri::command]
fn open_profile(app: tauri::AppHandle, address: String) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    let parsed = url::Url::parse(&address).map_err(|_| "Некорректная ссылка")?;
    if !["https", "http"].contains(&parsed.scheme())
        || parsed.host_str().is_none()
        || !parsed.username().is_empty()
    {
        return Err("Разрешены только HTTP(S) ссылки профилей".into());
    }
    app.opener()
        .open_url(address, None::<&str>)
        .map_err(|_| "Не удалось открыть профиль".into())
}
#[tauri::command]
async fn open_logs(app: tauri::AppHandle, core: tauri::State<'_, Core>) -> Result<(), String> {
    use tauri_plugin_opener::OpenerExt;
    let info = backend_request(core.inner().clone(), "system.info".into(), json!({})).await?;
    let path = info["log_dir"].as_str().ok_or("Нет каталога журналов")?;
    app.opener()
        .open_path(path, None::<&str>)
        .map_err(|_| "Не удалось открыть журнал".into())
}
#[tauri::command]
async fn browser_action(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    core: tauri::State<'_, Core>,
    action: String,
    params: Value,
) -> Result<Value, String> {
    if window.label() != "main" {
        return Err("Недоступный метод".into());
    }
    let core = core.inner().clone();
    if action == "list" || action == "import" || action == "create" {
        return backend_request(core, format!("browser.{action}"), params).await;
    }
    let id = params["id"].as_str().ok_or("Нет профиля")?;
    if id.len() != 32 || !id.bytes().all(|c| c.is_ascii_hexdigit()) {
        return Err("Некорректный профиль".into());
    }
    let is_open = backend_request(
        core.clone(),
        "browser.runtime.is_open".into(),
        json!({"id":id}),
    )
    .await?["open"]
        == true;
    if !is_open {
        if let Ok(mut relays) = app.state::<ProxyRelays>().lock() {
            if let Some(stop) = relays.remove(id) {
                stop.store(true, std::sync::atomic::Ordering::Relaxed);
            }
        }
    }
    if action == "delete" || action == "update" || action == "import_cookies" {
        if is_open {
            return Err("Сначала закройте окно браузерного профиля".into());
        }
        return backend_request(core, format!("browser.{action}"), params).await;
    }
    if action == "save" {
        if !is_open {
            return Err("Откройте профиль".into());
        }
        return backend_request(core, "browser.runtime.save".into(), json!({"id":id})).await;
    }
    if action == "capture" {
        if !is_open {
            return Err("Сначала откройте браузер".into());
        }
        let snapshot = read_script(core.clone(), id.to_owned(), include_str!("capture.js")).await?;
        if snapshot["blocked"].as_bool().unwrap_or(true)
            || !snapshot["ready"].as_bool().unwrap_or(false)
        {
            return Err("Откройте страницу артиста и дождитесь загрузки. При необходимости войдите вручную.".into());
        }
        let created = backend_request(
            core.clone(),
            "capture.start_internal".into(),
            json!({"profile_id":id,"urls":[snapshot["url"]],"name":"Открытый артист из браузера"}),
        )
        .await?;
        let result = backend_request(
            core.clone(),
            "capture.commit_internal".into(),
            json!({"id":created["id"],"snapshot":snapshot}),
        )
        .await;
        if result.is_err() {
            let _ = backend_request(
                core,
                "jobs.control".into(),
                json!({"id":created["id"],"action":"cancel"}),
            )
            .await;
        }
        return result;
    }
    if action == "queue" || action == "scout" {
        if !is_open {
            return Err("Сначала откройте выбранный браузер".into());
        }
        let created = backend_request(
            core.clone(),
            if action == "scout" { "scout.start_internal" } else { "capture.start_internal" }.into(),
            json!({"profile_id":id,"sources":params["sources"],"urls":params["urls"],"name":"Очередь артистов из браузера"}),
        )
        .await?;
        let job_id = created["id"].as_i64().ok_or("Нет задания")?;
        let closing = app.state::<BrowserClosing>().inner().clone();
        std::thread::spawn(move || run_browser_queue(core, job_id, closing));
        return Ok(created);
    }
    if action != "open" {
        return Err("Неизвестное действие".into());
    }
    if is_open {
        return backend_request(core, "browser.runtime.open".into(), json!({"id":id})).await;
    }
    let record = if id == "00000000000000000000000000000000" {
        json!({"name":"Без сохранённой сессии","cookies":[]})
    } else {
        backend_request(
            core.clone(),
            "browser.load_internal".into(),
            json!({"id":id}),
        )
        .await?
    };
    let relays = app.state::<ProxyRelays>().inner().clone();
    if let Ok(mut map) = relays.lock() {
        if let Some(stop) = map.remove(id) {
            stop.store(true, std::sync::atomic::Ordering::Relaxed);
        }
    }
    let mut relay_stop = None;
    let mut proxy_override = None;
    if let Some(proxy) = record.get("proxy").filter(|value| !value.is_null()) {
        if proxy.get("password").and_then(Value::as_str).is_some() {
            let (local_port, stop) = proxy_relay::start(proxy)?;
            relay_stop = Some(stop);
            proxy_override = Some(format!("http://127.0.0.1:{local_port}"));
        }
    }
    let result = backend_request(
        core.clone(),
        "browser.runtime.open".into(),
        json!({"id":id,"proxy_override":proxy_override}),
    )
    .await;
    if let Some(stop) = relay_stop {
        if result.is_ok() {
            relays
                .lock()
                .map_err(|_| "Прокси недоступен")?
                .insert(id.to_owned(), stop);
        } else {
            stop.store(true, std::sync::atomic::Ordering::Relaxed);
        }
    }
    if result.is_ok() {
        let closing = app.state::<BrowserClosing>().inner().clone();
        let id = id.to_owned();
        std::thread::spawn(move || watch_window(core, id, relays, closing));
    }
    result
}
type BrowserClosing = Arc<std::sync::atomic::AtomicBool>;

/// Polls an open profile window so the core notices a manual close promptly: it then
/// saves the session cookies and shuts the browser, and the proxy relay is stopped.
fn watch_window(core: Core, id: String, relays: ProxyRelays, closing: BrowserClosing) {
    loop {
        std::thread::sleep(Duration::from_secs(2));
        if closing.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        let open = tauri::async_runtime::block_on(backend_request(
            core.clone(),
            "browser.runtime.is_open".into(),
            json!({"id":id}),
        ));
        if matches!(open, Ok(ref state) if state["open"] == false) {
            if let Ok(mut map) = relays.lock() {
                if let Some(stop) = map.remove(&id) {
                    stop.store(true, std::sync::atomic::Ordering::Relaxed);
                }
            }
            break;
        }
    }
}

async fn read_script(core: Core, id: String, script: &'static str) -> Result<Value, String> {
    let result = backend_request(
        core,
        "browser.runtime.eval".into(),
        json!({"id":id,"script":script}),
    )
    .await?;
    // Up to 200 comments of 1500 characters; Cyrillic takes two bytes per character.
    if result.to_string().len() > 1_000_000 {
        return Err("Слишком большой ответ страницы".into());
    }
    Ok(result)
}

fn run_browser_queue(core: Core, job_id: i64, closing: BrowserClosing) {
    let request = |method: &str, params: Value| {
        tauri::async_runtime::block_on(backend_request(core.clone(), method.into(), params))
    };
    let mut target = String::new();
    let mut navigated = Instant::now();
    let mut cooldown = Instant::now();
    loop {
        std::thread::sleep(Duration::from_millis(500));
        if closing.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        let Ok(state) = request("capture.state", json!({"id":job_id})) else {
            break;
        };
        if state["stage"] == "interrupted"
            || !["running", "paused"].contains(&state["status"].as_str().unwrap_or(""))
        {
            break;
        }
        if state["status"] == "paused" {
            target.clear();
            continue;
        }
        let id = state["profile_id"].as_str().unwrap_or("");
        if request("browser.runtime.is_open", json!({"id":id}))
            .ok()
            .and_then(|v| v["open"].as_bool())
            != Some(true)
        {
            let _ = request(
                "capture.error_internal",
                json!({"id":job_id,"reason":"closed"}),
            );
            continue;
        }
        let Some(url) = state["url"].as_str() else {
            break;
        };
        if target != url {
            // The core paces page opens (delays, hourly cap, rate-limit breaks).
            if cooldown.elapsed() < Duration::from_secs(3)
                || state["wait_seconds"].as_f64().unwrap_or(0.0) > 0.0
            {
                continue;
            }
            if request("browser.runtime.navigate", json!({"id":id,"url":url})).is_err() {
                let _ = request(
                    "capture.error_internal",
                    json!({"id":job_id,"reason":"loading"}),
                );
                continue;
            }
            target = url.to_owned();
            navigated = Instant::now();
            continue;
        }
        if navigated.elapsed() < Duration::from_secs(3) {
            continue;
        }
        let observation = if state["scout"] == true && state["kind"] != "profile" {
            tauri::async_runtime::block_on(read_script(
                core.clone(),
                id.to_owned(),
                include_str!("scout.js"),
            ))
        } else {
            tauri::async_runtime::block_on(read_script(
                core.clone(),
                id.to_owned(),
                include_str!("capture.js"),
            ))
        };
        if closing.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        match observation {
            Ok(snapshot) if snapshot["blocked"] == true => {
                let reason = if snapshot["rate_limited"] == true {
                    "rate_limited"
                } else {
                    "blocked"
                };
                let _ = request(
                    "capture.error_internal",
                    json!({"id":job_id,"reason":reason}),
                );
            }
            Ok(snapshot)
                if snapshot["ready"] == true
                    && snapshot["url"].as_str().is_some_and(|actual| {
                        actual
                            .split(['?', '#'])
                            .next()
                            .unwrap_or("")
                            .trim_end_matches('/')
                            == url.trim_end_matches('/')
                    }) =>
            {
                if request(
                    if state["scout"] == true {
                        "scout.commit_internal"
                    } else {
                        "capture.commit_internal"
                    },
                    json!({"id":job_id,"snapshot":snapshot}),
                )
                .is_err()
                {
                    let _ = request(
                        "capture.error_internal",
                        json!({"id":job_id,"reason":"save"}),
                    );
                }
                cooldown = Instant::now();
                target.clear();
            }
            _ if navigated.elapsed() > Duration::from_secs(25) => {
                let _ = request(
                    "capture.error_internal",
                    json!({"id":job_id,"reason":"loading"}),
                );
            }
            _ => {}
        }
    }
}
fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(Arc::new(Mutex::new(None::<Backend>)) as Core)
        .manage(Arc::new(Mutex::new(HashMap::new())) as ProxyRelays)
        .manage(Arc::new(std::sync::atomic::AtomicBool::new(false)) as BrowserClosing)
        .invoke_handler(tauri::generate_handler![
            core_request,
            open_profile,
            open_logs,
            browser_action
        ])
        .build(tauri::generate_context!())
        .expect("Не удалось запустить Artist Lead Finder")
        .run(|app, event| {
            if let tauri::RunEvent::WindowEvent {
                label,
                event: tauri::WindowEvent::Destroyed,
                ..
            } = &event
            {
                if label == "main" {
                    app.exit(0);
                }
            }
            if matches!(event, tauri::RunEvent::Exit) {
                if let Ok(mut relays) = app.state::<ProxyRelays>().lock() {
                    for (_, stop) in relays.drain() {
                        stop.store(true, std::sync::atomic::Ordering::Relaxed);
                    }
                }
                app.state::<BrowserClosing>()
                    .store(true, std::sync::atomic::Ordering::Relaxed);
                if let Ok(mut core) = app.state::<Core>().lock() {
                    core.take();
                }
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn late_replies_are_skipped_and_foreign_ids_rejected() {
        assert_eq!(reply_order(&json!({"id": 4}), 5), ReplyOrder::Stale);
        assert_eq!(reply_order(&json!({"id": 5}), 5), ReplyOrder::Current);
        assert_eq!(reply_order(&json!({"id": 6}), 5), ReplyOrder::Invalid);
        assert_eq!(reply_order(&json!({"id": null}), 5), ReplyOrder::Invalid);
    }
}
