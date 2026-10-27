import { useEffect, useRef, useState } from 'react';
import {
  ArrowUpRight,
  ChevronRight,
  KeyRound,
  Laptop,
  MessageSquarePlus,
  Play,
  Send,
  Smartphone,
  Sparkles,
  Square,
  Wrench,
  X,
} from 'lucide-react';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import ReactMarkdown from 'react-markdown';
import type {
  AssistantChatDraft,
  AssistantDraft,
  AssistantItem,
  AssistantSend,
  AssistantSendPreview,
  AssistantState,
} from '../services/types';
import { PageHeader } from '../components/PageHeader';
import { Modal } from '../components/Modal';
import { SEND_STATES, awaitingMessages } from '../components/assistant/macSend';
import { chatBlocks } from '../components/assistant/chat';
import { Button } from '../components/ui/button';
import { ErrorToast } from '../components/Toaster';
import { errorText } from '../lib/errors';

const SUGGESTIONS = [
  'Кто ждёт ответа в iMessage?',
  'Кто из лидов может купить бит прямо сейчас?',
  'Я кому-то обещал пак битов или файл и забыл отправить?',
  'Кому стоит написать сегодня?',
  'Подготовь ответы всем, кто ждёт больше дня',
  'Обнови статусы в CRM по последним перепискам',
];

const dollars = (value: number) => (value < 0.01 && value > 0 ? '< $0.01' : `$${value.toFixed(2)}`);

function KeyForm({ onSaved, compact }: { onSaved: () => void; compact?: boolean }) {
  const [key, setKey] = useState('');
  const [error, setError] = useState('');
  const save = async () => {
    setError('');
    try {
      await api.request('assistant.set_key', { key });
      setKey('');
      onSaved();
    } catch (err) {
      setError(errorText(err));
    }
  };
  return (
    <div className="assistant-key">
      {!compact && (
        <p className="helper">
          Ассистент работает через API Anthropic. Создайте ключ на console.anthropic.com (раздел API Keys) и
          вставьте сюда — он хранится зашифрованным на этом компьютере. Обычная работа на Haiku 5.5 стоит
          центы в день; сложные ответы Haiku сам передаёт Sonnet 5.5.
        </p>
      )}
      <div className="assistant-key-row">
        <input
          type="password"
          placeholder="sk-ant-…"
          value={key}
          autoComplete="off"
          onChange={event => setKey(event.target.value)}
        />
        <Button disabled={!key.trim()} onClick={() => void save()}>
          <KeyRound size={14} /> Сохранить ключ
        </Button>
      </div>
      <ErrorToast message={error} />
    </div>
  );
}

