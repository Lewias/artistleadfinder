import { ErrorToast } from '../Toaster';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import {
  ChevronRight,
  DatabaseZap,
  Filter,
  MessageSquareText,
  Plus,
  ScanSearch,
  Star,
  Trash2,
  X,
} from 'lucide-react';
import type { CrmChannel, CrmChannelKind, CrmId, CrmSource, CrmStatus } from '../../services/types';
import { useResource } from '../../hooks/useResource';
import { Modal } from '../Modal';
import { Button } from '../ui/button';
import {
  colorNames,
  crmColors,
  kindLabels,
  kindPlaceholders,
  sourceText,
  statusColor,
  statusEmoji,
} from './crmText';
import { Emoji, EmojiPicker } from './Emoji';
import type { ContactDraft } from './crmDraft';

/** «Что импортировать?»: the parsing base and the other CRM with their counts. */
export function ImportModal({
  crm,
  busy,
  onPick,
  onClose,
}: {
  crm: CrmId;
  busy: boolean;
  onPick: (source: CrmSource['id']) => void;
  onClose: () => void;
}) {
  const sources = useResource<CrmSource[]>('crm.sources', { crm });
  return (
    <Modal title="Что импортировать?" icon={<DatabaseZap size={18} />} onClose={onClose}>
      <p className="helper">
        Выберите базу. Повторный импорт аккуратно объединит данные с уже существующими контактами.
      </p>
      <div className="crm-sources">
        {(sources.data ?? []).map(source => {
          const text = sourceText(source.id, crm);
          const Icon = source.id === 'leads' ? ScanSearch : MessageSquareText;
          return (
            <button
              key={source.id}
              type="button"
              className="crm-source"
              disabled={busy || !source.count}
              onClick={() => onPick(source.id)}
            >
              <span className="crm-source-icon">
                <Icon size={18} />
              </span>
              <span className="crm-source-text">
                <strong>{text.title}</strong>
                <small>{text.description}</small>
              </span>
              <span className="crm-source-count">Контактов: {source.count}</span>
              <ChevronRight size={16} />
            </button>
          );
        })}
        {sources.error && <p className="error-text">{sources.error}</p>}
      </div>
      <Button variant="outline" className="crm-wide-button" onClick={onClose}>
        Отмена
      </Button>
    </Modal>
  );
}

/** A status label as a colored chip with its emoji. */
export function StatusChip({ label, statuses }: { label: string; statuses: CrmStatus[] }) {
  const emoji = statusEmoji(label, statuses);
  return (
    <span className={`crm-chip ${statusColor(label, statuses)}`}>
      {emoji && <Emoji emoji={emoji} />}
      {label}
    </span>
  );
}

