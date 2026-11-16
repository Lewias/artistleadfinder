import { useEffect, useState } from 'react';
import { api } from '../services/api';
import type { BackgroundSettings as BackgroundValues } from '../services/types';
import { errorText } from '../lib/errors';
import { ErrorToast } from './Toaster';

export function BackgroundSettings() {
  const [settings, setSettings] = useState<BackgroundValues>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    let cancelled = false;
    void api
      .background()
      .then(value => {
        if (!cancelled) setSettings(value);
      })
      .catch(err => {
        if (!cancelled) setError(errorText(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);
  const change = async (key: keyof BackgroundValues, value: boolean) => {
    const previous = settings;
    if (!previous) return;
    setSettings({ ...previous, [key]: value });
    setBusy(true);
    setError('');
    try {
      setSettings(await api.background({ [key]: value }));
    } catch (err) {
      setSettings(previous);
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="panel">
      <h2>Фоновая работа</h2>
      <ErrorToast message={error} />
      {!settings ? (
        <p className="helper">{error ? 'Настройки фоновой работы недоступны.' : 'Загрузка…'}</p>
      ) : (
        <fieldset disabled={busy}>
          {(
            [
              ['close_to_tray', 'При закрытии сворачивать в трей'],
              ['autostart', 'Запускать вместе с Windows'],
              ['start_in_tray', 'При запуске с Windows оставаться в трее'],
              ['notifications', 'Уведомлять об ошибках и пропущенных публикациях'],
              ['keep_awake', 'Не давать компьютеру засыпать, пока запланированы посты Threads'],
            ] as const
          ).map(([key, label]) => (
            <label className="inline-check" key={key}>
              <input
                type="checkbox"
                checked={settings[key]}
                disabled={key === 'autostart' && !settings.autostart_supported}
                onChange={e => void change(key, e.target.checked)}
              />
              {label}
            </label>
          ))}
        </fieldset>
      )}
      <p className="helper">
        Из трея можно открыть окно, приостановить очередь или полностью выйти. Во сне и после полного выхода
        публикации не выполняются.
      </p>
    </section>
  );
}
