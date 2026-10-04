import { ErrorToast } from '../components/Toaster';
import { errorText } from '../lib/errors';
import { useState, type FormEvent } from 'react';
import { useResource } from '../hooks/useResource';
import { api } from '../services/api';
import type { SearchConfig, SearchJob, SettingsData } from '../services/types';
import { genres } from '../lib/format';
import { DataState } from '../components/DataState';
import { JobProgress } from '../components/JobProgress';
import { Button } from '../components/ui/button';
import { ScoutDiscovery } from '../components/ScoutDiscovery';
import { BrowserDiscovery } from '../components/BrowserDiscovery';

const split = (value: string) =>
  value
    .split(/[,\n]/)
    .map(item => item.trim())
    .filter(Boolean);
export function Discovery() {
  const settings = useResource<SettingsData>('settings.get');
  const jobs = useResource<SearchJob[]>('jobs.list', {}, 1000);
  return (
    <>
      <ScoutDiscovery />
      <details className="extra-tools">
        <summary>Дополнительные инструменты: ручной сбор ссылок и демонстрационный поиск</summary>
        <BrowserDiscovery />
        <div className="notice">
          Ниже — поиск по демонстрационному или импортированному набору. Для реальных страниц используйте
          браузер выше.
        </div>
        {settings.data ? (
          <DiscoveryForm settings={settings.data} jobs={jobs.data || []} refresh={jobs.refresh} />
        ) : (
          <DataState {...settings} retry={settings.refresh} />
        )}
        {jobs.error && <DataState {...jobs} retry={jobs.refresh} />}
      </details>
    </>
  );
}
function DiscoveryForm({
  settings,
  jobs,
  refresh,
}: {
  settings: SettingsData;
  jobs: SearchJob[];
  refresh: () => void;
}) {
  const [config, setConfig] = useState<SearchConfig>({
    name: 'Независимые артисты',
    seed_accounts: ['@northsidejay'],
    keywords: ['rapper', 'artist'],
    hashtags: ['#newmusic'],
    genres: [],
    min_followers: settings.min_followers,
    max_followers: settings.max_followers,
    activity_days: settings.activity_days,
    minimum_score: settings.minimum_score,
    target_leads: settings.target_leads,
  });
  const [error, setError] = useState('');
  const [queries, setQueries] = useState({
    seed_accounts: '@northsidejay',
    keywords: 'rapper, artist',
    hashtags: '#newmusic',
  });
  const [busy, setBusy] = useState(false);
  const active = jobs.find(
    job => ['queued', 'running', 'paused'].includes(job.status) && job.stage !== 'interrupted',
  );
  const update = <K extends keyof SearchConfig>(key: K, value: SearchConfig[K]) =>
    setConfig(current => ({ ...current, [key]: value }));
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api.request('jobs.start', {
        ...config,
        seed_accounts: split(queries.seed_accounts),
        keywords: split(queries.keywords),
        hashtags: split(queries.hashtags),
      });
      refresh();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="discovery-layout">
      <form className="panel search-form" onSubmit={event => void submit(event)}>
        <h2>Новый поиск</h2>
        <fieldset disabled={busy || Boolean(active)}>
          <label className="full">
            Название поиска
            <input
              required
              maxLength={160}
              value={config.name}
              onChange={event => update('name', event.target.value)}
            />
          </label>
          {(['seed_accounts', 'keywords', 'hashtags'] as const).map((key, index) => (
            <label className="full" key={key}>
              {['Исходные аккаунты', 'Ключевые слова', 'Хештеги'][index]}
              <textarea
                rows={2}
                value={queries[key]}
                onChange={event => setQueries(current => ({ ...current, [key]: event.target.value }))}
              />
              <small>Разделяйте запятыми</small>
            </label>
          ))}
          <div className="full">
            <span className="field-label">Жанры</span>
            <div className="genre-options">
              {genres.map(genre => (
                <label key={genre}>
                  <input
                    type="checkbox"
                    checked={config.genres.includes(genre)}
                    onChange={event =>
                      update(
                        'genres',
                        event.target.checked
                          ? [...config.genres, genre]
                          : config.genres.filter(value => value !== genre),
                      )
                    }
                  />
                  {genre}
                </label>
              ))}
            </div>
          </div>
          <label>
            Подписчики от
            <input
              type="number"
              min={0}
              max={1000000000}
              required
              value={config.min_followers}
              onChange={event => update('min_followers', Number(event.target.value))}
            />
          </label>
          <label>
            Подписчики до
            <input
              type="number"
              min={config.min_followers}
              max={1000000000}
              required
              value={config.max_followers}
              onChange={event => update('max_followers', Number(event.target.value))}
            />
          </label>
          <label>
            Активность за
            <select
              value={config.activity_days}
              onChange={event => update('activity_days', Number(event.target.value))}
            >
              {[7, 14, 30, 60, 90].map(days => (
                <option key={days} value={days}>
                  {days} дней
                </option>
              ))}
            </select>
          </label>
          <label>
            Минимальная оценка
            <input
              type="number"
              min={0}
              max={100}
              required
              value={config.minimum_score}
              onChange={event => update('minimum_score', Number(event.target.value))}
            />
          </label>
          <label>
            Целевое число лидов
            <input
              type="number"
              min={1}
              max={100000}
              required
              value={config.target_leads}
              onChange={event => update('target_leads', Number(event.target.value))}
            />
          </label>
          <div className="full">
            <Button type="submit" disabled={busy || Boolean(active)}>
              {busy ? 'Запускаем…' : 'Начать поиск'}
            </Button>
          </div>
        </fieldset>
        <ErrorToast message={error} />
      </form>
      <div>
        {jobs[0] ? (
          <JobProgress job={active || jobs[0]} refresh={refresh} />
        ) : (
          <section className="panel">
            <h2>От источников к результатам</h2>
            <p className="empty-copy">Создайте поиск. Прогресс и управление заданием появятся здесь.</p>
          </section>
        )}
      </div>
    </div>
  );
}
