#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod background;
mod proxy_relay;

use serde_json::{json, Value};
use std::{
    collections::HashMap,
    io::{BufRead, BufReader, Write},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{
        atomic::{AtomicBool, AtomicUsize, Ordering},
        mpsc::{self, Receiver},
        Arc, Mutex,
    },
    time::{Duration, Instant},
};
use tauri::{Emitter, Manager};

type ProxyRelays = Arc<Mutex<HashMap<String, Arc<std::sync::atomic::AtomicBool>>>>;

const CORE_TIMEOUT: &str = "Локальное ядро не ответило вовремя";
const HUNG_AFTER_TIMEOUTS: u32 = 3;
/// A hung core is replaced quickly; on quit the core gets time to save and close browsers.
const CORE_STOP_WAIT: Duration = Duration::from_secs(3);
const CORE_SAVE_WAIT: Duration = Duration::from_secs(20);
/// On quit: how long a send, a reply read or a page step already under way may finish.
const QUIT_WAIT: Duration = Duration::from_secs(100);
/// Set once the app quits: no core is started again after the last one stopped.
static CORE_STOPPED: AtomicBool = AtomicBool::new(false);

/// Windows: the core and everything it starts (Playwright, Chromium) run in one job that
/// the system closes together with the app, so no child outlives it, even after a crash
/// or the updater's hard exit.
#[cfg(windows)]
fn adopt(child: &Child) {
    use std::os::windows::io::AsRawHandle;
    use std::sync::OnceLock;
    use windows_sys::Win32::{Foundation::HANDLE, System::JobObjects::*};
    struct Job(HANDLE);
    // The handle is only passed to AssignProcessToJobObject and never closed.
    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}
    static JOB: OnceLock<Option<Job>> = OnceLock::new();
    let job = JOB.get_or_init(|| unsafe {
        let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if handle.is_null() {
            return None;
        }
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        let set = SetInformationJobObject(
            handle,
            JobObjectExtendedLimitInformation,
            &limits as *const _ as *const std::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        (set != 0).then_some(Job(handle))
    });
    if let Some(job) = job {
        unsafe { AssignProcessToJobObject(job.0, child.as_raw_handle() as HANDLE) };
    }
}

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
    /// How long the core may take to stop by itself before it and its children are killed.
    grace: Duration,
}
/// The frozen core. Windows: the one-file sidecar next to the app. macOS: a one-folder
/// build in Contents/Resources/core, because the one-file loader needs a System V
/// semaphore that some Macs (MDM profiles, security agents) refuse.
#[cfg(not(debug_assertions))]
fn core_executable() -> Result<std::path::PathBuf, String> {
    let exe = std::env::current_exe().map_err(|_| "Нет пути приложения")?;
    let dir = exe.parent().ok_or("Нет каталога приложения")?;
    Ok(if cfg!(target_os = "macos") {
        dir.parent()
            .ok_or("Нет каталога приложения")?
            .join("Resources/core/artist-core")
    } else if cfg!(windows) {
        dir.join("core").join("artist-core.exe")
    } else {
        dir.join("artist-core")
    })
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
        let mut command = Command::new(core_executable()?);
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
        #[cfg(windows)]
        adopt(&child);
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
            grace: CORE_STOP_WAIT,
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
        let deadline = Instant::now() + self.grace;
        while Instant::now() < deadline {
            if self.child.try_wait().ok().flatten().is_some() {
                return;
            }
            std::thread::sleep(Duration::from_millis(30));
        }
        // The whole tree: killing the core alone would leave Playwright and Chromium behind.
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            let _ = Command::new("taskkill")
                .args(["/PID", &self.child.id().to_string(), "/T", "/F"])
                .creation_flags(0x08000000)
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
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
            if CORE_STOPPED.load(Ordering::SeqCst) {
                return Err("Приложение завершает работу".into());
            }
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
        let closing = app.state::<ShellState>().inner().clone();
        std::thread::spawn(move || run_browser_queue(core, job_id, closing));
        return Ok(created);
    }
    if action == "threads_open" {
        if !is_open {
            open_window(&app, core.clone(), id).await?;
        }
        return backend_request(
            core,
            "browser.runtime.threads_open".into(),
            json!({"id":id}),
        )
        .await;
    }
    if action != "open" {
        return Err("Неизвестное действие".into());
    }
    if is_open {
        return backend_request(core, "browser.runtime.open".into(), json!({"id":id})).await;
    }
    open_window(&app, core, id).await
}

