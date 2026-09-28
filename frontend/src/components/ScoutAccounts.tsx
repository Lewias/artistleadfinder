import { useEffect, useState } from 'react';
import {
  Activity,
  ExternalLink,
  Globe2,
  Pause,
  Play,
  RotateCcw,
  SkipForward,
  Square,
  Target,
} from 'lucide-react';
import { api } from '../services/api';
import type { ScoutAccountRow } from '../services/types';
import { waitLabel } from '../lib/format';
import { Button } from './ui/button';
import { runStatus } from './scoutStatus';
import { DiscoveryMetricsTable } from './DiscoveryMetrics';

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

export function ScoutAccounts({
  rows,
  error,
  refresh,
  disabled,
}: {
  rows: ScoutAccountRow[] | undefined;
  error: string;
  refresh: () => void;
  disabled: boolean;
}) {
  const list = rows || [];
  const running = list.filter(row => row.run?.status === 'running' && row.run.stage !== 'interrupted').length;
  const found = list.reduce((sum, row) => sum + row.found, 0);
  return (
    <section className="accounts-section">
      <div className="section-title">
        <h2>
          Аккаунты <span className="page-count">{list.length}</span>
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
      {rows?.length === 0 && (
        <div className="panel empty-panel">
          Создайте профиль в разделе «Аккаунты» и войдите в Instagram — здесь появится карточка для запуска
          Lead Scout.
        </div>
      )}
      {list.map(row => (
        <AccountCard key={row.profile.id} row={row} disabled={disabled} refresh={refresh} />
      ))}
    </section>
  );
}

function AccountCard({
  row,
  disabled,
  refresh,
}: {
  row: ScoutAccountRow;
  disabled: boolean;
  refresh: () => void;
}) {
  const [target, setTarget] = useState(String(row.target));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => setTarget(String(row.target)), [row.target]);
  const { profile, run } = row;
  const id = profile.id;
  const active = Boolean(run && ['running', 'paused'].includes(run.status) && run.stage !== 'interrupted');
  const running = active && run?.status === 'running';
  const paused = active && run?.status === 'paused';
  const reached = row.found >= row.target;
  const targetValue = Number(target);
  const targetValid = Number.isInteger(targetValue) && targetValue >= 1 && targetValue <= 10000;
  const act = async (operation: () => Promise<unknown>) => {
    setBusy(true);
    setError('');
    try {
      await operation();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
      refresh();
    }
  };
  const saveTarget = async (reset = false) => {
    if (targetValue !== row.target || reset) {
      await api.request('scout.account_target', { profile_id: id, target: targetValue, reset });
    }
  };
  const control = (action: 'pause' | 'resume' | 'cancel') =>
    act(async () => {
      if (action === 'resume') await api.browser('open', { id });
      await api.request('jobs.control', { id: run!.id, action });
    });
  const start = () =>
    act(async () => {
      await saveTarget();
      await api.browser('open', { id });
      // The core picks the next sources by rotation and cooldown.
      await api.browser('scout', { id });
    });
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
        <label className="goal-field" title="Цель: сколько подходящих лидов найти">
          <Target size={15} aria-hidden="true" />
          <input
            aria-label={`Цель для ${profile.name}`}
            type="number"
            min={1}
            max={10000}
            value={target}
            disabled={busy || active}
            onChange={event => setTarget(event.target.value)}
            onBlur={() => targetValid && void act(() => saveTarget())}
          />
        </label>
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
        {!active && (
          <Button disabled={busy || disabled || !targetValid || reached} onClick={() => void start()}>
            <Play size={14} /> {row.found > 0 && !reached ? 'Продолжить поиск' : 'Start Scout'}
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
          {reached && (
            <Button
              icon
              variant="outline"
              aria-label="Новая цель с нуля"
              title="Новая цель с нуля"
              disabled={busy}
              onClick={() => void act(() => saveTarget(true))}
            >
              <RotateCcw size={16} />
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
            <dt>Найдено профилей</dt>
            <dd>{stats.discovered}</dd>
          </div>
          <div>
            <dt>Проанализировано</dt>
            <dd>{stats.analyzed}</dd>
          </div>
          <div>
            <dt>Лидов</dt>
            <dd className="good">{stats.leads}</dd>
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
