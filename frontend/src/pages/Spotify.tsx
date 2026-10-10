import { useEffect, useState } from 'react';
import { BookUser, ExternalLink, Play, RefreshCw, Save, Send, Square } from 'lucide-react';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import type { SpotifyArtist, SpotifySettings, SpotifyState, SpotifyView } from '../services/types';
import { number, relativeTime } from '../lib/format';
import { PageHeader } from '../components/PageHeader';
import { Button } from '../components/ui/button';
import { StatusBadge } from '../components/DataState';
import { ErrorToast } from '../components/Toaster';
import { errorText } from '../lib/errors';

const VIEWS: Record<SpotifyView, string> = {
  new: 'Новые',
  found: 'Все найденные',
  skipped: 'Отсеянные',
};
const RUN_LABELS: Record<string, [string, string]> = {
  running: ['running', 'Ищет'],
  done: ['completed', 'Готово'],
  stopped: ['cancelled', 'Остановлен'],
  failed: ['failed', 'Ошибка'],
  interrupted: ['cancelled', 'Прерван'],
};
const PAGE = 200;

const lines = (text: string) =>
  text
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean);

/** The search settings as the form edits them: lists as text, so typing is not reformatted. */
interface Draft extends Omit<SpotifySettings, 'queries' | 'seeds'> {
  queries: string;
  seeds: string;
}
const draftOf = (settings: SpotifySettings): Draft => ({
  ...settings,
  queries: settings.queries.join('\n'),
  seeds: settings.seeds.join('\n'),
});

function SettingsForm({ settings, onSaved }: { settings: SpotifySettings; onSaved: () => void }) {
  const [draft, setDraft] = useState<Draft>(() => draftOf(settings));
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setSaved(false);
    setDraft(current => ({ ...current, [key]: value }));
  };
  const save = async () => {
    setError('');
    try {
      await api.request('spotify.settings', {
        ...draft,
        queries: lines(draft.queries),
        seeds: lines(draft.seeds),
      });
      setSaved(true);
      onSaved();
    } catch (err) {
      setError(errorText(err));
    }
  };
  const numberInput = (key: 'min_listeners' | 'max_listeners' | 'release_days', label: string) => (
    <label>
      <span>{label}</span>
      <input
        type="number"
        min={0}
        value={draft[key]}
        onChange={event => set(key, Number(event.target.value))}
      />
    </label>
  );
  return (
    <section className="panel board cloud-form">
      <div className="board-heading">
        <h2>Настройки поиска</h2>
        <span className="helper">Берутся при следующем запуске</span>
      </div>
      <div className="cloud-grid">
        <fieldset>
          <legend>Кого искать</legend>
          <div className="cloud-stack">
            {numberInput('min_listeners', 'Слушателей в месяц — от')}
            {numberInput('max_listeners', 'Слушателей в месяц — до')}
            {numberInput('release_days', 'Последний релиз не старше, дней (0 — неважно)')}
            <small className="helper">Всегда только те, у кого в профиле Spotify указан Instagram.</small>
          </div>
        </fieldset>
        <fieldset>
          <legend>Где искать</legend>
          <div className="cloud-stack">
            <label className="check-row">
              <input
                type="checkbox"
                checked={draft.use_playlists}
                onChange={event => set('use_playlists', event.target.checked)}
              />
              <span>Плейлисты: приложение само находит их по фразам ниже</span>
            </label>
            <label className="check-row">
              <input
                type="checkbox"
                checked={draft.use_search}
                onChange={event => set('use_search', event.target.checked)}
              />
              <span>Поиск артистов по тем же фразам</span>
            </label>
            <label className="check-row">
              <input
                type="checkbox"
                checked={draft.use_related}
                onChange={event => set('use_related', event.target.checked)}
              />
              <span>«Поклонникам также нравятся» у небольших артистов — так находится больше всего</span>
            </label>
          </div>
        </fieldset>
      </div>
      <div className="cloud-grid">
        <label className="cloud-field">
          <span>Фразы для поиска плейлистов и артистов — по одной на строку</span>
          <textarea
            className="board-bulk"
            rows={6}
            value={draft.queries}
            onChange={event => set('queries', event.target.value)}
          />
          <small>{lines(draft.queries).length} из 40</small>
        </label>
        <label className="cloud-field">
          <span>Свои ссылки на артистов и плейлисты Spotify — необязательно</span>
          <textarea
            className="board-bulk"
            rows={6}
            placeholder="https://open.spotify.com/playlist/…"
            value={draft.seeds}
            onChange={event => set('seeds', event.target.value)}
          />
          <small>{lines(draft.seeds).length} из 200</small>
        </label>
      </div>
      <ErrorToast message={error} />
      <div className="board-footer">
        {saved && <span className="helper">Сохранено</span>}
        <Button onClick={() => void save()}>
          <Save size={15} /> Сохранить настройки
        </Button>
      </div>
    </section>
  );
}

