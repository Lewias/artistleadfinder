import { useEffect, useState, type FormEvent } from 'react';
import {
  Ban,
  Copy,
  KeyRound,
  Link2Off,
  LockKeyhole,
  Plus,
  ShieldCheck,
  Unlock,
  UserPlus,
} from 'lucide-react';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import type { AdminKey, AdminOverview, AdminUser, KeyRole } from '../services/types';
import { number } from '../lib/format';
import { reportError, toast } from '../lib/toast';
import { PageHeader } from '../components/PageHeader';
import { Button } from '../components/ui/button';
import { Modal } from '../components/Modal';

type Dialog =
  | { kind: 'user' }
  | { kind: 'key'; user?: AdminUser }
  | { kind: 'password'; user: AdminUser }
  | { kind: 'issued'; key: string; user?: AdminUser }
  | null;

const date = (value: string | null) =>
  value && !Number.isNaN(Date.parse(value))
    ? new Date(value).toLocaleDateString('ru-RU', { day: 'numeric', month: 'short', year: 'numeric' })
    : '—';

function keyState(key: AdminKey) {
  if (key.revoked_at) return { label: 'Отозван', tone: 'bad' };
  if (key.bound) return { label: 'Активирован', tone: 'good' };
  return { label: key.user_id ? 'Ждёт активации' : 'Свободный', tone: '' };
}

