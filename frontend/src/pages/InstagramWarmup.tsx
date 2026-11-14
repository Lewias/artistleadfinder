import { useState } from 'react';
import { Play, RefreshCw, Save, Square } from 'lucide-react';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import type { BrowserProfile } from '../services/types';
import { PageHeader } from '../components/PageHeader';
import { Button } from '../components/ui/button';
import { ErrorToast } from '../components/Toaster';
import { errorText } from '../lib/errors';
import './instagramWarmup.css';

interface Settings {
  queries: string[];
  message: string;
  messages: number;
  likes: number;
  follows: number;
  delay_seconds: number;
}
interface Run {
  targets: { messages: number; likes: number; follows: number };
  profile_id: string;
  status: string;
  stage: string;
  current: string;
  checked: number;
  sent: number;
  message_attempts: number;
  liked: number;
  followed: number;
  events: { at: number; text: string; username: string; outcome: string }[];
}
interface State {
  settings: Settings;
  profiles: BrowserProfile[];
  run?: Run;
}
const STATUS: Record<string, string> = {
  running: 'Идёт прогрев',
  done: 'Завершён',
  stopped: 'Остановлен',
  interrupted: 'Прерван перезапуском',
  failed: 'Остановлен из-за ошибки',
};
const STAGE: Record<string, string> = {
  open: 'Открываем аккаунт',
  search: 'Ищем магазины',
  qualify: 'Проверяем профиль',
  follow: 'Подписываемся',
  message: 'Отправляем сообщение',
  feed: 'Открываем ленту',
  like: 'Ставим лайки в ленте',
};

export function InstagramWarmup() {
  const resource = useResource<State>('instagram_warmup.state', {}, 3000);
  return (
    <div className="screen work-flow">
      <PageHeader
        page="instagram-warmup"
        actions={
          <Button variant="outline" icon aria-label="Обновить" onClick={resource.refresh}>
            <RefreshCw size={14} />
          </Button>
        }
      />
      <ErrorToast message={resource.error} />
      {resource.data ? (
        <WarmupForm data={resource.data} refresh={resource.refresh} />
      ) : (
        <p className="helper">Загружаем настройки прогрева…</p>
      )}
    </div>
  );
}

