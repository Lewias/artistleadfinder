import { ErrorToast } from './Toaster';
import { errorText } from '../lib/errors';
import { useEffect, useState } from 'react';
import { api } from '../services/api';
import type { BrowserProfile, CaptureQueue } from '../services/types';
import { useResource } from '../hooks/useResource';
import { Button } from './ui/button';
import { LeadDetail } from './LeadDetail';

const guest = '00000000000000000000000000000000';

export function BrowserDiscovery() {
  const [profiles, setProfiles] = useState<BrowserProfile[]>([]);
  const [profile, setProfile] = useState(guest);
  const [links, setLinks] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [lead, setLead] = useState<number | null>(null);
  const queue = useResource<CaptureQueue | null>('capture.latest', {}, 1000);
  useEffect(() => {
    void api
      .browser<BrowserProfile[]>('list')
      .then(setProfiles)
      .catch(err => setError(errorText(err)));
  }, []);
  const active =
    queue.data && ['running', 'paused'].includes(queue.data.status) && queue.data.stage !== 'interrupted';
  const run = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await operation();
      queue.refresh();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  const control = (action: string) =>
    run(async () => {
      await api.request('jobs.control', { id: queue.data?.id, action });
    });
  return (
    <section className="panel provider-panel">
      <h2>Реальные артисты из браузера</h2>
      <p className="helper">
        Откройте Instagram, найдите артиста и перейдите на его профиль. Добавление прочитает доступные
        сведения, рассчитает оценку и сохранит профиль в базе. Cookies необязательны.
      </p>
      <label>
        Браузерная сессия
        <select
          value={profile}
          disabled={busy || Boolean(active)}
          onChange={event => setProfile(event.target.value)}
        >
          <option value={guest}>Без сохранённых cookies</option>
          {profiles.map(item => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      <div className="actions">
        <Button
          disabled={busy}
          onClick={() =>
            void run(async () => {
              await api.browser('open', { id: profile });
              setMessage('В открытом окне найдите артиста. При необходимости войдите вручную.');
            })
          }
        >
          Открыть Instagram
        </Button>
        <Button
          variant="outline"
          disabled={busy || Boolean(active)}
          onClick={() =>
            void run(async () => {
              const result = await api.browser<{ lead_id: number; score: number }>('capture', {
                id: profile,
              });
              setLead(result.lead_id);
              setMessage(`Профиль сохранён. Оценка: ${result.score}/100.`);
            })
          }
        >
          Добавить открытого артиста
        </Button>
      </div>
      <label>
        Очередь ссылок или @имён
        <textarea
          rows={4}
          maxLength={25000}
          value={links}
          disabled={busy || Boolean(active)}
          placeholder={'https://www.instagram.com/artist_name/\n@another_artist'}
          onChange={event => setLinks(event.target.value)}
        />
      </label>
      <p className="helper">
        До 100 профилей, по одному на строку. Повторы убираются. Очередь использует выбранное окно — не
        переключайте в нём страницы во время обработки. Критерии оценки берутся из настроек.
      </p>
      <Button
        disabled={busy || Boolean(active) || !links.trim()}
        onClick={() =>
          void run(async () => {
            await api.browser('queue', {
              id: profile,
              urls: links
                .split(/[\n,]/)
                .map(value => value.trim())
                .filter(Boolean),
            });
            setMessage('Очередь запущена. Результаты появятся в базе и истории поисков.');
          })
        }
      >
        Обработать ссылки
      </Button>
      {queue.data && (
        <div className="panel">
          <h3>
            Обработка ссылок · {queue.data.cursor} / {queue.data.total}
          </h3>
          <progress value={queue.data.cursor} max={queue.data.total} />
          <p className="helper">
            {queue.data.stage === 'interrupted'
              ? 'Прервано при закрытии приложения. Создайте новую очередь; сохранённые профили останутся в базе.'
              : {
                  running: 'Обрабатываем',
                  paused: 'Приостановлено',
                  completed: 'Завершено',
                  cancelled: 'Остановлено',
                }[queue.data.status] || queue.data.status}
          </p>
          {active && (
            <>
              <p className="helper link-wrap">{queue.data.url}</p>
              <div className="actions">
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => void control(queue.data?.status === 'paused' ? 'resume' : 'pause')}
                >
                  {queue.data.status === 'paused' ? 'Продолжить' : 'Пауза'}
                </Button>
                <Button variant="outline" disabled={busy} onClick={() => void control('cancel')}>
                  Отменить очередь
                </Button>
              </div>
            </>
          )}
          {queue.data.error && (
            <p role="alert" className="error-text">
              {queue.data.error}
            </p>
          )}
        </div>
      )}
      {message && (
        <p role="status" className="notice">
          {message}
        </p>
      )}
      <ErrorToast message={error} />
      {queue.error && (
        <p role="alert" className="error-text">
          {queue.error}
        </p>
      )}
      <p className="helper">
        Закрытые или недоступные сведения не восстанавливаются догадками. Дата последней публикации пока не
        извлекается; это отражается в оценке и карточке. При запросе входа или ограничении сайта очередь
        приостанавливается.
      </p>
      {lead !== null && <LeadDetail id={lead} close={() => setLead(null)} refresh={() => {}} />}
    </section>
  );
}
