import { useState } from 'react';
import { Activity, ExternalLink, Globe2, Pause, Play, SkipForward, Square, Target } from 'lucide-react';
import { api } from '../services/api';
import type { ScoutAccountRow } from '../services/types';
import { waitLabel } from '../lib/format';
import { Button } from './ui/button';
import { runStatus, skipReasons } from './scoutStatus';
import { DiscoveryMetricsTable } from './DiscoveryMetrics';
import { controlScout, errorText, scoutActive } from './accountRuns';

const stepLabels: Record<string, string> = {
  source: 'Сетка источника',
  tagged_grid: 'Отметки источника',
  post: 'Публикация',
  tagged_post: 'Отмеченная публикация',
  followers: 'Подписчики источника',
  following: 'Подписки источника',
  profile: 'Проверка профиля',
};
const handle = (url: string | null | undefined) =>
  url ? `@${url.replace(/\/$/, '').split('/').pop()}` : '—';

/** Progress of each account's latest Scout run; goals and starts live on the «Аккаунты» page. */
export function ScoutAccounts({
  rows,
  error,
  refresh,
}: {
  rows: ScoutAccountRow[] | undefined;
  error: string;
  refresh: () => void;
}) {
  const list = (rows || []).filter(row => row.run);
  const running = list.filter(row => row.run?.status === 'running' && row.run.stage !== 'interrupted').length;
  const found = list.reduce((sum, row) => sum + row.found, 0);
  if (!list.length && !error) return null;
  return (
    <section className="accounts-section">
      <div className="section-title">
        <h2>
          Запуски <span className="page-count">{list.length}</span>
        </h2>
        <div className="stat-pills">
          <span className="stat-pill">
            <Activity size={13} /> Запущено <b>{running}</b>
          </span>
          <span className="stat-pill">
            <Target size={13} /> Найдено <b>{found}</b>
          </span>
        </div>
      </div>
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      {list.map(row => (
        <AccountCard key={row.profile.id} row={row} refresh={refresh} />
      ))}
    </section>
  );
}

