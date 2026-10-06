import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import { AudioLines, KeyRound, LogIn, LogOut, RefreshCw, UserPlus, UserRound, WifiOff } from 'lucide-react';
import { api } from '../../services/api';
import type { AccountState } from '../../services/types';
import { reportError } from '../../lib/toast';
import { Button } from '../ui/button';

const roleLabel = { admin: 'Админ', moderator: 'Модератор', user: 'Пользователь' } as const;

function Screen({
  icon,
  title,
  hint,
  children,
}: {
  icon: ReactNode;
  title: string;
  hint: string;
  children: ReactNode;
}) {
  return (
    <div className="gate">
      <div className="gate-card">
        <div className="gate-brand">
          <span className="brand-logo">
            <AudioLines size={17} strokeWidth={2.4} />
          </span>
          Artist Lead Finder
        </div>
        <span className="gate-icon">{icon}</span>
        <h1>{title}</h1>
        <p className="helper">{hint}</p>
        {children}
      </div>
    </div>
  );
}

/** Sign-in, access key and «no server» screens; the app opens once the account is ready. */
export function AccountGate({
  state,
  onChange,
}: {
  state: AccountState;
  onChange: (next: AccountState) => void;
}) {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [name, setName] = useState('');
  const [email, setEmail] = useState(state.user?.email ?? '');
  const [password, setPassword] = useState('');
  const [repeat, setRepeat] = useState('');
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState(false);
  const send = async (method: string, params: object = {}) => {
    setBusy(true);
    try {
      onChange(await api.request<AccountState>(method, params));
    } catch (err) {
      reportError(err);
    } finally {
      setBusy(false);
    }
  };
  const submit = (event: FormEvent, method: string, params: object) => {
    event.preventDefault();
    void send(method, params);
  };
  const logout = (
    <Button variant="outline" disabled={busy} onClick={() => void send('account.logout')}>
      <LogOut size={15} /> Выйти
    </Button>
  );

  if (!state.signed_in) {
    const registering = mode === 'register';
    const mismatch = registering && repeat.length > 0 && repeat !== password;
    const ready = registering
      ? name.trim() && email.trim() && password.length >= 8 && repeat === password
      : email.trim() && password;
    return (
      <Screen
        icon={registering ? <UserPlus size={20} /> : <LogIn size={20} />}
        title={registering ? 'Регистрация' : 'Вход'}
        hint={
          registering
            ? 'Создайте аккаунт. После регистрации приложение попросит ключ доступа.'
            : state.reason || 'Войдите в свой аккаунт.'
        }
      >
        <form
          className="gate-form"
          onSubmit={event =>
            registering
              ? submit(event, 'account.register', { name, email, password })
              : submit(event, 'account.login', { email, password })
          }
        >
          {registering && (
            <label>
              Имя
              <input
                autoComplete="name"
                autoFocus
                maxLength={80}
                value={name}
                onChange={event => setName(event.target.value)}
              />
            </label>
          )}
          <label>
            Email
            <input
              type="email"
              autoComplete="username"
              autoFocus={!registering}
              value={email}
              onChange={event => setEmail(event.target.value)}
            />
          </label>
          <label>
            Пароль
            <input
              type="password"
              autoComplete={registering ? 'new-password' : 'current-password'}
              placeholder={registering ? 'Не меньше 8 символов' : undefined}
              value={password}
              onChange={event => setPassword(event.target.value)}
            />
          </label>
          {registering && (
            <label>
              Повторите пароль
              <input
                type="password"
                autoComplete="new-password"
                aria-invalid={mismatch}
                value={repeat}
                onChange={event => setRepeat(event.target.value)}
              />
              {mismatch && <span className="gate-error">Пароли не совпадают</span>}
            </label>
          )}
          <Button type="submit" disabled={busy || !ready}>
            {registering ? <UserPlus size={15} /> : <LogIn size={15} />}{' '}
            {busy ? (registering ? 'Создаём…' : 'Вход…') : registering ? 'Зарегистрироваться' : 'Войти'}
          </Button>
        </form>
        <p className="gate-note">
          {registering ? 'Уже есть аккаунт?' : 'Нет аккаунта?'}{' '}
          <button
            type="button"
            className="gate-switch"
            disabled={busy}
            onClick={() => setMode(registering ? 'login' : 'register')}
          >
            {registering ? 'Войти' : 'Зарегистрироваться'}
          </button>
        </p>
      </Screen>
    );
  }
  if (!state.online) {
    return (
      <Screen
        icon={<WifiOff size={20} />}
        title="Нет связи с сервером"
        hint="Приложение работает только при связи с сервером. Проверьте интернет — окно откроется само, когда связь появится."
      >
        <div className="gate-actions">
          <Button disabled={busy} onClick={() => void send('account.state')}>
            <RefreshCw size={15} className={busy ? 'spin' : undefined} /> Повторить
          </Button>
          {logout}
        </div>
      </Screen>
    );
  }
  return (
    <Screen
      icon={<KeyRound size={20} />}
      title="Ключ доступа"
      hint={
        state.reason ||
        `${state.user?.email ?? ''}: введите ключ доступа. Его выдаёт администратор, вводить нужно один раз.`
      }
    >
      <form className="gate-form" onSubmit={event => submit(event, 'account.activate', { key })}>
        <label>
          Ключ
          <input
            autoFocus
            spellCheck={false}
            placeholder="ALF-XXXXX-XXXXX-XXXXX-XXXXX"
            value={key}
            onChange={event => setKey(event.target.value.toUpperCase())}
          />
        </label>
        <Button type="submit" disabled={busy || key.trim().length < 8}>
          <KeyRound size={15} /> Активировать
        </Button>
      </form>
      <div className="gate-actions">{logout}</div>
    </Screen>
  );
}

/** Name, role and sign-out in the top bar. */
export function AccountMenu({
  state,
  onChange,
}: {
  state: AccountState;
  onChange: (next: AccountState) => void;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [open]);
  const user = state.user;
  if (!user) return null;
  const name = user.display_name || user.email;
  const logout = async () => {
    try {
      onChange(await api.request<AccountState>('account.logout'));
    } catch (err) {
      reportError(err);
    }
  };
  return (
    <div className="account-menu" ref={root}>
      <button
        type="button"
        className="account-button"
        aria-haspopup="menu"
        aria-expanded={open}
        title={name}
        onClick={() => setOpen(value => !value)}
      >
        <UserRound size={16} />
        <span>{name}</span>
        {state.role && state.role !== 'user' && <em>{roleLabel[state.role]}</em>}
      </button>
      {open && (
        <div className="account-dropdown" role="menu">
          <strong>{name}</strong>
          {user.display_name && <span>{user.email}</span>}
          <span className="account-role">{roleLabel[state.role ?? 'user']}</span>
          <button type="button" role="menuitem" onClick={() => void logout()}>
            <LogOut size={14} /> Выйти из аккаунта
          </button>
        </div>
      )}
    </div>
  );
}
