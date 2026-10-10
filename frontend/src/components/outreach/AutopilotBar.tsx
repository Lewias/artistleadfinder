import { useEffect, useState } from 'react';
import { Radar, Rocket, Square } from 'lucide-react';
import { api } from '../../services/api';
import { useResource } from '../../hooks/useResource';
import type {
  AutopilotState,
  OutreachSender,
  OutreachWorkspace,
  ScoutAccountRow,
} from '../../services/types';
import { number } from '../../lib/format';
import { Button } from '../ui/button';
import { ErrorToast } from '../Toaster';
import { controlScout, errorText, scoutActive } from '../accountRuns';

const ACTIVE = ['starting', 'scouting', 'sending'];
const TARGET_KEY = 'autopilot-target';

function readTarget() {
  try {
    const value = Number(localStorage.getItem(TARGET_KEY));
    return value >= 1 && value <= 1000 ? value : 40;
  } catch {
    return 40;
  }
}

function statusLine(state: AutopilotState) {
  const who = state.accounts.join(', ');
  if (state.status === 'starting') return `Открываю окна и запускаю парсинг: ${who}…`;
  if (state.status === 'scouting')
    return `Ищу артистов: найдено ${number(state.scout_found)} из ${number(state.goal)} · ${who}. Потом сразу рассылка.`;
  if (state.status === 'sending')
    return `Пишу найденным: отправлено ${number(state.sent)} из ${number(state.total)}.`;
  return state.message;
}

function progress(state: AutopilotState) {
  if (state.status === 'scouting') return state.goal ? state.scout_found / state.goal : 0;
  if (state.status === 'sending') return state.total ? state.sent / state.total : 0;
  return 0;
}

/** «Найти и написать»: the chosen accounts find N new artists each, then write to all of them. */
export function AutopilotBar({ accounts }: { accounts: ScoutAccountRow[] }) {
  const autopilot = useResource<AutopilotState>('autopilot.state', {}, 3000);
  const workspace = useResource<OutreachWorkspace>('outreach.workspace', {}, 4000);
  const senders = useResource<OutreachSender[]>('outreach.senders', {}, 5000);
  const [state, setState] = useState<AutopilotState>();
  const [target, setTarget] = useState(readTarget);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!autopilot.data) return;
    setState(autopilot.data);
    if (ACTIVE.includes(autopilot.data.status)) setTarget(autopilot.data.target);
  }, [autopilot.data]);

  const all = senders.data ?? [];
  const chosen = workspace.data?.sender_ids ?? [];
  // With one account and none chosen, that one finds and writes.
  const profiles = chosen.length ? chosen : all.length === 1 ? [all[0].id] : [];
  const names = all.filter(item => profiles.includes(item.id)).map(item => item.name);
  const messages = workspace.data?.messages.length ?? 0;
  const active = !!state && ACTIVE.includes(state.status);
  const blocked = !profiles.length
    ? 'Отметьте аккаунты в «Настройках» → «Аккаунты и рассылка».'
    : !messages
      ? 'Добавьте хотя бы одно сообщение выше.'
      : '';

  const start = async () => {
    setBusy(true);
    setError('');
    try {
      try {
        localStorage.setItem(TARGET_KEY, String(target));
      } catch {
        // Only a remembered number.
      }
      setState(await api.request<AutopilotState>('autopilot.start', { profile_ids: profiles, target }));
      try {
        // Each account parses the usual way, in its own window.
        for (const id of profiles) {
          await api.browser('open', { id });
          await api.browser('scout', { id });
        }
      } catch (err) {
        setState(await api.request<AutopilotState>('autopilot.cancel'));
        throw err;
      }
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
      autopilot.refresh();
    }
  };
  // One button stops the whole chain: the parser runs, the outreach and the watch.
  const stop = async () => {
    setBusy(true);
    setError('');
    try {
      const status = state?.status;
      const ids = state?.profile_ids ?? [];
      setState(await api.request<AutopilotState>('autopilot.cancel'));
      for (const row of accounts.filter(item => ids.includes(item.profile.id) && scoutActive(item)))
        await controlScout(row, 'cancel');
      if (status === 'sending') await api.request('outreach.workspace_stop');
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
      autopilot.refresh();
    }
  };

  return (
    <section className="panel autopilot">
      <div className="board-heading">
        <h2>
          <Rocket size={17} /> Найти и написать
        </h2>
        <span className="helper">
          {names.length ? `Аккаунты: ${names.join(', ')}` : 'Аккаунты не выбраны'}
        </span>
      </div>
      <div className="autopilot-row">
        <label>
          <span>Новых артистов на аккаунт</span>
          <input
            type="number"
            min={1}
            max={1000}
            value={target}
            disabled={active || busy}
            onChange={event => setTarget(Number(event.target.value))}
          />
        </label>
        <span className="autopilot-spacer" />
        {active ? (
          <Button variant="outline" disabled={busy} onClick={() => void stop()}>
            <Square size={14} /> Остановить всё
          </Button>
        ) : (
          <Button
            className="autopilot-go"
            disabled={busy || !!blocked || target < 1}
            title={blocked || undefined}
            onClick={() => void start()}
          >
            <Radar size={16} /> Найти артистов и написать
          </Button>
        )}
      </div>
      {active && state && (
        <div className="inbox-progress" aria-hidden="true">
          <span style={{ width: `${Math.min(100, progress(state) * 100)}%` }} />
        </div>
      )}
      {state && state.status !== 'idle' ? (
        <p className="autopilot-status" role="status">
          {statusLine(state)}
        </p>
      ) : (
        blocked && <p className="helper">{blocked}</p>
      )}
      <p className="helper">
        Одна кнопка: окна аккаунтов откроются сами, парсер найдёт новых артистов по источникам выше, а когда
        закончит — каждому из них уйдёт одно из сообщений. Тем, кому уже писали или с кем есть переписка в
        Директе, не пишем.
      </p>
      <ErrorToast message={error} />
    </section>
  );
}
