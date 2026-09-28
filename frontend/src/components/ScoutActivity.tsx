import { useState } from 'react';
import { Activity, ScrollText } from 'lucide-react';
import { useResource } from '../hooks/useResource';
import type { ScoutAccountRow, ScoutEvent } from '../services/types';
import { eventText } from './scoutStatus';

type Tab = 'events' | 'log';
type LogFilter = 'all' | 'lead' | 'skipped';

export function ScoutActivity({ accounts }: { accounts: ScoutAccountRow[] }) {
  const runs = accounts
    .filter(row => row.run)
    .map(row => ({ id: row.run!.id, name: row.profile.name, status: row.run!.status }))
    .sort((a, b) => b.id - a.id);
  const [chosen, setChosen] = useState<number | null>(null);
  const [tab, setTab] = useState<Tab>('events');
  const [filter, setFilter] = useState<LogFilter>('all');
  const jobId = chosen ?? runs.find(run => run.status === 'running')?.id ?? runs[0]?.id ?? 0;
  const events = useResource<ScoutEvent[]>('scout.events', { job_id: jobId, limit: 300 }, jobId ? 1500 : 0);
  if (!runs.length) return null;
  const items = events.data || [];
  const logs = items.filter(
    event =>
      event.payload.log &&
      (filter === 'all' ||
        (filter === 'lead' && event.type === 'lead:found') ||
        (filter === 'skipped' && event.type === 'profile:skipped')),
  );
  return (
    <section className="panel scout-activity">
      <div className="section-heading">
        <div className="segmented" role="tablist" aria-label="Активность Scout">
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
      {tab === 'events' ? (
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
              <pre key={event.id} className={event.type === 'lead:found' ? 'log-lead' : 'log-skip'}>
                {event.payload.log}
              </pre>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
