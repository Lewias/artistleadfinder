import { useEffect, useMemo, useRef, useState } from 'react';
import { MessageCircle, Send } from 'lucide-react';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import { number, relativeTime } from '../lib/format';
import { Button } from '../components/ui/button';
import { ErrorToast } from '../components/Toaster';
import { errorText } from '../components/accountRuns';
import { SEND_STATES, awaitingMessages } from '../components/assistant/macSend';
import type { IMessageDialog, IMessageDialogThread, IMessageDialogsState } from '../services/types';

const MAX_TEXT = 2000;

/** A phone by its digits, an email in lower case: chat.db and the CRM write them differently. */
const handleKey = (value: string) =>
  value.includes('@') ? value.trim().toLowerCase() : value.replace(/\D/g, '');

const title = (row: { handle: string; contact: { name: string } | null }) => row.contact?.name || row.handle;

export function Dialogs() {
  const [fast, setFast] = useState(false);
  const live = useResource<IMessageDialogsState>('assistant.dialogs', {}, fast ? 2500 : 8000);
  const [state, setState] = useState<IMessageDialogsState>();
  const [chosen, setChosen] = useState('');
  const [thread, setThread] = useState<IMessageDialogThread>();
  const [filter, setFilter] = useState<'all' | 'waiting'>('all');
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const bodyRef = useRef<HTMLDivElement>(null);
  const seen = useRef('');

  useEffect(() => {
    if (live.data) setState(live.data);
  }, [live.data]);
  useEffect(() => setFast(awaitingMessages(state?.sends ?? [])), [state]);

  const load = async (handle: string) => {
    try {
      setThread(await api.request<IMessageDialogThread>('assistant.dialog', { handle }));
    } catch (err) {
      setError(errorText(err));
    }
  };
  const open = (handle: string) => {
    setChosen(handle);
    setText('');
    setError('');
    void load(handle);
  };

  // A new message in the open chat (theirs or ours): read it again.
  const current = state?.threads.find(row => row.handle === chosen);
  useEffect(() => {
    const mark = current ? `${current.last_at}|${current.messages}` : '';
    if (chosen && mark && seen.current && mark !== seen.current) void load(chosen);
    seen.current = mark;
  }, [chosen, current]);
  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [thread]);

  const send = async () => {
    const body = text.trim();
    if (!chosen || !body || busy) return;
    setBusy(true);
    setError('');
    try {
      await api.request('assistant.dialog_reply', { handle: chosen, text: body });
      setText('');
      setFast(true);
      live.refresh();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const rows = state?.threads ?? [];
  const waiting = rows.filter(row => !row.last_from_me);
  const shown: IMessageDialog[] = filter === 'waiting' ? waiting : rows;
  const sends = useMemo(() => {
    if (!chosen) return [];
    const key = handleKey(chosen);
    return (state?.sends ?? []).filter(send => handleKey(send.handle) === key).slice(0, 3);
  }, [state, chosen]);
  const available = state?.messages.available ?? true;
  const block = thread?.reply_block ?? '';

  return (
    <div className="dialogs-page">
      <ErrorToast message={error || live.error} />
      {state && !available ? (
        <section className="panel">
          <p className="helper">{state.messages.reason}</p>
          <p className="helper">
            Переписка берётся из «Сообщений» Mac: на Windows раздел пуст. Сообщения отсюда отправляет только
            приложение на Mac.
          </p>
        </section>
      ) : (
        <div className="dialogs-grid">
          <div className="panel dialogs-list">
            <div className="dialogs-thread-head">
              <strong>Переписки</strong>
              <select value={filter} onChange={event => setFilter(event.target.value as 'all' | 'waiting')}>
                <option value="all">Все · {number(rows.length)}</option>
                <option value="waiting">Ждут ответа · {number(waiting.length)}</option>
              </select>
            </div>
            {!shown.length && (
              <p className="helper">
                {live.loading && !state ? 'Читаю «Сообщения»…' : 'За последние 90 дней переписок нет.'}
              </p>
            )}
            {shown.map(row => (
              <button
                key={row.handle}
                type="button"
                className={`dialogs-row${chosen === row.handle ? ' active' : ''}`}
                onClick={() => open(row.handle)}
              >
                <span className="dialogs-row-top">
                  <span>
                    {!row.last_from_me && <i className="dialogs-dot" />}
                    {title(row)}
                  </span>
                  <small>{relativeTime(row.last_at, '')}</small>
                </span>
                <span className={`dialogs-preview${row.last_from_me ? '' : ' unread'}`}>
                  {row.last_from_me ? 'Вы: ' : ''}
                  {row.last_text || '—'}
                </span>
                <small className="helper">
                  {row.contact ? row.handle : 'нет в CRM'}
                  {row.contact?.statuses.length ? ` · ${row.contact.statuses.join(', ')}` : ''}
                </small>
              </button>
            ))}
          </div>

          <div className="panel dialogs-thread">
            {!thread ? (
              <p className="helper">
                <MessageCircle size={15} /> Выберите переписку слева.
              </p>
            ) : (
              <>
                <div className="dialogs-thread-head">
                  <strong>{title(thread)}</strong>
                  <span className="helper">
                    {thread.contact ? `${thread.handle} · ` : ''}
                    {number(thread.messages.length)} последних сообщений
                  </span>
                </div>
                <div className="dialogs-messages" ref={bodyRef}>
                  {thread.messages.map((message, index) => (
                    <div
                      key={`${message.at}-${index}`}
                      className={`dialogs-message ${message.from_me ? 'out' : 'in'}`}
                    >
                      <p>{message.text || '(вложение)'}</p>
                      <small>{relativeTime(message.at, '')}</small>
                    </div>
                  ))}
                  {!thread.messages.length && <p className="helper">Сообщений нет.</p>}
                </div>
                {sends.map(item => (
                  <p key={item.id} className="helper">
                    <span className={`send-state ${SEND_STATES[item.state].tone}`}>
                      {SEND_STATES[item.state].label}
                    </span>{' '}
                    «{item.text.slice(0, 80)}»{item.note ? ` · ${item.note}` : ''}
                  </p>
                ))}
                {block ? (
                  <p className="helper">{block}</p>
                ) : (
                  <div className="dialogs-compose">
                    <textarea
                      value={text}
                      rows={3}
                      maxLength={MAX_TEXT}
                      placeholder="Ответ — уйдёт через «Сообщения» этого Mac"
                      onChange={event => setText(event.target.value)}
                      onKeyDown={event => {
                        if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) void send();
                      }}
                    />
                    <Button disabled={busy || !text.trim()} onClick={() => void send()}>
                      <Send size={15} /> Отправить
                    </Button>
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
