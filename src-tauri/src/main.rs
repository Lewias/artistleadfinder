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
            #[cfg(windows)]
            let python = root.join(".venv/Scripts/python.exe");
            #[cfg(not(windows))]
            let python = root.join(".venv/bin/python");
            let mut c = Command::new(python);
            c.arg(root.join("backend/run_backend.py"));
            c
        };
        #[cfg(not(debug_assertions))]
        let mut command = Command::new(
            std::env::current_exe()
                .map_err(|_| "Нет пути приложения")?
                .parent()
                .ok_or("Нет каталога приложения")?
                .join(if cfg!(windows) {
                    "artist-core.exe"
                } else {
                    "artist-core"
                }),
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
        let snapshot = read_script(
            core.clone(),
            id.to_owned(),
            include_str!("capture.js"),
            Value::Null,
            false,
        )
        .await?;
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

/// Page reader for the current queue step; follow lists take their paging args.
fn page_script(state: &Value) -> &'static str {
    match (state["scout"] == true, state["kind"].as_str().unwrap_or("")) {
        (true, "source" | "tagged_grid") => include_str!("grid.js"),
        (true, "stories") => include_str!("story.js"),
        (true, "followers" | "following") => include_str!("follow.js"),
        // Profile resolver API step: one request from the open tab, no page load.
        (true, "profile") if access(state) == Access::InPlace => include_str!("profile_api.js"),
        (true, "profile") | (false, _) => include_str!("capture.js"),
        (true, _) => include_str!("scout.js"),
    }
}

/// How a queue step reaches its page.
#[derive(Debug, PartialEq)]
enum Access {
    /// Open the step URL, then read it (default).
    Navigate,
    /// Run the script in the tab that is already open (profile API request).
    InPlace,
    /// Nothing to open: the core answers from its cache.
    None,
}

fn access(state: &Value) -> Access {
    match state["access"].as_str() {
        Some("in_place") => Access::InPlace,
        Some("none") => Access::None,
        _ => Access::Navigate,
    }
}

/// Stop reason for a blocked page: the script's typed reason when it gives one.
fn block_reason(snapshot: &Value) -> &str {
    match snapshot["block_reason"].as_str() {
        Some(reason @ ("login" | "checkpoint" | "rate_limited" | "unavailable")) => reason,
        _ if snapshot["rate_limited"] == true => "rate_limited",
        _ => "blocked",
    }
}

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

