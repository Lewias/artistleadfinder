import { useEffect, useState, type FormEvent } from 'react';
import { open } from '@tauri-apps/plugin-dialog';
import {
  Check,
  Cookie,
  Globe2,
  LogIn,
  Pencil,
  Play,
  Plus,
  Radar,
  RefreshCw,
  ScanSearch,
  ShieldCheck,
  Sparkles,
  Square,
  SquareCheck,
  Trash2,
} from 'lucide-react';
import { PageHeader } from './PageHeader';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import type { BrowserProfile, OutreachWorkspace, ScoutAccountRow } from '../services/types';
import { Button } from './ui/button';
import { Modal } from './Modal';
import { ProxyFields } from './ProxyFields';
import { emptyForm, fromProfile, getProxy, type ProfileForm } from './profileForm';
import { controlScout, errorText, scoutActive, startScout } from './accountRuns';

type Dialog = { kind: 'create' } | { kind: 'edit' | 'delete'; profile: BrowserProfile } | null;

const secretStore = navigator.userAgent.includes('Mac') ? 'macOS Keychain' : 'Windows DPAPI';

/** Status line under an account name: what it is doing now, or whether it can start. */
function accountStatus(profile: BrowserProfile, scout: ScoutAccountRow | undefined, sending: boolean) {
  if (scout && scoutActive(scout)) {
    const run = scout.run!;
    if (run.status === 'paused')
      return run.error
        ? { text: 'Парсинг · нужна проверка в браузере', tone: 'warn' }
        : { text: `Парсинг на паузе · ${scout.found} из ${scout.target}`, tone: 'warn' };
    return { text: `Парсинг · найдено ${scout.found} из ${scout.target}`, tone: 'busy' };
  }
  if (sending) return { text: 'Рассылка идёт', tone: 'busy' };
  return profile.cookie_count
    ? { text: 'Сессия сохранена · готов', tone: 'ok' }
    : { text: 'Нет сессии — откройте окно и войдите', tone: 'off' };
}

