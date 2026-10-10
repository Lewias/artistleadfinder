import { ErrorToast } from '../Toaster';
import { useEffect, useState } from 'react';
import { api } from '../../services/api';
import { useResource } from '../../hooks/useResource';
import type { OutreachSender, OutreachTemplate, OutreachWorkspace } from '../../services/types';
import { statusLabels } from '../../lib/format';
import { Button } from '../ui/button';
import { Modal } from '../Modal';
import { StatusBadge } from '../DataState';
import { dateTime, senderStatusLabels } from './outreachText';

import { errorText } from '../../lib/errors';

const crmStatuses = ['new', 'reviewed', 'qualified', 'contacted', 'rejected'] as const;

const instagramCrmNote =
  'Все лиды из «Базы артистов» с выбранными статусами попадут в список. Отмеченные «Не связываться» не ' +
  'добавляются; тем, кому уже писали, рассылка повторно не отправит.';

/** Adds every lead with the chosen CRM statuses to the list (never «Не связываться»). */
export function CrmModal({
  onClose,
  onAdded,
  method = 'outreach.workspace_add_leads',
  note = instagramCrmNote,
}: {
  onClose: () => void;
  onAdded: (added: number) => void;
  /** The list to add to: the Instagram list by default, or the iMessage one. */
  method?: string;
  note?: string;
}) {
  const [chosen, setChosen] = useState<string[]>(['qualified']);
  const [error, setError] = useState('');
  const add = async () => {
    try {
      const result = await api.request<{ added: number }>(method, {
        statuses: chosen,
      });
      onAdded(result.added);
      onClose();
    } catch (err) {
      setError(errorText(err));
    }
  };
  return (
    <Modal title="Добавить из CRM" onClose={onClose}>
      <p className="helper">{note}</p>
      <div className="check-rows">
        {crmStatuses.map(status => (
          <label className="check-row" key={status}>
            <input
              type="checkbox"
              checked={chosen.includes(status)}
              onChange={event =>
                setChosen(event.target.checked ? [...chosen, status] : chosen.filter(item => item !== status))
              }
            />
            {statusLabels[status]}
          </label>
        ))}
      </div>
      <ErrorToast message={error} />
      <div className="actions">
        <Button disabled={!chosen.length} onClick={() => void add()}>
          Добавить
        </Button>
        <Button variant="outline" onClick={onClose}>
          Отмена
        </Button>
      </div>
    </Modal>
  );
}

/** Saved message texts: add them to the list, save the list's messages, remove. */
export function TemplatesModal({
  messages,
  onAdd,
  onClose,
  hint,
}: {
  messages: string[];
  onAdd: (texts: string[]) => void;
  onClose: () => void;
  /** Placeholders the channel fills in; Instagram's by default. */
  hint?: string;
}) {
  const templates = useResource<OutreachTemplate[]>('outreach.templates');
  const [error, setError] = useState('');
  const enabled = (templates.data ?? []).filter(item => item.enabled);
  const saved = new Set(enabled.map(item => item.body));
  const unsaved = messages.filter(text => !saved.has(text));
  const run = async (operation: () => Promise<unknown>) => {
    setError('');
    try {
      await operation();
      templates.refresh();
    } catch (err) {
      setError(errorText(err));
    }
  };
  const saveAll = () =>
    run(async () => {
      for (const body of unsaved) {
        await api.request('outreach.template_save', { name: body.slice(0, 60), body });
      }
    });
  return (
    <Modal title="Шаблоны сообщений" onClose={onClose}>
      <p className="helper">
        {hint ??
          'Переменные: {{firstName}}, {{username}}, {{artistName}}, {{followers}}. Пустое имя заменяется на «there».'}
      </p>
      <div className="actions">
        <Button variant="outline" disabled={!unsaved.length} onClick={() => void saveAll()}>
          Сохранить сообщения списка ({unsaved.length})
        </Button>
        <Button
          disabled={!enabled.some(item => !messages.includes(item.body))}
          onClick={() => {
            onAdd(enabled.map(item => item.body));
            onClose();
          }}
        >
          Добавить все
        </Button>
      </div>
      <ErrorToast message={error} />
      <div className="template-pick">
        {enabled.map(item => (
          <div className="message-card" key={item.id}>
            <p>{item.body}</p>
            <div className="actions">
              <Button
                variant="outline"
                disabled={messages.includes(item.body)}
                onClick={() => onAdd([item.body])}
              >
                {messages.includes(item.body) ? 'В списке' : 'Добавить'}
              </Button>
              <Button
                variant="outline"
                onClick={() =>
                  void run(() => api.request('outreach.template_save', { ...item, enabled: false }))
                }
              >
                Удалить
              </Button>
            </div>
          </div>
        ))}
        {templates.data && !enabled.length && (
          <p className="empty-copy">Сохранённых шаблонов нет. Сохраните сообщения из списка кнопкой выше.</p>
        )}
      </div>
    </Modal>
  );
}

type Settings = Record<string, unknown>;
const pacing = [
  ['outreach_send_interval_seconds', 'Пауза между сообщениями одного аккаунта, с', 30, 3600],
  ['outreach_daily_limit_per_sender', 'Сообщений с аккаунта за 24 часа', 1, 200],
] as const;
const inboxPacing = [
  ['inbox_max_threads', 'Чатов за одно чтение', 1, 1000],
  ['inbox_delay_min_seconds', 'Пауза между чатами от, с', 3, 300],
  ['inbox_delay_max_seconds', 'Пауза между чатами до, с', 3, 600],
] as const;
const regions = [
  ['US', 'США'],
  ['CA', 'Канада'],
  ['GB', 'Великобритания'],
  ['AU', 'Австралия'],
  ['DE', 'Германия'],
  ['FR', 'Франция'],
  ['BR', 'Бразилия'],
  ['NG', 'Нигерия'],
  ['ZA', 'ЮАР'],
  ['RU', 'Россия'],
  ['UA', 'Украина'],
  ['KZ', 'Казахстан'],
] as const;

