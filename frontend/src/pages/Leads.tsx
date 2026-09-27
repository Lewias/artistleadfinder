import { useState } from 'react';
import { SlidersHorizontal, Search } from 'lucide-react';
import { useResource } from '../hooks/useResource';
import type { Lead, LeadQuery } from '../services/types';
import { defaultQuery } from '../services/leadQuery';
import { number, date, activity, genres, statusLabels } from '../lib/format';
import { DataState, StatusBadge } from '../components/DataState';
import { LeadDetail } from '../components/LeadDetail';
import { Button } from '../components/ui/button';
import { ExportBar } from '../components/ExportBar';

export function Leads({ jobId }: { jobId?: number }) {
  const [query, setQuery] = useState<LeadQuery>({ ...defaultQuery, ...(jobId ? { job_id: jobId } : {}) });
  const [selected, setSelected] = useState<number[]>([]);
  const [detail, setDetail] = useState<number>();
  const resource = useResource<{ items: Lead[]; total: number }>('leads.list', query);
  const update = <K extends keyof LeadQuery>(key: K, value: LeadQuery[K]) =>
    setQuery(current => ({ ...current, page: 1, [key]: value }));
  const toggle = (id: number) =>
    setSelected(current => (current.includes(id) ? current.filter(value => value !== id) : [...current, id]));
  const items = resource.data?.items || [];
  return (
    <>
      <section className="panel filters">
        <div className="filters-header">
          <div>
            <h2>Найти артиста</h2>
            <p>Поиск по собранным профилям</p>
          </div>
          <span className="filter-total">{number(resource.data?.total || 0)} профилей</span>
        </div>
        <div className="filter-grid filter-primary">
          <label className="search-filter">
            Поиск профиля
            <span className="input-with-icon">
              <Search size={17} aria-hidden="true" />
              <input
                placeholder="Имя, username или биография"
                value={query.search}
                onChange={event => update('search', event.target.value)}
              />
            </span>
          </label>
          <label>
            Жанр
            <select value={query.genre} onChange={event => update('genre', event.target.value)}>
              <option value="">Все жанры</option>
              {genres.map(genre => (
                <option key={genre}>{genre}</option>
              ))}
            </select>
          </label>
          <label>
            Статус
            <select value={query.status} onChange={event => update('status', event.target.value)}>
              <option value="">Все статусы</option>
              {['new', 'reviewed', 'qualified', 'rejected', 'contacted'].map(status => (
                <option key={status} value={status}>
                  {statusLabels[status]}
                </option>
              ))}
            </select>
          </label>
          <label>
            Источник
            <select value={query.source} onChange={event => update('source', event.target.value)}>
              <option value="">Все</option>
              <option value="mock">Демо</option>
              <option value="imported">Импорт</option>
              <option value="instagram_scout">Скаут-источники</option>
              <option value="instagram_browser">Instagram из браузера</option>
            </select>
          </label>
        </div>
        <details className="advanced-filters">
          <summary>
            <SlidersHorizontal size={16} /> Дополнительные фильтры
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
        <div className="table-toolbar">
          <span>Выбрано: {selected.length}</span>
          <div className="actions">
            <Button
              variant="outline"
              onClick={() => {
                setQuery({ ...defaultQuery, ...(jobId ? { job_id: jobId } : {}) });
                setSelected([]);
              }}
            >
              Сбросить фильтры
            </Button>
            <Button variant="outline" onClick={resource.refresh}>
              Обновить
            </Button>
          </div>
        </div>
        <ExportBar query={query} selected={selected} />
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
                  <th>Артист</th>
                  <th>Жанр</th>
                  <th>Подписчики</th>
                  <th>Оценка</th>
                  <th>Активность</th>
                  <th>Источник</th>
                  <th>Статус</th>
                  <th>Открыт</th>
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
                    <td>{lead.primary_genre || '—'}</td>
                    <td>
                      {lead.unknown_fields?.includes('followers') ? 'Нет данных' : number(lead.followers)}
                    </td>
                    <td>
                      <span className="score-pill">{lead.lead_score}</span>
                    </td>
                    <td>{activity(lead.last_activity_at)}</td>
                    <td>
                      <span
                        title={lead.sources
                          .map(source => `${source.source_type}: ${source.source_value}`)
                          .join('\n')}
                      >
                        {[
                          ...new Set(
                            lead.sources.map(source =>
                              source.source_provider === 'mock' ? 'Демо' : source.source_provider,
                            ),
                          ),
                        ].join(', ')}
                      </span>
                    </td>
                    <td>
                      <StatusBadge value={lead.status} label={statusLabels[lead.status]} />
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
