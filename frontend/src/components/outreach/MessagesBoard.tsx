import { useEffect, useState, type KeyboardEvent, type ReactNode } from 'react';
import { LayoutGrid, LayoutTemplate, Plus, SquarePen, Trash2, X } from 'lucide-react';
import { api } from '../../services/api';
import { useResource } from '../../hooks/useResource';
import type { OutreachWorkspace } from '../../services/types';
import { Button } from '../ui/button';
import { Modal } from '../Modal';
import { ErrorToast } from '../Toaster';
import { errorText } from '../accountRuns';
import { parseMessages } from './outreachList';
import { TemplatesModal } from './OutreachModals';

/** The outreach messages: one of them goes to each recipient, the same for the parser's finds and the list. */
export function MessagesBoard({
  title = 'Сообщения',
  children,
}: {
  title?: ReactNode;
  children?: ReactNode;
}) {
  const workspace = useResource<OutreachWorkspace>('outreach.workspace', {}, 4000);
  const [messages, setMessages] = useState<string[]>([]);
  const [draft, setDraft] = useState('');
  const [bulk, setBulk] = useState<string | null>(null);
  const [dialog, setDialog] = useState<'templates' | 'clear' | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (workspace.data) setMessages(workspace.data.messages);
  }, [workspace.data]);

  const save = async (next: string[]) => {
    setBusy(true);
    setError('');
    try {
      const state = await api.request<OutreachWorkspace>('outreach.workspace_update', { messages: next });
      setMessages(state.messages);
      return true;
    } catch (err) {
      setError(errorText(err));
      return false;
    } finally {
      setBusy(false);
    }
  };
  const add = (texts: string[]) => (texts.length ? save([...messages, ...texts]) : Promise.resolve(false));
  const onKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey) return;
    event.preventDefault();
    const text = draft.trim();
    if (text) void add([text]).then(ok => ok && setDraft(''));
  };
  // «Массовый» edits all messages as text; «Карточки» saves them back.
  const toggleBulk = () => {
    if (bulk === null) return setBulk(messages.join('\n\n'));
    const parsed = parseMessages(bulk);
    if (parsed.join('\n\n') === messages.join('\n\n')) return setBulk(null);
    if (!parsed.length) return setDialog('clear');
    void save(parsed).then(ok => ok && setBulk(null));
  };

  return (
    <section className="panel board messages-board">
      <div className="board-heading">
        <h2>{title}</h2>
        <span className="helper">Enter — добавить, Shift+Enter — новая строка</span>
      </div>
      {bulk === null && (
        <div className="message-grid">
          {messages.map((text, index) => (
            <div className="message-card" key={text}>
              <p title={text}>{text}</p>
              <button
                type="button"
                aria-label={`Удалить сообщение ${index + 1}`}
                disabled={busy}
                onClick={() => void save(messages.filter(item => item !== text))}
              >
                <X size={13} />
              </button>
            </div>
          ))}
        </div>
      )}
      {bulk === null ? (
        <textarea
          className="message-input"
          rows={1}
          aria-label="Новое сообщение"
          placeholder="Введите сообщение и нажмите Enter…"
          value={draft}
          disabled={busy}
          onChange={event => setDraft(event.target.value)}
          onKeyDown={onKey}
        />
      ) : (
        <textarea
          className="board-bulk message-bulk"
          autoFocus
          aria-label="Список сообщений, отделяйте пустой строкой"
          placeholder={'Первое сообщение\n\nВторое сообщение — отделяйте пустой строкой'}
          value={bulk}
          disabled={busy}
          onChange={event => setBulk(event.target.value)}
        />
      )}
      <ErrorToast message={error} />
      <div className="board-footer">
        <div className="actions">
          <Button variant="outline" disabled={busy} onClick={toggleBulk}>
            {bulk === null ? <SquarePen size={15} /> : <LayoutGrid size={15} />}
            {bulk === null ? 'Массовый' : 'Карточки'}
          </Button>
          <Button variant="outline" disabled={bulk !== null} onClick={() => setDialog('templates')}>
            <LayoutTemplate size={15} /> Шаблоны
          </Button>
          {bulk === null && (
            <Button
              variant="outline"
              disabled={!draft.trim() || busy}
              onClick={() => void add([draft.trim()]).then(ok => ok && setDraft(''))}
            >
              <Plus size={15} /> Добавить
            </Button>
          )}
          <Button
            variant="danger"
            icon
            aria-label="Удалить все сообщения"
            title="Удалить все сообщения"
            disabled={!messages.length}
            onClick={() => setDialog('clear')}
          >
            <Trash2 size={15} />
          </Button>
        </div>
        {children}
      </div>

      {dialog === 'templates' && (
        <TemplatesModal
          messages={messages}
          onClose={() => setDialog(null)}
          onAdd={texts => void add(texts.filter(text => !messages.includes(text)))}
        />
      )}
      {dialog === 'clear' && (
        <Modal title="Удалить все сообщения?" onClose={() => setDialog(null)}>
          <p className="helper">
            Все варианты сообщений будут удалены из списка. Сохранённые шаблоны останутся.
          </p>
          <div className="actions">
            <Button
              variant="danger"
              onClick={() => {
                void save([]);
                setBulk(null);
                setDialog(null);
              }}
            >
              <Trash2 size={15} /> Очистить
            </Button>
            <Button variant="outline" onClick={() => setDialog(null)}>
              Отмена
            </Button>
          </div>
        </Modal>
      )}
    </section>
  );
}
