import { useEffect, useState, type KeyboardEvent } from 'react';
import { ArrowUpRight, AtSign, CornerDownLeft, Layers, Radar, SlidersHorizontal, X } from 'lucide-react';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import { Button } from './ui/button';
import { LeadDetail } from './LeadDetail';
import { PageHeader } from './PageHeader';
import { plural } from '../lib/format';
import { parseScoutSources, sourceEntries, sourceHandle } from './scoutSources';
import { ScoutAccounts } from './ScoutAccounts';

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
const MAX_SOURCES = 20;

export function ScoutDiscovery() {
  const [sources, setSources] = useState<string[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [draft, setDraft] = useState('');
  const [bulk, setBulk] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [lead, setLead] = useState<number | null>(null);
  const results = useResource<ScoutLead[]>('scout.results', {}, 4000);
  useEffect(() => {
    void api
      .request<string[]>('scout.sources', {})
      .then(value => {
        setSources(value);
        setLoaded(true);
      })
      .catch(err => setError(String(err)));
  }, []);
  // Chips are saved at once; an empty list is kept locally because the core needs at least one.
  const store = async (next: string[]) => {
    const checked = parseScoutSources(next.join('\n'));
    if (checked.error) {
      setError(checked.error);
      return false;
    }
    setError('');
    if (!next.length) {
      setSources([]);
      return true;
    }
    setSaving(true);
    try {
      setSources(await api.request<string[]>('scout.sources', { sources: next }));
      return true;
    } catch (err) {
      setError(String(err));
      return false;
    } finally {
      setSaving(false);
    }
  };
  const add = async (text: string) => {
    const entries = sourceEntries(text);
    if (!entries.length) return false;
    const known = new Set(sources.map(url => sourceHandle(url).toLowerCase()));
    const fresh = entries.filter(entry => !known.has(sourceHandle(entry).toLowerCase()));
    return store([...sources, ...fresh]);
  };
  const onKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    void add(draft).then(ok => ok && setDraft(''));
  };
  // Every account start saves the current list, so all accounts read the same sources.
  const saveSources = async () => {
    if (!sources.length) throw new Error('Добавьте хотя бы один источник.');
    return api.request<string[]>('scout.sources', { sources });
  };
  const openEvidence = (url: string) => void api.openProfile(url).catch(err => setError(String(err)));
  const full = sources.length >= MAX_SOURCES;
  return (
    <section className="scout-workspace">
      <PageHeader page="discovery" count={plural(sources.length, ['источник', 'источника', 'источников'])} />
      <div className="panel source-board">
        <div className="chip-cloud" aria-label="Источники">
          {sources.map(url => (
            <span className="chip" key={url}>
              {sourceHandle(url)}
              <button
                aria-label={`Удалить ${sourceHandle(url)}`}
                disabled={saving}
                onClick={() => void store(sources.filter(item => item !== url))}
              >
                <X size={14} />
              </button>
            </span>
          ))}
          {loaded && !sources.length && (
            <p className="helper">Источников пока нет. Добавьте аккаунты музыкальных пабликов ниже.</p>
          )}
        </div>
        {bulk === null ? (
          <label className="chip-input">
            <AtSign size={17} aria-hidden="true" />
            <input
              aria-label="Новый источник"
              placeholder={full ? 'Достигнут предел в 20 источников' : 'Добавьте источники и нажмите Enter…'}
              value={draft}
              disabled={!loaded || saving || full}
              onChange={event => setDraft(event.target.value)}
              onKeyDown={onKey}
            />
            <kbd aria-hidden="true">
              <CornerDownLeft size={13} />
            </kbd>
          </label>
        ) : (
          <div className="bulk-input">
            <textarea
              rows={4}
              aria-label="Список источников"
              placeholder={'rapgoat.tv\n@topdailyrap\nhttps://www.instagram.com/rapczn/'}
              value={bulk}
              onChange={event => setBulk(event.target.value)}
            />
            <div className="actions">
              <Button
                disabled={saving || !bulk.trim()}
                onClick={() => void add(bulk).then(ok => ok && setBulk(null))}
              >
                Добавить
              </Button>
              <Button variant="outline" onClick={() => setBulk(null)}>
                Отмена
              </Button>
            </div>
          </div>
        )}
        <div className="board-footer">
          <div className="actions">
            <Button variant="outline" disabled={bulk !== null || full} onClick={() => setBulk('')}>
              <Layers size={16} /> Массовый
            </Button>
          </div>
          <span className="board-status">
            {saving ? 'Сохраняем…' : `${sources.length} / ${MAX_SOURCES} источников · сохранено`}
          </span>
        </div>
        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
      </div>
      <ScoutAccounts
        saveSources={saveSources}
        disabled={!loaded || !sources.length}
        onChange={results.refresh}
      />
      {results.error && (
        <p role="alert" className="error-text">
          {results.error}
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
          оценке. Под каждой публикацией читаются до 200 доступных комментариев. Оценки показывают
          соответствие услуге, а не вероятность покупки. Закрытые профили, СМИ и магазины исключаются. При
          запросе входа поиск приостанавливается. Отправки сообщений нет.
        </p>
      </details>
      <div className="section-title">
        <h2>
          Лиды для работы <span className="page-count">{results.data?.length ?? '—'}</span>
        </h2>
        <p className="helper">По приоритету предложения · без отклонённых и уже получивших сообщение</p>
      </div>
      {!results.data && !results.error && (
        <div className="notice" role="status">
          Загружаем лидов…
        </div>
      )}
      {results.data?.length === 0 && (
        <div className="scout-empty">
          <div className="empty-orbit">
            <Radar size={36} strokeWidth={1.4} />
          </div>
          <h3>Здесь начнутся новые знакомства</h3>
          <p>Добавьте источники и запустите поиск на одном из аккаунтов.</p>
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