function ArtistRow({
  artist,
  chosen,
  onChoose,
  onOpen,
}: {
  artist: SpotifyArtist;
  chosen: boolean;
  onChoose: (value: boolean) => void;
  onOpen: (url: string) => void;
}) {
  const found = artist.status === 'found';
  return (
    <tr className={chosen ? 'selected' : undefined}>
      <td>
        {found && (
          <input
            type="checkbox"
            aria-label={`Выбрать ${artist.name}`}
            checked={chosen}
            onChange={event => onChoose(event.target.checked)}
          />
        )}
      </td>
      <td>
        <button type="button" className="cloud-user" onClick={() => onOpen(artist.url)}>
          {artist.name} <ExternalLink size={12} />
        </button>
        <small>{artist.source}</small>
      </td>
      <td>
        {artist.instagram ? (
          <button
            type="button"
            className="cloud-user"
            onClick={() => onOpen(`https://www.instagram.com/${artist.instagram}/`)}
          >
            @{artist.instagram} <ExternalLink size={12} />
          </button>
        ) : (
          '—'
        )}
      </td>
      <td>{number(artist.listeners)}</td>
      <td>
        {artist.release_name || '—'}
        {artist.release_date && <small>{new Date(artist.release_date).toLocaleDateString('ru-RU')}</small>}
      </td>
      <td>{[artist.city, artist.country].filter(Boolean).join(', ') || '—'}</td>
      <td>
        {found ? (
          <>
            {artist.outreach_at ? 'В рассылке' : 'Новый'}
            {artist.crm_at && <small>в CRM</small>}
          </>
        ) : (
          artist.status_text
        )}
      </td>
    </tr>
  );
}

