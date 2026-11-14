import { useRef, useState } from 'react';
import {
  AudioLines,
  ChevronLeft,
  ChevronRight,
  FilterX,
  ListFilter,
  RefreshCw,
  Send,
  SlidersHorizontal,
  Trash2,
  UsersRound,
} from 'lucide-react';
import { useResource } from '../hooks/useResource';
import type { DashboardData, Lead, LeadQuery } from '../services/types';
import { defaultQuery } from '../services/leadQuery';
import { number, date, genres, sourceLabels, statusLabels } from '../lib/format';
import { DataState } from '../components/DataState';
import { LeadDetail } from '../components/LeadDetail';
import { Button } from '../components/ui/button';
import {
  Checkbox,
  Dropdown,
  IconButton,
  Input,
  Panel,
  SearchField,
  StatusIndicator,
  Switch,
} from '../components/ui/desktop';
import { ExportBar } from '../components/ExportBar';
import { categoryLabels, methodLabel } from '../components/scoutStatus';
import { Modal } from '../components/Modal';
import { ErrorToast } from '../components/Toaster';
import { api } from '../services/api';
import { errorText } from '../lib/errors';

/** Master-detail presentation; the same list, query, polling and mutation contracts as Leads. */
export function ArtistDatabase({ onCampaign }: { onCampaign: (ids: number[]) => Promise<void> }) {
  const [query, setQuery] = useState<LeadQuery>({ ...defaultQuery });
  const [selected, setSelected] = useState<number[]>([]);
  const [detail, setDetail] = useState<number>();
  const [filtersOpen, setFiltersOpen] = useState(false);
  // The whole base opens first; a row opens that artist's profile.
  const [collectionOpen, setCollectionOpen] = useState(true);
  const [outreachError, setOutreachError] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState('');
  const searchRoot = useRef<HTMLDivElement>(null);
  const resource = useResource<{ items: Lead[]; total: number }>('leads.list', query, 5000);
  const update = <K extends keyof LeadQuery>(key: K, value: LeadQuery[K]) =>
    setQuery(current => ({ ...current, page: 1, [key]: value }));
  const toggle = (id: number) =>
    setSelected(current => (current.includes(id) ? current.filter(value => value !== id) : [...current, id]));
  const items = resource.data?.items || [];
  const active = items.find(lead => lead.id === detail)?.id ?? items[0]?.id;
  const allChecked = items.length > 0 && items.every(lead => selected.includes(lead.id));
  const pageCount = Math.max(1, Math.ceil((resource.data?.total || 0) / query.page_size));
  const reset = () => {
    setQuery({ ...defaultQuery });
    setSelected([]);
  };
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
  const choices = (values: string[], labels?: Record<string, string>) =>
    values.map(value => ({ value, label: labels?.[value] || value }));
  return (
    <section
      className={`alf-leads alf-reference-database${collectionOpen ? ' alf-show-collection' : ''}`}
      aria-label="База артистов"
      onKeyDown={event => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'f') {
          event.preventDefault();
          setCollectionOpen(true);
          requestAnimationFrame(() => searchRoot.current?.querySelector('input')?.focus());
        }
      }}
    >
      <header className="alf-workspace-header">
        <div className="alf-workspace-title">
          <span className="alf-module-icon">
            <UsersRound />
          </span>
          <div>
            <h1>База артистов</h1>
            <p>Профили, контакты и оценка соответствия</p>
          </div>
        </div>
        <div className="alf-workspace-actions">
          {active !== undefined && (
            <Button variant="outline" onClick={() => setCollectionOpen(false)}>
              Вернуться к профилю
            </Button>
          )}
          <LeadStats />
          <IconButton label="Обновить базу" onClick={resource.refresh}>
            <RefreshCw />
          </IconButton>
          <ExportBar query={query} selected={selected} compact />
        </div>
      </header>
      <div className="alf-lead-toolbar">
        <div className="alf-local-search" ref={searchRoot}>
          <SearchField
            aria-label="Поиск профиля"
            placeholder="Имя, username или биография"
            value={query.search}
            onChange={event => update('search', event.target.value)}
          />
        </div>
        <Dropdown
          label="Жанр"
          value={query.genre}
          options={[{ value: '', label: 'Все жанры' }, ...choices(genres)]}
          onChange={value => update('genre', value)}
        />
        <Dropdown
          label="Статус"
          value={query.status}
          options={[
            { value: '', label: 'Все статусы' },
            ...choices(['new', 'reviewed', 'qualified', 'rejected', 'contacted'], statusLabels),
          ]}
          onChange={value => update('status', value)}
        />
        <Button variant="outline" onClick={() => setFiltersOpen(true)}>
          <SlidersHorizontal />
          Фильтры
        </Button>
        <IconButton label="Сбросить фильтры" onClick={reset}>
          <FilterX />
        </IconButton>
      </div>
      <div className="alf-master-detail">
        <section className="alf-master alf-panel" aria-label="Список артистов">
          <header className="alf-master-heading">
            <ListFilter />
            <span>
              Артисты <b>{number(resource.data?.total || 0)}</b>
            </span>
            <Dropdown
              label="Сортировка"
              value={query.sort}
              options={choices(['score', 'followers', 'activity', 'created'], {
                score: 'По оценке',
                followers: 'По подписчикам',
                activity: 'По активности',
                created: 'По дате',
              })}
              onChange={value => update('sort', value)}
            />
          </header>
          <div className="alf-selection-bar">
            <Checkbox
              label="Выбрать страницу"
              checked={allChecked}
              onChange={event =>
                setSelected(current =>
                  event.target.checked
                    ? Array.from(new Set([...current, ...items.map(lead => lead.id)]))
                    : current.filter(id => !items.some(lead => lead.id === id)),
                )
              }
            />
            <span>{selected.length ? `Выбрано: ${number(selected.length)}` : 'Выбрать страницу'}</span>
            <span className="alf-flex" />
            <IconButton
              label="Добавить выбранных в рассылку"
              disabled={!selected.length}
              onClick={() => {
                setOutreachError('');
                void onCampaign(selected).catch(err => setOutreachError(errorText(err)));
              }}
            >
              <Send />
            </IconButton>
            <IconButton
              label="Удалить выбранных"
              disabled={!selected.length}
              onClick={() => {
                setDeleteError('');
                setConfirmDelete(true);
              }}
            >
              <Trash2 />
            </IconButton>
          </div>
          <DataState {...resource} retry={resource.refresh} />
          <div className="alf-artist-list" role="list" aria-label="Найденные артисты">
            {items.map((lead, index) => (
              <article
                className={`alf-artist-row${active === lead.id ? ' is-active' : ''}`}
                role="listitem"
                key={lead.id}
              >
                <Checkbox
                  label={`Выбрать ${lead.username}`}
                  checked={selected.includes(lead.id)}
                  onChange={() => toggle(lead.id)}
                />
                <button
                  className="alf-artist-open"
                  aria-pressed={active === lead.id}
                  onClick={() => {
                    setDetail(lead.id);
                    setCollectionOpen(false);
                  }}
                  onKeyDown={event => {
                    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                      event.preventDefault();
                      const next = Math.min(
                        items.length - 1,
                        Math.max(0, index + (event.key === 'ArrowDown' ? 1 : -1)),
                      );
                      setDetail(items[next].id);
                      const buttons = event.currentTarget
                        .closest('.alf-artist-list')
                        ?.querySelectorAll<HTMLButtonElement>('.alf-artist-open');
                      buttons?.item(next)?.focus();
                    }
                    if (event.key === ' ') {
                      event.preventDefault();
                      toggle(lead.id);
                    }
                    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
                      event.preventDefault();
                      setSelected(current =>
                        Array.from(new Set([...current, ...items.map(item => item.id)])),
                      );
                    }
                  }}
                >
                  <span className="alf-artist-avatar">
                    {lead.display_name?.slice(0, 1) || lead.username.slice(0, 1)}
                  </span>
                  <span className="alf-artist-copy">
                    <strong>{lead.display_name || `@${lead.username}`}</strong>
                    <span>@{lead.username}</span>
                    <small>
                      {lead.scout_profile
                        ? categoryLabels[lead.scout_profile.profile_type]
                        : lead.primary_genre || 'Жанр не определён'}
                      <i />
                      {lead.unknown_fields?.includes('followers')
                        ? 'Подписчики не прочитаны'
                        : `${number(lead.followers)} подп.`}
                    </small>
                  </span>
                  <span className="alf-score">
                    <b>{lead.lead_score}</b>
                    <small>/ 100</small>
                    <StatusIndicator
                      tone={
                        lead.do_not_contact ? 'danger' : lead.status === 'qualified' ? 'positive' : 'neutral'
                      }
                    >
                      {lead.do_not_contact ? 'Не связываться' : statusLabels[lead.status] || lead.status}
                    </StatusIndicator>
                  </span>
                </button>
              </article>
            ))}
            {resource.data && !items.length && (
              <div className="alf-empty">
                <AudioLines />
                <h2>Артисты не найдены</h2>
                <p>Измените фильтры или запустите поиск в разделе «Парсер и рассылка».</p>
                <Button variant="outline" onClick={reset}>
                  Сбросить фильтры
                </Button>
              </div>
            )}
          </div>
          <footer className="alf-master-footer">
            <span>
              Страница {query.page} / {pageCount}
            </span>
            <span className="alf-flex" />
            <IconButton
              label="Предыдущая страница"
              disabled={query.page <= 1}
              onClick={() => setQuery(current => ({ ...current, page: current.page - 1 }))}
            >
              <ChevronLeft />
            </IconButton>
            <IconButton
              label="Следующая страница"
              disabled={query.page >= pageCount}
              onClick={() => setQuery(current => ({ ...current, page: current.page + 1 }))}
            >
              <ChevronRight />
            </IconButton>
          </footer>
        </section>
        <div className="alf-detail-workspace" aria-label="Выбранный артист">
          {active !== undefined ? (
            <>
              <Button variant="outline" className="alf-back-to-list" onClick={() => setCollectionOpen(true)}>
                <ChevronLeft />
                Все артисты · {number(resource.data?.total || 0)}
              </Button>
              <LeadDetail
                key={active}
                id={active}
                embedded
                artists={items.map(lead => ({
                  value: String(lead.id),
                  label: lead.display_name || `@${lead.username}`,
                }))}
                onChooseArtist={setDetail}
                onBrowse={() => setCollectionOpen(true)}
                close={() => setDetail(undefined)}
                refresh={resource.refresh}
              />
              <Panel title="Сведения о записи" icon={<ListFilter />} className="alf-record-info">
                {items
                  .filter(lead => lead.id === active)
                  .map(lead => (
                    <div className="alf-record-grid" key={lead.id}>
                      <span>
                        Источник
                        <strong>
                          {lead.scout_profile
                            ? `@${lead.scout_profile.source_username} · ${methodLabel(lead.scout_profile.discovery_method)}`
                            : [
                                ...new Set(
                                  lead.sources.map(
                                    source => sourceLabels[source.source_provider] || source.source_provider,
                                  ),
                                ),
                              ].join(', ') || 'Не указан'}
                        </strong>
                      </span>
                      <span>
                        Добавлен<strong>{date(lead.created_at)}</strong>
                      </span>
                      <span>
                        Контакты
                        <strong>
                          {[
                            ...(lead.scout_profile?.emails || []),
                            ...(lead.scout_profile?.phones || []),
                          ].join(' · ') || 'Не найдены'}
                        </strong>
                      </span>
                    </div>
                  ))}
              </Panel>
            </>
          ) : (
            <div className="alf-detail-empty">
              <AudioLines />
              <h2>Рабочая область артиста</h2>
              <p>Выберите профиль в списке, чтобы увидеть контакты, анализ и историю общения.</p>
              <Button variant="outline" onClick={() => setCollectionOpen(true)}>
                Открыть базу артистов
              </Button>
            </div>
          )}
        </div>
      </div>
      <ErrorToast message={outreachError} />
      {filtersOpen && (
        <Modal title="Фильтры базы артистов" onClose={() => setFiltersOpen(false)}>
          <div className="alf-filter-form">
            <label>
              Источник
              <Dropdown
                label="Источник"
                value={query.source}
                options={choices(['', 'imported', 'instagram_scout', 'instagram_browser'], {
                  '': 'Все источники',
                  imported: 'Импорт',
                  instagram_scout: 'Скаут-источники',
                  instagram_browser: 'Instagram из браузера',
                })}
                onChange={value => update('source', value)}
              />
            </label>
            {(['min_followers', 'max_followers', 'minimum_score'] as const).map(key => (
              <label key={key}>
                {
                  {
                    min_followers: 'Подписчики от',
                    max_followers: 'Подписчики до',
                    minimum_score: 'Оценка от',
                  }[key]
                }
                <Input
                  type="number"
                  min={0}
                  max={key === 'minimum_score' ? 100 : undefined}
                  value={query[key]}
                  onChange={event => update(key, Number(event.target.value))}
                />
              </label>
            ))}
            <label>
              Активность
              <Dropdown
                label="Активность"
                value={String(query.activity_days || '')}
                options={[
                  { value: '', label: 'Любая' },
                  ...[7, 14, 30, 60, 90].map(day => ({ value: String(day), label: `${day} дней` })),
                ]}
                onChange={value => update('activity_days', value ? Number(value) : null)}
              />
            </label>
            <label>
              Порядок сортировки
              <Dropdown
                label="Порядок"
                value={String(query.descending)}
                options={[
                  { value: 'true', label: 'По убыванию' },
                  { value: 'false', label: 'По возрастанию' },
                ]}
                onChange={value => update('descending', value === 'true')}
              />
            </label>
            <label className="alf-filter-switch">
              Только подходящие
              <Switch
                label="Только подходящие"
                checked={query.status === 'qualified'}
                onChange={checked => update('status', checked ? 'qualified' : '')}
              />
            </label>
          </div>
          <div className="actions">
            <Button onClick={() => setFiltersOpen(false)}>Готово</Button>
            <Button variant="outline" onClick={reset}>
              Сбросить
            </Button>
          </div>
        </Modal>
      )}
      {confirmDelete && (
        <Modal
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
              <Trash2 />
              Удалить
            </Button>
            <Button variant="outline" disabled={deleting} onClick={() => setConfirmDelete(false)}>
              Отмена
            </Button>
          </div>
        </Modal>
      )}
    </section>
  );
}
function LeadStats() {
  const stats = useResource<DashboardData>('dashboard.get', {}, 15000);
  if (!stats.data) return null;
  return (
    <details className="alf-summary">
      <summary>Сводка</summary>
      <div>
        Всего: {number(stats.data.total)} · Подходят: {number(stats.data.qualified)} · Сегодня:{' '}
        {number(stats.data.today)}
      </div>
    </details>
  );
}
