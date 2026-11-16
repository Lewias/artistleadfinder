//! «Фоновая работа»: what the close button does, start with Windows, start in the tray,
//! system notifications and keeping the computer awake. Kept in a small file next to the
//! app's data so the shell knows them before the core is up.

use serde_json::{json, Value};
use std::path::{Path, PathBuf};

#[derive(Clone, Debug, PartialEq)]
pub struct Settings {
    /// The close button hides the window to the tray (the work goes on).
    pub close_to_tray: bool,
    /// Started with Windows: only the tray icon, no window.
    pub start_in_tray: bool,
    /// System notifications: reconnect an account, missed posts, a failed post.
    pub notifications: bool,
    /// Keep the computer from sleeping while Threads posts are planned (off by default).
    pub keep_awake: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            close_to_tray: true,
            start_in_tray: false,
            notifications: true,
            keep_awake: false,
        }
    }
}

impl Settings {
    pub fn file(dir: &Path) -> PathBuf {
        dir.join("background.json")
    }

    pub fn load(dir: &Path) -> Self {
        std::fs::read(Self::file(dir))
            .ok()
            .and_then(|bytes| serde_json::from_slice::<Value>(&bytes).ok())
            .map(|value| Self::default().merged(&value))
            .unwrap_or_default()
    }

    pub fn save(&self, dir: &Path) -> std::io::Result<()> {
        std::fs::create_dir_all(dir)?;
        std::fs::write(Self::file(dir), self.to_json().to_string())
    }

    /// Only the known flags of `value` change; anything else is ignored.
    pub fn merged(&self, value: &Value) -> Self {
        let flag = |key: &str, current: bool| value[key].as_bool().unwrap_or(current);
        Self {
            close_to_tray: flag("close_to_tray", self.close_to_tray),
            start_in_tray: flag("start_in_tray", self.start_in_tray),
            notifications: flag("notifications", self.notifications),
            keep_awake: flag("keep_awake", self.keep_awake),
        }
    }

    pub fn to_json(&self) -> Value {
        json!({
            "close_to_tray": self.close_to_tray,
            "start_in_tray": self.start_in_tray,
            "notifications": self.notifications,
            "keep_awake": self.keep_awake,
        })
    }
}

/// The argument the Windows start entry passes, so a start with Windows is told apart.
pub const AUTOSTART_ARG: &str = "--autostart";

#[cfg(windows)]
mod registry {
    use windows_sys::Win32::Foundation::ERROR_SUCCESS;
    use windows_sys::Win32::System::Registry::{
        RegDeleteKeyValueW, RegGetValueW, RegSetKeyValueW, HKEY_CURRENT_USER, REG_SZ, RRF_RT_REG_SZ,
    };

    const RUN: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
    const NAME: &str = "ArtistLeadFinder";

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(std::iter::once(0)).collect()
    }

    pub fn get() -> Option<String> {
        let (key, name) = (wide(RUN), wide(NAME));
        let mut size: u32 = 0;
        unsafe {
            if RegGetValueW(
                HKEY_CURRENT_USER,
                key.as_ptr(),
                name.as_ptr(),
                RRF_RT_REG_SZ,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                &mut size,
            ) != ERROR_SUCCESS
            {
                return None;
            }
            let mut buffer = vec![0u16; (size as usize).div_ceil(2)];
            if RegGetValueW(
                HKEY_CURRENT_USER,
                key.as_ptr(),
                name.as_ptr(),
                RRF_RT_REG_SZ,
                std::ptr::null_mut(),
                buffer.as_mut_ptr().cast(),
                &mut size,
            ) != ERROR_SUCCESS
            {
                return None;
            }
            let end = buffer.iter().position(|&c| c == 0).unwrap_or(buffer.len());
            Some(String::from_utf16_lossy(&buffer[..end]))
        }
    }

    pub fn set(command: &str) -> bool {
        let (key, name, data) = (wide(RUN), wide(NAME), wide(command));
        unsafe {
            RegSetKeyValueW(
                HKEY_CURRENT_USER,
                key.as_ptr(),
                name.as_ptr(),
                REG_SZ,
                data.as_ptr().cast(),
                (data.len() * 2) as u32,
            ) == ERROR_SUCCESS
        }
    }

    pub fn remove() -> bool {
        let (key, name) = (wide(RUN), wide(NAME));
        unsafe {
            RegDeleteKeyValueW(HKEY_CURRENT_USER, key.as_ptr(), name.as_ptr()) == ERROR_SUCCESS
        }
    }
}

/// The start entry for this very executable, quoted, with the autostart argument.
pub fn autostart_command(exe: &Path) -> String {
    format!("\"{}\" {AUTOSTART_ARG}", exe.display())
}

/// Whether Windows starts this app (this executable) at sign-in.
pub fn autostart_enabled() -> bool {
    #[cfg(windows)]
    {
        let Ok(exe) = std::env::current_exe() else {
            return false;
        };
        registry::get().is_some_and(|entry| entry == autostart_command(&exe))
    }
    #[cfg(not(windows))]
    false
}

pub fn set_autostart(enabled: bool) -> Result<(), String> {
    #[cfg(windows)]
    {
        if enabled {
            let exe = std::env::current_exe().map_err(|_| "Нет пути приложения")?;
            return registry::set(&autostart_command(&exe))
                .then_some(())
                .ok_or_else(|| "Не удалось включить запуск вместе с Windows".to_string());
        }
        // Nothing to remove is fine.
        let _ = registry::remove();
        Ok(())
    }
    #[cfg(not(windows))]
    {
        let _ = enabled;
        Err("Запуск вместе с системой есть только в Windows".into())
    }
}

/// Keeps the computer from going to sleep while `awake` (the display may still turn off).
/// The state belongs to the calling thread, so one thread calls it each time.
pub fn keep_awake(awake: bool) {
    #[cfg(windows)]
    unsafe {
        use windows_sys::Win32::System::Power::{
            SetThreadExecutionState, ES_CONTINUOUS, ES_SYSTEM_REQUIRED,
        };
        SetThreadExecutionState(if awake {
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED
        } else {
            ES_CONTINUOUS
        });
    }
    #[cfg(not(windows))]
    let _ = awake;
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults_hide_to_tray_and_never_hold_the_computer_awake() {
        let settings = Settings::default();
        assert!(settings.close_to_tray && settings.notifications);
        assert!(!settings.start_in_tray && !settings.keep_awake);
    }

    #[test]
    fn settings_merge_known_flags_and_survive_a_restart() {
        let dir = std::env::temp_dir().join(format!("alf-background-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        assert_eq!(Settings::load(&dir), Settings::default());
        let changed = Settings::default().merged(&json!({
            "close_to_tray": false,
            "keep_awake": true,
            "unknown": true,
            "notifications": "yes"
        }));
        assert!(!changed.close_to_tray && changed.keep_awake && changed.notifications);
        changed.save(&dir).unwrap();
        assert_eq!(Settings::load(&dir), changed);
        std::fs::write(Settings::file(&dir), b"not json").unwrap();
        assert_eq!(Settings::load(&dir), Settings::default());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn the_start_entry_names_this_executable_and_the_tray_argument() {
        let exe = Path::new(r"C:\Program Files\Artist Lead Finder\artist-lead-finder.exe");
        assert_eq!(
            autostart_command(exe),
            "\"C:\\Program Files\\Artist Lead Finder\\artist-lead-finder.exe\" --autostart"
        );
    }
}