/** The accounts that parse and write, their health, and the outreach and Direct pacing. */
export function OutreachSettingsPanel() {
  const workspace = useResource<OutreachWorkspace>('outreach.workspace');
  const senders = useResource<OutreachSender[]>('outreach.senders', {}, 5000);
  const [selected, setSelected] = useState<string[]>([]);
  useEffect(() => {
    if (workspace.data) setSelected(workspace.data.sender_ids);
  }, [workspace.data]);
  const settings = useResource<Settings>('settings.get');
  const [form, setForm] = useState<Settings>();
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const onSelect = async (ids: string[]) => {
    setError('');
    try {
      const next = await api.request<OutreachWorkspace>('outreach.workspace_update', { sender_ids: ids });
      setSelected(next.sender_ids);
    } catch (err) {
      setError(errorText(err));
    }
  };
  useEffect(() => {
    if (settings.data) setForm(settings.data);
  }, [settings.data]);
  const status = async (id: string, value: string) => {
    setError('');
    try {
      await api.request('outreach.sender_status', { id, status: value });
      senders.refresh();
    } catch (err) {
      setError(errorText(err));
    }
  };
  const saveSettings = async () => {
    setError('');
    try {
      await api.request('settings.save', form ?? {});
      setMessage('Сохранено.');
    } catch (err) {
      setError(errorText(err));
    }
  };
  return (
    <div className="settings-panel">
      <h3>Аккаунты</h3>
      <p className="helper">
        Отмеченные аккаунты ищут артистов и пишут им; несколько — по очереди. Окно аккаунта должно быть
        открыто на instagram.com. При входе, checkpoint или ограничении Instagram аккаунт останавливается,
        сообщения ждут — на другие аккаунты они не переносятся.
      </p>
      <div className="sender-pick">
        {(senders.data ?? []).map(sender => (
          <div className="sender-option" key={sender.id}>
            <label className="check-row">
              <input
                type="checkbox"
                checked={selected.includes(sender.id)}
                onChange={event =>
                  void onSelect(
                    event.target.checked
                      ? [...selected, sender.id]
                      : selected.filter(item => item !== sender.id),
                  )
                }
              />
              <strong>{sender.name}</strong>
            </label>
            <span className="sender-meta">
              <StatusBadge
                value={
                  sender.status === 'active' ? 'completed' : sender.status === 'paused' ? 'paused' : 'failed'
                }
                label={senderStatusLabels[sender.status]}
              />
              <span>{sender.open ? 'Окно открыто' : 'Окно закрыто'}</span>
              <span>
                {sender.sent_24h} / {sender.daily_limit} за 24 ч
              </span>
              {sender.until && <span>до {dateTime(sender.until)}</span>}
            </span>
            {sender.reason && sender.status !== 'active' && <small className="helper">{sender.reason}</small>}
            <span>
              {sender.status === 'active' ? (
                <Button variant="outline" onClick={() => void status(sender.id, 'paused')}>
                  Пауза
                </Button>
              ) : (
                <Button variant="outline" onClick={() => void status(sender.id, 'active')}>
                  Возобновить
                </Button>
              )}
            </span>
          </div>
        ))}
        {senders.data && !senders.data.length && (
          <p className="empty-copy">Нет аккаунтов. Добавьте их в разделе «Аккаунты».</p>
        )}
      </div>
      <h3>Темп</h3>
      {form && (
        <div className="modal-fields">
          {pacing.map(([key, label, min, max]) => (
            <label key={key}>
              <span>{label}</span>
              <input
                type="number"
                min={min}
                max={max}
                value={Number(form[key] ?? min)}
                onChange={event => setForm({ ...form, [key]: Number(event.target.value) })}
              />
            </label>
          ))}
          <label className="check-row">
            <input
              type="checkbox"
              checked={!!form.outreach_skip_previously_contacted}
              onChange={event =>
                setForm({ ...form, outreach_skip_previously_contacted: event.target.checked })
              }
            />
            Пропускать лидов, которым уже писали (из любых рассылок)
          </label>
        </div>
      )}
      <h3>Чтение Директа («Ответы»)</h3>
      <p className="helper">
        Номер без кода страны, например (312) 555-0199, читается как номер этой страны и помечается «код
        угадан».
      </p>
      {form && (
        <div className="modal-fields">
          <label>
            <span>Страна номеров без кода</span>
            <select
              value={String(form.inbox_region ?? 'US')}
              onChange={event => setForm({ ...form, inbox_region: event.target.value })}
            >
              {regions.map(([code, name]) => (
                <option key={code} value={code}>
                  {name}
                </option>
              ))}
            </select>
          </label>
          {inboxPacing.map(([key, label, min, max]) => (
            <label key={key}>
              <span>{label}</span>
              <input
                type="number"
                min={min}
                max={max}
                value={Number(form[key] ?? min)}
                onChange={event => setForm({ ...form, [key]: Number(event.target.value) })}
              />
            </label>
          ))}
        </div>
      )}
      <ErrorToast message={error} />
      {message && !error && <p className="helper">{message}</p>}
      <div className="actions">
        <Button disabled={!form} onClick={() => void saveSettings()}>
          Сохранить настройки
        </Button>
      </div>
    </div>
  );
}
