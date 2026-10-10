import { useEffect, useState, type KeyboardEvent, type ReactNode } from 'react';
import {
  AtSign,
  Copy,
  CornerDownLeft,
  Filter,
  LayoutGrid,
  Send,
  Square,
  SquarePen,
  Tag,
  Trash2,
  X,
} from 'lucide-react';
import { api } from '../../services/api';
import { useResource } from '../../hooks/useResource';
import type { OutreachSender, OutreachWorkspace } from '../../services/types';
import { number } from '../../lib/format';
import { Button } from '../ui/button';
import { Modal } from '../Modal';
import { ErrorToast } from '../Toaster';
import { errorText } from '../accountRuns';
import { chipTitle, chipTone, parseUsernames } from './outreachList';
import { CrmModal } from './OutreachModals';
import { MessagesBoard } from './MessagesBoard';

/** «Рассылка по списку»: usernames typed in, pasted or taken from the CRM, written to by hand. */
export function OutreachListTab() {
  const workspace = useResource<OutreachWorkspace>('outreach.workspace', {}, 4000);
  const senders = useResource<OutreachSender[]>('outreach.senders', {}, 5000);
  const [state, setState] = useState<OutreachWorkspace>();
  const [draft, setDraft] = useState('');
  const [bulk, setBulk] = useState<string | null>(null);
  const [dialog, setDialog] = useState<'crm' | 'clear' | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => {
    if (workspace.data) setState(workspace.data);
  }, [workspace.data]);

  const usernames = state?.usernames ?? [];
  const names = usernames.map(item => item.username);
  const sent = usernames.filter(item => item.status === 'sent').map(item => item.username);

  const call = async (method: string, params: object = {}) => {
    setBusy(true);
    setError('');
    try {
      setState(await api.request<OutreachWorkspace>(method, params));
      return true;
    } catch (err) {
      setError(errorText(err));
      return false;
    } finally {
      setBusy(false);
    }
  };
  const save = (params: { usernames?: string[]; sender_ids?: string[] }) =>
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
  // «Массовый» edits the whole list as text; «Карточки» saves it back.
  const toggleBulk = () => {
    if (bulk === null) return setBulk(names.join('\n'));
    const { usernames: parsed, invalid } = parseUsernames(bulk);
    if (invalid.length) return setError(`Не похоже на username: ${invalid.slice(0, 3).join(', ')}`);
    if (parsed.join('\n') === names.join('\n')) return setBulk(null);
    if (!parsed.length) return setDialog('clear');
    void save({ usernames: parsed }).then(ok => ok && setBulk(null));
  };
  const onKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    void addUsers(draft).then(ok => ok && setDraft(''));
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

  // Like the parser: the sender windows open by themselves and the queue finds each
  // recipient's profile there. With one account and none chosen, that one sends.
  const openSenders = async (ids: string[]) => {
    const closed = (senders.data ?? []).filter(item => ids.includes(item.id) && !item.open);
    for (const item of closed) await api.browser('open', { id: item.id });
    if (closed.length) senders.refresh();
  };
  const start = async () => {
    const all = senders.data ?? [];
    let ids = state?.sender_ids ?? [];
    if (!ids.length && all.length === 1) {
      ids = [all[0].id];
      if (!(await save({ sender_ids: ids }))) return;
    }
    if (!ids.length) {
      setError('Отметьте аккаунты в «Настройках» → «Аккаунты и рассылка».');
      return;
    }
    setBusy(true);
    setError('');
    try {
      await openSenders(ids);
    } catch (err) {
      setError(`Не удалось открыть браузер: ${errorText(err)}`);
      return;
    } finally {
      setBusy(false);
    }
    if (await call('outreach.workspace_start')) setNotice('');
  };
  const reopen = () => {
    setBusy(true);
    openSenders(state?.sender_ids ?? [])
      .catch(err => setError(`Не удалось открыть браузер: ${errorText(err)}`))
      .finally(() => setBusy(false));
  };
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
            <span className="board-warning">
              {' '}
              · ждёт аккаунт ·{' '}
              <button type="button" className="link-button" disabled={busy} onClick={reopen}>
                открыть браузер
              </button>
            </span>
          )}
        </>
      );
  } else if (campaign) {
    status = `Остановлен · последняя: отправлено ${number(campaign.sent_count)} из ${number(campaign.total_recipients)}`;
  }

  return (
    <>
      <section className="panel board outreach-board">
        <div className="board-heading">
          <h2>Кому писать</h2>
        </div>
        {bulk === null ? (
          <div className="chip-cloud board-chips" aria-label="Аккаунты для рассылки">
            {usernames.map(item => (
              <span
                key={item.username}
                className={`chip board-chip ${chipTone[item.status] ?? ''}`}
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
        ) : (
          <textarea
            className="board-bulk"
            autoFocus
            aria-label="Список usernames, по одному в строке"
            placeholder={'getabag.bo\n@shotbyjae_\nhttps://www.instagram.com/vezolotti/'}
            value={bulk}
            disabled={busy}
            onChange={event => setBulk(event.target.value)}
          />
        )}
        {bulk === null && (
          <label className="chip-input">
            <AtSign size={17} aria-hidden="true" />
            <input
              aria-label="Новый username"
              placeholder="Добавьте usernames и нажмите Enter…"
              value={draft}
              disabled={busy}
              onChange={event => setDraft(event.target.value)}
              onKeyDown={onKey}
            />
            <kbd aria-hidden="true">
              <CornerDownLeft size={13} />
            </kbd>
          </label>
        )}
        <div className="board-toolbar">
          <Button variant="outline" disabled={busy} onClick={toggleBulk}>
            {bulk === null ? <SquarePen size={15} /> : <LayoutGrid size={15} />}
            {bulk === null ? 'Массовый' : 'Карточки'}
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
          <Button
            variant="danger"
            icon
            aria-label="Очистить список аккаунтов"
            title="Очистить список аккаунтов"
            disabled={!usernames.length}
            onClick={() => setDialog('clear')}
          >
            <Trash2 size={15} />
          </Button>
        </div>
        <ErrorToast message={error} />
        {notice && !error && <p className="helper">{notice}</p>}
      </section>

      <MessagesBoard>
        <span className="board-status" role="status">
          {status}
        </span>
        {running ? (
          <Button variant="outline" disabled={busy} onClick={() => void call('outreach.workspace_stop')}>
            <Square size={14} /> Остановить
          </Button>
        ) : (
          <Button
            disabled={busy || !usernames.length || !state?.messages.length}
            onClick={() => void start()}
          >
            <Send size={15} /> Рассылка
          </Button>
        )}
      </MessagesBoard>

      {dialog === 'crm' && (
        <CrmModal
          onClose={() => setDialog(null)}
          onAdded={added => {
            setNotice(`Добавлено из CRM: ${added}`);
            workspace.refresh();
          }}
        />
      )}
      {dialog === 'clear' && (
        <Modal title="Очистить список аккаунтов?" onClose={() => setDialog(null)}>
          <p className="helper">
            Список usernames будет очищен. История отправленных сообщений и база контактов не изменятся.
          </p>
          <div className="actions">
            <Button
              variant="danger"
              onClick={() => {
                void save({ usernames: [] });
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
    </>
  );
}
