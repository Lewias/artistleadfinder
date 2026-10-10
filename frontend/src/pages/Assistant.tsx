import { useEffect, useRef, useState } from 'react';
import {
  ArrowUpRight,
  KeyRound,
  MessageSquarePlus,
  Play,
  Send,
  Sparkles,
  Square,
  Wrench,
  X,
} from 'lucide-react';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import type { AssistantDraft, AssistantState } from '../services/types';
import { PageHeader } from '../components/PageHeader';
import { Button } from '../components/ui/button';
import { ErrorToast } from '../components/Toaster';
import { errorText } from '../lib/errors';

const SUGGESTIONS = [
  'Кто ждёт ответа в iMessage?',
  'Подготовь ответы всем, кто ждёт больше дня',
  'Обнови статусы в CRM по последним перепискам',
  'Кому стоит написать повторно на этой неделе?',
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
  onChoose,
  onChanged,
}: {
  draft: AssistantDraft;
  chosen: boolean;
  onChoose: (value: boolean) => void;
  onChanged: () => void;
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
          disabled={edited}
          title={edited ? 'Сначала сохраните правку' : undefined}
          onClick={() => void act('assistant.drafts_send', { ids: [draft.id] })}
        >
          <Send size={14} /> Отправить
        </Button>
      </div>
      <ErrorToast message={error} />
    </article>
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
  const bottom = useRef<HTMLDivElement>(null);

  // Often while it works, rarely otherwise.
  useEffect(() => setPoll(data?.running ? 1500 : 10000), [data?.running]);
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
      <PageHeader page="assistant" count={`${dollars(data.usage.today)} сегодня`} />
      <div className="assistant-layout">
        <section className="panel assistant-chat">
          <div className="board-heading">
            <h2>
              <Sparkles size={16} /> Разговор
            </h2>
            <Button variant="outline" disabled={data.running} onClick={() => void call('assistant.new_chat')}>
              <MessageSquarePlus size={14} /> Новый разговор
            </Button>
          </div>
          <div className="assistant-feed">
            {!data.items.length && (
              <div className="assistant-empty">
                <p className="helper">
                  Спросите про CRM и переписку или дайте задачу. Ассистент меняет статусы и заметки сам, а
                  сообщения только готовит — отправляете вы.
                </p>
                <div className="assistant-suggestions">
                  {SUGGESTIONS.map(item => (
                    <button key={item} type="button" onClick={() => void ask(item)} disabled={data.running}>
                      {item}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {data.items.map((item, index) =>
              item.kind === 'user' || item.kind === 'assistant' ? (
                <div key={index} className={`assistant-bubble ${item.kind}`}>
                  {item.text}
                </div>
              ) : (
                <div key={index} className={`assistant-step ${item.kind}`}>
                  {item.kind === 'escalation' ? <ArrowUpRight size={13} /> : <Wrench size={12} />}
                  {item.kind === 'escalation' ? `Передал задачу ${data.models.strong}` : item.text}
                </div>
              ),
            )}
            {data.running && (
              <div className="assistant-step running">
                <span className="assistant-dot" /> {data.step || 'Думаю'}…
              </div>
            )}
            <div ref={bottom} />
          </div>
          {data.error && <p className="board-warning">{data.error}</p>}
          <div className="assistant-input">
            <textarea
              rows={2}
              placeholder="Например: подготовь ответы всем, кто ждёт больше дня (Ctrl+Enter — отправить)"
              value={question}
              onChange={event => setQuestion(event.target.value)}
              onKeyDown={event => {
                if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) void ask(question);
              }}
            />
            {data.running ? (
              <Button variant="outline" onClick={() => void call('assistant.stop')}>
                <Square size={14} /> Остановить
              </Button>
            ) : (
              <Button disabled={!question.trim()} onClick={() => void ask(question)}>
                <Send size={14} /> Спросить
              </Button>
            )}
          </div>
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
                  <Send size={14} /> Отправить выбранные ({chosen.length})
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
                  onChoose={value =>
                    setChosen(list => (value ? [...list, draft.id] : list.filter(id => id !== draft.id)))
                  }
                  onChanged={state.refresh}
                />
              ))}
            </div>
          </section>

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
    </div>
  );
}
