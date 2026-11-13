import { ErrorToast } from '../components/Toaster';
import { useEffect, useId, useRef, useState } from 'react';
import { FilterX, RefreshCw, Search, Send, SlidersHorizontal, Trash2 } from 'lucide-react';
import { useResource } from '../hooks/useResource';
import type { DashboardData, Lead, LeadQuery } from '../services/types';
import { PageHeader } from '../components/PageHeader';
import { defaultQuery } from '../services/leadQuery';
import { number, genres, statusLabels } from '../lib/format';
import { DataState, StatusBadge } from '../components/DataState';
import { LeadDetail } from '../components/LeadDetail';
import { Button } from '../components/ui/button';
import { ExportBar } from '../components/ExportBar';
import { categoryLabels } from '../components/scoutStatus';
import { LeadDialog } from '../design/LeadDialog';
import { ActionMenu } from '../design/ActionMenu';
import { api } from '../services/api';
import { errorText } from '../lib/errors';

export function Leads({
  jobId,
  onCampaign,
}: {
  jobId?: number;
  onCampaign?: (ids: number[]) => Promise<void>;
}) {
  const [query, setQuery] = useState<LeadQuery>({ ...defaultQuery, ...(jobId ? { job_id: jobId } : {}) });
  const filterId = useId();
  const [filtersOpen, setFiltersOpen] = useState(false);
  const filters = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!filtersOpen) return;
    const outside = (event: PointerEvent) => {
      if (!filters.current?.contains(event.target as Node)) setFiltersOpen(false);
    };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [filtersOpen]);
  const filterKeys = [
    'genre',
    'status',
    'source',
    'min_followers',
    'max_followers',
    'minimum_score',
    'activity_days',
    'sort',
    'descending',
  ] as const;
  const filterCount = filterKeys.filter(key => query[key] !== defaultQuery[key]).length;
  const [selected, setSelected] = useState<number[]>([]);
  const [detail, setDetail] = useState<number>();
  const [outreachError, setOutreachError] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  // Polled so that leads saved by a running Scout appear without a manual refresh.
  const resource = useResource<{ items: Lead[]; total: number }>('leads.list', query, 5000);
  const update = <K extends keyof LeadQuery>(key: K, value: LeadQuery[K]) =>
    setQuery(current => ({ ...current, page: 1, [key]: value }));
  const toggle = (id: number) =>
    setSelected(current => (current.includes(id) ? current.filter(value => value !== id) : [...current, id]));
  const items = resource.data?.items || [];
  const remove = async () => {
    setDeleting(true);
    setDeleteError('');
    try {
      await api.request('leads.delete', { ids: selected });
      setSelected([]);
      setConfirmDelete(false);
      resource.refresh();
    } catch (err) {
      setDeleteError(errorText(err));
    } finally {
      setDeleting(false);
    }
  };
  const reset = () => {
    setQuery({ ...defaultQuery, ...(jobId ? { job_id: jobId } : {}) });
    setSelected([]);
  };
  const toolbar = (
    <>
      <Button variant="outline" onClick={resource.refresh}>
        <RefreshCw size={15} /> Обновить
      </Button>
      <ExportBar query={query} selected={selected} compact />
      <ActionMenu label="Действия">
        {close => (
          <button
            role="menuitem"
            onClick={() => {
              close();
              reset();
            }}
          >
            <FilterX size={15} /> Сбросить фильтры
          </button>
        )}
      </ActionMenu>
    </>
  );
  return (
    <div className={`leads-v2${jobId ? ' embedded' : ''}${detail ? ' has-inspector' : ''}`}>
      {jobId ? (
        <div className="actions embedded-toolbar">{toolbar}</div>
      ) : (
        <>
          <PageHeader
            page="leads"
            count={resource.data ? number(resource.data.total) : undefined}
            actions={toolbar}
          />
        </>
      )}
      <section className="filter-bar" aria-label="Фильтры артистов">
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
        <div
          className="filter-disclosure"
          ref={filters}
          onBlur={event => {
            if (!event.currentTarget.contains(event.relatedTarget)) setFiltersOpen(false);
          }}
          onKeyDown={event => {
            if (event.key === 'Escape' && filtersOpen) {
              setFiltersOpen(false);
              trigger.current?.focus();
            }
          }}
        >
          <button
            type="button"
            ref={trigger}
            className="filter-trigger"
            aria-controls={filterId}
            aria-expanded={filtersOpen}
            onClick={() => setFiltersOpen(value => !value)}
          >
            <SlidersHorizontal size={16} /> Фильтры {filterCount > 0 && <strong>{filterCount}</strong>}
          </button>
          <div id={filterId} hidden={!filtersOpen} className="quiet-filter-popover">
            <div className="filter-grid">
              <label>
                Жанр
                <select
                  aria-label="Жанр"
                  value={query.genre}
                  onChange={event => update('genre', event.target.value)}
                >
                  <option value="">Все жанры</option>
                  {genres.map(genre => (
                    <option key={genre}>{genre}</option>
                  ))}
                </select>
              </label>
              <label>
                Статус
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
              </label>
              <label>
                Источник
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
              </label>
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
          </div>
        </div>
      </section>
      {selected.length > 0 && (
        <div className="lead-selection-bar" role="region" aria-label="Действия с выбранными">
          <span aria-live="polite">Выбрано: {number(selected.length)}</span>

          <>
            <button className="text-action" onClick={() => setSelected([])}>
              Снять выделение
            </button>
            {onCampaign && (
              <Button
                onClick={() => {
                  setOutreachError('');
                  void onCampaign(selected).catch(err => setOutreachError(errorText(err)));
                }}
              >
                <Send size={14} /> В рассылку
              </Button>
            )}
            <Button
              variant="outline"
              onClick={() => {
                setDeleteError('');
                setConfirmDelete(true);
              }}
            >
              <Trash2 size={14} /> Удалить
            </Button>
          </>
          <ErrorToast message={outreachError} />
        </div>
      )}
      {resource.error && resource.data ? (
        <div className="lead-inline-error" role="alert">
          <span>{resource.error} Показаны последние загруженные данные.</span>
          <Button variant="outline" onClick={resource.refresh}>
            Повторить
          </Button>
        </div>
      ) : (
        <DataState {...resource} retry={resource.refresh} />
      )}
      {resource.data && (
        <>
          <div className="table-container">
            <table aria-label="База артистов">
              <thead>
                <tr>
                  <th>
                    <input
                      aria-label="Выбрать страницу"
                      ref={node => {
                        if (node)
                          node.indeterminate =
                            items.some(lead => selected.includes(lead.id)) &&
                            !items.every(lead => selected.includes(lead.id));
                      }}
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
                  <th>Жанр / тип</th>
                  <th>Подписчики</th>
                  <th>Оценка</th>
                  <th>Статус</th>
                </tr>
              </thead>
              <tbody>
                {items.map(lead => (
                  <tr
                    key={lead.id}
                    onClick={event => {
                      if ((event.target as HTMLElement).closest('input,button,td:first-child')) return;
                      event.currentTarget.querySelector<HTMLButtonElement>('.profile-cell')?.focus();
                      setDetail(lead.id);
                    }}
                    data-selected={selected.includes(lead.id)}
                    data-inspected={detail === lead.id}
                  >
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
                        <span title={`${lead.display_name} · @${lead.username}`}>
                          <strong>{lead.display_name || lead.username}</strong>
                          <small>@{lead.username}</small>
                        </span>
                      </button>
                    </td>
                    <td>
                      {lead.primary_genre ||
                        (lead.scout_profile && categoryLabels[lead.scout_profile.profile_type]) ||
                        '—'}
                    </td>
                    <td>
                      {lead.unknown_fields?.includes('followers') ? (
                        <span title="Подписчики не прочитаны">—</span>
                      ) : (
                        number(lead.followers)
                      )}
                    </td>
                    <td>
                      <span className="score-pill">{lead.lead_score}</span>
                    </td>
                    <td>
                      <StatusBadge value={lead.status} label={statusLabels[lead.status]} />
                      {lead.do_not_contact && <small className="cell-sub dnc-mark">Не связываться</small>}
                    </td>
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
            {!jobId && <LeadStats />}
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
      {confirmDelete && (
        <LeadDialog
          busy={deleting}
          title={`Удалить из базы: ${selected.length}?`}
          onClose={() => !deleting && setConfirmDelete(false)}
        >
          <p className="helper">
            Вместе с артистами удалятся их источники, анализ, переписки и история рассылки. Контакты в CRM
            останутся.
          </p>
          <p className="helper">
            Парсер помнит этих артистов и не добавит их снова, пока не сброшена его память. Отменить удаление
            нельзя.
          </p>
          <ErrorToast message={deleteError} />
          <div className="actions">
            <Button variant="danger" disabled={deleting} onClick={() => void remove()}>
              <Trash2 size={15} /> Удалить
            </Button>
            <Button data-cancel variant="outline" disabled={deleting} onClick={() => setConfirmDelete(false)}>
              Отмена
            </Button>
          </div>
        </LeadDialog>
      )}
      {detail && (
        <LeadDetail
          key={detail}
          inspector
          id={detail}
          close={() => setDetail(undefined)}
          refresh={resource.refresh}
        />
      )}
    </div>
  );
}

function LeadStats() {
  const stats = useResource<DashboardData>('dashboard.get', {}, 15000);
  if (!stats.data) return null;
  return (
    <div className="lead-totals" aria-label="Показатели всей базы">
      <span>
        Подходят <b>{number(stats.data.qualified)}</b>
      </span>
      <span>
        Сегодня <b>{number(stats.data.today)}</b>
      </span>
    </div>
  );
}
