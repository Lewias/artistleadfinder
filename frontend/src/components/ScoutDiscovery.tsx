import { useEffect, useState } from 'react';
import { api } from '../services/api';
import type { BrowserProfile, CaptureQueue } from '../services/types';
import { useResource } from '../hooks/useResource';
import { Button } from './ui/button';
import { LeadDetail } from './LeadDetail';
import { parseScoutSources } from './scoutSources';
import { ArrowUpRight, Radar, Radio, ScanSearch, SlidersHorizontal, Sparkles } from 'lucide-react';

type Evidence = { source: string; url: string; caption: string; published_at: string | null };
type ServiceScore = { score: number; reasons: { text: string; evidence: Evidence | null }[] };
type ScoutLead = { id: number; username: string; name: string; priority: number; contacts: string[]; explanation: string; services: Record<string, ServiceScore>; evidence: Evidence[] };
const guest = '00000000000000000000000000000000';
const labels: Record<string, string> = { beats: 'Биты', mixing: 'Сведение / мастеринг', promotion: 'Продвижение' };

export function ScoutDiscovery() {
  const [sources, setSources] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [profile, setProfile] = useState(guest);
  const [profiles, setProfiles] = useState<BrowserProfile[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [lead, setLead] = useState<number | null>(null);
  const queue = useResource<CaptureQueue | null>('capture.latest', {}, 1500);
  const results = useResource<ScoutLead[]>('scout.results', {}, 4000);
  useEffect(() => {
    void api.request<string[]>('scout.sources', {}).then(value => { setSources(value.join('\n')); setLoaded(true); }).catch(err => setError(String(err)));
    void api.browser<BrowserProfile[]>('list').then(setProfiles).catch(err => setError(String(err)));
  }, []);
  const active = queue.data && ['running', 'paused'].includes(queue.data.status) && queue.data.stage !== 'interrupted';
  const completedWithoutCandidates = queue.data?.scout && queue.data.status === 'completed' && queue.data.candidates === 0;
  const sourceInput = parseScoutSources(sources);
  const sourceCount = sourceInput.values.length;
  const run = async (operation: () => Promise<void>) => {
    setBusy(true); setError('');
    try { await operation(); queue.refresh(); results.refresh(); } catch (err) { setError(String(err)); }
    finally { setBusy(false); }
  };
  const control = (action: string) => run(async () => { await api.request('jobs.control', { id: queue.data?.id, action }); });
  const openEvidence = (url: string) => void run(async () => { await api.openProfile(url); });
  return <section className="scout-workspace">
    <div className="scout-setup"><div className="panel source-panel">
    <div className="section-heading"><h2><Radio size={20} /> Ваши источники</h2><span className="tag">INSTAGRAM</span></div>
    <p>Каким музыкальным медиа вы доверяете? Добавьте их аккаунты — отсюда начнётся поиск артистов.</p>
    <label>Instagram-источники<textarea rows={4} maxLength={5000} disabled={!loaded || busy || Boolean(active)} value={sources} aria-invalid={Boolean(sourceInput.error)} aria-describedby={sourceInput.error ? 'scout-source-error' : undefined} placeholder={'@music_media\nhttps://www.instagram.com/music_scout/'} onChange={event => { setSources(event.target.value); setError(''); }} /></label>
    {sourceInput.error && <p id="scout-source-error" role="alert" className="error-text">{sourceInput.error}</p>}
    <div className="source-meta"><span>Один аккаунт на строку</span><span className={sourceCount > 20 ? 'source-over-limit' : ''}>{sourceCount} / 20 источников</span></div>
    <label>Сессия для чтения Instagram<select value={profile} disabled={busy || Boolean(active)} onChange={event => setProfile(event.target.value)}><option value={guest}>Без сохранённых cookies</option>{profiles.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
    <div className="actions"><Button disabled={!loaded || busy || Boolean(active) || sourceCount === 0 || Boolean(sourceInput.error)} onClick={() => void run(async () => {
      const values = sourceInput.values;
      await api.request('scout.sources', { sources: values });
      await api.browser('open', { id: profile });
      await api.browser('scout', { id: profile, sources: values });
    })}><ScanSearch size={17} /> Найти лидов</Button>
      <Button variant="outline" disabled={busy} onClick={() => void run(async () => { await api.browser('open', { id: profile }); })}>Открыть Instagram</Button>
    </div>
    </div><aside className="scout-guide">
      <div className="guide-icon"><Sparkles size={24} /></div><span className="tag">ОТ ИСТОЧНИКА К КОНТАКТУ</span>
      <h2>Вы задаёте направление.<br />Мы собираем кандидатов.</h2>
      <ol><li><span>01</span><div><strong>Читаем комментарии</strong><p>Находим авторов комментариев под публикациями источников.</p></div></li><li><span>02</span><div><strong>Проверяем артистов</strong><p>Изучаем профиль и музыкальный контекст.</p></div></li><li><span>03</span><div><strong>Объясняем предложение</strong><p>Оцениваем соответствие каждой услуге.</p></div></li></ol>
      <div className="service-tags"><span>Биты</span><span>Сведение</span><span>Продвижение</span></div>
    </aside></div>
    {queue.data?.scout && <div className="notice"><h3>{queue.data.stage === 'interrupted' ? 'Запуск прерван — запустите поиск снова' : ({ running: 'Поиск выполняется', paused: 'Требуется внимание', completed: 'Поиск завершён', cancelled: 'Поиск отменён' }[queue.data.status] || queue.data.status)}</h3>
      <p>Обработано шагов: {queue.data.cursor} / {queue.data.total} · Кандидатов: {queue.data.candidates ?? 0}</p>
      {active && <><p>{({ source: 'Читаем источник', post: 'Разбираем публикацию', profile: 'Проверяем исполнителя' }[queue.data.kind || ''] || '')}: {queue.data.url}</p>
        <div className="actions"><Button variant="outline" disabled={busy} onClick={() => void control(queue.data?.status === 'paused' ? 'resume' : 'pause')}>{queue.data.status === 'paused' ? 'Продолжить' : 'Пауза'}</Button>
          {queue.data.status === 'paused' && <Button variant="outline" disabled={busy} onClick={() => void run(async () => { await api.request('scout.skip', { id: queue.data?.id }); })}>Пропустить страницу</Button>}
          <Button variant="outline" disabled={busy} onClick={() => void control('cancel')}>Отменить</Button></div></>}
      {queue.data.error && <p role="alert" className="error-text">{queue.data.error}</p>}
      {!!queue.data.notices?.length && <details><summary>Пропуски и замечания ({queue.data.notices.length})</summary>{queue.data.notices.map((notice, i) => <p key={i}>{notice}</p>)}</details>}
    </div>}
    {active && !queue.data?.scout && <p className="helper">Сначала завершите текущую браузерную очередь в дополнительных инструментах.</p>}
    {(error || queue.error || results.error) && <p role="alert" className="error-text">{error || queue.error || results.error}</p>}
    <details className="scout-limits"><summary><SlidersHorizontal size={15} /> Как работает оценка и что учитывает поиск</summary><p className="helper">За запуск читаются до 12 доступных публикаций каждого источника и до 100 кандидатов. Закреплённые записи могут влиять на порядок. Под каждой публикацией читаются до 100 доступных комментариев с ограниченной подгрузкой. При повторном запуске комментарии читаются заново. Оценки показывают соответствие услуге, а не вероятность покупки. Проверяем открытый профиль каждого автора: биографию и признаки исполнителя. Закрытые профили, СМИ и магазины исключаются. Аудио и видео не анализируются. При запросе входа поиск приостанавливается. Отправки сообщений нет.</p></details>
    <div className="section-heading results-heading"><div><h2>Лиды для работы <span className="result-count">{results.data?.length ?? '—'}</span></h2><p className="helper">По приоритету предложения · Без отклонённых и уже получивших сообщение</p></div></div>
    {!results.data && !results.error && <div className="notice" role="status">Загружаем лидов…</div>}
    {results.data?.length === 0 && <div className="scout-empty"><div className="empty-orbit"><Radar size={40} strokeWidth={1.3} /></div><h3>{completedWithoutCandidates ? 'Поиск завершён без кандидатов' : 'Здесь начнутся новые знакомства'}</h3><p>{completedWithoutCandidates ? 'Среди авторов доступных комментариев не нашлось профилей с подтверждёнными признаками исполнителя. Проверьте уведомления поиска или попробуйте другой источник.' : <>Добавьте источники и запустите поиск.<br />Здесь появятся подтверждённые артисты, оценки и контакты.</>}</p><span className="tag">БИТЫ · СВЕДЕНИЕ · ПРОДВИЖЕНИЕ</span></div>}
    {results.data?.map(item => <article className="panel scout-lead" key={item.id}>
      <div className="lead-card-heading"><div className="avatar">{(item.name || item.username).slice(0, 2).toUpperCase()}</div><div><h3>{item.name || item.username}</h3><span className="helper">@{item.username}</span></div><Button variant="outline" onClick={() => setLead(item.id)}>Карточка <ArrowUpRight size={15} /></Button></div><p className="helper">{item.explanation}</p>
      <div className="scout-scores">{Object.entries(item.services).map(([key, service]) => <div key={key}><strong>{labels[key]} <span>{service.score}<small>/100</small></span></strong><div className="service-meter"><i style={{ width: `${Math.max(0, Math.min(100, service.score))}%` }} /></div><details><summary>Обоснование оценки</summary>
        {service.reasons.length ? service.reasons.map((reason, i) => <div key={i}><p>{reason.text}</p>{reason.evidence && <><blockquote>{reason.evidence.caption}</blockquote><Button variant="outline" onClick={() => openEvidence(reason.evidence!.url)}>Комментарий · {reason.evidence.published_at ? new Date(reason.evidence.published_at).toLocaleDateString('ru-RU') : 'Дата неизвестна'}</Button></>}</div>) : <p className="helper">Нет подтверждающих сигналов.</p>}
      </details></div>)}</div>
      <details><summary>Где найден артист</summary>{item.evidence.map((e, i) => <p key={i}>{e.source} <Button variant="outline" onClick={() => openEvidence(e.url)}>Открыть публикацию</Button></p>)}</details>
      <p>Контакт из биографии: {item.contacts.join(', ') || 'не указан'}</p>
    </article>)}
    {lead !== null && <LeadDetail id={lead} close={() => setLead(null)} refresh={results.refresh} />}
  </section>;
}
