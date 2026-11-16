import { useState, type FormEvent } from 'react';
import { Globe2, LogIn, Pencil, Plus, RefreshCw, ShieldCheck, Unplug } from 'lucide-react';
import type { BrowserProfile, ThreadsAccount } from '../services/types';
import { useThreads } from '../hooks/useThreads';
import { errorText } from '../lib/errors';
import { PageHeader } from '../components/PageHeader';
import { DataState } from '../components/DataState';
import { ErrorToast } from '../components/Toaster';
import { Button } from '../components/ui/button';
import { Menu } from '../components/Menu';
import { Modal } from '../components/Modal';
import { ProxyFields } from '../components/ProxyFields';
import { emptyForm, fromProfile, getProxy } from '../components/profileForm';
import './Threads.css';

type ProfileDialog = { kind: 'create' } | { kind: 'edit'; profile: BrowserProfile };

export function ThreadsAccounts() {
  const resource = useThreads();
  const { data, busy } = resource;
  const [dialog, setDialog] = useState<ProfileDialog | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [formError, setFormError] = useState('');
  const [disconnect, setDisconnect] = useState<ThreadsAccount>();
  const [notice, setNotice] = useState('');
  const openDialog = (next: ProfileDialog) => {
    setDialog(next);
    setForm(next.kind === 'edit' ? fromProfile(next.profile) : emptyForm());
    setFormError('');
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      const params = { name: form.name.trim(), proxy: getProxy(form) };
      const saved =
        dialog?.kind === 'edit'
          ? await resource.browser<BrowserProfile>('update', { ...params, id: dialog.profile.id })
          : await resource.browser<BrowserProfile>('create', params);
      if (saved) {
        setDialog(null);
        setNotice(
          dialog?.kind === 'edit'
            ? 'Настройки профиля сохранены.'
            : 'Профиль создан. Откройте браузер и войдите в Threads.',
        );
      }
    } catch (err) {
      setFormError(errorText(err));
    }
  };
  if (!data)
    return (
      <div className="profiles-page">
        <PageHeader page="threads-accounts" />
        <DataState {...resource} retry={resource.refresh} />
      </div>
    );
  const connected = data.accounts.filter(a => a.status !== 'disconnected');
  return (
    <div className="profiles-page threads-accounts-page">
      <PageHeader
        page="threads-accounts"
        count={
          <span title="Всего профилей / подключено к Threads">{`${data.browser_profiles.length} / ${connected.length}`}</span>
        }
        actions={
          <Button disabled={busy} onClick={() => openDialog({ kind: 'create' })}>
            <Plus size={16} /> Новый профиль
          </Button>
        }
      />
      <ErrorToast message={resource.mutationError || resource.error} />
      <div className="panel info-bar">
        <ShieldCheck size={16} />
        <span>Изоляция сессий</span>
        <span className="helper">Общие профили Instagram и Threads. Сессии сохраняются автоматически.</span>
      </div>
      {!data.browser_profiles.length && (
        <div className="panel empty-panel">Пока нет профилей. Нажмите «Новый профиль».</div>
      )}
      <div className="profile-list">
        {data.browser_profiles.map(profile => {
          const account = connected.find(a => a.browser_profile_id === profile.id);
          const waiting =
            data.browser_auth?.profile_id === profile.id && data.browser_auth.status === 'waiting';
          const tone = waiting ? 'busy' : account?.status === 'active' ? 'ok' : account ? 'warn' : 'off';
          const status = waiting
            ? 'Ожидаем вход в Threads'
            : account
              ? `@${account.username} · ${account.status_label}`
              : 'Войдите в Threads';
          return (
            <section className="panel account-row" key={profile.id}>
              <div className="account-title">
                <h3>{profile.name}</h3>
                <div className="account-meta">
                  <Globe2 size={13} />
                  <span>
                    {profile.proxy
                      ? `${profile.proxy.scheme.toUpperCase()} ${profile.proxy.host}:${profile.proxy.port}`
                      : 'Без прокси'}
                  </span>
                  <span className={`dot ${tone}`} />
                  <span>{status}</span>
                </div>
                {account?.reason && <p className="row-error">{account.reason}</p>}
              </div>
              <div className="account-actions">
                <Button
                  variant="outline"
                  disabled={busy}
                  title={`Открыть Threads в профиле «${profile.name}»`}
                  onClick={() => void resource.openBrowser(profile.id)}
                >
                  <LogIn size={16} /> Браузер
                </Button>
                <Menu label={`Ещё для ${profile.name}`} down disabled={busy}>
                  {close => (
                    <>
                      <button
                        type="button"
                        role="menuitem"
                        className="menu-item"
                        onClick={() => {
                          close();
                          void resource
                            .browser('save', { id: profile.id })
                            .then(saved => saved && setNotice(`Сессия «${profile.name}» сохранена.`));
                        }}
                      >
                        <RefreshCw size={15} /> Сохранить сессию сейчас
                      </button>
                      <button
                        type="button"
                        role="menuitem"
                        className="menu-item"
                        onClick={() => {
                          close();
                          openDialog({ kind: 'edit', profile });
                        }}
                      >
                        <Pencil size={15} /> Название и прокси…
                      </button>
                      {account && (
                        <button
                          type="button"
                          role="menuitem"
                          className="menu-item danger"
                          onClick={() => {
                            close();
                            setDisconnect(account);
                          }}
                        >
                          <Unplug size={15} /> Отключить Threads…
                        </button>
                      )}
                    </>
                  )}
                </Menu>
              </div>
            </section>
          );
        })}
      </div>
      {data.browser_auth && data.browser_auth.status !== 'done' && (
        <div className="threads-login-notice">
          <p role="status" className={data.browser_auth.status === 'error' ? 'row-error' : 'helper'}>
            {data.browser_auth.message}
          </p>
          {data.browser_auth.status === 'waiting' && (
            <div className="actions">
              <Button variant="outline" disabled={busy} onClick={resource.refresh}>
                Проверить вход
              </Button>
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => void resource.call('threads.browser_cancel')}
              >
                Отменить подключение
              </Button>
            </div>
          )}
        </div>
      )}
      {notice && (
        <p className="helper" role="status">
          {notice}
        </p>
      )}
      {dialog && (
        <Modal
          title={dialog.kind === 'edit' ? 'Настройки профиля' : 'Новый профиль'}
          onClose={() => !busy && setDialog(null)}
        >
          <form className="profile-form" onSubmit={event => void submit(event)}>
            <label>
              Название профиля
              <input
                required
                maxLength={80}
                autoFocus
                value={form.name}
                disabled={busy}
                onChange={event => setForm({ ...form, name: event.target.value })}
                placeholder="Например, рабочий профиль"
              />
            </label>
            <ProxyFields
              value={form.proxy}
              input={form.proxyInput}
              disabled={busy}
              onChange={proxy => setForm(current => ({ ...current, proxy }))}
              onInputChange={proxyInput => setForm(current => ({ ...current, proxyInput }))}
            />
            <ErrorToast message={formError || resource.mutationError} />
            <p className="helper">
              {dialog.kind === 'edit'
                ? 'Для смены прокси сначала закройте окно этого профиля. Изменения применяются к Instagram и Threads.'
                : 'Профиль будет доступен в Instagram и Threads. После создания откройте браузер и войдите в аккаунт.'}
            </p>
            <div className="actions">
              <Button type="submit" disabled={busy || !form.name.trim()}>
                {dialog.kind === 'edit' ? 'Сохранить' : 'Создать профиль'}
              </Button>
              <Button type="button" variant="outline" disabled={busy} onClick={() => setDialog(null)}>
                Отмена
              </Button>
            </div>
          </form>
        </Modal>
      )}
      {disconnect && (
        <Modal title={`Отключить @${disconnect.username}?`} onClose={() => !busy && setDisconnect(undefined)}>
          <p>Очередь этого аккаунта остановится. История публикаций и общий браузерный профиль сохранятся.</p>
          <div className="actions">
            <Button
              variant="danger"
              disabled={busy}
              onClick={() =>
                void resource
                  .call('threads.account_control', { id: disconnect.id, action: 'disconnect' })
                  .then(saved => saved && setDisconnect(undefined))
              }
            >
              Отключить Threads
            </Button>
            <Button variant="outline" disabled={busy} onClick={() => setDisconnect(undefined)}>
              Отмена
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