/** «Spotify»: small artists with an Instagram, found in the background, then written to. */
export function Spotify({ onWrite }: { onWrite: () => void }) {
  const [poll, setPoll] = useState(15000);
  const state = useResource<SpotifyState>('spotify.state', {}, poll);
  const [view, setView] = useState<SpotifyView>('new');
  const [limit, setLimit] = useState(PAGE);
  const results = useResource<{ rows: SpotifyArtist[]; total: number }>(
    'spotify.results',
    { view, limit },
    poll,
  );
  const [target, setTarget] = useState<number>();
  const [chosen, setChosen] = useState<string[]>([]);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const data = state.data;
  const running = !!data?.running;

  useEffect(() => setPoll(running ? 2500 : 15000), [running]);
  useEffect(() => {
    if (data && target === undefined) setTarget(data.settings.target);
  }, [data, target]);
  useEffect(() => {
    const ids = new Set(results.data?.rows.map(row => row.id));
    setChosen(list => list.filter(id => ids.has(id)));
  }, [results.data]);

  const refresh = () => {
    state.refresh();
    results.refresh();
  };
  const call = async <T,>(method: string, params: object = {}) => {
    setError('');
    setNotice('');
    try {
      const result = await api.request<T>(method, params);
      refresh();
      return result;
    } catch (err) {
      setError(errorText(err));
      return undefined;
    }
  };
  const write = async (params: object) => {
    const result = await call<{ added: number; count: number }>('spotify.to_outreach', params);
    if (result) {
      setChosen([]);
      onWrite();
    }
  };
  const toCrm = async () => {
    const result = await call<{ added: number; merged: number }>('spotify.to_crm', { ids: chosen });
    if (result) {
      setNotice(`В CRM: новых ${number(result.added)}, дополнено ${number(result.merged)}.`);
      setChosen([]);
    }
  };
  const open = (url: string) => api.openProfile(url).catch(err => setError(errorText(err)));

  if (!data) return <div className="screen">{state.error || 'Загрузка…'}</div>;
  const run = data.run;
  const rows = results.data?.rows ?? [];
  const selectable = rows.filter(row => row.status === 'found');
  const label = run ? RUN_LABELS[run.status] : undefined;

  return (
    <div className="screen work-flow">
      <PageHeader page="spotify" count={`${number(data.totals.new)} новых`} />
      <section className="panel board cloud-form">
        <div className="board-heading">
          <h2>Поиск {label && <StatusBadge value={label[0]} label={label[1]} />}</h2>
          <span className="helper">
            Работает на этом компьютере в фоне, без аккаунта Spotify. Уже проверенных артистов не проверяет
            повторно.
          </span>
        </div>
        <div className="cloud-counters">
          <div>
            <span>Найдено всего</span>
            <b>{number(data.totals.found)}</b>
          </div>
          <div>
            <span>Ещё не в рассылке</span>
            <b>{number(data.totals.new)}</b>
          </div>
          <div>
            <span>Проверено артистов</span>
            <b>{number(data.totals.checked)}</b>
          </div>
        </div>
        {run && (
          <p className={run.status === 'failed' ? 'board-warning' : 'autopilot-status'}>
            {run.status === 'running'
              ? `${run.step || 'Работаю'} · проверено ${number(run.checked)} · найдено ${number(run.found)} из ${number(run.target)}`
              : `${run.message || 'Поиск завершён.'} ${relativeTime(run.finished_at || run.started_at, '')}`}
          </p>
        )}
        <ErrorToast message={error || state.error} />
        <div className="board-footer">
          <label className="spotify-target">
            <span>Найти артистов с Instagram</span>
            <input
              type="number"
              min={1}
              max={1000}
              value={target ?? ''}
              disabled={running}
              onChange={event => setTarget(Number(event.target.value))}
            />
          </label>
          {running ? (
            <Button variant="outline" onClick={() => void call('spotify.stop')}>
              <Square size={15} /> Остановить
            </Button>
          ) : (
            <Button
              className="autopilot-go"
              disabled={!target || target < 1}
              onClick={() => void call('spotify.start', { target })}
            >
              <Play size={16} /> Начать поиск
            </Button>
          )}
        </div>
      </section>

      <section className="panel cloud-detail">
        <div className="board-heading">
          <h2>Артисты</h2>
          <Button variant="outline" icon aria-label="Обновить" onClick={refresh}>
            <RefreshCw size={14} />
          </Button>
        </div>
        <div className="crm-tabs" role="tablist" aria-label="Артисты">
          {(Object.keys(VIEWS) as SpotifyView[]).map(key => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={view === key}
              className={view === key ? 'active' : ''}
              onClick={() => {
                setView(key);
                setLimit(PAGE);
                setChosen([]);
              }}
            >
              {VIEWS[key]}
            </button>
          ))}
        </div>
        {view !== 'skipped' && (
          <div className="board-footer spotify-actions">
            <Button variant="outline" disabled={!chosen.length} onClick={() => void toCrm()}>
              <BookUser size={15} /> В CRM ({chosen.length})
            </Button>
            <Button variant="outline" disabled={!chosen.length} onClick={() => void write({ ids: chosen })}>
              <Send size={15} /> Написать выбранным ({chosen.length})
            </Button>
            <Button disabled={!data.totals.new} onClick={() => void write({ all_new: true })}>
              <Send size={15} /> Написать всем новым ({number(data.totals.new)})
            </Button>
          </div>
        )}
        {notice && <p className="helper">{notice}</p>}
        <p className="helper">
          «Написать» добавляет их Instagram в список «Рассылки»: сообщения и аккаунт выбираете там, повторно
          никому не пишем.
        </p>
        <ErrorToast message={results.error} />
        {results.data && !rows.length && (
          <p className="empty-copy">
            {view === 'new'
              ? 'Новых артистов пока нет — запустите поиск.'
              : view === 'found'
                ? 'Пока никого не нашли.'
                : 'Отсеянных пока нет.'}
          </p>
        )}
        {rows.length > 0 && (
          <div className="crm-table cloud-table">
            <table>
              <thead>
                <tr>
                  <th>
                    {selectable.length > 0 && (
                      <input
                        type="checkbox"
                        aria-label="Выбрать всех"
                        checked={selectable.every(row => chosen.includes(row.id))}
                        onChange={event =>
                          setChosen(event.target.checked ? selectable.map(row => row.id) : [])
                        }
                      />
                    )}
                  </th>
                  <th>Артист</th>
                  <th>Instagram</th>
                  <th>Слушатели</th>
                  <th>Последний релиз</th>
                  <th>Где слушают</th>
                  <th>{view === 'skipped' ? 'Почему отсеян' : 'Статус'}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(artist => (
                  <ArtistRow
                    key={artist.id}
                    artist={artist}
                    chosen={chosen.includes(artist.id)}
                    onChoose={value =>
                      setChosen(list => (value ? [...list, artist.id] : list.filter(id => id !== artist.id)))
                    }
                    onOpen={url => void open(url)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {results.data && results.data.total > rows.length && (
          <Button variant="outline" onClick={() => setLimit(value => value + PAGE)}>
            Показать ещё ({number(results.data.total - rows.length)})
          </Button>
        )}
      </section>

      {/* Saved settings remount the form; the target alone changes with every start. */}
      <SettingsForm
        key={JSON.stringify({ ...data.settings, target: 0 })}
        settings={data.settings}
        onSaved={state.refresh}
      />
    </div>
  );
}
