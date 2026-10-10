import { useEffect, useState } from 'react';
import { Send } from 'lucide-react';
import { api } from '../../services/api';
import { useResource } from '../../hooks/useResource';
import type { BrowserProfile, CloudJobDetail, CloudOutreachSources } from '../../services/types';
import { number } from '../../lib/format';
import { Button } from '../ui/button';
import { ErrorToast } from '../Toaster';
import { errorText } from '../../lib/errors';

type Source = 'found' | 'list';

/** «Рассылка» on the server: the app's messages and pace, sent from an account with a proxy. */
export function OutreachPanel({
  profiles,
  onStarted,
}: {
  profiles: BrowserProfile[];
  onStarted: (id: string) => void;
}) {
  const sources = useResource<CloudOutreachSources>('cloud.outreach_sources', {}, 30000);
  const writers = profiles.filter(item => item.cookie_count > 0 && item.proxy);
  const [profile, setProfile] = useState('');
  const [source, setSource] = useState<Source>('found');
  const [limit, setLimit] = useState(50);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!profile && writers.length) setProfile(writers[0].id);
  }, [profile, writers]);
  useEffect(() => {
    // Nothing found by the parser yet: the app's own list is the natural choice.
    if (sources.data && !sources.data.found && sources.data.list) setSource('list');
  }, [sources.data]);

  const waiting = sources.data ? sources.data[source] : 0;
  const messages = sources.data?.messages ?? 0;
  const start = async () => {
    setBusy(true);
    setError('');
    try {
      const detail = await api.request<CloudJobDetail>('cloud.outreach_start', {
        profile_id: profile,
        source,
        limit,
        request_id: crypto.randomUUID(),
      });
      onStarted(detail.job.id);
      sources.refresh();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel board cloud-form">
      <div className="board-heading">
        <h2>
          <Send size={16} /> Рассылка в облаке
        </h2>
        <span className="helper">Сообщения уходят с сервера: компьютер можно выключить</span>
      </div>
      <div className="cloud-grid">
        <fieldset>
          <legend>С какого аккаунта</legend>
          <div className="cloud-stack">
            <select value={profile} onChange={event => setProfile(event.target.value)}>
              {!writers.length && <option value="">Нет аккаунтов с прокси</option>}
              {writers.map(item => (
                <option key={item.id} value={item.id}>
                  {item.name} — {item.proxy?.host}:{item.proxy?.port}
                </option>
              ))}
            </select>
            <small className="helper">
              Только аккаунты с прокси: Instagram видит IP прокси, а не сервера. Пока идёт облачная рассылка,
              не пишите этим аккаунтом с компьютера. Если аккаунт занят облачным поиском, рассылка дождётся
              его конца.
            </small>
          </div>
        </fieldset>
        <fieldset>
          <legend>Кому</legend>
          <div className="cloud-stack">
            <label className="check-row">
              <input type="radio" checked={source === 'found'} onChange={() => setSource('found')} />
              Новым лидам облачного парсера ({number(sources.data?.found ?? 0)})
            </label>
            <label className="check-row">
              <input type="radio" checked={source === 'list'} onChange={() => setSource('list')} />
              По списку «Рассылки» ({number(sources.data?.list ?? 0)})
            </label>
            <label>
              <span>Скольким написать</span>
              <input
                type="number"
                min={1}
                max={500}
                value={limit}
                onChange={event => setLimit(Number(event.target.value))}
              />
            </label>
            <small className="helper">
              Сообщений: {number(messages)} — из «Парсер и рассылка». Паузы между сообщениями и дневной лимит
              — из настроек рассылки. Кому уже писали или с кем уже есть переписка, не пишем.
            </small>
          </div>
        </fieldset>
      </div>
      <ErrorToast message={error || sources.error || ''} />
      <div className="board-footer">
        <span className="helper">
          {waiting
            ? `Напишем ${number(Math.min(waiting, limit || 0))} из ${number(waiting)}`
            : 'Писать некому'}
        </span>
        <Button
          className="autopilot-go"
          disabled={busy || !profile || !waiting || !messages || limit < 1 || limit > 500}
          onClick={() => void start()}
        >
          <Send size={16} /> {busy ? 'Отправляю…' : 'Запустить рассылку на сервере'}
        </Button>
      </div>
    </section>
  );
}
