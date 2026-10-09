import { useEffect, useState } from 'react';
import { Inbox, Mail, Phone, Square, UserPlus, EyeOff } from 'lucide-react';
import { api } from '../../services/api';
import { useResource } from '../../hooks/useResource';
import type { InboxScan, InboxState, OutreachSender } from '../../services/types';
import { number, plural, relativeTime } from '../../lib/format';
import { Button } from '../ui/button';
import { ErrorToast } from '../Toaster';
import { errorText } from '../accountRuns';

const periods = [
  [7, '7 дней'],
  [30, '30 дней'],
  [90, '3 месяца'],
  [365, 'год'],
] as const;

function scanLine(scan: InboxScan) {
  const totals = `ответили ${number(scan.replied)} · найдено ${number(scan.found)}`;
  if (scan.status === 'running')
    return `Читаю Директ ${scan.sender_name}: ${number(scan.done)} из ${plural(scan.total, ['чата', 'чатов', 'чатов'])} · ${totals}`;
  const when = relativeTime(scan.finished_at ?? scan.started_at);
  const read = `${scan.sender_name} · ${when} · прочитано ${number(scan.done - scan.errors)} из ${number(scan.total)}`;
  return `${scan.status === 'done' ? 'Последняя проверка' : 'Остановлено'}: ${read} · ${totals}`;
}

/** «Ответы»: phones and emails people wrote back to outreach, read from the sender's Direct. */
export function InboxPanel({ senders }: { senders?: OutreachSender[] }) {
  const inbox = useResource<InboxState>('inbox.state', {}, 3000);
  const [state, setState] = useState<InboxState>();
  const [sender, setSender] = useState('');
  const [days, setDays] = useState(30);
  const [selected, setSelected] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => {
    if (inbox.data) setState(inbox.data);
  }, [inbox.data]);
  useEffect(() => {
    if (!sender && senders?.length) setSender(senders[0].id);
  }, [sender, senders]);

  const scan = state?.scan;
  const running = scan?.status === 'running';
  const findings = state?.findings ?? [];
  const chosen = selected.filter(id => findings.some(item => item.id === id));
  const allChecked = !!findings.length && chosen.length === findings.length;
  const account = senders?.find(item => item.id === sender);

  const call = async <T,>(method: string, params: object = {}) => {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      return await api.request<T>(method, params);
    } catch (err) {
      setError(errorText(err));
      return undefined;
    } finally {
      setBusy(false);
    }
  };
  const start = async () => {
    const next = await call<InboxState>('inbox.start', { sender, days });
    if (next) setState(next);
  };
  const stop = async () => {
    const next = await call<InboxState>('inbox.stop');
    if (next) setState(next);
  };
  const add = async () => {
    const result = await call<{ added: number; merged: number; state: InboxState }>('inbox.add_to_crm', {
      ids: chosen,
    });
    if (!result) return;
    setState(result.state);
    setSelected([]);
    setNotice(
      `В CRM iMessage: ${plural(result.added, ['новый контакт', 'новых контакта', 'новых контактов'])}` +
        (result.merged ? `, ${number(result.merged)} дополнено` : '') +
        '.',
    );
  };
  const hide = async () => {
    const next = await call<InboxState>('inbox.hide', { ids: chosen });
    if (!next) return;
    setState(next);
    setSelected([]);
  };

  return (
    <div className="inbox-panel">
      <section className="panel inbox-controls">
        <div className="inbox-run">
          <label>
            <span>Аккаунт</span>
            <select
              value={sender}
              disabled={running || busy}
              onChange={event => setSender(event.target.value)}
            >
              {(senders ?? []).map(item => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Кому писали за</span>
            <select
              value={days}
              disabled={running || busy}
              onChange={event => setDays(Number(event.target.value))}
            >
              {periods.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          {running ? (
            <Button variant="outline" disabled={busy} onClick={() => void stop()}>
              <Square size={14} /> Остановить
            </Button>
          ) : (
            <Button disabled={busy || !sender} onClick={() => void start()}>
              <Inbox size={15} /> Проверить Директ
            </Button>
          )}
        </div>
        {scan && <p className="inbox-status">{scanLine(scan)}</p>}
        {running && (
          <div className="inbox-progress" aria-hidden="true">
            <span style={{ width: `${scan.total ? (scan.done / scan.total) * 100 : 0}%` }} />
          </div>
        )}
        {running && scan.waiting && <p className="helper">{scan.waiting}</p>}
        {scan?.status === 'stopped' && scan.reason && <p className="helper">{scan.reason}</p>}
        {!running && account && !account.open && (
          <p className="helper">Откройте окно аккаунта в разделе «Аккаунты»: чаты читаются в нём.</p>
        )}
        <p className="helper">
          Открываются чаты тех, кому писала рассылка с этого аккаунта. Ничего не печатается и не отправляется,
          но собеседник увидит, что сообщения просмотрены.
        </p>
      </section>

      <div className="inbox-actions">
        <strong>
          {findings.length
            ? `На проверку: ${plural(findings.length, ['контакт', 'контакта', 'контактов'])}`
            : 'Новых контактов нет'}
        </strong>
        {!!state?.counts.added && <span className="helper">Уже в CRM: {number(state.counts.added)}</span>}
        <span className="inbox-actions-buttons">
          <Button variant="outline" disabled={busy || !chosen.length} onClick={() => void hide()}>
            <EyeOff size={14} /> Скрыть
          </Button>
          <Button disabled={busy || !chosen.length} onClick={() => void add()}>
            <UserPlus size={15} /> Добавить в CRM iMessage{chosen.length ? ` (${chosen.length})` : ''}
          </Button>
        </span>
      </div>
      <ErrorToast message={error || inbox.error} />
      {notice && <p className="helper">{notice}</p>}

      {findings.length > 0 && (
        <div className="crm-table inbox-table">
          <table>
            <thead>
              <tr>
                <th className="crm-check-cell">
                  <input
                    type="checkbox"
                    aria-label="Выбрать все"
                    checked={allChecked}
                    onChange={() => setSelected(allChecked ? [] : findings.map(item => item.id))}
                  />
                </th>
                <th>Артист</th>
                <th>Контакт</th>
                <th>Что написал</th>
                <th>Аккаунт</th>
              </tr>
            </thead>
            <tbody>
              {findings.map(item => (
                <tr key={item.id}>
                  <td className="crm-check-cell">
                    <input
                      type="checkbox"
                      aria-label={`Выбрать ${item.username}`}
                      checked={chosen.includes(item.id)}
                      onChange={() =>
                        setSelected(current =>
                          current.includes(item.id)
                            ? current.filter(id => id !== item.id)
                            : [...current, item.id],
                        )
                      }
                    />
                  </td>
                  <td>
                    <strong>@{item.username}</strong>
                  </td>
                  <td>
                    <span className="inbox-value">
                      {item.kind === 'phone' ? <Phone size={13} /> : <Mail size={13} />}
                      {item.value}
                    </span>
                    {item.guessed && (
                      <small className="inbox-guessed" title={`В сообщении: ${item.raw}`}>
                        код страны угадан
                      </small>
                    )}
                  </td>
                  <td className="inbox-snippet">«{item.snippet}»</td>
                  <td className="helper">{item.sender}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
