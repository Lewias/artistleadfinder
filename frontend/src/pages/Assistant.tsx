import { useEffect, useRef, useState } from 'react';
import {
  ArrowUpRight,
  ChevronRight,
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
import { SEND_STATES, awaitingMessages } from '../components/assistant/macSend';
import { chatBlocks } from '../components/assistant/chat';
import { KeyForm } from '../components/assistant/AssistantSettings';
import { dollars } from '../lib/format';
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

/** A draft the assistant made: the chat asks whether to send it, and nothing goes out
 * without a button here. The card shows the exact recipient and text, so pressing
 * «Отправить» is the confirmation; a text that changed meanwhile is refused. */
function ChatDraftCard({
  draft,
  pending,
  send,
  mac,
  onChanged,
}: {
  draft: AssistantChatDraft;
  pending?: AssistantDraft;
  send?: AssistantSend;
  mac: boolean;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const text = pending?.text ?? draft.text;
  const act = async (work: () => Promise<unknown>) => {
    setBusy(true);
    setError('');
    try {
      await work();
    } catch (err) {
      setError(errorText(err));
    }
    setBusy(false);
    onChanged();
  };
  const sendMac = () =>
    act(async () => {
      const preview = await api.request<AssistantSendPreview>('assistant.send_preview', { id: draft.id });
      if (preview.text !== text || preview.recipient !== draft.handle)
        throw new Error('Черновик изменился. Проверьте текст и нажмите ещё раз.');
      await api.request('assistant.send_confirm', { id: draft.id, token: preview.token });
    });
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
      <div className="assistant-send-text">{text}</div>
      {send?.note && send.state !== 'sent' && send.state !== 'delivered' && (
        <small className={send.state === 'failed' ? 'board-warning' : 'helper'}>{send.note}</small>
      )}
      {pending && (
        <>
          <div className="assistant-card-actions">
            <b className="assistant-card-question">Отправить?</b>
            {mac && (
              <Button disabled={busy} onClick={() => void sendMac()}>
                <Send size={14} /> {busy ? 'Отправляю…' : 'Отправить'}
              </Button>
            )}
            <Button
              variant={mac ? 'outline' : undefined}
              disabled={busy}
              title="В очередь iMessage: отправит Команда на iPhone"
              onClick={() => void act(() => api.request('assistant.drafts_send', { ids: [draft.id] }))}
            >
              <Smartphone size={14} /> {mac ? 'В очередь iPhone' : 'Отправить через iPhone'}
            </Button>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void act(() => api.request('assistant.draft_reject', { id: draft.id }))}
            >
              Не отправлять
            </Button>
          </div>
          <small className="helper">
            {mac
              ? 'Уйдёт один раз, ровно этот текст, через Сообщения на этом Mac. '
              : 'Уйдёт ровно этот текст. '}
            Чтобы изменить, попросите ассистента в чате.
          </small>
        </>
      )}
      <ErrorToast message={error} />
    </article>
  );
}

/** «Ассистент»: the chat with Claude; the messages it prepares are asked about right in the chat. */
export function Assistant() {
  const [poll, setPoll] = useState(10000);
  const state = useResource<AssistantState>('assistant.state', {}, poll);
  const data = state.data;
  const [question, setQuestion] = useState('');
  const [error, setError] = useState('');
  const bottom = useRef<HTMLDivElement>(null);
  const waiting = awaitingMessages(data?.sends ?? []);

  // Often while it works or Messages is sending, rarely otherwise.
  useEffect(() => setPoll(data?.running ? 1500 : waiting ? 3000 : 10000), [data?.running, waiting]);
  // The braces matter: newer WebViews return a Promise from scrollIntoView, and React
  // would call a returned value as the effect's cleanup, which blanks the whole window.
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' });
  }, [data?.items.length, data?.running]);
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
  if (!data) return <div className="screen">{state.error || 'Загрузка…'}</div>;
  const blocks = chatBlocks(data.items);
  const live = data.running;
  const side = data.actions.length > 0 || data.sends.length > 0;
  // Drafts still waiting from an earlier chat: asked about at the top of this one.
  const earlier = data.drafts.filter(draft => !data.chat_drafts.some(item => item.id === draft.id));
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
      <div className={`assistant-layout${side ? '' : ' single'}`}>
        <section className="assistant-chat">
          <div className="assistant-feed">
            {!data.items.length && !data.running && !earlier.length && (
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
            {earlier.length > 0 && (
              <div className="assistant-msg">
                <div className="assistant-md">
                  <p>Из прошлых чатов ещё не отправлено:</p>
                </div>
                {earlier.map(draft => (
                  <ChatDraftCard
                    key={draft.id}
                    draft={{ ...draft, status: 'pending', campaign_id: null }}
                    pending={draft}
                    mac={data.mac_send.enabled}
                    onChanged={state.refresh}
                  />
                ))}
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
                          onChanged={state.refresh}
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
            Ассистент готовит сообщения, но уходят они только после вашей кнопки. Ключ, модели и расходы — в
            «Настройках».
          </p>
          <ErrorToast message={error || state.error} />
        </section>

        {side && (
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
          </aside>
        )}
      </div>
    </div>
  );
}
