import { ErrorToast } from '../components/Toaster';
import { useState } from 'react';
import { FilterX, RefreshCw, Search, Send, SlidersHorizontal } from 'lucide-react';
import { useResource } from '../hooks/useResource';
import type { DashboardData, Lead, LeadQuery } from '../services/types';
import { PageHeader } from '../components/PageHeader';
import { defaultQuery } from '../services/leadQuery';
import { number, date, genres, sourceLabels, statusLabels } from '../lib/format';
import { DataState, StatusBadge } from '../components/DataState';
import { LeadDetail } from '../components/LeadDetail';
import { Button } from '../components/ui/button';
import { ExportBar } from '../components/ExportBar';
import { categoryLabels, methodLabel } from '../components/scoutStatus';

export function Leads({
  jobId,
  onCampaign,
}: {
  jobId?: number;
  onCampaign?: (ids: number[]) => Promise<void>;
}) {
  const [query, setQuery] = useState<LeadQuery>({ ...defaultQuery, ...(jobId ? { job_id: jobId } : {}) });
  const [selected, setSelected] = useState<number[]>([]);
  const [detail, setDetail] = useState<number>();
  const [outreachError, setOutreachError] = useState('');
  // Polled so that leads saved by a running Scout appear without a manual refresh.
  const resource = useResource<{ items: Lead[]; total: number }>('leads.list', query, 5000);
  const update = <K extends keyof LeadQuery>(key: K, value: LeadQuery[K]) =>
    setQuery(current => ({ ...current, page: 1, [key]: value }));
  const toggle = (id: number) =>
    setSelected(current => (current.includes(id) ? current.filter(value => value !== id) : [...current, id]));
  const items = resource.data?.items || [];
  const reset = () => {
    setQuery({ ...defaultQuery, ...(jobId ? { job_id: jobId } : {}) });
    setSelected([]);
  };
  const toolbar = (
    <>
      <Button variant="outline" onClick={resource.refresh}>
        <RefreshCw size={15} /> Обновить
      </Button>
      <Button variant="outline" onClick={reset}>
        <FilterX size={15} /> Сбросить
      </Button>
      <ExportBar query={query} selected={selected} />
      {onCampaign && (
        <Button
          disabled={!selected.length}
          onClick={() => {
            setOutreachError('');
            void onCampaign(selected).catch(err => setOutreachError(String(err)));
          }}
          title="Добавить выбранных в «Первичную рассылку»"
        >
          <Send size={15} /> В рассылку
        </Button>
      )}
      <ErrorToast message={outreachError} />
    </>
  );
  return (
    <>
      {jobId ? (
        <div className="actions embedded-toolbar">{toolbar}</div>
      ) : (
        <>
          <PageHeader page="leads" count={number(resource.data?.total || 0)} actions={toolbar} />
          <LeadStats />
        </>
      )}
      <section className="panel filter-bar">
        <label className="search-filter">
          <span className="input-with-icon">
            <Search size={16} aria-hidden="true" />
            <input
              aria-label="Поиск профиля"
              placeholder="Имя, username или биография"
              value={query.search}
              onChange={event => update('search', event.target.value)}
            />
          </span>
        </label>
        <select aria-label="Жанр" value={query.genre} onChange={event => update('genre', event.target.value)}>
          <option value="">Все жанры</option>
          {genres.map(genre => (
            <option key={genre}>{genre}</option>
          ))}
        </select>
        <select
          aria-label="Статус"
          value={query.status}
          onChange={event => update('status', event.target.value)}
        >
          <option value="">Все статусы</option>
          {['new', 'reviewed', 'qualified', 'rejected', 'contacted'].map(status => (
            <option key={status} value={status}>
              {statusLabels[status]}
            </option>
          ))}
        </select>
        <select
          aria-label="Источник"
          value={query.source}
          onChange={event => update('source', event.target.value)}
        >
          <option value="">Все источники</option>
          <option value="mock">Демо</option>
          <option value="imported">Импорт</option>
          <option value="instagram_scout">Скаут-источники</option>
          <option value="instagram_browser">Instagram из браузера</option>
        </select>
        <details className="advanced-filters">
          <summary>
            <SlidersHorizontal size={15} /> Ещё фильтры
          </summary>
          <div className="filter-grid">
            <label>
              Подписчики от
              <input
                type="number"
                min={0}
                value={query.min_followers}
                onChange={event => update('min_followers', Number(event.target.value))}
              />
            </label>
            <label>
              Подписчики до
              <input
                type="number"
                min={0}
                value={query.max_followers}
                onChange={event => update('max_followers', Number(event.target.value))}
              />
            </label>
            <label>
              Оценка от
              <input
                type="number"
                min={0}
                max={100}
                value={query.minimum_score}
                onChange={event => update('minimum_score', Number(event.target.value))}
              />
            </label>
            <label>
              Активность
              <select
                value={query.activity_days || ''}
                onChange={event =>
                  update('activity_days', event.target.value ? Number(event.target.value) : null)
                }
              >
                <option value="">Любая</option>
                {[7, 14, 30, 60, 90].map(days => (
                  <option key={days} value={days}>
                    {days} дней
                  </option>
                ))}
              </select>
            </label>
            <label>
              Сортировка
              <select value={query.sort} onChange={event => update('sort', event.target.value)}>
                <option value="score">Оценка</option>
                <option value="followers">Подписчики</option>
                <option value="activity">Активность</option>
                <option value="created">Дата открытия</option>
              </select>
            </label>
            <label>
              Порядок
              <select
                value={String(query.descending)}
                onChange={event => update('descending', event.target.value === 'true')}
              >
                <option value="true">По убыванию</option>
                <option value="false">По возрастанию</option>
              </select>
            </label>
          </div>
        </details>
        <span className="selection-count">Выбрано: {selected.length}</span>
      </section>
      <DataState {...resource} retry={resource.refresh} />
      {resource.data && (
        <>
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>
                    <input
                      aria-label="Выбрать страницу"
                      type="checkbox"
                      checked={items.length > 0 && items.every(lead => selected.includes(lead.id))}
                      onChange={event =>
                        setSelected(current =>
                          event.target.checked
                            ? Array.from(new Set([...current, ...items.map(lead => lead.id)]))
                            : current.filter(id => !items.some(lead => lead.id === id)),
                        )
                      }
                    />
                  </th>
                  <th>Instagram</th>
                  <th>Тип</th>
                  <th>Подписчики</th>
                  <th>Контакты</th>
                  <th>Источник</th>
                  <th>Оценка</th>
                  <th>Статус</th>
                  <th>Дата</th>
                </tr>
              </thead>
              <tbody>
                {items.map(lead => (
                  <tr key={lead.id}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Выбрать ${lead.username}`}
                        checked={selected.includes(lead.id)}
                        onChange={() => toggle(lead.id)}
                      />
                    </td>
                    <td>
                      <button className="profile-cell" onClick={() => setDetail(lead.id)}>
                        <span className="avatar">{lead.username.slice(0, 1).toUpperCase()}</span>
                        <span>
                          <strong>@{lead.username}</strong>
                          <small>{lead.display_name}</small>
                        </span>
                      </button>
                    </td>
                    <td>
                      {lead.scout_profile ? (
                        <span title={lead.primary_genre || undefined}>
                          {categoryLabels[lead.scout_profile.profile_type]}{' '}
                          <small>{lead.scout_profile.confidence}%</small>
                        </span>
                      ) : (
                        lead.primary_genre || '—'
                      )}
                    </td>
                    <td>
                      {lead.unknown_fields?.includes('followers') ? 'Нет данных' : number(lead.followers)}
                    </td>
                    <td className="contact-cell">
                      {lead.scout_profile?.emails[0] || lead.scout_profile?.phones[0] ? (
                        <>
                          {lead.scout_profile.emails[0] && (
                            <span title={lead.scout_profile.emails.join('\n')}>
                              {lead.scout_profile.emails[0]}
                            </span>
                          )}
                          {lead.scout_profile.phones[0] && <small>{lead.scout_profile.phones[0]}</small>}
                        </>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td>
                      <span
                        className="source-chip"
                        title={[
                          lead.scout_profile
                            ? `Найден через: ${methodLabel(lead.scout_profile.discovery_method)}`
                            : '',
                          ...lead.sources.map(source => `${source.source_type}: ${source.source_value}`),
                        ]
                          .filter(Boolean)
                          .join('\n')}
                      >
                        {lead.scout_profile
                          ? `@${lead.scout_profile.source_username}`
                          : [
                              ...new Set(
                                lead.sources.map(
                                  source => sourceLabels[source.source_provider] || source.source_provider,
                                ),
                              ),
                            ].join(', ')}
                      </span>
                    </td>
                    <td>
                      <span className="score-pill">{lead.lead_score}</span>
                    </td>
                    <td>
                      <StatusBadge value={lead.status} label={statusLabels[lead.status]} />
                      {lead.do_not_contact && <small className="cell-sub dnc-mark">Не связываться</small>}
                    </td>
                    <td>{date(lead.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {items.length === 0 && (
              <p className="empty-copy">
                Нет профилей с такими условиями. Измените фильтры или запустите поиск.
              </p>
            )}
          </div>
          <div className="pagination">
            <span>
              Страница {query.page} из {Math.max(1, Math.ceil(resource.data.total / query.page_size))}
            </span>
            <div className="actions">
              <Button
                variant="outline"
                disabled={query.page <= 1}
                onClick={() => setQuery(current => ({ ...current, page: current.page - 1 }))}
              >
                Назад
              </Button>
              <Button
                variant="outline"
                disabled={query.page * query.page_size >= resource.data.total}
                onClick={() => setQuery(current => ({ ...current, page: current.page + 1 }))}
              >
                Далее
              </Button>
            </div>
          </div>
        </>
      )}
      {detail && <LeadDetail id={detail} close={() => setDetail(undefined)} refresh={resource.refresh} />}
    </>
  );
}

function LeadStats() {
  const stats = useResource<DashboardData>('dashboard.get', {}, 15000);
  if (!stats.data) return null;
  return (
    <div className="stat-cards">
      {[
        { label: 'Всего', value: stats.data.total },
        { label: 'Подходят', value: stats.data.qualified },
        { label: 'Сегодня', value: stats.data.today },
      ].map(item => (
        <div className="stat-card" key={item.label}>
          <strong>{number(item.value)}</strong>
          <span>{item.label}</span>
        </div>
      ))}
    </div>
  );
}
