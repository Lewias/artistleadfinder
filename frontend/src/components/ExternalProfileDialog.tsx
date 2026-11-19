import { useState, type FormEvent } from 'react';
import { Download, Plus } from 'lucide-react';
import { api } from '../services/api';
import type { BrowserProfile, ExternalBrowserProvider } from '../services/types';
import { errorText } from '../lib/errors';
import { Modal } from './Modal';
import { ErrorToast } from './Toaster';
import { Button } from './ui/button';

const defaultPorts = { nst: '8848', dolphin: '3001' };
type RemoteProfile = { id: string; name: string };
type ProfilePage = { profiles: RemoteProfile[]; cursor: string | null };
type ImportResult = { added: number; skipped: number };

export function ExternalProfileDialog({
  profiles,
  onClose,
  onAdded,
}: {
  profiles: BrowserProfile[];
  onClose: () => void;
  onAdded: (result: ImportResult) => void;
}) {
  const [provider, setProvider] = useState<ExternalBrowserProvider>('nst');
  const [port, setPort] = useState(defaultPorts.nst);
  const [token, setToken] = useState('');
  const [rows, setRows] = useState<RemoteProfile[]>();
  const [cursor, setCursor] = useState<string | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [busy, setBusy] = useState<'list' | 'add'>();
  const [error, setError] = useState('');
  const linked = new Set(
    profiles.flatMap(profile => (profile.external?.provider === provider ? [profile.external.id] : [])),
  );
  const selected = rows?.filter(row => chosen.includes(row.id) && !linked.has(row.id)) ?? [];
  const connection = { provider, port: Number(port), token: token.trim() };

  const reset = () => {
    setRows(undefined);
    setCursor(null);
    setChosen([]);
    setError('');
  };
  const load = async (next: string | null = null) => {
    setBusy('list');
    setError('');
    try {
      const page = await api.browser<ProfilePage>('external_list', { ...connection, cursor: next });
      const combined = [...(next ? (rows ?? []) : []), ...page.profiles];
      setRows([...new Map(combined.map(row => [row.id, row])).values()]);
      setCursor(page.cursor);
      if (!next) setChosen([]);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(undefined);
    }
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!busy) void load();
  };
  const add = async () => {
    if (!selected.length || busy) return;
    setBusy('add');
    setError('');
    try {
      const result = await api.browser<ImportResult>('external_add', {
        ...connection,
        token: provider === 'nst' ? connection.token : '',
        profiles: selected,
      });
      onAdded(result);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(undefined);
    }
  };

  return (
    <Modal title="Подключить профили браузера" onClose={onClose} dismissible={!busy}>
      <form className="profile-form" onSubmit={submit}>
        <label>
          Браузер
          <select
            value={provider}
            disabled={Boolean(busy)}
            onChange={event => {
              const value = event.target.value as ExternalBrowserProvider;
              setProvider(value);
              setPort(defaultPorts[value]);
              setToken('');
              reset();
            }}
          >
            <option value="nst">NSTbrowser</option>
            <option value="dolphin">Dolphin Anty</option>
          </select>
        </label>
        <label>
          {provider === 'nst' ? 'API-ключ NSTbrowser' : 'API-токен Dolphin Anty'}
          <input
            type="password"
            autoComplete="off"
            required
            maxLength={8192}
            disabled={Boolean(busy)}
            value={token}
            onChange={event => {
              setToken(event.target.value);
              reset();
            }}
          />
        </label>
        <label>
          Порт локального API
          <input
            type="number"
            min={1}
            max={65535}
            required
            disabled={Boolean(busy)}
            value={port}
            onChange={event => {
              setPort(event.target.value);
              reset();
            }}
          />
        </label>
        <p className="helper">
          {provider === 'nst'
            ? 'Откройте NSTbrowser на этом компьютере. Ключ хранится зашифрованным.'
            : 'Возьмите токен в личном кабинете Dolphin Anty. Для запуска профиля войдите в настольное приложение Dolphin Anty. Запуск по API доступен на платных тарифах.'}
        </p>
        <div className="actions">
          <Button type="submit" variant="outline" disabled={Boolean(busy) || !token.trim() || !port}>
            <Download size={15} /> {busy === 'list' ? 'Загружаем…' : 'Загрузить список'}
          </Button>
        </div>
        {rows && (
          <>
            {rows.length ? (
              <div className="external-profile-list" role="group" aria-label="Доступные профили">
                {rows.map(row => (
                  <label key={row.id} className="external-profile-option">
                    <input
                      type="checkbox"
                      checked={linked.has(row.id) || chosen.includes(row.id)}
                      disabled={Boolean(busy) || linked.has(row.id)}
                      onChange={event => {
                        const checked = event.target.checked;
                        setChosen(ids => (checked ? [...ids, row.id] : ids.filter(id => id !== row.id)));
                      }}
                    />
                    <span>{row.name}</span>
                    {linked.has(row.id) && <span className="helper">Добавлен</span>}
                  </label>
                ))}
              </div>
            ) : (
              <p className="helper">В этом браузере пока нет профилей.</p>
            )}
            {cursor && (
              <div className="actions">
                <Button
                  type="button"
                  variant="outline"
                  disabled={Boolean(busy)}
                  onClick={() => void load(cursor)}
                >
                  Загрузить ещё
                </Button>
              </div>
            )}
          </>
        )}
        <ErrorToast message={error} />
        <div className="actions">
          <Button
            type="button"
            disabled={Boolean(busy) || !selected.length || selected.length > 500}
            onClick={() => void add()}
          >
            <Plus size={15} /> {busy === 'add' ? 'Добавляем…' : `Добавить профили (${selected.length})`}
          </Button>
          <Button type="button" variant="outline" disabled={Boolean(busy)} onClick={onClose}>
            Отмена
          </Button>
        </div>
      </form>
    </Modal>
  );
}