function DraftCard({
  draft,
  chosen,
  mac,
  onChoose,
  onChanged,
  onSendMac,
}: {
  draft: AssistantDraft;
  chosen: boolean;
  /** Messages on this Mac can send it (after a confirmation). */
  mac: boolean;
  onChoose: (value: boolean) => void;
  onChanged: () => void;
  onSendMac: () => void;
}) {
  const [text, setText] = useState(draft.text);
  const [error, setError] = useState('');
  const edited = text.trim() !== draft.text;
  const act = async (method: string, params: object) => {
    setError('');
    try {
      await api.request(method, params);
      onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  };
  return (
    <article className={`assistant-draft${chosen ? ' chosen' : ''}`}>
      <header>
        <label className="check-row">
          <input type="checkbox" checked={chosen} onChange={event => onChoose(event.target.checked)} />
          <b>{draft.contact_name || draft.handle}</b>
        </label>
        <span className={`assistant-model${draft.strong ? ' strong' : ''}`}>
          {draft.strong && <Sparkles size={12} />} {draft.model}
        </span>
      </header>
      <small className="helper">
        {draft.handle}
        {draft.reason ? ` · ${draft.reason}` : ''}
      </small>
      <textarea
        rows={Math.min(8, Math.max(3, text.split('\n').length + 1))}
        value={text}
        onChange={event => setText(event.target.value)}
      />
      <div className="assistant-draft-actions">
        {edited && (
          <Button
            variant="outline"
            onClick={() => void act('assistant.draft_update', { id: draft.id, text })}
          >
            Сохранить правку
          </Button>
        )}
        <Button variant="outline" onClick={() => void act('assistant.draft_reject', { id: draft.id })}>
          <X size={14} /> Отклонить
        </Button>
        <Button
          variant={mac ? 'outline' : undefined}
          disabled={edited}
          title={edited ? 'Сначала сохраните правку' : 'В очередь iMessage: отправит Команда на iPhone'}
          onClick={() => void act('assistant.drafts_send', { ids: [draft.id] })}
        >
          <Smartphone size={14} /> В очередь iPhone
        </Button>
        {mac && (
          <Button
            disabled={edited}
            title={edited ? 'Сначала сохраните правку' : 'Через Сообщения на этом Mac, после подтверждения'}
            onClick={onSendMac}
          >
            <Laptop size={14} /> Отправить с Mac
          </Button>
        )}
      </div>
      <ErrorToast message={error} />
    </article>
  );
}

/** The answer as Markdown; links stay text (the app window must not navigate away). */
function Markdown({ text }: { text: string }) {
  return (
    <ReactMarkdown
      components={{
        a: ({ href, children }) => (
          <span className="assistant-link" title={href}>
            {children}
          </span>
        ),
      }}
    >
      {text}
    </ReactMarkdown>
  );
}

/** What the assistant did for this answer, folded into one line. */
function Steps({ steps, strong }: { steps: AssistantItem[]; strong: string }) {
  if (!steps.length) return null;
  const label = (step: AssistantItem) =>
    step.kind === 'escalation' ? `Передал задачу ${strong}` : step.text;
  return (
    <details className="assistant-steps">
      <summary>
        <ChevronRight size={13} className="chevron" />
        <span>Шаги · {[...new Set(steps.map(label))].join(' · ')}</span>
      </summary>
      <ul>
        {steps.map((step, index) => (
          <li key={index} className={step.kind}>
            {step.kind === 'escalation' ? <ArrowUpRight size={12} /> : <Wrench size={11} />} {label(step)}
          </li>
        ))}
      </ul>
    </details>
  );
}

/** A draft the assistant made in this chat: it goes out only when the user presses a button here. */
function ChatDraftCard({
  draft,
  pending,
  send,
  mac,
  onSendMac,
  onAct,
}: {
  draft: AssistantChatDraft;
  pending?: AssistantDraft;
  send?: AssistantSend;
  mac: boolean;
  onSendMac: (draft: AssistantDraft) => void;
  onAct: (method: string, params: object) => void;
}) {
  const state = send
    ? { label: SEND_STATES[send.state].label, tone: SEND_STATES[send.state].tone }
    : draft.status === 'queued'
      ? { label: 'В очереди iPhone', tone: 'wait' }
      : draft.status === 'rejected'
        ? { label: 'Не отправлено', tone: 'idle' }
        : null;
  return (
    <article className="assistant-card">
      <header>
        <span>
          <b>{draft.contact_name || draft.handle}</b> <small className="helper">{draft.handle}</small>
        </span>
        {state && <span className={`send-state ${state.tone}`}>{state.label}</span>}
        {!state && pending && (
          <span className={`assistant-model${pending.strong ? ' strong' : ''}`}>
            {pending.strong && <Sparkles size={12} />} {pending.model}
          </span>
        )}
      </header>
      <div className="assistant-send-text">{pending?.text ?? draft.text}</div>
      {send?.note && send.state !== 'sent' && send.state !== 'delivered' && (
        <small className="helper">{send.note}</small>
      )}
      {pending && (
        <div className="assistant-card-actions">
          {mac && (
            <Button onClick={() => onSendMac(pending)}>
              <Laptop size={14} /> Отправить с Mac
            </Button>
          )}
          <Button
            variant={mac ? 'outline' : undefined}
            onClick={() => onAct('assistant.drafts_send', { ids: [pending.id] })}
          >
            <Smartphone size={14} /> В очередь iPhone
          </Button>
          <Button variant="outline" onClick={() => onAct('assistant.draft_reject', { id: pending.id })}>
            Не отправлять
          </Button>
          <small className="helper">Уйдёт ровно этот текст. Править — в «Черновиках» справа.</small>
        </div>
      )}
    </article>
  );
}

/** The last step before Messages sends: the exact recipient and text, one button. */
function ConfirmSend({
  preview,
  onClose,
  onSent,
}: {
  preview: AssistantSendPreview;
  onClose: () => void;
  onSent: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const send = async () => {
    setBusy(true);
    setError('');
    try {
      await api.request('assistant.send_confirm', { id: preview.draft_id, token: preview.token });
      onSent();
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  };
  return (
    <Modal
      title="Отправить через Сообщения?"
      narrow
      icon={<Laptop size={18} />}
      onClose={() => !busy && onClose()}
    >
      <p className="helper">
        <b>Кому:</b> {preview.contact}
        {preview.contact !== preview.recipient ? ` · ${preview.recipient}` : ''}
      </p>
      <div className="assistant-send-text">{preview.text}</div>
      <p className="helper">
        Сообщение уйдёт один раз, с этого Mac, ровно в таком виде. Статус придёт из базы Сообщений; если он
        неизвестен, проверьте переписку — повторно приложение его не отправит.
      </p>
      <ErrorToast message={error} />
      <div className="actions">
        <Button disabled={busy} onClick={() => void send()}>
          <Send size={14} /> Отправить
        </Button>
        <Button variant="outline" disabled={busy} onClick={onClose}>
          Отмена
        </Button>
      </div>
    </Modal>
  );
}

/** «Ассистент»: the chat with Claude, its drafts waiting for the user, and what it costs. */
export function Assistant() {
  const [poll, setPoll] = useState(10000);
  const state = useResource<AssistantState>('assistant.state', {}, poll);
  const data = state.data;
  const [question, setQuestion] = useState('');
  const [chosen, setChosen] = useState<number[]>([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [changingKey, setChangingKey] = useState(false);
  const [confirming, setConfirming] = useState<AssistantSendPreview | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const waiting = awaitingMessages(data?.sends ?? []);

  // Often while it works or Messages is sending, rarely otherwise.
  useEffect(() => setPoll(data?.running ? 1500 : waiting ? 3000 : 10000), [data?.running, waiting]);
  useEffect(() => bottom.current?.scrollIntoView({ block: 'end' }), [data?.items.length, data?.running]);
  useEffect(() => {
    const ids = new Set(data?.drafts.map(draft => draft.id));
    setChosen(value => value.filter(id => ids.has(id)));
  }, [data?.drafts]);

  const call = async (method: string, params: object = {}) => {
    setError('');
    try {
      const result = await api.request<AssistantState & { campaign_id?: number }>(method, params);
      state.refresh();
      return result;
    } catch (err) {
      setError(errorText(err));
      return undefined;
    }
  };
  const ask = async (text: string) => {
    if (!text.trim()) return;
    const result = await call('assistant.ask', { text });
    if (result) {
      setQuestion('');
      setPoll(1500);
    }
  };
  const sendMac = async (draft: AssistantDraft) => {
    setNotice('');
    setError('');
    try {
      setConfirming(await api.request<AssistantSendPreview>('assistant.send_preview', { id: draft.id }));
    } catch (err) {
      setError(errorText(err));
    }
  };
  const sendChosen = async () => {
    const result = await call('assistant.drafts_send', { ids: chosen });
    if (result?.campaign_id) {
      setNotice(
        `Отправлено в очередь iMessage (рассылка №${result.campaign_id}). Запустите Команду на iPhone.`,
      );
      setChosen([]);
    }
  };

  if (!data) return <div className="screen">{state.error || 'Загрузка…'}</div>;
  const blocks = chatBlocks(data.items);
  const live = data.running;
  if (!data.configured)
    return (
      <div className="screen work-flow">
        <PageHeader page="assistant" />
        <section className="panel board">
          <div className="board-heading">
            <h2>Подключите Claude</h2>
          </div>
          <KeyForm onSaved={state.refresh} />
        </section>
      </div>
    );

  return (
    <div className="screen work-flow">
      <PageHeader
        page="assistant"
        count={`${dollars(data.usage.today)} сегодня`}
        actions={
          <Button variant="outline" disabled={data.running} onClick={() => void call('assistant.new_chat')}>
            <MessageSquarePlus size={14} /> Новый чат
          </Button>
        }
      />
      <div className="assistant-layout">
        <section className="assistant-chat">
          <div className="assistant-feed">
            {!data.items.length && !data.running && (
              <div className="assistant-empty">
                <div className="assistant-empty-title">Что хотите узнать?</div>
                <div className="assistant-suggestions">
                  {SUGGESTIONS.map(item => (
                    <button key={item} type="button" onClick={() => void ask(item)}>
                      {item}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {blocks.map((block, index) =>
              block.kind === 'user' ? (
                <div key={index} className="assistant-msg user">
                  <div className="assistant-msg-bubble">{block.text}</div>
                </div>
              ) : (
                <div key={index} className="assistant-msg">
                  {block.texts.map((value, line) => (
                    <div key={line} className="assistant-md">
                      <Markdown text={value} />
                    </div>
                  ))}
                  {block.drafts.map(id => {
                    const draft = data.chat_drafts.find(item => item.id === id);
                    return (
                      draft && (
                        <ChatDraftCard
                          key={id}
                          draft={draft}
                          pending={data.drafts.find(item => item.id === id)}
                          send={data.sends.find(item => item.draft_id === id)}
                          mac={data.mac_send.enabled}
                          onSendMac={pending => void sendMac(pending)}
                          onAct={(method, params) => void call(method, params)}
                        />
                      )
                    );
                  })}
                  {!(live && index === blocks.length - 1) && (
                    <Steps steps={block.steps} strong={data.models.strong} />
                  )}
                  {live && index === blocks.length - 1 && (
                    <div className="assistant-working">
                      <span className="assistant-dot" /> {data.step || 'Думаю'}…
                    </div>
                  )}
                </div>
              ),
            )}
            {data.running && data.items[data.items.length - 1]?.kind === 'user' && (
              <div className="assistant-msg">
                <div className="assistant-working">
                  <span className="assistant-dot" /> {data.step || 'Думаю'}…
                </div>
              </div>
            )}
            {data.error && <p className="board-warning">{data.error}</p>}
            <div ref={bottom} />
          </div>
          <form
            className="assistant-ask"
            onSubmit={event => {
              event.preventDefault();
              void ask(question);
            }}
          >
            <textarea
              rows={2}
              placeholder="Спросите что угодно о лидах и переписке…"
              value={question}
              onChange={event => setQuestion(event.target.value)}
              onKeyDown={event => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  void ask(question);
                }
              }}
            />
            {data.running ? (
              <Button type="button" variant="outline" onClick={() => void call('assistant.stop')}>
                <Square size={14} /> Остановить
              </Button>
            ) : (
              <Button type="submit" disabled={!question.trim()}>
                <Send size={14} /> Спросить
              </Button>
            )}
          </form>
          <p className="assistant-disclosure">
            Вопросы и фрагменты переписок, которые просматривает ассистент, отправляются в API Anthropic.
            Ассистент готовит сообщения, но уходят они только после вашей кнопки.
          </p>
          <ErrorToast message={error || state.error} />
        </section>

        <aside className="assistant-side">
          {data.actions.length > 0 && (
            <section className="panel">
              <div className="board-heading">
                <h2>Действия</h2>
                <span className="helper">Выполняются только по вашей кнопке</span>
              </div>
              <div className="assistant-drafts">
                {data.actions.map(action => (
                  <article key={action.id} className={`assistant-action ${action.status}`}>
                    <p>{action.summary}</p>
                    {action.status === 'pending' ? (
                      <div className="assistant-draft-actions">
                        <Button
                          variant="outline"
                          onClick={() => void call('assistant.action_reject', { id: action.id })}
                        >
                          <X size={14} /> Отклонить
                        </Button>
                        <Button onClick={() => void call('assistant.action_run', { id: action.id })}>
                          <Play size={14} /> Выполнить
                        </Button>
                      </div>
                    ) : (
                      <small className={action.status === 'failed' ? 'board-warning' : 'helper'}>
                        {action.status === 'failed' ? 'Не выполнено: ' : '✓ '}
                        {action.result}
                      </small>
                    )}
                  </article>
                ))}
              </div>
            </section>
          )}
          <section className="panel">
            <div className="board-heading">
              <h2>Черновики ({data.drafts.length})</h2>
              {chosen.length > 0 && (
                <Button onClick={() => void sendChosen()}>
                  <Smartphone size={14} /> В очередь iPhone ({chosen.length})
                </Button>
              )}
            </div>
            {notice && <p className="helper">{notice}</p>}
            {!data.drafts.length && (
              <p className="empty-copy">
                Здесь появятся сообщения, которые подготовит ассистент. Ничего не уйдёт без вашей кнопки.
              </p>
            )}
            <div className="assistant-drafts">
              {data.drafts.map(draft => (
                <DraftCard
                  key={`${draft.id}:${draft.text}`}
                  draft={draft}
                  chosen={chosen.includes(draft.id)}
                  mac={data.mac_send.enabled}
                  onChoose={value =>
                    setChosen(list => (value ? [...list, draft.id] : list.filter(id => id !== draft.id)))
                  }
                  onChanged={state.refresh}
                  onSendMac={() => void sendMac(draft)}
                />
              ))}
            </div>
          </section>

          {data.sends.length > 0 && (
            <section className="panel">
              <div className="board-heading">
                <h2>Отправлено с Mac</h2>
                <span className="helper">Статус из базы Сообщений</span>
              </div>
              <div className="assistant-drafts">
                {data.sends.map(send => (
                  <article key={send.id} className="assistant-send">
                    <header>
                      <b>{send.contact_name || send.handle}</b>
                      <span className={`send-state ${SEND_STATES[send.state].tone}`}>
                        {SEND_STATES[send.state].label}
                      </span>
                    </header>
                    <small className="helper">{send.text}</small>
                    {send.note && (
                      <small className={send.state === 'failed' ? 'board-warning' : 'helper'}>
                        {send.note}
                      </small>
                    )}
                  </article>
                ))}
              </div>
            </section>
          )}

          <section className="panel assistant-settings">
            <div className="board-heading">
              <h2>Настройки и расходы</h2>
            </div>
            <div className="cloud-counters">
              <div>
                <span>Сегодня</span>
                <b>{dollars(data.usage.today)}</b>
              </div>
              <div>
                <span>30 дней</span>
                <b>{dollars(data.usage.month)}</b>
              </div>
              {Object.entries(data.usage.by_model).map(([model, value]) => (
                <div key={model}>
                  <span>{model}</span>
                  <b>{dollars(value)}</b>
                </div>
              ))}
            </div>
            <label className="check-row">
              <input
                type="checkbox"
                checked={data.escalation}
                onChange={event => void call('assistant.settings', { escalation: event.target.checked })}
              />
              {data.models.main} сам передаёт сложные ответы {data.models.strong}
            </label>
            <p className={data.messages.available ? 'helper' : 'board-warning'}>
              {data.messages.available ? 'Переписка iMessage на этом Mac доступна.' : data.messages.reason}
            </p>
            {changingKey ? (
              <KeyForm
                compact
                onSaved={() => {
                  setChangingKey(false);
                  state.refresh();
                }}
              />
            ) : (
              <Button variant="outline" onClick={() => setChangingKey(true)}>
                <KeyRound size={14} /> Сменить ключ Anthropic
              </Button>
            )}
          </section>
        </aside>
      </div>
      {confirming && (
        <ConfirmSend
          preview={confirming}
          onClose={() => setConfirming(null)}
          onSent={() => {
            setConfirming(null);
            setNotice('Передано в Сообщения. Статус появится в «Отправлено с Mac».');
            state.refresh();
          }}
        />
      )}
    </div>
  );
}