async fn read_script(
    core: Core,
    id: String,
    script: &'static str,
    args: Value,
    fresh: bool,
) -> Result<Value, String> {
    let result = backend_request(
        core,
        "browser.runtime.eval".into(),
        json!({"id":id,"script":script,"args":args,"fresh":fresh}),
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
    let mut attempt = 0;
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
        // The core retried a transient failure: open the page again.
        let current_attempt = state["attempt"].as_u64().unwrap_or(0);
        if current_attempt != attempt {
            attempt = current_attempt;
            target.clear();
        }
        let step_access = access(&state);
        if step_access == Access::None {
            // Served from the core's cache: no page, no pacing.
            if request(
                "scout.commit_internal",
                json!({"id":job_id,"snapshot":{"url":url,"ready":true,"blocked":false}}),
            )
            .is_err()
            {
                let _ = request(
                    "capture.error_internal",
                    json!({"id":job_id,"reason":"save"}),
                );
            }
            target.clear();
            continue;
        }
        if target != url {
            // The core paces page opens and API requests (delays, hourly cap, 429 breaks).
            if cooldown.elapsed() < Duration::from_secs(3)
                || state["wait_seconds"].as_f64().unwrap_or(0.0) > 0.0
            {
                continue;
            }
            if step_access == Access::Navigate
                && request("browser.runtime.navigate", json!({"id":id,"url":url})).is_err()
            {
                let _ = request(
                    "capture.error_internal",
                    json!({"id":job_id,"reason":"loading"}),
                );
                continue;
            }
            target = url.to_owned();
            navigated = Instant::now();
            if step_access == Access::Navigate {
                continue;
            }
        }
        if step_access == Access::Navigate && navigated.elapsed() < Duration::from_secs(3) {
            continue;
        }
        let observation = tauri::async_runtime::block_on(read_script(
            core.clone(),
            id.to_owned(),
            page_script(&state),
            state["args"].clone(),
            step_access == Access::InPlace,
        ));
        if closing.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        match observation {
            Ok(snapshot) if snapshot["blocked"] == true => {
                let reason = block_reason(&snapshot);
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
            // An in-tab request is not repeated every tick: the core decides on a retry.
            _ if step_access == Access::InPlace
                || navigated.elapsed() > Duration::from_secs(25) =>
            {
                let _ = request(
                    "capture.error_internal",
                    json!({"id":job_id,"reason":"loading"}),
                );
                if step_access == Access::InPlace {
                    cooldown = Instant::now();
                    target.clear();
                }
            }
            _ => {}
        }
    }
}
/// Profile page of an outreach recipient; None for anything but a plain username.
fn recipient_url(args: &Value) -> Option<String> {
    let name = args["username"].as_str()?;
    let valid = (1..=30).contains(&name.len())
        && name
            .bytes()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_' || c == b'.');
    valid.then(|| format!("https://www.instagram.com/{name}/"))
}

/// Outreach send queue. The core picks a due job whose sender window is open and idle
/// and claims it; the send script runs once in that window and the result goes back to
/// the core. A failed script call is reported as a browser failure (never resent by the
/// core); a lost result leaves the claim, which the core settles for manual review.
fn run_outreach_driver(core: Core, closing: BrowserClosing) {
    let request = |method: &str, params: Value| {
        tauri::async_runtime::block_on(backend_request(core.clone(), method.into(), params))
    };
    loop {
        std::thread::sleep(Duration::from_secs(3));
        if closing.load(std::sync::atomic::Ordering::Relaxed) {
            break;
        }
        let Ok(job) = request("outreach.next_internal", json!({})) else {
            continue;
        };
        let (Some(profile_id), Some(job_id)) = (job["profile_id"].as_str(), job["job_id"].as_i64())
        else {
            continue;
        };
        // Open the recipient's profile in the sender window first, the way the parser opens
        // its pages. Nothing has been sent yet, so a failed page load is a plain retry and
        // a 429 on the page stops the sender for its rate-limit break.
        let opened = recipient_url(&job["args"]).map(|url| {
            request(
                "browser.runtime.navigate",
                json!({"id":profile_id,"url":url}),
            )
        });
        let result = match opened {
            None => json!({"outcome": "error", "error": "bad_request"}),
            Some(Err(_)) => json!({"outcome": "error", "error": "network"}),
            Some(Ok(page)) if page["rate_limited"] == true => {
                json!({"outcome": "error", "error": "rate_limited"})
            }
            // «Отправить сообщение», the text, Enter: the way it is sent by hand.
            Some(Ok(_)) => request(
                "browser.runtime.send_message",
                json!({"id":profile_id,"username":job["args"]["username"],"text":job["args"]["text"]}),
            )
            .unwrap_or_else(|_| json!({"outcome": "error", "error": "browser"})),
        };
        let _ = request(
            "outreach.commit_internal",
            json!({"job_id": job_id, "token": job["token"], "result": result}),
        );
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
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .manage(Arc::new(Mutex::new(None::<Backend>)) as Core)
        .manage(Arc::new(Mutex::new(HashMap::new())) as ProxyRelays)
        .manage(Arc::new(std::sync::atomic::AtomicBool::new(false)) as BrowserClosing)
        .setup(|app| {
            let core = app.state::<Core>().inner().clone();
            let closing = app.state::<BrowserClosing>().inner().clone();
            std::thread::spawn(move || run_outreach_driver(core, closing));
            Ok(())
        })
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
    fn page_script_follows_the_queue_step() {
        let scout = |kind: &str| json!({"scout": true, "kind": kind});
        assert_eq!(page_script(&scout("profile")), include_str!("capture.js"));
        assert_eq!(page_script(&scout("followers")), include_str!("follow.js"));
        assert_eq!(page_script(&scout("following")), include_str!("follow.js"));
        assert_eq!(page_script(&scout("source")), include_str!("grid.js"));
        assert_eq!(page_script(&scout("tagged_grid")), include_str!("grid.js"));
        assert_eq!(page_script(&scout("stories")), include_str!("story.js"));
        assert_eq!(page_script(&scout("post")), include_str!("scout.js"));
        assert_eq!(page_script(&scout("tagged_post")), include_str!("scout.js"));
        assert_eq!(page_script(&scout("story_media")), include_str!("scout.js"));
        assert_eq!(
            block_reason(&json!({"block_reason": "checkpoint"})),
            "checkpoint"
        );
        assert_eq!(block_reason(&json!({"rate_limited": true})), "rate_limited");
        assert_eq!(block_reason(&json!({"block_reason": "weird"})), "blocked");
        assert_eq!(
            page_script(&json!({"kind": "source"})),
            include_str!("capture.js")
        );
    }

    #[test]
    fn outreach_opens_only_plain_usernames() {
        assert_eq!(
            recipient_url(&json!({"username": "shot.by_jae1"})).as_deref(),
            Some("https://www.instagram.com/shot.by_jae1/")
        );
        assert_eq!(recipient_url(&json!({"username": "../accounts"})), None);
        assert_eq!(recipient_url(&json!({"username": "Upper"})), None);
        assert_eq!(recipient_url(&json!({"username": ""})), None);
        assert_eq!(recipient_url(&json!({})), None);
    }

    #[test]
    fn profile_steps_choose_page_access() {
        let profile = |access: &str| json!({"scout": true, "kind": "profile", "access": access});
        assert_eq!(access(&profile("in_place")), Access::InPlace);
        assert_eq!(access(&profile("none")), Access::None);
        assert_eq!(access(&profile("navigate")), Access::Navigate);
        assert_eq!(access(&json!({"kind": "profile"})), Access::Navigate);
        assert_eq!(
            page_script(&profile("in_place")),
            include_str!("profile_api.js")
        );
        assert_eq!(
            page_script(&profile("navigate")),
            include_str!("capture.js")
        );
        // Manual link queues never use the API script.
        assert_eq!(
            page_script(&json!({"kind": "profile", "access": "in_place"})),
            include_str!("capture.js")
        );
    }

    #[test]
    fn late_replies_are_skipped_and_foreign_ids_rejected() {
        assert_eq!(reply_order(&json!({"id": 4}), 5), ReplyOrder::Stale);
        assert_eq!(reply_order(&json!({"id": 5}), 5), ReplyOrder::Current);
        assert_eq!(reply_order(&json!({"id": 6}), 5), ReplyOrder::Invalid);
        assert_eq!(reply_order(&json!({"id": null}), 5), ReplyOrder::Invalid);
    }
}
