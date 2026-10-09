import { useEffect, useState } from 'react';
import { Rocket, Square } from 'lucide-react';
import { api } from '../../services/api';
import { useResource } from '../../hooks/useResource';
import type { AutopilotState, OutreachSender } from '../../services/types';
import { number } from '../../lib/format';
import { Button } from '../ui/button';
import { ErrorToast } from '../Toaster';
import { errorText } from '../accountRuns';

const ACTIVE = ['starting', 'scouting', 'sending'];

function statusLine(state: AutopilotState) {
  if (state.status === 'starting') return `${state.account}: запускаю парсинг…`;
  if (state.status === 'scouting')
    return `${state.account}: парсинг, найдено ${number(state.scout_found)} из ${number(state.target)}. Потом рассылка всем новым.`;
  if (state.status === 'sending')
    return `${state.account}: рассылка, отправлено ${number(state.sent)} из ${number(state.total)}.`;
  return state.message;
}

/** «Найти и написать»: one account finds N new leads, then «Рассылка» writes to all of them. */
export function AutopilotBar({ senders, disabled }: { senders?: OutreachSender[]; disabled: boolean }) {
  const autopilot = useResource<AutopilotState>('autopilot.state', {}, 3000);
  const [state, setState] = useState<AutopilotState>();
  const [profile, setProfile] = useState('');
  const [target, setTarget] = useState(40);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (!autopilot.data) return;
    setState(autopilot.data);
    // A running autopilot shows its own account and goal.
    if (ACTIVE.includes(autopilot.data.status)) {
      setProfile(autopilot.data.profile_id);
      setTarget(autopilot.data.target);
    }
  }, [autopilot.data]);
  useEffect(() => {
    if (!profile && senders?.length) setProfile(senders[0].id);
  }, [profile, senders]);
  const active = !!state && ACTIVE.includes(state.status);

  const start = async () => {
    setBusy(true);
    setError('');
    try {
      setState(await api.request<AutopilotState>('autopilot.start', { profile_id: profile, target }));
      try {
        // The parser starts the usual way, in the account's own window.
        await api.browser('open', { id: profile });
        await api.browser('scout', { id: profile });
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
  const cancel = async () => {
    setBusy(true);
    try {
      setState(await api.request<AutopilotState>('autopilot.cancel'));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel autopilot">
      <div className="autopilot-row">
        <strong className="autopilot-title">
          <Rocket size={16} /> Найти и написать
        </strong>
        <label>
          <span>Аккаунт</span>
          <select
            value={profile}
            disabled={active || busy}
            onChange={event => setProfile(event.target.value)}
          >
            {(senders ?? []).map(item => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Новых лидов</span>
          <input
            type="number"
            min={1}
            max={1000}
            value={target}
            disabled={active || busy}
            onChange={event => setTarget(Number(event.target.value))}
          />
        </label>
        {active ? (
          <Button variant="outline" disabled={busy} onClick={() => void cancel()}>
            <Square size={14} /> Остановить автопилот
          </Button>
        ) : (
          <Button disabled={busy || disabled || !profile || target < 1} onClick={() => void start()}>
            <Rocket size={15} /> Найти и написать
          </Button>
        )}
      </div>
      {state && state.status !== 'idle' && <p className="autopilot-status">{statusLine(state)}</p>}
      <p className="helper">
        Парсер ищет новых лидов этим аккаунтом, а когда закончит, «Рассылка» сама пишет всем из списка, кому
        ещё не писали, сообщениями ниже. «Остановить автопилот» отменяет только запуск рассылки; идущий
        парсинг или рассылку останавливайте там же, где обычно.
      </p>
      <ErrorToast message={error} />
    </section>
  );
}
