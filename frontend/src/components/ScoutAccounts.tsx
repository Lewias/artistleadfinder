import { useEffect, useState } from 'react';
import { Play, Square, UserRound } from 'lucide-react';
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
  if (accounts.data?.length === 0) {
    return (
      <div className="panel">
        <h2>Аккаунты</h2>
        <p className="helper">
          Создайте браузерный профиль во вкладке «Профили» и войдите в Instagram — здесь появится строка для
          запуска поиска.
        </p>
      </div>
    );
  }
  return (
    <div className="panel scout-accounts">
      <h2>Аккаунты</h2>
      <p className="helper">
        У каждого аккаунта своя цель, свой темп и своё окно браузера. Аккаунты работают независимо и могут
        искать одновременно; один и тот же кандидат не проверяется дважды.
      </p>
      {accounts.error && (
        <p role="alert" className="error-text">
          {accounts.error}
        </p>
      )}
      {accounts.data?.map(row => (
        <AccountRow
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
    </div>
  );
}

function AccountRow({
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
  return (
    <article className="account-row">
      <div className="account-info">
        <div className="avatar">
          <UserRound size={18} />
        </div>
        <div>
          <h3>{profile.name}</h3>
          <p className="helper">
            {profile.cookie_count
              ? `Сессия сохранена (${profile.cookie_count} cookies)`
              : 'Сессия не сохранена'}
            {' · '}
            {profile.proxy ? `Прокси ${profile.proxy.host}:${profile.proxy.port}` : 'Без прокси'}
          </p>
          <p>
            <strong>{runStatus(row)}</strong>
            {run && active && run.kind && stepLabels[run.kind] && (
              <span className="helper">
                {' '}
                · {stepLabels[run.kind]}
                {run.url ? `: ${run.url}` : ''}
              </span>
            )}
          </p>
          {active && run?.status === 'running' && !!run.wait_seconds && run.wait_reason && (
            <p className="helper">
              {run.wait_reason} Осталось {waitLabel(run.wait_seconds)}.
            </p>
          )}
          {run && (
            <p className="helper">
              В последнем запуске: найдено {run.found ?? 0}, кандидатов {run.candidates ?? 0}, публикаций в
              запасе {run.backlog ?? 0}
            </p>
          )}
        </div>
      </div>
      <div className="account-goal">
        <div className="goal-line">
          <span>
            Найдено <strong>{row.found}</strong> из
          </span>
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
          <span>лидов</span>
        </div>
        <progress value={percent} max={100} />
        <div className="actions">
          {run?.status === 'running' && active ? (
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void act(() => api.request('jobs.control', { id: run.id, action: 'pause' }))}
            >
              <Square size={15} /> Стоп
            </Button>
          ) : (
            <Button disabled={busy || disabled || !targetValid || reached} onClick={() => void start()}>
              <Play size={15} /> {continuing ? 'Продолжить' : 'Запуск'}
            </Button>
          )}
          {run?.status === 'paused' && active && run.error && (
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void act(() => api.request('scout.skip', { id: run.id }))}
            >
              Пропустить страницу
            </Button>
          )}
          {reached && (
            <Button variant="outline" disabled={busy} onClick={() => void act(() => saveTarget(true))}>
              Новая цель с нуля
            </Button>
          )}
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => void act(() => api.browser('open', { id }))}
          >
            Открыть браузер
          </Button>
        </div>
      </div>
      {(error || run?.error) && (
        <p role="alert" className="error-text full">
          {error || run?.error}
        </p>
      )}
      {!!run?.notices?.length && (
        <details className="full">
          <summary>Пропуски и замечания ({run.notices.length})</summary>
          {run.notices.map((notice, i) => (
            <p key={i}>{notice}</p>
          ))}
        </details>
      )}
    </article>
  );
}