/** Accounts, roles, blocking and access keys; only admins reach this page. */
export function Admin() {
  const resource = useResource<AdminOverview>('admin.overview', {}, 30000);
  const [data, setData] = useState<AdminOverview>();
  const [dialog, setDialog] = useState<Dialog>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (resource.data) setData(resource.data);
  }, [resource.data]);
  const users = data?.users ?? [];
  const keys = data?.keys ?? [];
  const nameOf = (id: string | null) => {
    const user = users.find(item => item.id === id);
    return user ? user.display_name || user.email : '—';
  };

  const call = async <T,>(method: string, params: object): Promise<T | null> => {
    setBusy(true);
    try {
      return await api.request<T>(method, params);
    } catch (err) {
      reportError(err);
      return null;
    } finally {
      setBusy(false);
    }
  };
  const update = async (method: string, params: object, done?: string) => {
    const next = await call<AdminOverview>(method, params);
    if (next) {
      setData(next);
      if (done) toast.success(done);
    }
  };

  return (
    <div className="screen">
      <PageHeader
        page="admin"
        count={number(users.length)}
        actions={
          <div className="actions">
            <Button variant="outline" onClick={() => setDialog({ kind: 'key' })}>
              <KeyRound size={15} /> Выпустить ключ
            </Button>
            <Button onClick={() => setDialog({ kind: 'user' })}>
              <UserPlus size={15} /> Новый пользователь
            </Button>
          </div>
        }
      />
      {resource.error && !data && <p className="error-text">{resource.error}</p>}

      <section className="panel">
        <div className="board-heading">
          <h2>Пользователи</h2>
          <span className="helper">Админ видит и правит CRM всех пользователей.</span>
        </div>
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th>Пользователь</th>
                <th>Роль</th>
                <th>Ключи</th>
                <th>Создан</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {users.map(user => {
                const own = user.id === data?.me;
                const userKeys = keys.filter(key => key.user_id === user.id && !key.revoked_at);
                return (
                  <tr key={user.id} className={user.blocked ? 'admin-blocked' : undefined}>
                    <td>
                      <strong>{user.display_name || user.email}</strong>
                      {user.display_name && <small className="job-detail">{user.email}</small>}
                      {user.blocked && <span className="status-badge job-failed">Заблокирован</span>}
                    </td>
                    <td>
                      {user.role === 'admin' ? (
                        <span className="status-badge" title="Роль админа меняется только на сервере">
                          Админ
                        </span>
                      ) : (
                        <select
                          aria-label={`Роль ${user.email}`}
                          value={user.role}
                          disabled={busy}
                          title="Модератор видит и правит CRM всех пользователей"
                          onChange={event =>
                            void update('admin.set_role', { user_id: user.id, role: event.target.value })
                          }
                        >
                          <option value="user">Пользователь</option>
                          <option value="moderator">Модератор</option>
                        </select>
                      )}
                    </td>
                    <td>
                      {userKeys.length
                        ? `${userKeys.length} · ${keyState(userKeys[0]).label.toLowerCase()}`
                        : 'нет доступа'}
                    </td>
                    <td>{date(user.created_at)}</td>
                    <td>
                      <div className="actions job-actions">
                        <Button
                          variant="outline"
                          disabled={busy}
                          onClick={() => setDialog({ kind: 'key', user })}
                        >
                          <Plus size={14} /> Ключ
                        </Button>
                        <Button
                          variant="outline"
                          disabled={busy}
                          onClick={() => setDialog({ kind: 'password', user })}
                        >
                          <LockKeyhole size={14} /> Пароль
                        </Button>
                        {!own && (
                          <Button
                            variant={user.blocked ? 'outline' : 'danger'}
                            disabled={busy}
                            onClick={() =>
                              void update(
                                'admin.set_blocked',
                                { user_id: user.id, blocked: !user.blocked },
                                user.blocked ? 'Пользователь разблокирован' : 'Пользователь заблокирован',
                              )
                            }
                          >
                            {user.blocked ? <Unlock size={14} /> : <Ban size={14} />}
                            {user.blocked ? 'Разблокировать' : 'Заблокировать'}
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className="panel">
        <div className="board-heading">
          <h2>Ключи доступа</h2>
          <span className="helper">
            Ключ открывает доступ аккаунту на любом компьютере. «Отвязать» снимает доступ, и ключ можно выдать
            другому.
          </span>
        </div>
        <div className="table-container">
          <table>
            <thead>
              <tr>
                <th>Ключ</th>
                <th>Пользователь</th>
                <th>Роль</th>
                <th>Состояние</th>
                <th>Заметка</th>
                <th>Выпущен</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {keys.map(key => {
                const state = keyState(key);
                return (
                  <tr key={key.id}>
                    <td>
                      <code>ALF-…-{key.key_hint}</code>
                    </td>
                    <td>{key.user_id ? nameOf(key.user_id) : 'любой, кто активирует первым'}</td>
                    <td>{key.role === 'moderator' ? 'Модератор' : 'Пользователь'}</td>
                    <td>
                      <span className={`admin-key ${state.tone}`}>{state.label}</span>
                    </td>
                    <td>{key.note || '—'}</td>
                    <td>{date(key.created_at)}</td>
                    <td>
                      {!key.revoked_at && (
                        <div className="actions job-actions">
                          {key.bound && (
                            <Button
                              variant="outline"
                              disabled={busy}
                              onClick={() =>
                                void update(
                                  'admin.unbind_key',
                                  { id: key.id },
                                  'Ключ отвязан, у аккаунта больше нет доступа',
                                )
                              }
                            >
                              <Link2Off size={14} /> Отвязать
                            </Button>
                          )}
                          <Button
                            variant="danger"
                            disabled={busy}
                            onClick={() => void update('admin.revoke_key', { id: key.id }, 'Ключ отозван')}
                          >
                            <Ban size={14} /> Отозвать
                          </Button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
              {!keys.length && (
                <tr>
                  <td colSpan={6} className="helper">
                    Ключей пока нет.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {dialog?.kind === 'user' && (
        <NewUser
          busy={busy}
          onClose={() => setDialog(null)}
          onCreate={async params => {
            const result = await call<{ user: { id: string; email: string }; key: string | null }>(
              'admin.create_user',
              params,
            );
            if (!result) return;
            resource.refresh();
            toast.success(`Пользователь ${result.user.email} создан`);
            setDialog(result.key ? { kind: 'issued', key: result.key } : null);
          }}
        />
      )}
      {dialog?.kind === 'key' && (
        <IssueKey
          users={users}
          user={dialog.user}
          busy={busy}
          onClose={() => setDialog(null)}
          onIssue={async params => {
            const next = await call<AdminOverview>('admin.issue_key', params);
            if (!next?.key) return;
            setData(next);
            setDialog({
              kind: 'issued',
              key: next.key,
              user: users.find(item => item.id === params.user_id),
            });
          }}
        />
      )}
      {dialog?.kind === 'password' && (
        <SetPassword
          user={dialog.user}
          busy={busy}
          onClose={() => setDialog(null)}
          onSave={async password => {
            if (await call('admin.set_password', { user_id: dialog.user.id, password })) {
              toast.success('Пароль изменён');
              setDialog(null);
            }
          }}
        />
      )}
      {dialog?.kind === 'issued' && (
        <Modal title="Ключ выпущен" onClose={() => setDialog(null)}>
          <p className="helper">
            Передайте ключ
            {dialog.user ? ` пользователю ${dialog.user.display_name || dialog.user.email}` : ''}. Он
            показывается один раз: сервер хранит только его отпечаток.
          </p>
          <code className="admin-issued">{dialog.key}</code>
          <div className="actions">
            <Button
              onClick={() =>
                void navigator.clipboard
                  .writeText(dialog.key)
                  .then(() => toast.success('Ключ скопирован'))
                  .catch(reportError)
              }
            >
              <Copy size={15} /> Скопировать
            </Button>
            <Button variant="outline" onClick={() => setDialog(null)}>
              Готово
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}

function NewUser({
  busy,
  onClose,
  onCreate,
}: {
  busy: boolean;
  onClose: () => void;
  onCreate: (params: {
    email: string;
    password: string;
    display_name: string;
    issue_key: boolean;
    role: KeyRole;
  }) => void;
}) {
  const [email, setEmail] = useState('');
  const [name, setName] = useState('');
  const [password, setPassword] = useState('');
  const [issueKey, setIssueKey] = useState(true);
  const [role, setRole] = useState<KeyRole>('user');
  const submit = (event: FormEvent) => {
    event.preventDefault();
    onCreate({ email, password, display_name: name, issue_key: issueKey, role });
  };
  return (
    <Modal title="Новый пользователь" onClose={onClose}>
      <form className="admin-form" onSubmit={submit}>
        <label>
          Email
          <input type="email" autoFocus value={email} onChange={event => setEmail(event.target.value)} />
        </label>
        <label>
          Имя
          <input
            value={name}
            maxLength={80}
            placeholder="Как подписывать его CRM"
            onChange={event => setName(event.target.value)}
          />
        </label>
        <label>
          Пароль
          <input
            type="text"
            autoComplete="new-password"
            value={password}
            placeholder="Не короче 8 символов"
            onChange={event => setPassword(event.target.value)}
          />
        </label>
        <label className="check-row">
          <input type="checkbox" checked={issueKey} onChange={event => setIssueKey(event.target.checked)} />
          Сразу выпустить ключ доступа
        </label>
        {issueKey && <RolePicker value={role} onChange={setRole} />}
        <div className="actions">
          <Button type="submit" disabled={busy || !email.includes('@') || password.length < 8}>
            <UserPlus size={15} /> Создать
          </Button>
          <Button variant="outline" onClick={onClose}>
            Отмена
          </Button>
        </div>
      </form>
    </Modal>
  );
}

const keyRoles: { id: KeyRole; label: string; hint: string }[] = [
  { id: 'user', label: 'Пользователь', hint: 'обычный софт, только своя CRM' },
  { id: 'moderator', label: 'Модератор', hint: 'обычный софт и CRM всех пользователей' },
];

/** The role the key grants when it is activated. */
function RolePicker({ value, onChange }: { value: KeyRole; onChange: (role: KeyRole) => void }) {
  return (
    <label>
      Роль
      <select value={value} onChange={event => onChange(event.target.value as KeyRole)}>
        {keyRoles.map(item => (
          <option key={item.id} value={item.id}>
            {item.label} — {item.hint}
          </option>
        ))}
      </select>
    </label>
  );
}

function IssueKey({
  users,
  user,
  busy,
  onClose,
  onIssue,
}: {
  users: AdminUser[];
  user?: AdminUser;
  busy: boolean;
  onClose: () => void;
  onIssue: (params: { user_id: string | null; note: string; role: KeyRole }) => void;
}) {
  const [owner, setOwner] = useState(user?.id ?? '');
  const [note, setNote] = useState('');
  const [role, setRole] = useState<KeyRole>(user?.role === 'moderator' ? 'moderator' : 'user');
  const target = users.find(item => item.id === owner);
  return (
    <Modal title="Выпустить ключ" onClose={onClose}>
      <div className="admin-form">
        <label>
          Для кого
          <select value={owner} onChange={event => setOwner(event.target.value)}>
            <option value="">Любой аккаунт (кто активирует первым)</option>
            {users.map(item => (
              <option key={item.id} value={item.id}>
                {item.display_name || item.email}
              </option>
            ))}
          </select>
        </label>
        {target?.role === 'admin' ? (
          <p className="helper">Админ остаётся админом: роль ключа на него не действует.</p>
        ) : (
          <RolePicker value={role} onChange={setRole} />
        )}
        <label>
          Заметка
          <input
            value={note}
            maxLength={200}
            placeholder="Например: ноутбук Кирилла"
            onChange={event => setNote(event.target.value)}
          />
        </label>
        <div className="actions">
          <Button disabled={busy} onClick={() => onIssue({ user_id: owner || null, note, role })}>
            <ShieldCheck size={15} /> Выпустить
          </Button>
          <Button variant="outline" onClick={onClose}>
            Отмена
          </Button>
        </div>
      </div>
    </Modal>
  );
}

function SetPassword({
  user,
  busy,
  onClose,
  onSave,
}: {
  user: AdminUser;
  busy: boolean;
  onClose: () => void;
  onSave: (password: string) => void;
}) {
  const [password, setPassword] = useState('');
  return (
    <Modal title={`Новый пароль: ${user.display_name || user.email}`} onClose={onClose}>
      <form
        className="admin-form"
        onSubmit={event => {
          event.preventDefault();
          onSave(password);
        }}
      >
        <label>
          Пароль
          <input
            type="text"
            autoFocus
            autoComplete="new-password"
            value={password}
            placeholder="Не короче 8 символов"
            onChange={event => setPassword(event.target.value)}
          />
        </label>
        <div className="actions">
          <Button type="submit" disabled={busy || password.length < 8}>
            <LockKeyhole size={15} /> Сохранить
          </Button>
          <Button variant="outline" onClick={onClose}>
            Отмена
          </Button>
        </div>
      </form>
    </Modal>
  );
}