/// Opens a saved browser profile, starting its proxy relay first when it has one.
/// Used by the button and by the assistant's parser starts, so both behave the same.
async fn open_window(app: &tauri::AppHandle, core: Core, id: &str) -> Result<Value, String> {
    if id.len() != 32 || !id.bytes().all(|c| c.is_ascii_hexdigit()) {
        return Err("Некорректный профиль".into());
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
        let closing = app.state::<ShellState>().inner().clone();
        let id = id.to_owned();
        std::thread::spawn(move || watch_window(core, id, relays, closing));
    }
    result
}
/// What the background loops share with the window and the tray.
#[derive(Default)]
struct Shell {
    /// The app is quitting: the loops hand out no new work and stop.
    closing: AtomicBool,
    /// Paused from the tray: no sends, reply reads or warmup steps until resumed.
    paused: AtomicBool,
    /// Work under way (a send and its commit, a page step) that a quit waits for.
    busy: AtomicUsize,
    /// One quit at a time; a second caller waits until the first one is done.
    exiting: Mutex<()>,
    /// The Threads queue is paused, as the core last said (the tray shows it).
    autopost_paused: AtomicBool,
}
type ShellState = Arc<Shell>;

/// One unit of work a quit waits for.
struct Working<'a>(&'a AtomicUsize);
impl Drop for Working<'_> {
    fn drop(&mut self) {
        self.0.fetch_sub(1, Ordering::SeqCst);
    }
}

impl Shell {
    fn closing(&self) -> bool {
        self.closing.load(Ordering::SeqCst)
    }
    fn paused(&self) -> bool {
        self.paused.load(Ordering::SeqCst)
    }
    /// Starts a unit of work, or None once the app is quitting. It is counted before the
    /// check, so a quit either sees the work and waits or the work sees the quit.
    fn work(&self) -> Option<Working<'_>> {
        self.busy.fetch_add(1, Ordering::SeqCst);
        let work = Working(&self.busy);
        (!self.closing()).then_some(work)
    }
}

/// Page reader for the current queue step; follow lists take their paging args.
fn page_script(state: &Value) -> &'static str {
    match (state["scout"] == true, state["kind"].as_str().unwrap_or("")) {
        (true, "source" | "tagged_grid") => include_str!("grid.js"),
        (true, "followers" | "following") => include_str!("follow.js"),
        (true, "profile") | (false, _) => include_str!("capture.js"),
        (true, _) => include_str!("scout.js"),
    }
}

/// How a queue step reaches its page.
#[derive(Debug, PartialEq)]
enum Access {
    /// Open the step URL, then read it (default).
    Navigate,
    /// Nothing to open: the core answers from its cache.
    None,
    /// Nothing to open, but the core makes one request from the open tab (fast profile
    /// request), so the step waits its pause like a page.
    Request,
}

