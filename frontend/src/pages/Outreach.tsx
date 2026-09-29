import { useEffect, useState, type KeyboardEvent, type ReactNode } from 'react';
import {
  AtSign,
  Copy,
  CornerDownLeft,
  Filter,
  LayoutTemplate,
  Plus,
  Send,
  SlidersHorizontal,
  Square,
  SquarePen,
  Tag,
  Trash2,
  X,
} from 'lucide-react';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import type { OutreachSender, OutreachWorkspace } from '../services/types';
import { number } from '../lib/format';
import { PageHeader } from '../components/PageHeader';
import { Button } from '../components/ui/button';
import {
  accountsLabel,
  chipTitle,
  chipTone,
  messagesLabel,
  parseMessages,
  parseUsernames,
} from '../components/outreach/outreachList';
import { CrmModal, OutreachSettingsModal, TemplatesModal } from '../components/outreach/OutreachModals';
import { Modal } from '../components/Modal';

type Dialog = 'crm' | 'templates' | 'settings' | 'clear-users' | 'clear-messages' | null;

export function Outreach() {
  const workspace = useResource<OutreachWorkspace>('outreach.workspace', {}, 4000);
  const senders = useResource<OutreachSender[]>('outreach.senders', {}, 5000);
  const [state, setState] = useState<OutreachWorkspace>();
  const [userDraft, setUserDraft] = useState('');
  const [userBulk, setUserBulk] = useState<string | null>(null);
  const [messageDraft, setMessageDraft] = useState('');
  const [messageBulk, setMessageBulk] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => {
    if (workspace.data) setState(workspace.data);
  }, [workspace.data]);

  const usernames = state?.usernames ?? [];
  const messages = state?.messages ?? [];
  const names = usernames.map(item => item.username);
  const sent = usernames.filter(item => item.status === 'sent').map(item => item.username);

  const call = async (method: string, params: object = {}) => {
    setBusy(true);
    setError('');
    try {
      setState(await api.request<OutreachWorkspace>(method, params));
      return true;
    } catch (err) {
      setError(String(err).replace(/^Error:\s*/, ''));
      return false;
    } finally {
      setBusy(false);
    }
  };
  const save = (params: { usernames?: string[]; messages?: string[]; sender_ids?: string[] }) =>
    call('outreach.workspace_update', params);

  const addUsers = async (text: string) => {
    const { usernames: parsed, invalid } = parseUsernames(text);
    if (invalid.length) {
      setError(`Не похоже на username: ${invalid.slice(0, 3).join(', ')}`);
      return false;
    }
    if (!parsed.length) return false;
    return save({ usernames: [...names, ...parsed] });
  };
  const addMessages = (texts: string[]) =>
    texts.length ? save({ messages: [...messages, ...texts] }) : Promise.resolve(false);

  const onUserKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    void addUsers(userDraft).then(ok => ok && setUserDraft(''));
  };
  const onMessageKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey) return;
    event.preventDefault();
    const text = messageDraft.trim();
    if (text) void addMessages([text]).then(ok => ok && setMessageDraft(''));
  };
  const copySent = async () => {
    try {
      await navigator.clipboard.writeText(sent.map(name => `@${name}`).join('\n'));
      setNotice(`Скопировано: ${sent.length}`);
    } catch {
      setError('Не удалось скопировать в буфер обмена.');
    }
  };

  const campaign = state?.campaign;
  const running = !!state?.running;
  const selected = (senders.data ?? []).filter(item => state?.sender_ids.includes(item.id));
  const ready = selected.some(item => item.status === 'active' && item.open);
  let status: ReactNode = 'Остановлен';
  if (running && campaign) {
    const done = campaign.sent_count + campaign.failed_count + campaign.skipped_count;
    status =
      campaign.status === 'paused' ? (
        'На паузе'
      ) : (
        <>
          Идёт рассылка · отправлено {number(campaign.sent_count)} из {number(campaign.total_recipients)}
          {done < campaign.total_recipients && !ready && (
            <span className="board-warning"> · ждёт аккаунт: откройте окно отправителя на instagram.com</span>
          )}
        </>
      );
  } else if (campaign) {
    status = `Остановлен · последняя: отправлено ${number(campaign.sent_count)} из ${number(campaign.total_recipients)}`;
  }

  return (
    <>
      <PageHeader
        page="outreach"
        count={`${accountsLabel(usernames.length)} · ${messagesLabel(messages.length)}`}
      />
      <section className="panel outreach-board">
        <div className="chip-cloud outreach-chips" aria-label="Аккаунты для рассылки">
          {usernames.map(item => (
            <span
              key={item.username}
              className={`chip outreach-chip ${chipTone[item.status] ?? ''}`}
              title={chipTitle(item)}
            >
              {item.username}
              <button
                type="button"
                aria-label={`Убрать ${item.username}`}
                disabled={busy}
                onClick={() => void save({ usernames: names.filter(name => name !== item.username) })}
              >
                <X size={13} />
              </button>
            </span>
          ))}
          {!usernames.length && (
            <p className="empty-copy">
              Список пуст. Добавьте usernames ниже, вставьте список через «Массовый» или возьмите лидов по
              статусу из CRM.
            </p>
          )}
        </div>

        {userBulk === null ? (
          <label className="chip-input">
            <AtSign size={17} aria-hidden="true" />
            <input
              aria-label="Новый username"
              placeholder="Добавьте usernames и нажмите Enter…"
              value={userDraft}
              disabled={busy}
              onChange={event => setUserDraft(event.target.value)}
              onKeyDown={onUserKey}
            />
            <kbd aria-hidden="true">
              <CornerDownLeft size={13} />
            </kbd>
          </label>
        ) : (
          <div className="bulk-input">
            <textarea
              rows={5}
              autoFocus
              aria-label="Список usernames"
              placeholder={'getabag.bo\n@shotbyjae_\nhttps://www.instagram.com/vezolotti/'}
              value={userBulk}
              onChange={event => setUserBulk(event.target.value)}
            />
            <div className="actions">
              <Button
                disabled={busy || !userBulk.trim()}
                onClick={() => void addUsers(userBulk).then(ok => ok && setUserBulk(null))}
              >
                Добавить
              </Button>
              <Button variant="outline" onClick={() => setUserBulk(null)}>
                Отмена
              </Button>
            </div>
          </div>
        )}

        <div className="board-toolbar">
          <Button variant="outline" onClick={() => setUserBulk(userBulk === null ? '' : null)}>
            <SquarePen size={15} /> Массовый
          </Button>
          <Button variant="outline" onClick={() => setDialog('crm')}>
            <Tag size={15} /> CRM Метки
          </Button>
          <Button variant="outline" disabled={!sent.length} onClick={() => void copySent()}>
            <Copy size={15} /> Скопировать отправленные
          </Button>
          <Button
            variant="outline"
            disabled={!sent.length || busy}
            onClick={() => void save({ usernames: names.filter(name => !sent.includes(name)) })}
          >
            <Filter size={15} /> Убрать отправленные
          </Button>
          <Button variant="outline" onClick={() => setDialog('settings')}>
            <SlidersHorizontal size={15} /> Настройки
          </Button>
          <Button
            variant="danger"
            icon
            aria-label="Очистить список аккаунтов"
            title="Очистить список аккаунтов"
            disabled={!usernames.length}
            onClick={() => setDialog('clear-users')}
          >
            <Trash2 size={15} />
          </Button>
        </div>

        <div className="board-heading">
          <h2>Сообщения</h2>
          <span className="helper">Enter — добавить, Shift+Enter — новая строка</span>
        </div>
        {messages.length > 0 && (
          <div className="message-grid">
            {messages.map((text, index) => (
              <div className="message-card" key={text}>
                <p>{text}</p>
                <button
                  type="button"
                  aria-label={`Удалить сообщение ${index + 1}`}
                  disabled={busy}
                  onClick={() => void save({ messages: messages.filter(item => item !== text) })}
                >
                  <X size={13} />
                </button>
              </div>
            ))}
          </div>
        )}

        {messageBulk === null ? (
          <textarea
            className="message-input"
            rows={1}
            aria-label="Новое сообщение"
            placeholder="Введите сообщение и нажмите Enter…"
            value={messageDraft}
            disabled={busy}
            onChange={event => setMessageDraft(event.target.value)}
            onKeyDown={onMessageKey}
          />
        ) : (
          <div className="bulk-input">
            <textarea
              rows={6}
              autoFocus
              aria-label="Список сообщений"
              placeholder={'Первое сообщение\n\nВторое сообщение — отделяйте пустой строкой'}
              value={messageBulk}
              onChange={event => setMessageBulk(event.target.value)}
            />
            <div className="actions">
              <Button
                disabled={busy || !messageBulk.trim()}
                onClick={() =>
                  void addMessages(parseMessages(messageBulk)).then(ok => ok && setMessageBulk(null))
                }
              >
                Добавить
              </Button>
              <Button variant="outline" onClick={() => setMessageBulk(null)}>
                Отмена
              </Button>
            </div>
          </div>
        )}

        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
        {notice && !error && <p className="helper">{notice}</p>}

        <div className="board-footer">
          <div className="actions">
            <Button variant="outline" onClick={() => setMessageBulk(messageBulk === null ? '' : null)}>
              <SquarePen size={15} /> Массовый
            </Button>
            <Button variant="outline" onClick={() => setDialog('templates')}>
              <LayoutTemplate size={15} /> Шаблоны
            </Button>
            <Button
              variant="outline"
              disabled={!messageDraft.trim() || busy}
              onClick={() => void addMessages([messageDraft.trim()]).then(ok => ok && setMessageDraft(''))}
            >
              <Plus size={15} /> Добавить
            </Button>
            <Button
              variant="danger"
              icon
              aria-label="Удалить все сообщения"
              title="Удалить все сообщения"
              disabled={!messages.length}
              onClick={() => setDialog('clear-messages')}
            >
              <Trash2 size={15} />
            </Button>
          </div>
          <span className="board-status" role="status">
            {status}
          </span>
          {running ? (
            <Button variant="outline" disabled={busy} onClick={() => void call('outreach.workspace_stop')}>
              <Square size={14} /> Остановить
            </Button>
          ) : (
            <Button
              disabled={busy || !usernames.length || !messages.length}
              onClick={() => void call('outreach.workspace_start').then(ok => ok && setNotice(''))}
            >
              <Send size={15} /> Рассылка
            </Button>
          )}
        </div>
      </section>

      {dialog === 'crm' && (
        <CrmModal
          onClose={() => setDialog(null)}
          onAdded={added => {
            setNotice(`Добавлено из CRM: ${added}`);
            workspace.refresh();
          }}
        />
      )}
      {dialog === 'templates' && (
        <TemplatesModal
          messages={messages}
          onClose={() => setDialog(null)}
          onAdd={texts => void addMessages(texts.filter(text => !messages.includes(text)))}
        />
      )}
      {dialog === 'settings' && state && (
        <OutreachSettingsModal
          selected={state.sender_ids}
          senders={senders}
          onSelect={ids => void save({ sender_ids: ids })}
          onClose={() => setDialog(null)}
        />
      )}
      {(dialog === 'clear-users' || dialog === 'clear-messages') && (
        <Modal
          title={dialog === 'clear-users' ? 'Очистить список аккаунтов?' : 'Удалить все сообщения?'}
          onClose={() => setDialog(null)}
        >
          <p className="helper">
            {dialog === 'clear-users'
              ? 'Список usernames будет очищен. История отправленных сообщений и база контактов не изменятся.'
              : 'Все варианты сообщений будут удалены из списка. Сохранённые шаблоны останутся.'}
          </p>
          <div className="actions">
            <Button
              variant="danger"
              onClick={() => {
                void save(dialog === 'clear-users' ? { usernames: [] } : { messages: [] });
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
    </>
  );
}