export function BrowserProfiles() {
  const [profiles, setProfiles] = useState<BrowserProfile[]>([]);
  const scout = useResource<ScoutAccountRow[]>('scout.accounts', {}, 3000);
  const workspaceResource = useResource<OutreachWorkspace>('outreach.workspace', {}, 4000);
  const [workspace, setWorkspace] = useState<OutreachWorkspace>();
  const [targets, setTargets] = useState<Record<string, string>>({});
  const [dialog, setDialog] = useState<Dialog>(null);
  const [form, setForm] = useState<ProfileForm>(emptyForm);
  const [formError, setFormError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [message, setMessage] = useState('');
  const refresh = async () => setProfiles(await api.browser<BrowserProfile[]>('list'));
  useEffect(() => {
    void refresh().catch(err => setMessage(errorText(err)));
    // Sessions are saved automatically when a browser window closes; keep counts current.
    const timer = setInterval(() => void refresh().catch(() => undefined), 5000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (workspaceResource.data) setWorkspace(workspaceResource.data);
  }, [workspaceResource.data]);

  const scoutRows = new Map((scout.data ?? []).map(row => [row.profile.id, row]));
  const selected = (workspace?.sender_ids ?? []).filter(id => profiles.some(profile => profile.id === id));
  const outreachSenders = workspace?.running ? (workspace.campaign?.sender_ids ?? []) : [];
  const launched = profiles.filter(
    profile => scoutActive(scoutRows.get(profile.id)) || outreachSenders.includes(profile.id),
  ).length;
  const withSession = profiles.filter(profile => profile.cookie_count > 0).length;

  /** Runs one account's action; its error is shown as «[name] Ошибка: …» under the list. */
  const run = async (profile: BrowserProfile, operation: () => Promise<unknown>) => {
    setBusy(profile.id);
    setMessage('');
    setErrors(current => Object.fromEntries(Object.entries(current).filter(([id]) => id !== profile.id)));
    try {
      await operation();
    } catch (err) {
      setErrors(current => ({ ...current, [profile.id]: errorText(err) }));
    } finally {
      setBusy(null);
      scout.refresh();
      workspaceResource.refresh();
      void refresh().catch(() => undefined);
    }
  };
  const setWorkspaceSenders = async (ids: string[]) =>
    setWorkspace(await api.request<OutreachWorkspace>('outreach.workspace_update', { sender_ids: ids }));
  const toggleSelected = (profile: BrowserProfile) =>
    void run(profile, () =>
      setWorkspaceSenders(
        selected.includes(profile.id) ? selected.filter(id => id !== profile.id) : [...selected, profile.id],
      ),
    );
  const outreach = (profile: BrowserProfile) =>
    void run(profile, async () => {
      if (outreachSenders.includes(profile.id)) {
        setWorkspace(await api.request<OutreachWorkspace>('outreach.workspace_stop'));
        return;
      }
      await setWorkspaceSenders([profile.id]);
      setWorkspace(await api.request<OutreachWorkspace>('outreach.workspace_start'));
    });
  const parse = (profile: BrowserProfile, row: ScoutAccountRow | undefined) =>
    void run(profile, async () => {
      if (!row) throw new Error('Данные парсера ещё загружаются, повторите через секунду.');
      if (!scoutActive(row)) return startScout(row);
      return controlScout(row, row.run!.status === 'paused' && row.run!.error ? 'resume' : 'cancel');
    });
  const saveTarget = (profile: BrowserProfile, row: ScoutAccountRow | undefined) => {
    const value = Number(targets[profile.id]);
    if (!row || targets[profile.id] === undefined || value === row.target) return;
    if (!Number.isInteger(value) || value < 1 || value > 10000) {
      setErrors(current => ({ ...current, [profile.id]: 'Цель: от 1 до 10000 лидов.' }));
      return;
    }
    void run(profile, () => api.request('scout.account_target', { profile_id: profile.id, target: value }));
  };
  const pickCookies = (profile: BrowserProfile) =>
    void run(profile, async () => {
      const path = await open({
        title: `Cookies для ${profile.name}`,
        multiple: false,
        filters: [{ name: 'Cookies JSON / Netscape', extensions: ['json', 'txt'] }],
      });
      if (!path || Array.isArray(path)) return;
      await api.browser('import_cookies', { id: profile.id, path });
      setMessage(`Cookies для «${profile.name}» обновлены. Откройте окно и проверьте вход.`);
    });

  const openDialog = (next: Dialog) => {
    setDialog(next);
    setFormError('');
    setForm(next?.kind === 'edit' ? fromProfile(next.profile) : emptyForm());
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy('form');
    setFormError('');
    try {
      const proxy = getProxy(form);
      if (dialog?.kind === 'edit') {
        await api.browser('update', { id: dialog.profile.id, name: form.name.trim(), proxy });
        setMessage('Настройки профиля сохранены. Прокси применится при следующем открытии окна.');
      } else {
        await api.browser('create', { name: form.name.trim(), proxy });
        setMessage('Профиль создан. Откройте окно, чтобы войти в Instagram, или импортируйте cookies.');
      }
      setDialog(null);
      await refresh();
      scout.refresh();
    } catch (err) {
      setFormError(errorText(err));
    } finally {
      setBusy(null);
    }
  };
  const remove = (profile: BrowserProfile) =>
    void run(profile, async () => {
      await api.browser('delete', { id: profile.id });
      setDialog(null);
      setMessage(`Профиль «${profile.name}» удалён.`);
    });

  const attention = profiles.flatMap(profile => {
    const row = scoutRows.get(profile.id);
    const text = errors[profile.id] || (scoutActive(row) && row?.run?.error) || '';
    return text ? [`[${profile.name}] Ошибка: ${text}`] : [];
  });

  return (
    <div className="profiles-page">
      <PageHeader
        page="profiles"
        count={
          <span title="Всего профилей / с сохранённой сессией">{`${profiles.length} / ${withSession}`}</span>
        }
        actions={
          <Button onClick={() => openDialog({ kind: 'create' })}>
            <Plus size={16} /> Новый профиль
          </Button>
        }
      >
        <div className="stat-pills">
          <span className="stat-pill small" title="Отмеченные аккаунты: для «Парсинг» и «Рассылка» на досках">
            <SquareCheck size={12} /> Выбрано <b>{selected.length}</b>
          </span>
          <span className="stat-pill small">
            <Sparkles size={12} /> Запущено <b>{launched}</b>
          </span>
        </div>
      </PageHeader>
      <div className="panel info-bar">
        <ShieldCheck size={16} />
        <span>Изоляция сессий</span>
        <span className="tag">{secretStore}</span>
        <span className="helper">
          Каждый профиль — отдельное окно Chromium. Cookies и пароль прокси хранятся зашифрованными.
        </span>
      </div>
      {profiles.length === 0 && (
        <div className="panel empty-panel">Пока нет профилей. Нажмите «Новый профиль».</div>
      )}
      <div className="profile-list">
        {profiles.map(profile => {
          const row = scoutRows.get(profile.id);
          const active = scoutActive(row);
          const needsResume = active && row?.run?.status === 'paused' && Boolean(row.run.error);
          const sending = outreachSenders.includes(profile.id);
          const status = accountStatus(profile, row, sending);
          const locked = busy !== null;
          const checked = selected.includes(profile.id);
          return (
            <section className="panel account-row" key={profile.id}>
              <button
                type="button"
                role="checkbox"
                aria-checked={checked}
                aria-label={`Выбрать ${profile.name}`}
                className={`row-check${checked ? ' checked' : ''}`}
                disabled={locked}
                onClick={() => toggleSelected(profile)}
              >
                {checked && <Check size={14} strokeWidth={3} />}
              </button>
              <div className="account-title">
                <h3>{profile.name}</h3>
                <div className="account-meta">
                  <Globe2 size={13} />
                  <span>
                    {profile.proxy
                      ? `${profile.proxy.scheme.toUpperCase()} ${profile.proxy.host}:${profile.proxy.port}`
                      : '—'}
                  </span>
                  <span className={`dot ${status.tone}`} />
                  <span>{status.text}</span>
                </div>
              </div>
              <div className="account-actions">
                <label className="target-field" title="Цель парсера: сколько подходящих лидов найти">
                  <ScanSearch size={15} aria-hidden="true" />
                  <input
                    aria-label={`Цель парсера для ${profile.name}`}
                    type="number"
                    min={1}
                    max={10000}
                    value={targets[profile.id] ?? String(row?.target ?? '')}
                    disabled={locked || active || !row}
                    onChange={event =>
                      setTargets(current => ({ ...current, [profile.id]: event.target.value }))
                    }
                    onBlur={() => saveTarget(profile, row)}
                  />
                </label>
                <Button
                  disabled={locked}
                  title={sending ? 'Остановить рассылку' : 'Запустить рассылку списка с этого аккаунта'}
                  onClick={() => outreach(profile)}
                >
                  {sending ? <Square size={13} /> : <Play size={14} />} {sending ? 'Стоп' : 'Рассылка'}
                </Button>
                <Button
                  variant={active && !needsResume ? 'danger' : 'green'}
                  disabled={locked || !row}
                  title={
                    active
                      ? needsResume
                        ? 'Продолжить после проверки в браузере'
                        : 'Остановить парсинг, прогресс сохранится'
                      : 'Искать лидов по SMM-источникам'
                  }
                  onClick={() => parse(profile, row)}
                >
                  {active && !needsResume ? <Square size={13} /> : <Radar size={15} />}
                  {active ? (needsResume ? 'Продолжить' : 'Стоп') : 'Парсинг'}
                </Button>
                <Button
                  icon
                  variant="outline"
                  aria-label="Открыть окно браузера"
                  title="Открыть окно браузера (вход в Instagram)"
                  disabled={locked}
                  onClick={() =>
                    void run(profile, async () => {
                      const result = await api.browser<{ warning?: string }>('open', { id: profile.id });
                      setMessage(
                        result.warning ||
                          `Окно «${profile.name}» открыто. Сессия сохранится автоматически при закрытии.`,
                      );
                    })
                  }
                >
                  <LogIn size={16} />
                </Button>
                <Button
                  icon
                  variant="outline"
                  aria-label="Импорт cookies"
                  title="Импорт cookies"
                  disabled={locked}
                  onClick={() => pickCookies(profile)}
                >
                  <Cookie size={16} />
                </Button>
                <Button
                  icon
                  variant="outline"
                  aria-label="Настроить профиль"
                  title="Название и прокси"
                  disabled={locked}
                  onClick={() => openDialog({ kind: 'edit', profile })}
                >
                  <Pencil size={16} />
                </Button>
                <Button
                  icon
                  variant="outline"
                  aria-label="Сохранить сессию"
                  title="Сохранить сессию из открытого окна сейчас"
                  disabled={locked}
                  onClick={() =>
                    void run(profile, async () => {
                      await api.browser('save', { id: profile.id });
                      setMessage(`Сессия «${profile.name}» сохранена.`);
                    })
                  }
                >
                  <RefreshCw size={16} />
                </Button>
                <Button
                  icon
                  variant="danger"
                  aria-label="Удалить профиль"
                  title="Удалить профиль"
                  disabled={locked}
                  onClick={() => openDialog({ kind: 'delete', profile })}
                >
                  <Trash2 size={16} />
                </Button>
              </div>
            </section>
          );
        })}
      </div>
      {attention.map(text => (
        <p role="alert" className="row-error" key={text}>
          {text}
        </p>
      ))}
      {message && (
        <p role="status" className="helper">
          {message}
        </p>
      )}

      {(dialog?.kind === 'create' || dialog?.kind === 'edit') && (
        <Modal
          title={dialog.kind === 'edit' ? 'Настройки профиля' : 'Новый профиль'}
          onClose={() => setDialog(null)}
        >
          <form className="profile-form" onSubmit={event => void submit(event)}>
            <label>
              Название профиля
              <input
                maxLength={80}
                required
                autoFocus
                value={form.name}
                disabled={busy === 'form'}
                placeholder="Например, рабочий Instagram"
                onChange={event => setForm({ ...form, name: event.target.value })}
              />
            </label>
            <ProxyFields
              value={form.proxy}
              input={form.proxyInput}
              disabled={busy === 'form'}
              onChange={proxy => setForm(current => ({ ...current, proxy }))}
              onInputChange={proxyInput => setForm(current => ({ ...current, proxyInput }))}
            />
            {formError && (
              <p role="alert" className="error-text">
                {formError}
              </p>
            )}
            <p className="helper">
              {dialog.kind === 'edit'
                ? 'Для смены прокси сначала закройте окно этого профиля.'
                : 'Создаётся пустая сессия: войдите вручную после открытия окна или импортируйте cookies. Поддерживаются HTTP и SOCKS5 с логином и паролем.'}
            </p>
            <div className="actions">
              <Button type="submit" disabled={busy === 'form' || !form.name.trim()}>
                {dialog.kind === 'edit' ? 'Сохранить' : 'Создать профиль'}
              </Button>
              <Button type="button" variant="outline" onClick={() => setDialog(null)}>
                Отмена
              </Button>
            </div>
          </form>
        </Modal>
      )}
      {dialog?.kind === 'delete' && (
        <Modal title={`Удалить «${dialog.profile.name}»?`} onClose={() => setDialog(null)}>
          <p className="helper">
            Профиль и его сохранённые cookies будут удалены. Перед этим закройте окно браузера профиля. Лиды и
            история рассылок останутся.
          </p>
          <div className="actions">
            <Button variant="danger" disabled={busy !== null} onClick={() => remove(dialog.profile)}>
              <Trash2 size={15} /> Удалить профиль
            </Button>
            <Button variant="outline" onClick={() => setDialog(null)}>
              Отмена
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
