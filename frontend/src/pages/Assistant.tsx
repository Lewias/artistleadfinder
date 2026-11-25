import { useEffect, useRef, useState } from 'react';
import { ChevronRight, MessageSquarePlus, Play, Send, Square, Wrench, X } from 'lucide-react';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import ReactMarkdown from 'react-markdown';
import type { AssistantChatSend, AssistantItem, AssistantSend, AssistantState } from '../services/types';
import { PageHeader } from '../components/PageHeader';
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
  'Что лучше ответить тем, кто ждёт больше дня?',
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
function Steps({ steps }: { steps: AssistantItem[] }) {
  if (!steps.length) return null;
  return (
    <details className="assistant-steps">
      <summary>
        <ChevronRight size={13} className="chevron" />
        <span>Шаги · {[...new Set(steps.map(step => step.text))].join(' · ')}</span>
      </summary>
      <ul>
        {steps.map((step, index) => (
          <li key={index}>
            <Wrench size={11} /> {step.text}
          </li>
        ))}
      </ul>
    </details>
  );
}

/** Shows an iMessage already submitted, without controls that could send it again. */
function ChatSendCard({ message, send }: { message: AssistantChatSend; send?: AssistantSend }) {
  const state = send
    ? SEND_STATES[send.state]
    : { label: message.campaign_id ? 'Передано в очередь iPhone' : 'Передано в Сообщения', tone: 'wait' };
  return (
    <article className="assistant-card">
      <header>
        <span>
          <b>{message.contact_name || message.handle}</b> <small className="helper">{message.handle}</small>
        </span>
        <span className={`send-state ${state.tone}`}>{state.label}</span>
      </header>
      <div className="assistant-send-text">{message.text}</div>
      {send?.note && send.state !== 'sent' && send.state !== 'delivered' && (
        <small className={send.state === 'failed' ? 'board-warning' : 'helper'}>{send.note}</small>
      )}
    </article>
  );
}

/** «Ассистент»: the chat with the model and the status of submitted messages. */
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
      const result = await api.request<AssistantState>(method, params);
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
  if (!data.configured)
    return (
      <div className="screen work-flow">
        <PageHeader page="assistant" />
        <section className="panel board">
          <div className="board-heading">
            <h2>Ассистент временно недоступен</h2>
          </div>
          <p className="helper">Перезапустите приложение или обновите его до последней версии.</p>
          <Button variant="outline" onClick={state.refresh}>
            Повторить
          </Button>
        </section>
      </div>
    );

  return (
    <div className="screen work-flow assistant-screen">
      <PageHeader
        page="assistant"
        actions={
          <Button variant="outline" disabled={data.running} onClick={() => void call('assistant.new_chat')}>
            <MessageSquarePlus size={14} /> Новый чат
          </Button>
        }
      />
      <div className={`assistant-layout${side ? '' : ' single'}`}>
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
                  {block.sends.map(id => {
                    const message = data.chat_sends.find(item => item.id === id);
                    return (
                      message && (
                        <ChatSendCard
                          key={id}
                          message={message}
                          send={data.sends.find(item => item.draft_id === id)}
                        />
                      )
                    );
                  })}
                  {!(live && index === blocks.length - 1) && <Steps steps={block.steps} />}
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
            Вопросы и выбранные фрагменты переписки отправляются сервису ассистента. Для отправки начните
            команду с «Отправь» или «Ответь» и укажите, кому написать в iMessage.
          </p>
          <ErrorToast message={error || state.error} />
        </section>

        {side && (
          <aside className="assistant-side">
            {data.actions.length > 0 && (
              <section className="panel">
                <div className="board-heading">
                  <h2>Действия</h2>
                  <span className="helper">Подтверждение требуется только для iMessage</span>
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