function AccountCard({ row, refresh }: { row: ScoutAccountRow; refresh: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const { profile, run } = row;
  const id = profile.id;
  const active = scoutActive(row);
  const running = active && run?.status === 'running';
  const paused = active && run?.status === 'paused';
  const reached = row.found >= row.target;
  const act = async (operation: () => Promise<unknown>) => {
    setBusy(true);
    setError('');
    try {
      await operation();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
      refresh();
    }
  };
  const control = (action: 'pause' | 'resume' | 'cancel') => act(() => controlScout(row, action));
  const percent = Math.min(100, Math.round((row.found / Math.max(1, row.target)) * 100));
  const status = runStatus(row);
  const attention = active && Boolean(run?.error);
  const tone = reached ? 'green' : running ? 'violet' : attention ? 'amber' : 'muted';
  const stats = run?.stats;
  return (
    <article className="panel account-card">
      <div className="account-head">
        <div>
          <h3>{profile.name}</h3>
          <div className="account-meta">
            <Globe2 size={13} />
            <span>{profile.proxy ? `${profile.proxy.host}:${profile.proxy.port}` : '—'}</span>
            <span className={`dot ${profile.cookie_count ? 'ok' : 'off'}`} />
            <span>{profile.cookie_count ? 'Сессия сохранена' : 'Нет сессии'}</span>
          </div>
        </div>
        <span className={`status-chip ${tone}`}>{status}</span>
      </div>
      <div className="account-controls">
        {running && (
          <Button variant="outline" disabled={busy} onClick={() => void control('pause')}>
            <Pause size={14} /> Пауза
          </Button>
        )}
        {paused && (
          <Button disabled={busy} onClick={() => void control('resume')}>
            <Play size={14} /> Продолжить
          </Button>
        )}
        {active && (
          <Button variant="danger" disabled={busy} onClick={() => void control('cancel')}>
            <Square size={14} /> Stop
          </Button>
        )}
        <span className="goal-progress">
          Найдено <b>{row.found}</b> из {row.target}
        </span>
        <div className="icon-actions">
          {paused && run?.error && (
            <Button
              icon
              variant="outline"
              aria-label="Пропустить страницу"
              title="Пропустить страницу"
              disabled={busy}
              onClick={() => void act(() => api.request('scout.skip', { id: run.id }))}
            >
              <SkipForward size={16} />
            </Button>
          )}
          <Button
            icon
            variant="outline"
            aria-label="Открыть браузер"
            title="Открыть браузер"
            disabled={busy}
            onClick={() => void act(() => api.browser('open', { id }))}
          >
            <ExternalLink size={16} />
          </Button>
        </div>
      </div>
      <div className="goal-bar" aria-hidden="true">
        <i style={{ width: `${percent}%` }} />
      </div>
      {stats && (
        <dl className="run-stats">
          <div>
            <dt>Источник</dt>
            <dd>{handle(stats.current_source)}</dd>
          </div>
          <div>
            <dt>Профиль</dt>
            <dd>{stats.current_profile ? `@${stats.current_profile}` : '—'}</dd>
          </div>
          <div>
            <dt>Источники</dt>
            <dd>
              {stats.sources_done.length} / {stats.sources.length}
            </dd>
          </div>
          <div>
            <dt>Кандидатов</dt>
            <dd>{stats.discovered}</dd>
          </div>
          <div>
            <dt>Прочитано</dt>
            <dd>{stats.resolved ?? stats.analyzed}</dd>
          </div>
          <div>
            <dt title="Профилей, тип которых определил классификатор">Тип определён</dt>
            <dd>{stats.classified ?? '—'}</dd>
          </div>
          <div>
            <dt>Лидов</dt>
            <dd className="good">{stats.leads}</dd>
          </div>
          <div>
            <dt>Обновлено</dt>
            <dd>{stats.leads_updated ?? 0}</dd>
          </div>
          <div>
            <dt>Пропущено</dt>
            <dd>{stats.skipped}</dd>
          </div>
          <div>
            <dt>Ошибок</dt>
            <dd className={stats.errors ? 'bad' : ''}>{stats.errors}</dd>
          </div>
        </dl>
      )}
      {stats?.skips && Object.keys(stats.skips).length > 0 && (
        <ul className="skip-breakdown" aria-label="Причины пропуска">
          {Object.entries(stats.skips)
            .sort((a, b) => b[1] - a[1])
            .map(([reason, count]) => (
              <li key={reason}>
                {skipReasons[reason] || reason}: <b>{count}</b>
              </li>
            ))}
        </ul>
      )}
      {stats?.providers && Object.keys(stats.providers).length > 0 && (
        <details className="discovery-metrics">
          <summary>Discovery по источникам</summary>
          <DiscoveryMetricsTable providers={stats.providers} />
        </details>
      )}
      {run && active && run.kind && stepLabels[run.kind] && (
        <p className="account-detail">
          {stepLabels[run.kind]}
          {run.url ? ` · ${run.url.replace('https://www.instagram.com', '')}` : ''}
          {running && !!run.wait_seconds && run.wait_reason
            ? ` · ${run.wait_reason} Осталось ${waitLabel(run.wait_seconds)}.`
            : ''}
          {run.backlog ? ` · публикаций в запасе ${run.backlog}` : ''}
        </p>
      )}
      {(error || attention) && (
        <p role="alert" className="error-text">
          {error || run?.error}
        </p>
      )}
      {!!run?.notices?.length && (
        <details className="account-notices">
          <summary>Пропуски и замечания ({run.notices.length})</summary>
          {run.notices.map((notice, i) => (
            <p key={i}>{notice}</p>
          ))}
        </details>
      )}
    </article>
  );
}
