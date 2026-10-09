import { ErrorToast } from '../components/Toaster';
import { useEffect, useMemo, useState } from 'react';
import { open, save as saveDialog } from '@tauri-apps/plugin-dialog';
import {
  ArrowUpDown,
  Bell,
  DatabaseZap,
  FileDown,
  FileUp,
  Plus,
  RotateCcw,
  Search,
  Send,
  SlidersHorizontal,
  Star,
  Table2,
  Trash2,
  UsersRound,
  type LucideIcon,
} from 'lucide-react';
import type {
  CrmChannelKind,
  CrmContact,
  CrmFilters,
  CrmId,
  CrmImportResult,
  CrmList,
  CrmSource,
  CrmStatus,
  CrmTab,
} from '../services/types';
import { useResource } from '../hooks/useResource';
import { api } from '../services/api';
import { number } from '../lib/format';
import { PageHeader } from '../components/PageHeader';
import { Modal } from '../components/Modal';
import { Button } from '../components/ui/button';
import { errorText } from '../components/accountRuns';
import {
  ColumnFilter,
  ContactModal,
  FilterOption,
  ImportModal,
  StatusChip,
  StatusesModal,
} from '../components/crm/CrmModals';
import { draftOf, emptyDraft, kindIcons, type ContactDraft } from '../components/crm/crmDraft';
import { dueLabel, dueState, initials, kindLabels, lastContactLabel, money } from '../components/crm/crmText';

type Sort = 'created' | 'name' | 'last' | 'next';
type Dialog = 'import' | 'statuses' | 'contact' | 'purge' | null;
const PAGE_SIZE = 100;
// An owner id that matches no contact: the user CRM page before the owners load.
const NOBODY = '-';

const tabs: { id: CrmTab; label: string; icon: LucideIcon }[] = [
  { id: 'all', label: 'Все контакты', icon: Table2 },
  { id: 'attention', label: 'Требуют внимания', icon: Bell },
  { id: 'trash', label: 'Корзина', icon: Trash2 },
];

