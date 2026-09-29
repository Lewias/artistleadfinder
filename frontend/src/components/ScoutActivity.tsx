import { useState } from 'react';
import { Activity, ListChecks, ScrollText } from 'lucide-react';
import { useResource } from '../hooks/useResource';
import type { ScoutAccountRow, ScoutEvent } from '../services/types';
import { activityItem, eventText, type ActivityItem } from './scoutStatus';

type Tab = 'recent' | 'events' | 'log';
type LogFilter = 'all' | 'lead' | 'skipped' | 'discovery';

export function ScoutActivity({ accounts }: { accounts: ScoutAccountRow[] }) {
  const runs = accounts
    .filter(row => row.run)
    .map(row => ({ id: row.run!.id, name: row.profile.name, status: row.run!.status }))
    .sort((a, b) => b.id - a.id);
  const [chosen, setChosen] = useState<number | null>(null);
  const [tab, setTab] = useState<Tab>('recent');
  const [filter, setFilter] = useState<LogFilter>('all');
  const jobId = chosen ?? runs.find(run => run.status === 'running')?.id ?? runs[0]?.id ?? 0;
  const events = useResource<ScoutEvent[]>('scout.events', { job_id: jobId, limit: 300 }, jobId ? 1500 : 0);
  if (!runs.length) return null;
  const items = events.data || [];
  const logs = items.filter(
    event =>
      event.payload.log &&
      (filter === 'all' ||
        (filter === 'lead' && (event.type === 'scout:lead-created' || event.type === 'scout:lead-updated')) ||
        (filter === 'skipped' && event.type === 'scout:profile-skipped') ||
        (filter === 'discovery' && event.type === 'scout:discovery-page')),
  );
  const recent = items
    .map(activityItem)
    .filter((item): item is ActivityItem => item !== null)
    .reverse()
    .slice(0, 40);
  return (
    <section className="panel scout-activity">
      <div className="section-heading">
        <div className="segmented" role="tablist" aria-label="Активность Scout">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'recent'}
            className={tab === 'recent' ? 'active' : ''}
            onClick={() => setTab('recent')}
          >
            <ListChecks size={14} /> Недавнее
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'events'}
            className={tab === 'events' ? 'active' : ''}
            onClick={() => setTab('events')}
          >
            <Activity size={14} /> События
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'log'}
            className={tab === 'log' ? 'active' : ''}
            onClick={() => setTab('log')}
          >
            <ScrollText size={14} /> Журнал профилей
          </button>
        </div>
        <select
          aria-label="Запуск"
          value={jobId}
          onChange={event => setChosen(Number(event.target.value))}
          className="run-select"
        >
          {runs.map(run => (
            <option key={run.id} value={run.id}>
              #{run.id} · {run.name}
            </option>
          ))}
        </select>
      </div>
      {events.error && (
        <p role="alert" className="error-text">
          {events.error}
        </p>
      )}
      {tab === 'recent' ? (
        <ol className="activity-list" aria-live="polite">
          {recent.length === 0 && <li className="helper">Решений по профилям пока нет.</li>}
          {recent.map(item => (
            <li key={item.id} className={`activity-${item.tone}`}>
              <strong>@{item.username}</strong>
              <span>{item.type || '—'}</span>
              <span>{item.outcome}</span>
            </li>
          ))}
        </ol>
      ) : tab === 'events' ? (
        <ol className="event-feed" aria-live="polite">
          {items.length === 0 && <li className="helper">Событий пока нет.</li>}
          {[...items].reverse().map(event => (
            <li key={event.id} className={`event event-${event.type.replace(':', '-')}`}>
              <time>{new Date(event.created_at).toLocaleTimeString('ru-RU')}</time>
              <code>{event.type}</code>
              <span>{eventText(event)}</span>
            </li>
          ))}
        </ol>
      ) : (
        <>
          <div className="segmented small" role="radiogroup" aria-label="Фильтр журнала">
            {(
              [
                ['all', 'Все'],
                ['lead', 'Лиды'],
                ['skipped', 'Пропущенные'],
                ['discovery', 'Discovery'],
              ] as const
            ).map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="radio"
                aria-checked={filter === id}
                className={filter === id ? 'active' : ''}
                onClick={() => setFilter(id)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="log-viewer">
            {logs.length === 0 && <p className="helper">Записей пока нет.</p>}
            {[...logs].reverse().map(event => (
              <pre
                key={event.id}
                className={
                  event.type === 'scout:lead-created' || event.type === 'scout:lead-updated'
                    ? 'log-lead'
                    : event.type === 'scout:discovery-page'
                      ? 'log-page'
                      : 'log-skip'
                }
              >
                {event.payload.log}
              </pre>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
