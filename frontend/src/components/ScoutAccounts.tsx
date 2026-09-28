import { useEffect, useState } from 'react';
import { Activity, ExternalLink, Globe2, Play, RotateCcw, SkipForward, Square, Target } from 'lucide-react';
import { api } from '../services/api';
import type { ScoutAccountRow } from '../services/types';
import { useResource } from '../hooks/useResource';
import { waitLabel } from '../lib/format';
import { Button } from './ui/button';
import { runStatus } from './scoutStatus';

const stepLabels: Record<string, string> = {
  source: 'Читаем источник',
  post: 'Разбираем публикацию',
  profile: 'Проверяем исполнителя',
};

export function ScoutAccounts({
  saveSources,
  disabled,
  onChange,
}: {
  saveSources: () => Promise<string[]>;
  disabled: boolean;
  onChange: () => void;
}) {
  const accounts = useResource<ScoutAccountRow[]>('scout.accounts', {}, 2000);
  const rows = accounts.data || [];
  const running = rows.filter(row => row.run?.status === 'running' && row.run.stage !== 'interrupted').length;
  const found = rows.reduce((sum, row) => sum + row.found, 0);
  return (
    <section className="accounts-section">
      <div className="section-title">
        <h2>
          Аккаунты <span className="page-count">{rows.length}</span>
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
      {accounts.error && (
        <p role="alert" className="error-text">
          {accounts.error}
        </p>
      )}
      {accounts.data?.length === 0 && (
        <div className="panel empty-panel">
          Создайте профиль в разделе «Аккаунты» и войдите в Instagram — здесь появится карточка для запуска
          поиска.
        </div>
      )}
      {rows.map(row => (
        <AccountCard
          key={row.profile.id}
          row={row}
          disabled={disabled}
          saveSources={saveSources}
          refresh={() => {
            accounts.refresh();
            onChange();
          }}
        />
      ))}
    </section>
  );
}

function AccountCard({
  row,
  disabled,
  saveSources,
  refresh,
}: {
  row: ScoutAccountRow;
  disabled: boolean;
  saveSources: () => Promise<string[]>;
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
  const reached = row.found >= row.target;
  const targetValue = Number(target);
  const targetValid = Number.isInteger(targetValue) && targetValue >= 1 && targetValue <= 10000;
  const act = async (operation: () => Promise<void>) => {
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
  const start = () =>
    act(async () => {
      await saveTarget();
      await api.browser('open', { id });
      if (run?.status === 'paused' && run.stage !== 'interrupted') {
        await api.request('jobs.control', { id: run.id, action: 'resume' });
      } else {
        const sources = await saveSources();
        await api.browser('scout', { id, sources });
      }
    });
  const percent = Math.min(100, Math.round((row.found / Math.max(1, row.target)) * 100));
  const continuing = (run?.status === 'paused' && run.stage !== 'interrupted') || (row.found > 0 && !reached);
  const status = runStatus(row);
  // Errors of finished runs are history, not something that needs attention now.
  const attention = active && Boolean(run?.error);
  const tone = reached ? 'green' : running ? 'violet' : attention ? 'amber' : 'muted';
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
        {running ? (
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => void act(() => api.request('jobs.control', { id: run!.id, action: 'pause' }))}
          >
            <Square size={14} /> Стоп
          </Button>
        ) : (
          <Button disabled={busy || disabled || !targetValid || reached} onClick={() => void start()}>
            <Play size={14} /> {continuing ? 'Продолжить' : 'Запуск'}
          </Button>
        )}
        <span className="goal-progress">
          Найдено <b>{row.found}</b> из {row.target}
        </span>
        <div className="icon-actions">
          {run?.status === 'paused' && active && run.error && (
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
      {run && (
        <p className="account-detail">
          {active && run.kind && stepLabels[run.kind] ? (
            <>
              {stepLabels[run.kind]}
              {run.url ? ` · ${run.url.replace('https://www.instagram.com', '')}` : ''}
              {running && !!run.wait_seconds && run.wait_reason
                ? ` · ${run.wait_reason} Осталось ${waitLabel(run.wait_seconds)}.`
                : ''}
            </>
          ) : (
            <>
              Последний запуск: найдено {run.found ?? 0}, кандидатов {run.candidates ?? 0}, публикаций в
              запасе {run.backlog ?? 0}
            </>
          )}
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
