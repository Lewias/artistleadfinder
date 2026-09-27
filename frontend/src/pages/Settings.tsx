import { useState, type FormEvent } from 'react';
import { open } from '@tauri-apps/plugin-dialog';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import type { ProviderHealth, SettingsData } from '../services/types';
import { date } from '../lib/format';
import { DataState } from '../components/DataState';
import { Button } from '../components/ui/button';

const weightNames: Record<string, string> = {
  artist: 'Вероятный артист',
  music_bio: 'Музыкальная биография',
  music_link: 'Музыкальная ссылка',
  recent_activity: 'Недавняя музыка',
  followers: 'Диапазон аудитории',
  genre: 'Целевой жанр',
  strong_activity: 'Высокая активность',
};
export function Settings() {
  const resource = useResource<SettingsData>('settings.get');
  const health = useResource<ProviderHealth[]>('providers.health', {}, 5000);
  if (!resource.data) return <DataState {...resource} retry={resource.refresh} />;
  return (
    <SettingsForm
      initial={resource.data}
      health={health.data || []}
      refresh={() => {
        resource.refresh();
        health.refresh();
      }}
    />
  );
}
function SettingsForm({
  initial,
  health,
  refresh,
}: {
  initial: SettingsData;
  health: ProviderHealth[];
  refresh: () => void;
}) {
  const [settings, setSettings] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const total = Object.values(settings.weights).reduce((sum, weight) => sum + weight, 0);
  const update = <K extends keyof SettingsData>(key: K, value: SettingsData[K]) =>
    setSettings(current => ({ ...current, [key]: value }));
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setMessage('');
    setError('');
    try {
      await api.request('settings.save', settings);
      setMessage('Настройки сохранены. Применяются к новым поискам.');
      refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };
  const importData = async () => {
    setBusy(true);
    setMessage('');
    setError('');
    try {
      const path = await open({
        title: 'Импорт набора профилей',
        multiple: false,
        filters: [{ name: 'Profiles', extensions: ['json', 'csv'] }],
      });
      if (!path || Array.isArray(path)) return;
      const result = await api.request<{ count: number }>('providers.import', { path });
      setMessage(
        `Импортировано профилей: ${result.count}. Включите источник «Импорт» и сохраните настройки.`,
      );
      refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <form onSubmit={event => void submit(event)} className="settings-layout">
        <section className="panel">
          <h2>Поиск по умолчанию</h2>
          <fieldset disabled={busy}>
            <label>
              Подписчики от
              <input
                required
                type="number"
                min={0}
                value={settings.min_followers}
                onChange={event => update('min_followers', Number(event.target.value))}
              />
            </label>
            <label>
              Подписчики до
              <input
                required
                type="number"
                min={settings.min_followers}
                value={settings.max_followers}
                onChange={event => update('max_followers', Number(event.target.value))}
              />
            </label>
            <label>
              Активность за
              <select
                value={settings.activity_days}
                onChange={event => update('activity_days', Number(event.target.value))}
              >
                {[7, 14, 30, 60, 90].map(days => (
                  <option value={days} key={days}>
                    {days} дней
                  </option>
                ))}
              </select>
            </label>
            <label>
              Оценка от
              <input
                required
                type="number"
                min={0}
                max={100}
                value={settings.minimum_score}
                onChange={event => update('minimum_score', Number(event.target.value))}
              />
            </label>
            <label>
              Цель поиска
              <input
                required
                type="number"
                min={1}
                max={100000}
                value={settings.target_leads}
                onChange={event => update('target_leads', Number(event.target.value))}
              />
            </label>
          </fieldset>
        </section>
        <section className="panel">
          <h2>Веса оценки</h2>
          <fieldset disabled={busy}>
            {Object.entries(weightNames).map(([key, label]) => (
              <label key={key}>
                {label}
                <input
                  type="number"
                  required
                  min={0}
                  max={100}
                  value={settings.weights[key]}
                  onChange={event =>
                    update('weights', { ...settings.weights, [key]: Number(event.target.value) })
                  }
                />
              </label>
            ))}
          </fieldset>
          <p className={total === 100 ? 'helper' : 'error-text'}>Сумма весов: {total} / 100</p>
        </section>
        <section className="panel">
          <h2>Темп сбора Instagram</h2>
          <fieldset disabled={busy}>
            {(
              [
                ['page_delay_min', 'Пауза между страницами от, с', 3, 300],
                ['page_delay_max', 'Пауза между страницами до, с', settings.page_delay_min, 600],
                ['profiles_per_hour', 'Профилей в час (0 — без лимита)', 0, 1000],
                ['profiles_per_run', 'Профилей за запуск (0 — без лимита)', 0, 100000],
                ['rate_limit_pause_minutes', 'Перерыв после ограничения, мин', 5, 1440],
              ] as const
            ).map(([key, label, min, max]) => (
              <label key={key}>
                {label}
                <input
                  required
                  type="number"
                  min={min}
                  max={max}
                  value={settings[key]}
                  onChange={event => update(key, Number(event.target.value))}
                />
              </label>
            ))}
          </fieldset>
          <p className="helper">
            Пауза выбирается случайно в заданном диапазоне. При лимите в час очередь ждёт и продолжает сама.
            Если Instagram ограничил запросы, очередь встаёт на паузу и после продолжения выдерживает перерыв.
            Счётчики сбрасываются при перезапуске приложения.
          </p>
        </section>
        <section className="panel">
          <h2>Источники поиска</h2>
          <div className="genre-options">
            {[
              { id: 'mock', label: 'Демонстрационный' },
              { id: 'imported', label: 'Импорт' },
            ].map(provider => (
              <label key={provider.id}>
                <input
                  type="checkbox"
                  disabled={busy}
                  checked={settings.enabled_providers.includes(provider.id)}
                  onChange={event =>
                    update(
                      'enabled_providers',
                      event.target.checked
                        ? [...settings.enabled_providers, provider.id]
                        : settings.enabled_providers.filter(value => value !== provider.id),
                    )
                  }
                />
                {provider.label}
              </label>
            ))}
          </div>
          <p className="helper">
            JSON или CSV, до 25 MB. Обязательные поля: platform, username. Поддерживаются поля Candidate из
            документации. Файл проверяется и сохраняется локально.
          </p>
          <Button type="button" variant="outline" disabled={busy} onClick={() => void importData()}>
            Импортировать набор
          </Button>
        </section>
        <section className="panel">
          <h2>AI-классификация</h2>
          <p className="helper">
            Используются объяснимые правила. Приложение работает без API-ключа. Облачные и локальные
            AI-адаптеры пока не подключены.
          </p>
          <label className="inline-check">
            <input type="checkbox" disabled />
            Включить AI <span className="helper">Планируется</span>
          </label>
          <p className="helper">
            При добавлении интеграции секреты будут храниться в Windows Credential Manager.
          </p>
        </section>
        <div className="full actions">
          <Button type="submit" disabled={busy || total !== 100}>
            Сохранить настройки
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() => void api.openLogs().catch(err => setError(String(err)))}
          >
            Открыть папку журналов
          </Button>
        </div>
      </form>
      {message && (
        <p role="status" className="notice">
          {message}
        </p>
      )}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      <section className="panel provider-panel">
        <h2>Состояние источников</h2>
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th>Источник</th>
                <th>Состояние</th>
                <th>Последний запрос</th>
                <th>Успешный запрос</th>
                <th>Последняя ошибка</th>
              </tr>
            </thead>
            <tbody>
              {health.map(provider => (
                <tr key={provider.provider}>
                  <td>{provider.provider}</td>
                  <td>{provider.status}</td>
                  <td>{date(provider.last_request_at || null)}</td>
                  <td>{date(provider.last_success_at || null)}</td>
                  <td className="error-cell">{provider.last_error || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="helper">Meta Instagram не подключён. Квоты и ограничения не обходятся.</p>
      </section>
    </>
  );
}
