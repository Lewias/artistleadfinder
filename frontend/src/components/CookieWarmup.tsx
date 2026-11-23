import { useState } from 'react';
import { Cookie, Play, Square } from 'lucide-react';
import { useResource } from '../hooks/useResource';
import { api } from '../services/api';
import type { BrowserProfile } from '../services/types';
import { errorText } from '../lib/errors';
import { ErrorToast } from './Toaster';
import { Modal } from './Modal';
import { Button } from './ui/button';

interface Settings {
  minutes: number;
  sites: string[];
}
interface State {
  settings: Settings;
  profiles: BrowserProfile[];
  run?: {
    profile_id: string;
    profile_name: string;
    status: string;
    started_at: number;
    completed_at: number | null;
    current: string;
    visited: number;
    events: { at: number; text: string }[];
  };
}
const statuses: Record<string, string> = {
  running: 'Идёт прогрев cookies',
  stopping: 'Останавливаем прогрев',
  done: 'Прогрев завершён',
  stopped: 'Прогрев остановлен',
  interrupted: 'Прерван перезапуском',
  failed: 'Остановлен из-за ошибки',
};
const active = (status?: string) => status === 'running' || status === 'stopping';

export function CookieWarmupButton() {
  const [open, setOpen] = useState(false);
  const resource = useResource<State>('cookie_warmup.state', {}, 3000);
  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)}>
        <Cookie size={14} />
        {active(resource.data?.run?.status) ? 'Cookies · идёт прогрев' : 'Прогрев cookies'}
      </Button>
      {open && (
        <Modal title="Прогрев cookies" onClose={() => setOpen(false)}>
          <ErrorToast message={resource.error} />
          {resource.data ? (
            <CookieWarmupForm data={resource.data} refresh={resource.refresh} />
          ) : (
            <p className="helper">Загружаем профили NSTbrowser…</p>
          )}
        </Modal>
      )}
    </>
  );
}

function CookieWarmupForm({ data, refresh }: { data: State; refresh: () => void }) {
  const [profile, setProfile] = useState(data.run?.profile_id ?? data.profiles[0]?.id ?? '');
  const [minutes, setMinutes] = useState(data.settings.minutes);
  const [sites, setSites] = useState(data.settings.sites.join('\n'));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const run = data.run;
  const running = active(run?.status);
  const start = async () => {
    setPending(true);
    setError('');
    try {
      await api.request('cookie_warmup.start', {
        profile_id: profile,
        settings: {
          minutes,
          sites: sites
            .split('\n')
            .map(value => value.trim())
            .filter(Boolean),
        },
      });
      refresh();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setPending(false);
    }
  };
  const stop = async () => {
    setPending(true);
    setError('');
    try {
      await api.request('cookie_warmup.stop');
      refresh();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setPending(false);
    }
  };
  return (
    <form
      className="cookie-warmup-form"
      onSubmit={event => {
        event.preventDefault();
        void start();
      }}
    >
      <fieldset className="ig-warmup-fields" disabled={pending || running}>
        <label>
          <span>Профиль NSTbrowser</span>
          <select value={profile} required onChange={event => setProfile(event.target.value)}>
            <option value="" disabled>
              Выберите профиль
            </option>
            {data.profiles.map(item => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>Время, минут</span>
          <input
            type="number"
            min={1}
            max={120}
            step={1}
            required
            value={minutes}
            onChange={event => setMinutes(Number(event.target.value))}
          />
        </label>
        <label>
          <span>Сайты — один адрес с https:// на строку</span>
          <textarea rows={5} required value={sites} onChange={event => setSites(event.target.value)} />
        </label>
      </fieldset>
      {!data.profiles.length && (
        <p className="helper">Добавьте профиль NSTbrowser в «Аккаунты» через «NST / Dolphin».</p>
      )}
      <p className="helper">Откройте NSTbrowser. Прогрев посещает сайты через Google и принимает cookies.</p>
      <ErrorToast message={error} />
      <div className="ig-warmup-actions">
        {running ? (
          <Button
            type="button"
            variant="danger"
            disabled={pending || run?.status === 'stopping'}
            onClick={() => void stop()}
          >
            <Square size={14} /> {run?.status === 'stopping' ? 'Останавливаем…' : 'Остановить'}
          </Button>
        ) : (
          <Button type="submit" disabled={pending || !data.profiles.some(item => item.id === profile)}>
            <Play size={14} /> {pending ? 'Запускаем…' : 'Запустить'}
          </Button>
        )}
      </div>
      {run && (
        <section aria-live="polite">
          <div className="board-heading">
            <b>{statuses[run.status] ?? run.status}</b>
            <span className="helper">
              {run.profile_name} · просмотров материалов {run.visited}
            </span>
          </div>
          {running && run.current && <p className="helper">{run.current}</p>}
          <div className="crm-table ig-warmup-log cookie-warmup-log">
            <table>
              <thead>
                <tr>
                  <th>Время</th>
                  <th>Действие</th>
                </tr>
              </thead>
              <tbody>
                {[...run.events].reverse().map((event, index) => (
                  <tr key={`${event.at}-${index}`}>
                    <td>{new Date(event.at * 1000).toLocaleTimeString('ru-RU')}</td>
                    <td>{event.text}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </form>
  );
}