function WarmupForm({ data, refresh }: { data: State; refresh: () => void }) {
  const [draft, setDraft] = useState(data.settings);
  const [queries, setQueries] = useState(data.settings.queries.join('\n'));
  const [selected, setSelected] = useState(data.run?.profile_id ?? data.profiles[0]?.id ?? '');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const run = data.run;
  const running = run?.status === 'running';
  const validAccount = data.profiles.some(profile => profile.id === selected);
  const set = <K extends keyof Settings>(key: K, value: Settings[K]) => {
    setSaved(false);
    setDraft(current => ({ ...current, [key]: value }));
  };
  const act = async (action: 'save' | 'start' | 'stop') => {
    setError('');
    setPending(true);
    try {
      if (action !== 'stop') {
        await api.request('instagram_warmup.settings', {
          ...draft,
          queries: queries
            .split('\n')
            .map(value => value.trim())
            .filter(Boolean),
        });
        setSaved(true);
      }
      if (action === 'start') await api.request('instagram_warmup.start', { profile_id: selected });
      if (action === 'stop') await api.request('instagram_warmup.stop');
      refresh();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setPending(false);
    }
  };
  const numeric = (
    key: 'messages' | 'likes' | 'follows' | 'delay_seconds',
    label: string,
    min: number,
    max: number,
  ) => (
    <label>
      <span>{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        step={1}
        required
        value={draft[key]}
        onChange={event => set(key, Number(event.target.value))}
      />
    </label>
  );
  return (
    <>
      <section className="panel ig-warmup-panel">
        <div className="board-heading">
          <h2>Один запуск для выбранного аккаунта</h2>
          {saved && <span className="helper">Настройки сохранены</span>}
        </div>
        <form
          onSubmit={event => {
            event.preventDefault();
            void act('start');
          }}
        >
          <fieldset disabled={pending || running} className="ig-warmup-fields">
            <label>
              <span>Аккаунт Instagram</span>
              <select value={selected} onChange={event => setSelected(event.target.value)} required>
                <option value="" disabled>
                  Выберите аккаунт
                </option>
                {data.profiles.map(profile => (
                  <option key={profile.id} value={profile.id}>
                    {profile.name}
                    {profile.cookie_count ? '' : ' · нужно войти в Instagram'}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>Поиск магазинов — одна фраза на строку</span>
              <textarea
                value={queries}
                rows={4}
                required
                maxLength={1010}
                onChange={event => {
                  setQueries(event.target.value);
                  setSaved(false);
                }}
              />
            </label>
            <label>
              <span>Текст сообщения каждому магазину</span>
              <textarea
                value={draft.message}
                rows={4}
                maxLength={1000}
                required={draft.messages > 0}
                placeholder="Введите текст для отправки"
                onChange={event => set('message', event.target.value)}
              />
              <small className="helper">
                {draft.message.length} / 1000 · по одному сообщению разным магазинам
              </small>
            </label>
            <div className="ig-warmup-grid">
              {numeric('messages', 'Магазинов для сообщения', 0, 6)}
              {numeric('likes', 'Лайков в ленте', 0, 5)}
              {numeric('follows', 'Подписок на магазины', 0, 5)}
              {numeric('delay_seconds', 'Пауза между шагами, сек.', 10, 120)}
            </div>
          </fieldset>
          <ErrorToast message={error} />
          <div className="ig-warmup-actions">
            <Button
              type="button"
              variant="outline"
              disabled={pending || running}
              onClick={() => void act('save')}
            >
              <Save size={14} /> Сохранить настройки
            </Button>
            {running ? (
              <Button
                type="button"
                variant="danger"
                disabled={pending}
                onClick={event => {
                  event.preventDefault();
                  void act('stop');
                }}
              >
                <Square size={14} /> Остановить
              </Button>
            ) : (
              <Button type="submit" disabled={pending || !validAccount}>
                <Play size={14} /> {pending ? 'Запускаем…' : 'Запустить прогрев'}
              </Button>
            )}
          </div>
        </form>
        {!data.profiles.length && <p className="helper">Добавьте профиль в разделе «Аккаунты».</p>}
        <p className="helper">
          После запуска действия выполняются в окне выбранного аккаунта, пока ALF открыт. Завершите другие
          задачи этого аккаунта перед прогревом. При ограничении Instagram запуск остановится. Повторных
          сообщений прогрева тому же магазину с этого аккаунта нет.
        </p>
      </section>
      {run && (
        <section className="panel ig-warmup-panel" aria-live="polite">
          <div className="board-heading">
            <h2>{STATUS[run.status] ?? run.status}</h2>
            <span className="helper">
              {data.profiles.find(profile => profile.id === run.profile_id)?.name ?? 'Аккаунт'}
            </span>
          </div>
          {running && (
            <p>
              {STAGE[run.stage]}
              {run.current ? ` · @${run.current}` : ''}
            </p>
          )}
          <div className="cloud-counters">
            <div>
              <span>Сообщения</span>
              <b>
                {run.sent} / {run.targets.messages}
              </b>
            </div>
            <div>
              <span>Лайки</span>
              <b>
                {run.liked} / {run.targets.likes}
              </b>
            </div>
            <div>
              <span>Подписки</span>
              <b>
                {run.followed} / {run.targets.follows}
              </b>
            </div>
            <div>
              <span>Профилей проверено</span>
              <b>{run.checked}</b>
            </div>
          </div>
          <p className="helper">
            Показаны подтверждённые действия. Если подходящих магазинов или постов недостаточно, итог будет
            меньше цели. Уже открытые диалоги пропускаются.
          </p>
          <div className="crm-table ig-warmup-log">
            <table>
              <thead>
                <tr>
                  <th>Время</th>
                  <th>Профиль</th>
                  <th>Результат</th>
                </tr>
              </thead>
              <tbody>
                {[...run.events].reverse().map((event, index) => (
                  <tr key={`${event.at}-${index}`}>
                    <td>{new Date(event.at * 1000).toLocaleTimeString('ru-RU')}</td>
                    <td>{event.username ? `@${event.username}` : '—'}</td>
                    <td>{event.text}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </>
  );
}
