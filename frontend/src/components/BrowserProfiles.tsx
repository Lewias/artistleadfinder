import { useEffect, useState, type FormEvent } from 'react';
import { open } from '@tauri-apps/plugin-dialog';
import { Cookie, Globe2, Plus, ShieldCheck } from 'lucide-react';
import { api } from '../services/api';
import type { BrowserProfile, BrowserProxy } from '../services/types';
import { Button } from './ui/button';
import { parseProxyInput, type ProxyForm } from './proxyInput';

type ProfileForm = { name: string; proxy: ProxyForm; proxyInput: string };
const emptyProxy = (): ProxyForm => ({
  scheme: 'none',
  host: '',
  port: '',
  auth: false,
  username: '',
  password: '',
  hasPassword: false,
});
const emptyForm = (): ProfileForm => ({ name: '', proxy: emptyProxy(), proxyInput: '' });
const fromProfile = (profile: BrowserProfile): ProfileForm => ({
  name: profile.name,
  proxy: profile.proxy
    ? {
        scheme: profile.proxy.scheme,
        host: profile.proxy.host,
        port: String(profile.proxy.port),
        auth: Boolean(profile.proxy.username),
        username: profile.proxy.username || '',
        password: '',
        hasPassword: Boolean(profile.proxy.has_password),
      }
    : emptyProxy(),
  proxyInput: '',
});
function getProxy(draft: ProfileForm): (BrowserProxy & { password?: string }) | null {
  const form = draft.proxyInput.trim() ? parseProxyInput(draft.proxyInput, draft.proxy.scheme) : draft.proxy;
  if (form.scheme === 'none') return null;
  const host = form.host.trim();
  const port = Number(form.port);
  if (
    !/^[A-Za-z0-9.-]+$/.test(host) ||
    host.length > 253 ||
    host.startsWith('.') ||
    host.endsWith('.') ||
    host.includes('..') ||
    host.startsWith('-') ||
    host.endsWith('-')
  )
    throw new Error('Укажите корректный адрес прокси без протокола и логина.');
  if (!Number.isInteger(port) || port < 1 || port > 65535)
    throw new Error('Порт прокси должен быть от 1 до 65535.');
  if (!form.auth) return { scheme: form.scheme, host, port };
  const username = form.username.trim();
  const hasControl = (value: string) =>
    [...value].some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127);
  if (
    !username ||
    username.includes(':') ||
    hasControl(username) ||
    new TextEncoder().encode(username).length > 128
  )
    throw new Error('Укажите корректный логин прокси.');
  if (!form.password && !form.hasPassword) throw new Error('Укажите пароль прокси.');
  if (form.password && (hasControl(form.password) || new TextEncoder().encode(form.password).length > 255))
    throw new Error('Некорректный пароль прокси.');
  return { scheme: form.scheme, host, port, username, ...(form.password ? { password: form.password } : {}) };
}
function ProxyFields({
  value,
  input,
  onChange,
  onInputChange,
  disabled,
}: {
  value: ProxyForm;
  input: string;
  onChange: (value: ProxyForm) => void;
  onInputChange: (value: string) => void;
  disabled: boolean;
}) {
  const [inputError, setInputError] = useState('');
  const apply = (text: string) => {
    try {
      onChange(parseProxyInput(text, value.scheme));
      onInputChange(text);
      setInputError('');
    } catch (error) {
      onInputChange(text);
      setInputError(error instanceof Error ? error.message : 'Некорректный формат прокси.');
    }
  };
  return (
    <div className="profile-proxy-fields">
      <label>
        Подключение
        <select
          value={value.scheme}
          disabled={disabled}
          onChange={event => onChange({ ...value, scheme: event.target.value as ProxyForm['scheme'] })}
        >
          <option value="none">Без прокси</option>
          <option value="http">HTTP-прокси</option>
          <option value="socks5">SOCKS5-прокси</option>
        </select>
      </label>
      <label>
        Прокси одной строкой
        <input
          type="text"
          value={input}
          disabled={disabled}
          placeholder="socks5://логин:пароль@адрес:порт"
          autoComplete="off"
          spellCheck={false}
          onPaste={event => {
            event.preventDefault();
            apply(event.clipboardData.getData('text'));
          }}
          onChange={event => {
            onInputChange(event.target.value);
            setInputError('');
          }}
          onBlur={() => {
            if (input) apply(input);
          }}
        />
      </label>
      <p className="helper profile-proxy-hint">
        Можно вставить и без <code>socks5://</code> — такой прокси будет считаться SOCKS5. Для HTTP укажите{' '}
        <code>http://</code> или выберите тип выше.
      </p>
      {inputError && (
        <p className="error-text" role="alert">
          {inputError}
        </p>
      )}
      {value.scheme !== 'none' && value.host && (
        <p className="helper profile-proxy-summary">
          Настроен {value.scheme.toUpperCase()}: {value.host}:{value.port}
          {value.auth ? ' · с авторизацией' : ''}. Для замены вставьте новую строку, для удаления выберите
          «Без прокси».
        </p>
      )}
    </div>
  );
}
export function BrowserProfiles() {
  const [profiles, setProfiles] = useState<BrowserProfile[]>([]);
  const [draft, setDraft] = useState<ProfileForm>(emptyForm);
  const [editing, setEditing] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<ProfileForm>(emptyForm);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [deleting, setDeleting] = useState<string | null>(null);
  const refresh = async () => setProfiles(await api.browser<BrowserProfile[]>('list'));
  useEffect(() => {
    void refresh().catch(err => setError(String(err)));
    // Sessions are saved automatically when a browser window closes; keep counts current.
    const timer = setInterval(() => void refresh().catch(() => undefined), 5000);
    return () => clearInterval(timer);
  }, []);
  const run = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await operation();
      await refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };
  const create = (event: FormEvent) => {
    event.preventDefault();
    void run(async () => {
      await api.browser('create', { name: draft.name.trim(), proxy: getProxy(draft) });
      setDraft(emptyForm());
      setMessage('Профиль создан. Откройте его, чтобы войти в Instagram вручную, или импортируйте cookies.');
    });
  };
  const pickCookies = (profile: BrowserProfile) =>
    void run(async () => {
      const path = await open({
        title: `Cookies для ${profile.name}`,
        multiple: false,
        filters: [{ name: 'Cookies JSON / Netscape', extensions: ['json', 'txt'] }],
      });
      if (!path || Array.isArray(path)) return;
      await api.browser('import_cookies', { id: profile.id, path });
      setMessage(`Cookies для «${profile.name}» обновлены. Откройте профиль и проверьте вход.`);
    });
  const saveProfile = (event: FormEvent, id: string) => {
    event.preventDefault();
    void run(async () => {
      await api.browser('update', { id, name: editDraft.name.trim(), proxy: getProxy(editDraft) });
      setEditing(null);
      setMessage('Настройки профиля сохранены. Прокси применится при следующем открытии браузера.');
    });
  };
  return (
    <div className="profiles-page">
      <div className="profiles-intro">
        <ShieldCheck size={19} />
        <span>
          Каждый профиль открывается в отдельной сессии. Сохранённые cookies и настройки прокси защищены
          Windows DPAPI.
        </span>
      </div>
      <section className="panel profile-create">
        <div className="profile-section-title">
          <div>
            <span className="profile-kicker">НОВЫЙ ПРОФИЛЬ</span>
            <h2>Добавить профиль</h2>
            <p className="helper">
              Создайте пустую сессию. Войти можно вручную после открытия окна или через импорт собственных
              cookies.
            </p>
          </div>
          <Plus size={22} />
        </div>
        <form onSubmit={create}>
          <label>
            Название профиля
            <input
              maxLength={80}
              required
              value={draft.name}
              disabled={busy}
              placeholder="Например, рабочий Instagram"
              onChange={event => setDraft({ ...draft, name: event.target.value })}
            />
          </label>
          <ProxyFields
            value={draft.proxy}
            input={draft.proxyInput}
            disabled={busy}
            onChange={proxy => setDraft(current => ({ ...current, proxy }))}
            onInputChange={proxyInput => setDraft(current => ({ ...current, proxyInput }))}
          />
          <Button type="submit" disabled={busy || !draft.name.trim()}>
            Создать профиль
          </Button>
        </form>
        <p className="helper profile-footnote">
          Поддерживаются HTTP и SOCKS5 с логином и паролем. Пароль хранится зашифрованным и не показывается
          после сохранения.
        </p>
      </section>
      <div className="profile-list-heading">
        <h2>
          Ваши профили <span>{profiles.length}</span>
        </h2>
        <p className="helper">Для смены прокси или cookies сначала закройте окно соответствующего профиля.</p>
      </div>
      {profiles.length === 0 && (
        <div className="panel profile-empty">Пока нет профилей. Создайте первый выше.</div>
      )}
      <div className="profile-list">
        {profiles.map(profile => (
          <section className="panel profile-card" key={profile.id}>
            <div className="profile-card-head">
              <div className="profile-avatar">{profile.name.charAt(0).toUpperCase()}</div>
              <div className="profile-identity">
                <h3>{profile.name}</h3>
                <span>Instagram · отдельная сессия</span>
              </div>
            </div>
            <div className="profile-facts">
              <div>
                <Cookie size={16} />
                <span>
                  {profile.cookie_count
                    ? `${profile.cookie_count} cookies сохранено`
                    : 'Cookies не добавлены'}
                </span>
              </div>
              <div>
                <Globe2 size={16} />
                <span>
                  {profile.proxy
                    ? `${profile.proxy.scheme.toUpperCase()} · ${profile.proxy.host}:${profile.proxy.port}${profile.proxy.has_password ? ' · с авторизацией' : ''}`
                    : 'Без прокси'}
                </span>
              </div>
            </div>
            <div className="actions profile-actions">
              <Button
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    const result = await api.browser<{ warning?: string }>('open', { id: profile.id });
                    setMessage(
                      result.warning ||
                        'Браузер открыт. Проверьте вход: сессия сохранится автоматически при закрытии окна.',
                    );
                  })
                }
              >
                Открыть браузер
              </Button>
              <Button
                variant="outline"
                disabled={busy}
                onClick={() =>
                  void run(async () => {
                    await api.browser('save', { id: profile.id });
                    setMessage('Cookies текущей сессии сохранены.');
                  })
                }
              >
                Сохранить сессию
              </Button>
              <Button variant="outline" disabled={busy} onClick={() => pickCookies(profile)}>
                Импорт cookies
              </Button>
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => {
                  setEditing(editing === profile.id ? null : profile.id);
                  setEditDraft(fromProfile(profile));
                  setDeleting(null);
                }}
              >
                Настроить
              </Button>
            </div>
            {editing === profile.id && (
              <form className="profile-editor" onSubmit={event => saveProfile(event, profile.id)}>
                <label>
                  Название
                  <input
                    maxLength={80}
                    required
                    value={editDraft.name}
                    disabled={busy}
                    onChange={event => setEditDraft({ ...editDraft, name: event.target.value })}
                  />
                </label>
                <ProxyFields
                  value={editDraft.proxy}
                  input={editDraft.proxyInput}
                  disabled={busy}
                  onChange={proxy => setEditDraft(current => ({ ...current, proxy }))}
                  onInputChange={proxyInput => setEditDraft(current => ({ ...current, proxyInput }))}
                />
                <div className="actions">
                  <Button type="submit" disabled={busy || !editDraft.name.trim()}>
                    Сохранить изменения
                  </Button>
                  <Button type="button" variant="outline" onClick={() => setEditing(null)}>
                    Отмена
                  </Button>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => {
                      setEditing(null);
                      setDeleting(profile.id);
                    }}
                  >
                    Удалить профиль
                  </Button>
                </div>
              </form>
            )}
            {deleting === profile.id && (
              <div className="profile-delete" role="alert">
                <p>
                  Удалить профиль «{profile.name}» и его сохранённые cookies? Перед этим закройте окно
                  браузера.
                </p>
                <div className="actions">
                  <Button
                    disabled={busy}
                    onClick={() =>
                      void run(async () => {
                        await api.browser('delete', { id: profile.id });
                        setDeleting(null);
                        setMessage('Профиль удалён.');
                      })
                    }
                  >
                    Удалить профиль
                  </Button>
                  <Button variant="outline" onClick={() => setDeleting(null)}>
                    Отмена
                  </Button>
                </div>
              </div>
            )}
          </section>
        ))}
      </div>
      {message && (
        <p role="status" className="notice">
          {message}
        </p>
      )}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      <p className="helper">
        Файл cookies остаётся на вашем устройстве. Импорт принимает только cookies Instagram в формате JSON
        или Netscape cookies.txt до 1 МБ. Наличие cookies не подтверждает успешный вход.
      </p>
    </div>
  );
}