/** One channel's CRM: the Instagram and iMessage tables are separate and import into each other. */
export function Crm({ crm, onWrite, others = false }: { crm: CrmId; onWrite: () => void; others?: boolean }) {
  const [tab, setTab] = useState<CrmTab>('all');
  // Own CRM by default; `others` (admin, moderator) shows one other user's CRM at a time.
  // Until the owners arrive the list asks for nobody's contacts.
  const [owner, setOwner] = useState(others ? NOBODY : '');
  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<Sort>('created');
  const [descending, setDescending] = useState(true);
  const [filters, setFilters] = useState<CrmFilters>({});
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<number[]>([]);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [draft, setDraft] = useState<ContactDraft>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const query = { crm, tab, search, sort, descending, filters, page, page_size: PAGE_SIZE, owner };
  const resource = useResource<CrmList>('crm.list', query, 10000);
  const [data, setData] = useState<CrmList>();
  useEffect(() => {
    if (resource.data) setData(resource.data);
  }, [resource.data]);
  // Another tab, search or filter is another list: the selection and page start over.
  useEffect(() => {
    setSelected([]);
    setPage(1);
  }, [tab, search, sort, descending, filters, owner]);

  const otherOwners = useMemo(() => (data?.owners ?? []).filter(item => !item.mine), [data?.owners]);
  useEffect(() => {
    if (others && otherOwners.length && !otherOwners.some(item => item.id === owner)) {
      setOwner(otherOwners[0].id);
    }
  }, [others, otherOwners, owner]);

  const items = data?.items ?? [];
  const statuses = data?.statuses ?? [];
  const counts = data?.counts ?? { all: 0, attention: 0, trash: 0 };
  const trash = tab === 'trash';
  const filtered = Object.values(filters).some(value => (Array.isArray(value) ? value.length : value));

  const run = async <T,>(action: () => Promise<T>) => {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await action();
      resource.refresh();
      return result;
    } catch (err) {
      setError(errorText(err));
      return undefined;
    } finally {
      setBusy(false);
    }
  };
  const request = <T,>(method: string, params: object = {}) =>
    run(() => api.request<T>(method, { crm, ...params }));

  const importFrom = async (source: CrmSource['id']) => {
    const result = await request<CrmImportResult>('crm.import_verse', { source });
    if (!result) return;
    setDialog(null);
    setNotice(importText(result));
  };
  const importFile = async () => {
    const path = await open({
      multiple: false,
      directory: false,
      filters: [{ name: 'Таблица', extensions: ['xlsx', 'csv'] }],
    });
    if (typeof path !== 'string') return;
    const result = await request<CrmImportResult>('crm.import_file', { path });
    if (result) setNotice(importText(result));
  };
  const exportFile = async () => {
    const path = await saveDialog({
      title: 'Экспорт CRM',
      defaultPath: `crm-${crm}.xlsx`,
      filters: [
        { name: 'Excel', extensions: ['xlsx'] },
        { name: 'CSV', extensions: ['csv'] },
      ],
    });
    if (!path) return;
    const result = await request<{ count: number }>(
      'crm.export',
      selected.length ? { path, ids: selected, owner } : { path, owner },
    );
    if (result) setNotice(`Сохранено контактов: ${number(result.count)}`);
  };
  const saveContact = async (value: ContactDraft) => {
    const result = await request<CrmContact>('crm.save', value);
    if (result) setDialog(null);
  };
  const saveStatuses = async (value: CrmStatus[]) => {
    const result = await request('crm.statuses_save', { statuses: value });
    if (result) setDialog(null);
  };
  const write = async (ids: number[]) => {
    const result = await request<{ added: number; found: number }>('crm.write', { ids });
    if (result) onWrite();
  };
  const bulk = async (method: string, params: object = {}) => {
    const result = await request(method, { ids: selected, ...params });
    if (result) setSelected([]);
  };

  const toggleSort = (key: Sort) => {
    if (sort === key) setDescending(value => !value);
    else {
      setSort(key);
      setDescending(key !== 'name');
    }
  };
  const sortButton = (key: Sort, label: string) => (
    <button
      type="button"
      className={`crm-head-button${sort === key ? ' active' : ''}`}
      aria-label={`Сортировать: ${label}`}
      title={sort === key ? (descending ? 'По убыванию' : 'По возрастанию') : 'Сортировать'}
      onClick={() => toggleSort(key)}
    >
      <ArrowUpDown size={12} />
    </button>
  );
  const pick = <K extends keyof CrmFilters>(key: K, value: CrmFilters[K]) =>
    setFilters(current => ({ ...current, [key]: current[key] === value ? undefined : value }));
  const toggleIn = <K extends 'statuses' | 'channels'>(key: K, value: string) =>
    setFilters(current => {
      const list = (current[key] ?? []) as string[];
      const next = list.includes(value) ? list.filter(item => item !== value) : [...list, value];
      return { ...current, [key]: next.length ? next : undefined };
    });
  const allChecked = items.length > 0 && items.every(item => selected.includes(item.id));
  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE));
  const writeKinds: CrmChannelKind[] = crm === 'instagram' ? ['instagram'] : ['phone', 'email'];

  return (
    <div className="crm-page">
      <PageHeader
        page={
          crm === 'instagram'
            ? others
              ? 'crm-users'
              : 'crm'
            : others
              ? 'imessage-crm-users'
              : 'imessage-crm'
        }
        count={number(counts.all)}
      >
        <div className="crm-toolbar">
          {others && (
            <select
              className="crm-owner-filter"
              aria-label="Чья CRM"
              value={owner}
              disabled={!otherOwners.length}
              onChange={event => setOwner(event.target.value)}
            >
              {!otherOwners.length && <option value={NOBODY}>Пока ни у кого нет контактов</option>}
              {otherOwners.map(item => (
                <option key={item.id} value={item.id}>
                  {item.name || 'Без имени'} · {number(item.count)}
                </option>
              ))}
            </select>
          )}
          {!others && (
            <>
              <Button variant="outline" disabled={busy} onClick={() => setDialog('import')}>
                <DatabaseZap size={15} /> Импортировать
              </Button>
              <Button variant="outline" disabled={busy} onClick={() => void importFile()}>
                <FileUp size={15} /> Импорт XLSX / CSV
              </Button>
            </>
          )}
          <Button variant="outline" disabled={busy || !counts.all} onClick={() => void exportFile()}>
            <FileDown size={15} /> Экспорт XLSX / CSV
          </Button>
          {!others && (
            <>
              <Button variant="outline" disabled={!data} onClick={() => setDialog('statuses')}>
                <SlidersHorizontal size={15} /> Настроить статусы
              </Button>
              <Button
                onClick={() => {
                  setError('');
                  setDraft(emptyDraft(crm));
                  setDialog('contact');
                }}
              >
                <Plus size={15} /> Добавить контакт
              </Button>
            </>
          )}
        </div>
      </PageHeader>

      <div className="crm-tabs-row">
        <div className="crm-tabs" role="tablist" aria-label="Контакты">
          {tabs.map(item => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={tab === item.id}
              className={tab === item.id ? 'active' : ''}
              onClick={() => setTab(item.id)}
            >
              <item.icon size={14} /> {item.label}
              <span className="crm-tab-count">{number(counts[item.id])}</span>
            </button>
          ))}
        </div>
        <div className="crm-totals">
          <span>
            Принёс <strong>{money(data?.totals.earned ?? 0)}</strong>
          </span>
          <span>
            Потенциал <strong>{money(data?.totals.potential ?? 0)}</strong>
          </span>
        </div>
      </div>

      <label className="crm-search">
        <Search size={15} aria-hidden="true" />
        <input
          aria-label="Поиск по контактам"
          placeholder="Поиск по контактам, каналам, статусам и заметкам…"
          value={search}
          onChange={event => setSearch(event.target.value)}
        />
        {filtered && (
          <button type="button" className="crm-reset" onClick={() => setFilters({})}>
            Сбросить фильтры
          </button>
        )}
        <span className="crm-found">
          Найдено: <strong>{number(data?.total ?? 0)}</strong>
        </span>
      </label>

      <ErrorToast message={error} />
      {notice && !error && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}

      {selected.length > 0 && (
        <div className="crm-selection">
          <span>Выбрано: {number(selected.length)}</span>
          {trash ? (
            <>
              <Button variant="outline" disabled={busy} onClick={() => void bulk('crm.restore')}>
                <RotateCcw size={14} /> Восстановить
              </Button>
              <Button variant="outline" disabled={busy} onClick={() => void bulk('crm.purge')}>
                <Trash2 size={14} /> Удалить навсегда
              </Button>
            </>
          ) : (
            <>
              <Button variant="outline" disabled={busy} onClick={() => void write(selected)}>
                <Send size={14} /> Написать
              </Button>
              <select
                aria-label="Добавить статус"
                value=""
                disabled={busy}
                onChange={event =>
                  event.target.value && void bulk('crm.label', { label: event.target.value })
                }
              >
                <option value="">+ Статус</option>
                {statuses.map(status => (
                  <option key={status.label} value={status.label}>
                    {status.label}
                  </option>
                ))}
              </select>
              <Button variant="outline" disabled={busy} onClick={() => void bulk('crm.trash')}>
                <Trash2 size={14} /> В корзину
              </Button>
            </>
          )}
          <Button variant="outline" onClick={() => setSelected([])}>
            Снять выбор
          </Button>
        </div>
      )}
      {trash && counts.trash > 0 && selected.length === 0 && (
        <div className="crm-selection">
          <span>В корзине контакты не участвуют в рассылках и подсчётах.</span>
          <Button variant="outline" disabled={busy} onClick={() => setDialog('purge')}>
            <Trash2 size={14} /> Очистить корзину
          </Button>
        </div>
      )}

      <div className="crm-table">
        <table>
          <thead>
            <tr>
              <th className="crm-check-cell">
                <input
                  type="checkbox"
                  aria-label="Выбрать все на странице"
                  checked={allChecked}
                  onChange={() =>
                    setSelected(current =>
                      allChecked
                        ? current.filter(id => !items.some(item => item.id === id))
                        : Array.from(new Set([...current, ...items.map(item => item.id)])),
                    )
                  }
                />
              </th>
              <th>Контакт {sortButton('name', 'контакт')}</th>
              <th>
                Статусы{' '}
                <ColumnFilter label="статусы" active={!!filters.statuses}>
                  {() =>
                    (data?.labels ?? []).map(label => (
                      <FilterOption
                        key={label}
                        checked={!!filters.statuses?.includes(label)}
                        label={<StatusChip label={label} statuses={statuses} />}
                        onClick={() => toggleIn('statuses', label)}
                      />
                    ))
                  }
                </ColumnFilter>
              </th>
              <th>
                Каналы{' '}
                <ColumnFilter label="каналы" active={!!filters.channels}>
                  {() =>
                    (Object.keys(kindLabels) as CrmChannelKind[]).map(kind => (
                      <FilterOption
                        key={kind}
                        checked={!!filters.channels?.includes(kind)}
                        label={kindLabels[kind]}
                        onClick={() => toggleIn('channels', kind)}
                      />
                    ))
                  }
                </ColumnFilter>
              </th>
              <th>
                Последний контакт {sortButton('last', 'последний контакт')}
                <ColumnFilter label="последний контакт" active={!!filters.last}>
                  {close =>
                    (
                      [
                        ['never', 'Никогда'],
                        ['week', 'За 7 дней'],
                        ['month', 'За 30 дней'],
                        ['older', 'Больше 30 дней назад'],
                      ] as const
                    ).map(([value, label]) => (
                      <FilterOption
                        key={value}
                        checked={filters.last === value}
                        label={label}
                        onClick={() => {
                          pick('last', value);
                          close();
                        }}
                      />
                    ))
                  }
                </ColumnFilter>
              </th>
              <th>
                Следующее действие {sortButton('next', 'следующее действие')}
                <ColumnFilter label="следующее действие" active={!!filters.next}>
                  {close =>
                    (
                      [
                        ['due', 'На сегодня и просроченные'],
                        ['planned', 'Запланированы'],
                        ['none', 'Нет действия'],
                      ] as const
                    ).map(([value, label]) => (
                      <FilterOption
                        key={value}
                        checked={filters.next === value}
                        label={label}
                        onClick={() => {
                          pick('next', value);
                          close();
                        }}
                      />
                    ))
                  }
                </ColumnFilter>
              </th>
              <th>
                Заметки{' '}
                <ColumnFilter label="заметки" active={!!filters.notes}>
                  {close =>
                    (
                      [
                        ['with', 'С заметками'],
                        ['without', 'Без заметок'],
                      ] as const
                    ).map(([value, label]) => (
                      <FilterOption
                        key={value}
                        checked={filters.notes === value}
                        label={label}
                        onClick={() => {
                          pick('notes', value);
                          close();
                        }}
                      />
                    ))
                  }
                </ColumnFilter>
              </th>
              <th>
                Деньги{' '}
                <ColumnFilter label="деньги" active={!!filters.money}>
                  {close =>
                    (
                      [
                        ['earned', 'Принесли деньги'],
                        ['potential', 'Есть потенциал'],
                        ['none', 'Без сумм'],
                      ] as const
                    ).map(([value, label]) => (
                      <FilterOption
                        key={value}
                        checked={filters.money === value}
                        label={label}
                        onClick={() => {
                          pick('money', value);
                          close();
                        }}
                      />
                    ))
                  }
                </ColumnFilter>
              </th>
              <th className="crm-action-cell">Действие</th>
            </tr>
          </thead>
          <tbody>
            {items.map(contact => {
              const due = dueState(contact.next_action_at);
              // Writing goes through this computer's own lists: only to your own contacts.
              const canWrite =
                contact.mine !== false && contact.channels.some(item => writeKinds.includes(item.kind));
              const openContact = () => {
                setError('');
                setDraft(draftOf(contact));
                setDialog('contact');
              };
              return (
                <tr key={contact.id} className={selected.includes(contact.id) ? 'selected' : undefined}>
                  <td className="crm-check-cell">
                    <input
                      type="checkbox"
                      aria-label={`Выбрать ${contact.name}`}
                      checked={selected.includes(contact.id)}
                      onChange={() =>
                        setSelected(current =>
                          current.includes(contact.id)
                            ? current.filter(id => id !== contact.id)
                            : [...current, contact.id],
                        )
                      }
                    />
                  </td>
                  <td>
                    <button type="button" className="crm-person" onClick={openContact}>
                      <span className="crm-avatar">{initials(contact.name)}</span>
                      <strong>{contact.name}</strong>
                    </button>
                  </td>
                  <td>
                    <div className="crm-chips">
                      {contact.statuses.map(label => (
                        <StatusChip key={label} label={label} statuses={statuses} />
                      ))}
                    </div>
                  </td>
                  <td>
                    <div className="crm-channels">
                      {contact.channels.slice(0, 2).map((channel, index) => {
                        const Icon = kindIcons[channel.kind];
                        return (
                          <span key={channel.kind + channel.value} title={kindLabels[channel.kind]}>
                            {index === 0 ? (
                              <Star size={11} className="crm-main" aria-label="Основной" />
                            ) : (
                              <i className="crm-star-space" />
                            )}
                            <Icon size={12} />
                            <b>{channel.value}</b>
                          </span>
                        );
                      })}
                      {contact.channels.length > 2 && (
                        <small
                          title={contact.channels
                            .slice(2)
                            .map(item => item.value)
                            .join('\n')}
                        >
                          +{contact.channels.length - 2}
                        </small>
                      )}
                    </div>
                  </td>
                  <td className="crm-muted">{lastContactLabel(contact.last_contact_at)}</td>
                  <td>
                    {contact.next_action || contact.next_action_at ? (
                      <div className="crm-next">
                        <strong>{contact.next_action || 'Действие'}</strong>
                        {contact.next_action_at && (
                          <small className={due ?? undefined}>{dueLabel(contact.next_action_at)}</small>
                        )}
                      </div>
                    ) : (
                      <strong>Нет</strong>
                    )}
                  </td>
                  <td className="crm-notes" title={contact.notes || undefined}>
                    {contact.notes.split('\n')[0]}
                  </td>
                  <td>
                    <div className="crm-money">
                      <strong title="Принёс">{money(contact.earned)}</strong>
                      <small title="Потенциал">{money(contact.potential)}</small>
                    </div>
                  </td>
                  <td className="crm-action-cell">
                    {trash ? (
                      <Button
                        variant="outline"
                        disabled={busy}
                        onClick={() => void request('crm.restore', { ids: [contact.id] })}
                      >
                        <RotateCcw size={13} /> Вернуть
                      </Button>
                    ) : (
                      <Button
                        variant="outline"
                        disabled={busy || !canWrite}
                        title={
                          contact.mine === false
                            ? 'Написать можно только своим контактам'
                            : canWrite
                              ? crm === 'instagram'
                                ? 'Добавить в «Первичную рассылку»'
                                : 'Добавить в получатели рассылки iMessage'
                              : crm === 'instagram'
                                ? 'Нет Instagram'
                                : 'Нет телефона или email'
                        }
                        onClick={() => void write([contact.id])}
                      >
                        <Send size={13} /> Написать
                      </Button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {data && items.length === 0 && (
          <div className="crm-empty">
            <UsersRound size={26} />
            <strong>
              {data.total === 0 && (search || filtered)
                ? 'Никого не нашли.'
                : trash
                  ? 'Корзина пуста.'
                  : tab === 'attention'
                    ? 'Нет контактов с действием на сегодня.'
                    : 'Контактов пока нет.'}
            </strong>
            {tab === 'all' && !search && !filtered && (
              <Button variant="outline" onClick={() => setDialog('import')}>
                <DatabaseZap size={15} /> Импортировать
              </Button>
            )}
          </div>
        )}
        {resource.error && !data && <p className="error-text crm-empty">{resource.error}</p>}
      </div>

      {pages > 1 && (
        <div className="pagination">
          <span>
            Страница {page} из {pages}
          </span>
          <div className="actions">
            <Button variant="outline" disabled={page <= 1} onClick={() => setPage(value => value - 1)}>
              Назад
            </Button>
            <Button variant="outline" disabled={page >= pages} onClick={() => setPage(value => value + 1)}>
              Далее
            </Button>
          </div>
        </div>
      )}

      {dialog === 'import' && (
        <ImportModal
          crm={crm}
          busy={busy}
          onPick={source => void importFrom(source)}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === 'statuses' && (
        <StatusesModal
          statuses={statuses}
          busy={busy}
          error={error}
          onSave={value => void saveStatuses(value)}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === 'contact' && draft && (
        <ContactModal
          initial={draft}
          statuses={statuses}
          busy={busy}
          error={error}
          onSave={value => void saveContact(value)}
          onTrash={
            draft.id && !trash
              ? () => void request('crm.trash', { ids: [draft.id] }).then(result => result && setDialog(null))
              : undefined
          }
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === 'purge' && (
        <ConfirmPurge
          count={counts.trash}
          busy={busy}
          onConfirm={() => void request('crm.purge').then(result => result && setDialog(null))}
          onClose={() => setDialog(null)}
        />
      )}
    </div>
  );
}

function importText(result: CrmImportResult) {
  const parts = [`Добавлено: ${number(result.added)}`, `объединено: ${number(result.merged)}`];
  if (result.skipped) parts.push(`пропущено: ${number(result.skipped)}`);
  return parts.join(', ');
}

function ConfirmPurge({
  count,
  busy,
  onConfirm,
  onClose,
}: {
  count: number;
  busy: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  return (
    <Modal title="Очистить корзину?" onClose={onClose}>
      <p className="helper">Контакты ({number(count)}) удалятся навсегда, вернуть их будет нельзя.</p>
      <div className="actions modal-actions">
        <Button variant="outline" onClick={onClose}>
          Отмена
        </Button>
        <Button variant="danger" disabled={busy} onClick={onConfirm}>
          Удалить навсегда
        </Button>
      </div>
    </Modal>
  );
}