/** «Настроить статусы»: emoji, names and colors; a removed status leaves the contacts too. */
export function StatusesModal({
  statuses,
  busy,
  error,
  onSave,
  onClose,
}: {
  statuses: CrmStatus[];
  busy: boolean;
  error: string;
  onSave: (statuses: CrmStatus[]) => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState<CrmStatus[]>(statuses);
  const update = (index: number, patch: Partial<CrmStatus>) =>
    setDraft(current => current.map((item, i) => (i === index ? { ...item, ...patch } : item)));
  return (
    <Modal title="Настроить статусы" onClose={onClose}>
      <p className="helper">Статусы этой CRM. Удалённый статус снимется и с контактов.</p>
      <div className="crm-status-list">
        {draft.map((status, index) => (
          <div className="crm-status-row" key={index}>
            <div className="crm-swatches" role="radiogroup" aria-label={`Цвет статуса ${status.label}`}>
              {crmColors.map(color => (
                <button
                  key={color}
                  type="button"
                  role="radio"
                  aria-checked={status.color === color}
                  aria-label={colorNames[color]}
                  title={colorNames[color]}
                  className={`crm-swatch ${color}${status.color === color ? ' active' : ''}`}
                  onClick={() => update(index, { color })}
                />
              ))}
            </div>
            <EmojiPicker
              value={status.emoji ?? ''}
              label={status.label}
              onChange={emoji => update(index, { emoji })}
            />
            <input
              aria-label="Название статуса"
              value={status.label}
              maxLength={40}
              onChange={event => update(index, { label: event.target.value })}
            />
            <Button
              variant="outline"
              icon
              aria-label={`Удалить статус ${status.label}`}
              onClick={() => setDraft(current => current.filter((_, i) => i !== index))}
            >
              <Trash2 size={14} />
            </Button>
          </div>
        ))}
      </div>
      <Button
        variant="outline"
        disabled={draft.length >= 40}
        onClick={() => setDraft(current => [...current, { label: '', color: 'violet' }])}
      >
        <Plus size={15} /> Добавить статус
      </Button>
      <ErrorToast message={error} />
      <div className="actions modal-actions">
        <Button variant="outline" onClick={onClose}>
          Отмена
        </Button>
        <Button
          disabled={busy}
          onClick={() => onSave(draft.map(item => ({ ...item, label: item.label.trim() })))}
        >
          Сохранить
        </Button>
      </div>
    </Modal>
  );
}

/** Add or edit one contact. The first channel is the main one (star). */
export function ContactModal({
  initial,
  statuses,
  busy,
  error,
  onSave,
  onTrash,
  onClose,
}: {
  initial: ContactDraft;
  statuses: CrmStatus[];
  busy: boolean;
  error: string;
  onSave: (draft: ContactDraft) => void;
  onTrash?: () => void;
  onClose: () => void;
}) {
  const [draft, setDraft] = useState(initial);
  const set = <K extends keyof ContactDraft>(key: K, value: ContactDraft[K]) =>
    setDraft(current => ({ ...current, [key]: value }));
  const setChannel = (index: number, patch: Partial<CrmChannel>) =>
    set(
      'channels',
      draft.channels.map((item, i) => (i === index ? { ...item, ...patch } : item)),
    );
  const labels = [
    ...statuses.map(status => status.label),
    ...draft.statuses.filter(label => !statuses.some(s => s.label === label)),
  ];
  const toggle = (label: string) =>
    set(
      'statuses',
      draft.statuses.includes(label)
        ? draft.statuses.filter(item => item !== label)
        : [...draft.statuses, label],
    );
  return (
    <Modal title={initial.id ? 'Контакт' : 'Новый контакт'} onClose={onClose}>
      <div className="modal-fields crm-form">
        <label>
          Имя
          <input
            value={draft.name}
            maxLength={160}
            placeholder="Если пусто — первый канал"
            onChange={event => set('name', event.target.value)}
          />
        </label>
        <div className="crm-form-block">
          <span className="crm-form-label">Каналы</span>
          {draft.channels.map((channel, index) => (
            <div className="crm-channel-row" key={index}>
              <button
                type="button"
                className={`crm-star${index === 0 ? ' active' : ''}`}
                aria-label="Сделать основным"
                title={index === 0 ? 'Основной канал' : 'Сделать основным'}
                onClick={() => set('channels', [channel, ...draft.channels.filter((_, i) => i !== index)])}
              >
                <Star size={14} />
              </button>
              <select
                aria-label="Тип канала"
                value={channel.kind}
                onChange={event => setChannel(index, { kind: event.target.value as CrmChannelKind })}
              >
                {(Object.keys(kindLabels) as CrmChannelKind[]).map(kind => (
                  <option key={kind} value={kind}>
                    {kindLabels[kind]}
                  </option>
                ))}
              </select>
              <input
                aria-label={kindLabels[channel.kind]}
                value={channel.value}
                placeholder={kindPlaceholders[channel.kind]}
                onChange={event => setChannel(index, { value: event.target.value })}
              />
              <Button
                variant="outline"
                icon
                aria-label="Убрать канал"
                onClick={() =>
                  set(
                    'channels',
                    draft.channels.filter((_, i) => i !== index),
                  )
                }
              >
                <X size={14} />
              </Button>
            </div>
          ))}
          <Button
            variant="outline"
            className="crm-add-channel"
            onClick={() => set('channels', [...draft.channels, { kind: 'email', value: '' }])}
          >
            <Plus size={14} /> Канал
          </Button>
        </div>
        <div className="crm-form-block">
          <span className="crm-form-label">Статусы</span>
          <div className="crm-chip-picker">
            {labels.map(label => (
              <button
                key={label}
                type="button"
                aria-pressed={draft.statuses.includes(label)}
                className={`crm-chip ${statusColor(label, statuses)}${draft.statuses.includes(label) ? ' on' : ' off'}`}
                onClick={() => toggle(label)}
              >
                {statusEmoji(label, statuses) && <Emoji emoji={statusEmoji(label, statuses)} />}
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="crm-form-grid">
          <label>
            Следующее действие
            <input
              value={draft.next_action}
              maxLength={300}
              placeholder="Например: отправить бит"
              onChange={event => set('next_action', event.target.value)}
            />
          </label>
          <label>
            Когда
            <input
              type="date"
              value={draft.next_action_at}
              onChange={event => set('next_action_at', event.target.value)}
            />
          </label>
          <label>
            Принёс, $
            <input
              inputMode="decimal"
              value={draft.earned}
              placeholder="0"
              onChange={event => set('earned', event.target.value)}
            />
          </label>
          <label>
            Потенциал, $
            <input
              inputMode="decimal"
              value={draft.potential}
              placeholder="0"
              onChange={event => set('potential', event.target.value)}
            />
          </label>
          <label>
            Последний контакт
            <input
              type="date"
              value={draft.last_contact_at}
              onChange={event => set('last_contact_at', event.target.value)}
            />
          </label>
        </div>
        <label>
          Заметки
          <textarea
            rows={4}
            maxLength={5000}
            value={draft.notes}
            onChange={event => set('notes', event.target.value)}
          />
        </label>
      </div>
      <ErrorToast message={error} />
      <div className="actions modal-actions">
        {onTrash && (
          <Button variant="outline" className="crm-trash-button" disabled={busy} onClick={onTrash}>
            <Trash2 size={14} /> В корзину
          </Button>
        )}
        <Button variant="outline" onClick={onClose}>
          Отмена
        </Button>
        <Button
          disabled={busy}
          onClick={() => onSave({ ...draft, channels: draft.channels.filter(item => item.value.trim()) })}
        >
          Сохранить
        </Button>
      </div>
    </Modal>
  );
}

/** Filter button of a column header with a small popover of options. */
export function ColumnFilter({
  label,
  active,
  children,
}: {
  label: string;
  active: boolean;
  children: (close: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  // The table scrolls and clips overflow, so the popover is fixed to the window.
  const [place, setPlace] = useState<{ top: number; left: number }>();
  const root = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const close = () => setOpen(false);
    const onDown = (event: MouseEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    window.addEventListener('resize', close);
    document.addEventListener('scroll', close, true);
    return () => {
      document.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('resize', close);
      document.removeEventListener('scroll', close, true);
    };
  }, [open]);
  const toggle = () => {
    const rect = root.current?.getBoundingClientRect();
    if (rect) setPlace({ top: rect.bottom + 6, left: Math.min(rect.left, window.innerWidth - 240) });
    setOpen(value => !value);
  };
  return (
    <span className="crm-filter" ref={root}>
      <button
        type="button"
        className={`crm-head-button${active ? ' active' : ''}`}
        aria-label={`Фильтр: ${label}`}
        title={`Фильтр: ${label}`}
        aria-expanded={open}
        onClick={toggle}
      >
        <Filter size={12} />
      </button>
      {open && place && (
        <div className="crm-filter-menu" role="menu" style={place}>
          {children(() => setOpen(false))}
        </div>
      )}
    </span>
  );
}

export function FilterOption({
  checked,
  label,
  onClick,
}: {
  checked: boolean;
  label: ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitemcheckbox"
      aria-checked={checked}
      className={`crm-filter-option${checked ? ' checked' : ''}`}
      onClick={onClick}
    >
      <span className="crm-check" aria-hidden="true" />
      {label}
    </button>
  );
}