fn access(state: &Value) -> Access {
    match state["access"].as_str() {
        Some("none") => Access::None,
        Some("request") => Access::Request,
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
fn watch_window(core: Core, id: String, relays: ProxyRelays, closing: ShellState) {
    loop {
        std::thread::sleep(Duration::from_secs(2));
        if closing.closing() {
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

/// Grid pages: the core also reads what the page's own data says about the posts.
fn reads_feed(state: &Value) -> bool {
    state["scout"] == true && matches!(state["kind"].as_str(), Some("source" | "tagged_grid"))
}

async fn read_script(
    core: Core,
    id: String,
    script: &'static str,
    args: Value,
    feed: bool,
) -> Result<Value, String> {
    let result = backend_request(
        core,
        "browser.runtime.eval".into(),
        json!({"id":id,"script":script,"args":args,"feed":feed}),
    )
    .await?;
    // A page answer is small (author, caption, a grid or a follow list); refuse runaway ones.
    if result.to_string().len() > 1_000_000 {
        return Err("Слишком большой ответ страницы".into());
    }
    Ok(result)
}

fn run_browser_queue(core: Core, job_id: i64, closing: ShellState) {
    let request = |method: &str, params: Value| {
        tauri::async_runtime::block_on(backend_request(core.clone(), method.into(), params))
    };
    let mut target = String::new();
    let mut navigated = Instant::now();
    let mut cooldown = Instant::now();
    let mut attempt = 0;
    loop {
        std::thread::sleep(Duration::from_millis(500));
        // A page step that has begun is read and committed even when a quit comes meanwhile.
        let Some(_work) = closing.work() else {
            break;
        };
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
        if step_access == Access::Request
            && (cooldown.elapsed() < Duration::from_secs(3)
                || state["wait_seconds"].as_f64().unwrap_or(0.0) > 0.0)
        {
            // The core paces its fast request like a page open.
            continue;
        }
        if step_access == Access::None || step_access == Access::Request {
            // Served by the core (cache or its own request): no page to open.
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
            if step_access == Access::Request {
                cooldown = Instant::now();
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
            reads_feed(&state),
        ));
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
/// Profile page of an outreach recipient; None for anything but a plain username.
fn recipient_url(args: &Value) -> Option<String> {
    let name = args["username"].as_str()?;
    let valid = (1..=30).contains(&name.len())
        && name
            .bytes()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_' || c == b'.');
    valid.then(|| format!("https://www.instagram.com/{name}/"))
}

/// Thread or profile page of a reply reading; None for anything else the core might name.
fn inbox_url(job: &Value) -> Option<String> {
    let url = job["url"].as_str()?;
    if let Some(id) = url
        .strip_prefix("https://www.instagram.com/direct/t/")
        .and_then(|rest| rest.strip_suffix('/'))
    {
        let valid = (1..=80).contains(&id.len())
            && id
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || c == b'_' || c == b'-');
        return valid.then(|| url.to_string());
    }
    recipient_url(job).filter(|profile| profile == url)
}

/// «Ответы»: one outreach thread read in its sender window when nothing is due to be sent.
/// The core picks the thread and its pause; the read types and sends nothing.
fn read_inbox_thread(request: &impl Fn(&str, Value) -> Result<Value, String>) {
    let Ok(job) = request("inbox.next_internal", json!({})) else {
        return;
    };
    let (Some(profile_id), Some(item_id)) = (job["profile_id"].as_str(), job["item_id"].as_i64())
    else {
        return;
    };
    let opened = inbox_url(&job).map(|url| {
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
        Some(Ok(_)) => request(
            "browser.runtime.read_thread",
            json!({"id":profile_id,"username":job["username"],"outbound":job["outbound"]}),
        )
        .unwrap_or_else(|_| json!({"outcome": "error", "error": "browser"})),
    };
    let _ = request(
        "inbox.commit_internal",
        json!({"item_id": item_id, "kind": job["kind"], "result": result}),
    );
}

/// The assistant's round: its parser starts and «Автоведение». Windows are not opened here
/// unless a parser run needs one; an account window opens for work and closes after it.
fn assistant_rounds(app: tauri::AppHandle, core: Core, closing: ShellState) {
    loop {
        if closing.closing() {
            break;
        }
        start_requested_parsers(&app, &core, &closing);
        std::thread::sleep(Duration::from_secs(20));
    }
}

/// Parser runs the assistant asked for (a confirmed proposal or «Автоведение»): the window
/// is opened when it is closed and the run starts the way the «Парсер» button starts it.
/// The core hears whether it started.
fn start_requested_parsers(app: &tauri::AppHandle, core: &Core, closing: &ShellState) {
    let request = |method: &str, params: Value| {
        tauri::async_runtime::block_on(backend_request(core.clone(), method.into(), params))
    };
    loop {
        // A start that was taken is answered even when a quit comes meanwhile.
        let Some(_work) = closing.work() else {
            break;
        };
        let Ok(wanted) = request("assistant.shell_next_internal", json!({})) else {
            break;
        };
        let Some(id) = wanted["profile_id"].as_str() else {
            break;
        };
        let started = (|| {
            let open = request("browser.runtime.is_open", json!({"id":id}))?;
            if open["open"].as_bool() != Some(true) {
                tauri::async_runtime::block_on(open_window(app, core.clone(), id))?;
            }
            let created = request(
                "scout.start_internal",
                json!({"profile_id":id,"name":"Парсер от ассистента"}),
            )?;
            created["id"]
                .as_i64()
                .ok_or_else(|| "Нет задания".to_string())
        })();
        let answer = match started {
            Ok(job_id) => {
                let (queue_core, queue_closing) = (core.clone(), closing.clone());
                std::thread::spawn(move || run_browser_queue(queue_core, job_id, queue_closing));
                json!({"profile_id":id,"job_id":job_id})
            }
            Err(error) => json!({"profile_id":id,"error":error}),
        };
        let _ = request("assistant.shell_result_internal", answer);
    }
}

/// Page a queued job is sent from: the recipient's profile, or for a follow-up its own
/// thread when the core knows it. None for anything else the core might name.
fn send_url(job: &Value) -> Option<String> {
    if job["kind"] == "followup" {
        inbox_url(&job["args"])
    } else {
        recipient_url(&job["args"])
    }
}

/// Outreach send queue (initial messages and follow-up steps). The core picks a due job
/// whose sender window is open and idle and claims it; the send script runs once in that
/// window and the result goes back to the core. A failed script call is reported as a
/// browser failure (never resent by the core); a lost result leaves the claim, which the
/// core settles for manual review. Reply reads take turns with sends in the same loop, so
/// one window is never driven by both at once and a busy queue never starves them.
/// Paused from the tray, it takes nothing new: no send, reply read or warmup step. On quit,
/// a send already claimed finishes and is committed before the core stops.
fn run_outreach_driver(core: Core, shell: ShellState) {
    let request = |method: &str, params: Value| {
        tauri::async_runtime::block_on(backend_request(core.clone(), method.into(), params))
    };
    loop {
        std::thread::sleep(Duration::from_secs(3));
        let Some(_work) = shell.work() else {
            break;
        };
        if !shell.paused() {
            let _ = request("instagram_warmup.tick_internal", json!({}));
        }
        // A window the app has no more work for closes by itself (the core decides).
        let _ = request("browser.runtime.close_idle", json!({}));
        if shell.paused() {
            continue;
        }
        let job = request("outreach.next_internal", json!({})).unwrap_or(Value::Null);
        let (Some(profile_id), Some(job_id)) = (job["profile_id"].as_str(), job["job_id"].as_i64())
        else {
            // Nothing to send: the same window reads replies, never both at once.
            read_inbox_thread(&request);
            continue;
        };
        let kind = job["kind"].as_str().unwrap_or("initial");
        // Open the recipient's profile (or the follow-up's thread) in the sender window
        // first, the way the parser opens its pages. Nothing has been sent yet, so a failed
        // page load is a plain retry and a 429 on the page stops the sender for its break.
        let opened = send_url(&job).map(|url| {
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
            // «Отправить сообщение», the text, Enter: the way it is sent by hand. The claim
            // goes along so the core can still stop it right before Enter.
            Some(Ok(_)) => request(
                "browser.runtime.send_message",
                json!({
                    "id": profile_id,
                    "username": job["args"]["username"],
                    "text": job["args"]["text"],
                    "args": job["args"],
                    "job": {"kind": kind, "job_id": job_id, "token": job["token"]},
                }),
            )
            .unwrap_or_else(|_| json!({"outcome": "error", "error": "browser"})),
        };
        let _ = request(
            "outreach.commit_internal",
            json!({"job_id": job_id, "token": job["token"], "kind": kind, "result": result}),
        );
        // A reply read may follow before the next send (the core paces both).
        if !shell.closing() && !shell.paused() {
            read_inbox_thread(&request);
        }
    }
}

/// Quits for good: no new work is handed out, work under way finishes and is committed,
/// then the core saves its state, closes its browsers and stops, and whatever is left of
/// it is killed. Safe to call more than once and from any thread.
fn shutdown(app: &tauri::AppHandle) {
    let shell = app.state::<ShellState>().inner().clone();
    let _once = shell
        .exiting
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    shell.closing.store(true, Ordering::SeqCst);
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.hide();
    }
    let deadline = Instant::now() + QUIT_WAIT;
    while shell.busy.load(Ordering::SeqCst) > 0 && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(50));
    }
    if let Ok(mut relays) = app.state::<ProxyRelays>().lock() {
        for (_, stop) in relays.drain() {
            stop.store(true, Ordering::Relaxed);
        }
    }
    CORE_STOPPED.store(true, Ordering::SeqCst);
    let core = app.state::<Core>().inner().clone();
    // The lock waits for a request still in flight.
    let backend = core
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner())
        .take();
    if let Some(mut backend) = backend {
        backend.grace = CORE_SAVE_WAIT;
        drop(backend);
    }
}

/// The Windows updater runs the installer and ends the process with `std::process::exit`
/// right after `cleanup_before_exit`, which drops the app's resources: dropping this one
/// quits properly first, so the installer finds no core or browser still running.
struct QuitOnCleanup(tauri::AppHandle);
impl tauri::Resource for QuitOnCleanup {}
impl Drop for QuitOnCleanup {
    fn drop(&mut self) {
        shutdown(&self.0);
    }
}

const PAUSE_LABEL: &str = "Приостановить рассылку";
const RESUME_LABEL: &str = "Продолжить рассылку";
const PAUSE_EVENT: &str = "automation-paused";
const AUTOPOST_LABEL: &str = "Приостановить автопостинг";
const AUTOPOST_RESUME_LABEL: &str = "Продолжить автопостинг";

/// The tray menu items whose text and state change.
struct TrayMenu {
    open: tauri::menu::MenuItem<tauri::Wry>,
    pause: tauri::menu::MenuItem<tauri::Wry>,
    autopost: tauri::menu::MenuItem<tauri::Wry>,
    quit: tauri::menu::MenuItem<tauri::Wry>,
}

type Background = Arc<Mutex<background::Settings>>;

fn show_autopost(app: &tauri::AppHandle, paused: bool) {
    app.state::<ShellState>()
        .autopost_paused
        .store(paused, Ordering::SeqCst);
    if let Some(menu) = app.try_state::<TrayMenu>() {
        let _ = menu.autopost.set_text(if paused {
            AUTOPOST_RESUME_LABEL
        } else {
            AUTOPOST_LABEL
        });
    }
}

/// «Приостановить автопостинг»: the Threads queue lives in the core, so the core is asked.
fn set_autopost(app: &tauri::AppHandle, paused: bool) {
    let app = app.clone();
    let core = app.state::<Core>().inner().clone();
    std::thread::spawn(move || {
        let answer = tauri::async_runtime::block_on(backend_request(
            core,
            "threads.pause_internal".into(),
            json!({ "paused": paused }),
        ));
        if let Ok(state) = answer {
            show_autopost(&app, state["paused"] == true);
        }
    });
}

fn notify(app: &tauri::AppHandle, title: &str, body: &str) {
    use tauri_plugin_notification::NotificationExt;
    let _ = app.notification().builder().title(title).body(body).show();
}

/// What the Threads queue tells the shell: its pause for the tray, its notices for the
/// system notifications, and whether a post is planned soon (to keep the computer awake
/// when the user asked for it). One thread, because the awake request is per thread.
fn background_rounds(app: tauri::AppHandle) {
    let shell = app.state::<ShellState>().inner().clone();
    let core = app.state::<Core>().inner().clone();
    let mut shown = None;
    while !shell.closing() {
        std::thread::sleep(Duration::from_secs(5));
        if shell.closing() {
            break;
        }
        let Ok(found) = tauri::async_runtime::block_on(backend_request(
            core.clone(),
            "threads.shell_internal".into(),
            json!({}),
        )) else {
            background::keep_awake(false);
            continue;
        };
        let paused = found["paused"] == true;
        // Open due browser profiles through the same proxy relay as Instagram.
        for id in found["browser_profiles"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
        {
            if shell.closing() {
                break;
            }
            let open = tauri::async_runtime::block_on(backend_request(
                core.clone(),
                "browser.runtime.is_open".into(),
                json!({"id": id}),
            ));
            if matches!(open, Ok(ref value) if value["open"] == false) {
                let _ = tauri::async_runtime::block_on(open_window(&app, core.clone(), id));
            }
        }
        if shown != Some(paused) {
            show_autopost(&app, paused);
            shown = Some(paused);
        }
        let settings = app
            .state::<Background>()
            .lock()
            .map(|settings| settings.clone())
            .unwrap_or_default();
        if settings.notifications {
            for notice in found["notices"].as_array().into_iter().flatten() {
                notify(
                    &app,
                    notice["title"].as_str().unwrap_or("Artist Lead Finder"),
                    notice["text"].as_str().unwrap_or(""),
                );
            }
        }
        let soon = found["next_in"]
            .as_f64()
            .is_some_and(|seconds| seconds < 24.0 * 3600.0);
        background::keep_awake(settings.keep_awake && !paused && soon);
    }
    background::keep_awake(false);
}

/// «Настройки → Фоновая работа»: reads them, or changes the given ones.
#[tauri::command]
fn background_settings(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    values: Option<Value>,
) -> Result<Value, String> {
    if window.label() != "main" {
        return Err("Недоступный метод".into());
    }
    let dir = app
        .path()
        .app_data_dir()
        .map_err(|_| "Нет папки приложения")?;
    let state = app.state::<Background>();
    let mut settings = state.lock().map_err(|_| "Настройки недоступны")?;
    if let Some(values) = values {
        if let Some(enabled) = values["autostart"].as_bool() {
            background::set_autostart(enabled)?;
        }
        let next = settings.merged(&values);
        next.save(&dir)
            .map_err(|_| "Не удалось сохранить настройки")?;
        *settings = next;
    }
    let mut answer = settings.to_json();
    answer["autostart"] = json!(background::autostart_enabled());
    answer["autostart_supported"] = json!(cfg!(windows));
    Ok(answer)
}

/// The pause survives a restart (an update must not resume sending by itself).
fn pause_marker(app: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    app.path()
        .app_data_dir()
        .ok()
        .map(|dir| dir.join("automation-paused"))
}

fn set_paused(app: &tauri::AppHandle, paused: bool) {
    app.state::<ShellState>()
        .paused
        .store(paused, Ordering::SeqCst);
    if let Some(marker) = pause_marker(app) {
        if paused {
            let _ = std::fs::create_dir_all(marker.parent().unwrap_or(&marker))
                .and_then(|_| std::fs::write(&marker, b""));
        } else {
            let _ = std::fs::remove_file(&marker);
        }
    }
    if let Some(menu) = app.try_state::<TrayMenu>() {
        let _ = menu
            .pause
            .set_text(if paused { RESUME_LABEL } else { PAUSE_LABEL });
    }
    let _ = app.emit(PAUSE_EVENT, paused);
}

/// The main window back from the tray, a second launch or the dock.
fn show_main(app: &tauri::AppHandle) {
    if app.state::<ShellState>().closing() {
        return;
    }
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

/// «Выйти»: the quit runs off the event loop, so the tray stays responsive meanwhile.
fn quit(app: &tauri::AppHandle) {
    if app.state::<ShellState>().closing() {
        return;
    }
    if let Some(menu) = app.try_state::<TrayMenu>() {
        for item in [&menu.open, &menu.pause, &menu.autopost, &menu.quit] {
            let _ = item.set_enabled(false);
        }
    }
    if let Some(tray) = app.tray_by_id("main") {
        let _ = tray.set_tooltip(Some("Artist Lead Finder — завершение работы…"));
    }
    let app = app.clone();
    std::thread::spawn(move || {
        shutdown(&app);
        app.exit(0);
    });
}

fn build_tray(app: &tauri::App) -> tauri::Result<()> {
    use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
    use tauri::tray::{MouseButton, TrayIconBuilder, TrayIconEvent};
    let paused = app.state::<ShellState>().paused();
    let open = MenuItem::with_id(app, "open", "Открыть", true, None::<&str>)?;
    let pause = MenuItem::with_id(
        app,
        "pause",
        if paused { RESUME_LABEL } else { PAUSE_LABEL },
        true,
        None::<&str>,
    )?;
    let autopost = MenuItem::with_id(app, "autopost", AUTOPOST_LABEL, true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "Выйти", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[
            &open,
            &autopost,
            &pause,
            &PredefinedMenuItem::separator(app)?,
            &quit_item,
        ],
    )?;
    TrayIconBuilder::with_id("main")
        .icon(tauri::image::Image::from_bytes(include_bytes!(
            "../icons/icon.png"
        ))?)
        .tooltip("Artist Lead Finder")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "open" => show_main(app),
            "pause" => set_paused(app, !app.state::<ShellState>().paused()),
            "autopost" => set_autopost(
                app,
                !app.state::<ShellState>()
                    .autopost_paused
                    .load(Ordering::SeqCst),
            ),
            "quit" => quit(app),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::DoubleClick {
                button: MouseButton::Left,
                ..
            } = event
            {
                show_main(tray.app_handle());
            }
        })
        .build(app)?;
    app.manage(TrayMenu {
        open,
        pause,
        autopost,
        quit: quit_item,
    });
    Ok(())
}

/// The window's pause notice: reads the state, or pauses and resumes like the tray.
#[tauri::command]
fn automation(
    app: tauri::AppHandle,
    window: tauri::WebviewWindow,
    paused: Option<bool>,
) -> Result<bool, String> {
    if window.label() != "main" {
        return Err("Недоступный метод".into());
    }
    if let Some(paused) = paused {
        set_paused(&app, paused);
    }
    Ok(app.state::<ShellState>().paused())
}

fn main() {
    tauri::Builder::default()
        // A second launch only brings this one's window back, hidden in the tray or not.
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            show_main(app);
        }))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .plugin(tauri_plugin_notification::init())
        .manage(Arc::new(Mutex::new(None::<Backend>)) as Core)
        .manage(Arc::new(Mutex::new(HashMap::new())) as ProxyRelays)
        .manage(ShellState::default())
        .setup(|app| {
            let handle = app.handle().clone();
            if pause_marker(&handle).is_some_and(|marker| marker.exists()) {
                app.state::<ShellState>()
                    .paused
                    .store(true, Ordering::SeqCst);
            }
            build_tray(app)?;
            app.resources_table().add(QuitOnCleanup(handle.clone()));
            let settings = handle
                .path()
                .app_data_dir()
                .map(|dir| background::Settings::load(&dir))
                .unwrap_or_default();
            // Started with Windows and asked to stay in the tray: no window until opened.
            let quiet = settings.start_in_tray
                && std::env::args().any(|arg| arg == background::AUTOSTART_ARG);
            app.manage(Arc::new(Mutex::new(settings)) as Background);
            if !quiet {
                show_main(&handle);
            }
            std::thread::spawn(move || background_rounds(handle));
            let core = app.state::<Core>().inner().clone();
            let closing = app.state::<ShellState>().inner().clone();
            std::thread::spawn(move || run_outreach_driver(core, closing));
            let rounds_core = app.state::<Core>().inner().clone();
            let rounds_closing = app.state::<ShellState>().inner().clone();
            let handle = app.handle().clone();
            std::thread::spawn(move || assistant_rounds(handle, rounds_core, rounds_closing));
            Ok(())
        })
        // The close button hides the window by default: the core, the queues and the open
        // browsers keep working, and the tray brings the window back. With «сворачивать в
        // трей» off it quits the way «Выйти» does.
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" && !window.state::<ShellState>().closing() {
                    api.prevent_close();
                    let to_tray = window
                        .try_state::<Background>()
                        .and_then(|settings| settings.lock().ok().map(|s| s.close_to_tray))
                        .unwrap_or(true);
                    if to_tray {
                        let _ = window.hide();
                    } else {
                        quit(window.app_handle());
                    }
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            core_request,
            open_profile,
            open_logs,
            browser_action,
            automation,
            background_settings
        ])
        .build(tauri::generate_context!())
        .expect("Не удалось запустить Artist Lead Finder")
        .run(|app, event| match event {
            tauri::RunEvent::WindowEvent {
                label,
                event: tauri::WindowEvent::Destroyed,
                ..
            } if label == "main" => app.exit(0),
            // «Выйти» has already stopped everything; a restart after an update or any
            // other exit stops it here, before the process ends.
            tauri::RunEvent::Exit => shutdown(app),
            #[cfg(target_os = "macos")]
            tauri::RunEvent::Reopen { .. } => show_main(app),
            _ => {}
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
        assert_eq!(page_script(&scout("post")), include_str!("scout.js"));
        assert_eq!(page_script(&scout("tagged_post")), include_str!("scout.js"));
        // Only grids hand their posts' data to the core.
        assert!(reads_feed(&scout("source")) && reads_feed(&scout("tagged_grid")));
        assert!(!reads_feed(&scout("post")) && !reads_feed(&json!({"kind": "source"})));
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
    fn reply_reading_opens_only_threads_and_its_profile() {
        let job = |url: &str| json!({"username": "shot.by_jae1", "url": url});
        let thread = "https://www.instagram.com/direct/t/340282366841710300949128/";
        assert_eq!(inbox_url(&job(thread)).as_deref(), Some(thread));
        let profile = "https://www.instagram.com/shot.by_jae1/";
        assert_eq!(inbox_url(&job(profile)).as_deref(), Some(profile));
        assert_eq!(
            inbox_url(&job("https://www.instagram.com/someone_else/")),
            None
        );
        assert_eq!(
            inbox_url(&job("https://www.instagram.com/direct/t/../../x/")),
            None
        );
        assert_eq!(inbox_url(&job("https://evil.example/direct/t/1/")), None);
        assert_eq!(inbox_url(&json!({})), None);
    }

    #[test]
    fn followups_open_their_own_thread_initial_messages_only_the_profile() {
        let thread = "https://www.instagram.com/direct/t/340282366/";
        let job = |kind: &str, url: &str| json!({"kind": kind, "args": {"username": "shot.by_jae1", "url": url}});
        assert_eq!(send_url(&job("followup", thread)).as_deref(), Some(thread));
        let profile = "https://www.instagram.com/shot.by_jae1/";
        assert_eq!(
            send_url(&job("followup", profile)).as_deref(),
            Some(profile)
        );
        assert_eq!(
            send_url(&job("followup", "https://www.instagram.com/other/")),
            None
        );
        // An initial message never goes to a thread the core names: only to the profile.
        assert_eq!(send_url(&job("initial", thread)).as_deref(), Some(profile));
        assert_eq!(
            send_url(&json!({"args": {"username": "shot.by_jae1"}})).as_deref(),
            Some(profile)
        );
    }

    #[test]
    fn profile_steps_choose_page_access() {
        let profile = |access: &str| json!({"scout": true, "kind": "profile", "access": access});
        assert_eq!(access(&profile("none")), Access::None);
        assert_eq!(access(&profile("request")), Access::Request);
        assert_eq!(access(&profile("navigate")), Access::Navigate);
        assert_eq!(access(&json!({"kind": "profile"})), Access::Navigate);
        assert_eq!(
            page_script(&profile("navigate")),
            include_str!("capture.js")
        );
    }

    #[test]
    fn a_quit_waits_for_work_under_way_and_hands_out_none() {
        let shell = Shell::default();
        let work = shell.work();
        assert!(work.is_some());
        shell.closing.store(true, Ordering::SeqCst);
        // Refused work is not left counted, or the quit would wait for nothing.
        assert!(shell.work().is_none());
        assert_eq!(shell.busy.load(Ordering::SeqCst), 1);
        drop(work);
        assert_eq!(shell.busy.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn late_replies_are_skipped_and_foreign_ids_rejected() {
        assert_eq!(reply_order(&json!({"id": 4}), 5), ReplyOrder::Stale);
        assert_eq!(reply_order(&json!({"id": 5}), 5), ReplyOrder::Current);
        assert_eq!(reply_order(&json!({"id": 6}), 5), ReplyOrder::Invalid);
        assert_eq!(reply_order(&json!({"id": null}), 5), ReplyOrder::Invalid);
    }
}
