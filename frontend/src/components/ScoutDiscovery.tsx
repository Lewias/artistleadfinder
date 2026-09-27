import { useEffect, useState } from 'react';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import { Button } from './ui/button';
import { LeadDetail } from './LeadDetail';
import { parseScoutSources } from './scoutSources';
import { ScoutAccounts } from './ScoutAccounts';
import { ArrowUpRight, Radar, Radio, Save, SlidersHorizontal, Sparkles } from 'lucide-react';

type Evidence = { source: string; url: string; caption: string; published_at: string | null };
type ServiceScore = { score: number; reasons: { text: string; evidence: Evidence | null }[] };
type ScoutLead = {
  id: number;
  username: string;
  name: string;
  priority: number;
  contacts: string[];
  explanation: string;
  services: Record<string, ServiceScore>;
  evidence: Evidence[];
};
const labels: Record<string, string> = {
  beats: 'Биты',
  mixing: 'Сведение / мастеринг',
  promotion: 'Продвижение',
};

export function ScoutDiscovery() {
  const [sources, setSources] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [lead, setLead] = useState<number | null>(null);
  const results = useResource<ScoutLead[]>('scout.results', {}, 4000);
  useEffect(() => {
    void api
      .request<string[]>('scout.sources', {})
      .then(value => {
        setSources(value.join('\n'));
        setLoaded(true);
      })
      .catch(err => setError(String(err)));
  }, []);
  const sourceInput = parseScoutSources(sources);
  const sourceCount = sourceInput.values.length;
  const sourcesReady = loaded && sourceCount > 0 && !sourceInput.error;
  const run = async (operation: () => Promise<void>) => {
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await operation();
      results.refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };
  // Every account start saves the current list, so all accounts read the same sources.
  const saveSources = async () => {
    if (!sourcesReady) throw new Error(sourceInput.error || 'Добавьте хотя бы один источник.');
    return api.request<string[]>('scout.sources', { sources: sourceInput.values });
  };
  const openEvidence = (url: string) =>
    void run(async () => {
      await api.openProfile(url);
    });
  return (
    <section className="scout-workspace">
      <div className="scout-setup">
        <div className="panel source-panel">
          <div className="section-heading">
            <h2>
              <Radio size={20} /> Ваши источники
            </h2>
            <span className="tag">INSTAGRAM</span>
          </div>
          <p>Каким музыкальным медиа вы доверяете? Добавьте их аккаунты — отсюда начнётся поиск артистов.</p>
          <label>
            Instagram-источники
            <textarea
              rows={4}
              maxLength={5000}
              disabled={!loaded || busy}
              value={sources}
              aria-invalid={Boolean(sourceInput.error)}
              aria-describedby={sourceInput.error ? 'scout-source-error' : undefined}
              placeholder={'@music_media\nhttps://www.instagram.com/music_scout/'}
              onChange={event => {
                setSources(event.target.value);
                setError('');
              }}
            />
          </label>
          {sourceInput.error && (
            <p id="scout-source-error" role="alert" className="error-text">
              {sourceInput.error}
            </p>
          )}
          <div className="source-meta">
            <span>Один аккаунт на строку</span>
            <span className={sourceCount > 20 ? 'source-over-limit' : ''}>{sourceCount} / 20 источников</span>
          </div>
          <div className="actions">
            <Button
              variant="outline"
              disabled={busy || !sourcesReady}
              onClick={() =>
                void run(async () => {
                  const saved = await saveSources();
                  setSources(saved.join('\n'));
                  setMessage(`Источники сохранены: ${saved.length}.`);
                })
              }
            >
              <Save size={17} /> Сохранить источники
            </Button>
          </div>
          {message && (
            <p role="status" className="helper">
              {message}
            </p>
          )}
        </div>
        <aside className="scout-guide">
          <div className="guide-icon">
            <Sparkles size={24} />
          </div>
          <span className="tag">ОТ ИСТОЧНИКА К КОНТАКТУ</span>
          <h2>
            Вы задаёте направление.
            <br />
            Мы собираем кандидатов.
          </h2>
          <ol>
            <li>
              <span>01</span>
              <div>
                <strong>Читаем комментарии</strong>
                <p>Находим авторов комментариев под публикациями источников.</p>
              </div>
            </li>
            <li>
              <span>02</span>
              <div>
                <strong>Проверяем артистов</strong>
                <p>Изучаем профиль и музыкальный контекст.</p>
              </div>
            </li>
            <li>
              <span>03</span>
              <div>
                <strong>Объясняем предложение</strong>
                <p>Оцениваем соответствие каждой услуге.</p>
              </div>
            </li>
          </ol>
          <div className="service-tags">
            <span>Биты</span>
            <span>Сведение</span>
            <span>Продвижение</span>
          </div>
        </aside>
      </div>
      <ScoutAccounts saveSources={saveSources} disabled={!sourcesReady} onChange={results.refresh} />
      {(error || results.error) && (
        <p role="alert" className="error-text">
          {error || results.error}
        </p>
      )}
      <details className="scout-limits">
        <summary>
          <SlidersHorizontal size={15} /> Как работает оценка и что учитывает поиск
        </summary>
        <p className="helper">
          Аккаунт ищет, пока не наберёт свою цель подходящих лидов: сетка каждого источника прокручивается до
          120 публикаций, они разбираются пачками по 12. С одной публикации берётся не больше 30 новых
          кандидатов; уже проверенные авторы не открываются повторно, их комментарий добавляется к прежней
          оценке. Закреплённые записи могут влиять на порядок. Под каждой публикацией читаются до 200
          доступных комментариев с ограниченной подгрузкой. При повторном запуске комментарии читаются заново.
          Оценки показывают соответствие услуге, а не вероятность покупки. Проверяем открытый профиль каждого
          автора: биографию и признаки исполнителя. Закрытые профили, СМИ и магазины исключаются. Аудио и
          видео не анализируются. При запросе входа поиск приостанавливается. Отправки сообщений нет.
        </p>
      </details>
      <div className="section-heading results-heading">
        <div>
          <h2>
            Лиды для работы <span className="result-count">{results.data?.length ?? '—'}</span>
          </h2>
          <p className="helper">По приоритету предложения · Без отклонённых и уже получивших сообщение</p>
        </div>
      </div>
      {!results.data && !results.error && (
        <div className="notice" role="status">
          Загружаем лидов…
        </div>
      )}
      {results.data?.length === 0 && (
        <div className="scout-empty">
          <div className="empty-orbit">
            <Radar size={40} strokeWidth={1.3} />
          </div>
          <h3>Здесь начнутся новые знакомства</h3>
          <p>
            Добавьте источники и запустите поиск на одном из аккаунтов.
            <br />
            Здесь появятся подтверждённые артисты, оценки и контакты.
          </p>
          <span className="tag">БИТЫ · СВЕДЕНИЕ · ПРОДВИЖЕНИЕ</span>
        </div>
      )}
      {results.data?.map(item => (
        <article className="panel scout-lead" key={item.id}>
          <div className="lead-card-heading">
            <div className="avatar">{(item.name || item.username).slice(0, 2).toUpperCase()}</div>
            <div>
              <h3>{item.name || item.username}</h3>
              <span className="helper">@{item.username}</span>
            </div>
            <Button variant="outline" onClick={() => setLead(item.id)}>
              Карточка <ArrowUpRight size={15} />
            </Button>
          </div>
          <p className="helper">{item.explanation}</p>
          <div className="scout-scores">
            {Object.entries(item.services).map(([key, service]) => (
              <div key={key}>
                <strong>
                  {labels[key]}{' '}
                  <span>
                    {service.score}
                    <small>/100</small>
                  </span>
                </strong>
                <div className="service-meter">
                  <i style={{ width: `${Math.max(0, Math.min(100, service.score))}%` }} />
                </div>
                <details>
                  <summary>Обоснование оценки</summary>
                  {service.reasons.length ? (
                    service.reasons.map((reason, i) => (
                      <div key={i}>
                        <p>{reason.text}</p>
                        {reason.evidence && (
                          <>
                            <blockquote>{reason.evidence.caption}</blockquote>
                            <Button variant="outline" onClick={() => openEvidence(reason.evidence!.url)}>
                              Комментарий ·{' '}
                              {reason.evidence.published_at
                                ? new Date(reason.evidence.published_at).toLocaleDateString('ru-RU')
                                : 'Дата неизвестна'}
                            </Button>
                          </>
                        )}
                      </div>
                    ))
                  ) : (
                    <p className="helper">Нет подтверждающих сигналов.</p>
                  )}
                </details>
              </div>
            ))}
          </div>
          <details>
            <summary>Где найден артист</summary>
            {item.evidence.map((e, i) => (
              <p key={i}>
                {e.source}{' '}
                <Button variant="outline" onClick={() => openEvidence(e.url)}>
                  Открыть публикацию
                </Button>
              </p>
            ))}
          </details>
          <p>Контакт из биографии: {item.contacts.join(', ') || 'не указан'}</p>
        </article>
      ))}
      {lead !== null && <LeadDetail id={lead} close={() => setLead(null)} refresh={results.refresh} />}
    </section>
  );
}
