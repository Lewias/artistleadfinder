use serde_json::{json, Value};
use std::{
    sync::atomic::{AtomicBool, Ordering},
    time::{Duration, Instant},
};

use super::{backend_request, read_script, Core};

static BUSY: AtomicBool = AtomicBool::new(false);
static CANCEL: AtomicBool = AtomicBool::new(false);

struct SearchGuard;
impl Drop for SearchGuard {
    fn drop(&mut self) {
        BUSY.store(false, Ordering::SeqCst);
    }
}

pub async fn run(core: Core, action: &str, params: Value) -> Result<Value, String> {
    if action == "search_cancel" {
        CANCEL.store(true, Ordering::SeqCst);
        return Ok(json!({"ok":true}));
    }
    let query = params["query"].as_str().unwrap_or("").trim();
    if query.chars().count() < 2
        || query.chars().count() > 200
        || query.chars().any(char::is_control)
    {
        return Err("Введите запрос от 2 до 200 символов".into());
    }
    if BUSY.swap(true, Ordering::SeqCst) {
        return Err("Поиск уже выполняется".into());
    }
    let guard = SearchGuard;
    CANCEL.store(false, Ordering::SeqCst);
    let search_query =
        format!("site:instagram.com {query} -inurl:/p/ -inurl:/reel/ -inurl:/explore/");
    let mut url = url::Url::parse("https://search.brave.com/search").unwrap();
    url.query_pairs_mut()
        .append_pair("q", &search_query)
        .append_pair("source", "web");
    backend_request(
        core.clone(),
        "browser.runtime.search_open".into(),
        json!({"url":url.as_str()}),
    )
    .await?;
    tauri::async_runtime::spawn_blocking(move || {
        let _guard = guard;
        let started = Instant::now();
        let mut previous = String::new();
        loop {
            std::thread::sleep(Duration::from_secs(1));
            if CANCEL.load(Ordering::SeqCst) { return Err("Поиск отменён".into()); }
            if started.elapsed() > Duration::from_secs(35) { return Err("Не удалось прочитать результаты. Проверьте окно поиска и повторите запрос.".into()); }
            let status = tauri::async_runtime::block_on(backend_request(core.clone(), "browser.runtime.is_open".into(), json!({"id":"search"})))?;
            if status["open"] != true { return Err("Окно поиска закрыто".into()); }
            if started.elapsed() < Duration::from_secs(3) { continue; }
            let snapshot = tauri::async_runtime::block_on(read_script(core.clone(), "search".into(), include_str!("search.js")))?;
            if CANCEL.load(Ordering::SeqCst) { return Err("Поиск отменён".into()); }
            if snapshot["blocked"] == true { return Err("Поисковик требует подтверждения или ограничил запросы. Проверьте окно поиска.".into()); }
            if snapshot["query"] != search_query || snapshot["ready"] != true { continue; }
            let current = snapshot["results"].to_string();
            if current == previous {
                return Ok(json!({"query":search_query,"search_url":url.as_str(),"results":snapshot["results"]}));
            }
            previous = current;
        }
    }).await.map_err(|_| "Ошибка поиска")?
}
