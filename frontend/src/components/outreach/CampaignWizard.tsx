import { useState } from 'react';
import { ArrowLeft, ArrowRight, Check } from 'lucide-react';
import { api } from '../../services/api';
import { useResource } from '../../hooks/useResource';
import type {
  AudienceLead,
  AudienceQuery,
  CampaignPreview,
  FollowUpSequence,
  OutreachSender,
  OutreachTemplate,
} from '../../services/types';
import { number, statusLabels } from '../../lib/format';
import { Button } from '../ui/button';
import { categoryLabels, methodLabel } from '../scoutStatus';
import { localToIso, reasonLabel, senderStatusLabels } from './outreachText';

const steps = ['Название', 'Лиды', 'Отправители', 'Сообщение', 'Проверка'] as const;
const emptyQuery: AudienceQuery = {
  search: '',
  profile_types: [],
  min_followers: 0,
  max_followers: null,
  has_email: false,
  has_phone: false,
  statuses: [],
  source_username: '',
  discovery_method: '',
  min_confidence: 0,
  created_from: null,
  created_to: null,
  include_dnc: false,
  include_contacted: false,
  page: 1,
  page_size: 50,
};
const methods = ['post', 'reel', 'tagged', 'story', 'comment', 'followers', 'following'];

export function CampaignWizard({
  initialLeadIds,
  onCreated,
  onCancel,
}: {
  initialLeadIds?: number[];
  onCreated: (id: number) => void;
  onCancel: () => void;
}) {
  const [step, setStep] = useState(0);
  const [name, setName] = useState('');
  const [selected, setSelected] = useState<number[]>(initialLeadIds || []);
  const [query, setQuery] = useState<AudienceQuery>(emptyQuery);
  const [senderIds, setSenderIds] = useState<string[]>([]);
  const [templateId, setTemplateId] = useState<number>();
  const [sequenceId, setSequenceId] = useState<number>();
  const [scheduleLater, setScheduleLater] = useState(false);
  const [scheduledAt, setScheduledAt] = useState('');
  const [preview, setPreview] = useState<CampaignPreview>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const audience = useResource<{ total: number; ids: number[]; items: AudienceLead[] }>(
    'outreach.audience',
    query,
  );
  const senders = useResource<OutreachSender[]>('outreach.senders', {}, 5000);
  const templates = useResource<OutreachTemplate[]>('outreach.templates');
  const sequences = useResource<FollowUpSequence[]>('outreach.sequences');
  const enabledTemplates = (templates.data || []).filter(item => item.enabled);
  const template = enabledTemplates.find(item => item.id === templateId);
  const update = <K extends keyof AudienceQuery>(key: K, value: AudienceQuery[K]) =>
    setQuery(current => ({ ...current, page: 1, [key]: value }));
  const toggleIn = <T,>(list: T[], value: T) =>
    list.includes(value) ? list.filter(item => item !== value) : [...list, value];
  const items = audience.data?.items || [];
  const valid = [
    name.trim().length > 0,
    selected.length > 0,
    senderIds.length > 0,
    Boolean(template) && (!scheduleLater || Boolean(localToIso(scheduledAt))),
    Boolean(preview),
  ];

  const loadPreview = async () => {
    setBusy(true);
    setError('');
    try {
      setPreview(
        await api.request<CampaignPreview>('outreach.preview', {
          lead_ids: selected,
          sender_ids: senderIds,
          template_id: templateId,
          limit: 6,
        }),
      );
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };
  const next = () => {
    if (step === 3) void loadPreview();
    setStep(value => Math.min(value + 1, steps.length - 1));
  };
  const create = async () => {
    setBusy(true);
    setError('');
    try {
      const campaign = await api.request<{ id: number }>('outreach.campaign_create', {
        name: name.trim(),
        lead_ids: selected,
        sender_ids: senderIds,
        template_id: templateId,
        followup_sequence_id: sequenceId || null,
        scheduled_at: scheduleLater ? localToIso(scheduledAt) : null,
      });
      onCreated(campaign.id);
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="panel campaign-wizard">
      <div className="section-heading">
        <h2>Новая кампания</h2>
        <Button variant="outline" onClick={onCancel}>
          Отмена
        </Button>
      </div>
      <ol className="wizard-steps">
        {steps.map((label, index) => (
          <li key={label} className={index === step ? 'active' : index < step ? 'done' : ''}>
            <span>{index < step ? <Check size={12} /> : index + 1}</span> {label}
          </li>
        ))}
      </ol>

      {step === 0 && (
        <label className="wizard-field">
          Название кампании
          <input
            autoFocus
            maxLength={160}
            value={name}
            placeholder="Например, Artist Outreach September"
            onChange={event => setName(event.target.value)}
          />
        </label>
      )}

      {step === 1 && (
        <div className="wizard-body">
          <div className="filter-grid audience-filters">
            <label>
              Поиск
              <input value={query.search} onChange={event => update('search', event.target.value)} />
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
                value={query.max_followers ?? ''}
                onChange={event =>
                  update('max_followers', event.target.value ? Number(event.target.value) : null)
                }
              />
            </label>
            <label>
              Статус CRM
              <select
                value={query.statuses[0] || ''}
                onChange={event => update('statuses', event.target.value ? [event.target.value] : [])}
              >
                <option value="">Любой</option>
                {['new', 'reviewed', 'qualified', 'rejected'].map(status => (
                  <option key={status} value={status}>
                    {statusLabels[status]}
                  </option>
                ))}
              </select>
            </label>
            <label>
              SMM-источник
              <input
                placeholder="@паблик"
                value={query.source_username}
                onChange={event => update('source_username', event.target.value)}
              />
            </label>
            <label>
              Как найден
              <select
                value={query.discovery_method}
                onChange={event => update('discovery_method', event.target.value)}
              >
                <option value="">Любой способ</option>
                {methods.map(method => (
                  <option key={method} value={method}>
                    {methodLabel(method)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Уверенность типа от, %
              <input
                type="number"
                min={0}
                max={100}
                value={query.min_confidence}
                onChange={event => update('min_confidence', Number(event.target.value))}
              />
            </label>
            <label>
              Добавлен с
              <input
                type="date"
                value={query.created_from || ''}
                onChange={event => update('created_from', event.target.value || null)}
              />
            </label>
            <label>
              Добавлен по
              <input
                type="date"
                value={query.created_to || ''}
                onChange={event => update('created_to', event.target.value || null)}
              />
            </label>
          </div>
          <div className="check-rows">
            <fieldset className="check-row">
              <legend>Тип профиля</legend>
              {(['artist', 'producer', 'media'] as const).map(type => (
                <label className="inline-check" key={type}>
                  <input
                    type="checkbox"
                    checked={query.profile_types.includes(type)}
                    onChange={() => update('profile_types', toggleIn(query.profile_types, type))}
                  />
                  {categoryLabels[type]}
                </label>
              ))}
            </fieldset>
            <fieldset className="check-row">
              <legend>Контакты</legend>
              <label className="inline-check">
                <input
                  type="checkbox"
                  checked={query.has_email}
                  onChange={event => update('has_email', event.target.checked)}
                />
                Есть email
              </label>
              <label className="inline-check">
                <input
                  type="checkbox"
                  checked={query.has_phone}
                  onChange={event => update('has_phone', event.target.checked)}
                />
                Есть телефон
              </label>
            </fieldset>
          </div>
          <div className="audience-toolbar">
            <strong>Выбрано лидов: {number(selected.length)}</strong>
            <span className="helper">По фильтру: {number(audience.data?.total || 0)}</span>
            <Button
              variant="outline"
              disabled={!audience.data?.ids.length}
              onClick={() =>
                setSelected(current => Array.from(new Set([...current, ...(audience.data?.ids || [])])))
              }
            >
              Выбрать всех по фильтру
            </Button>
            <Button variant="outline" disabled={!selected.length} onClick={() => setSelected([])}>
              Снять выбор
            </Button>
          </div>
          <p className="helper">
            Лиды с отметкой «Не связываться» и те, кому уже писали, в список не попадают; при запуске они всё
            равно проверяются ещё раз.
          </p>
          <div className="table-container audience-table">
            <table>
              <thead>
                <tr>
                  <th>
                    <input
                      type="checkbox"
                      aria-label="Выбрать страницу"
                      checked={items.length > 0 && items.every(item => selected.includes(item.id))}
                      onChange={event =>
                        setSelected(current =>
                          event.target.checked
                            ? Array.from(new Set([...current, ...items.map(item => item.id)]))
                            : current.filter(id => !items.some(item => item.id === id)),
                        )
                      }
                    />
                  </th>
                  <th>Instagram</th>
                  <th>Тип</th>
                  <th>Подписчики</th>
                  <th>Контакты</th>
                  <th>Источник</th>
                  <th>Статус</th>
                </tr>
              </thead>
              <tbody>
                {items.map(item => (
                  <tr key={item.id}>
                    <td>
                      <input
                        type="checkbox"
                        aria-label={`Выбрать ${item.username}`}
                        checked={selected.includes(item.id)}
                        onChange={() => setSelected(current => toggleIn(current, item.id))}
                      />
                    </td>
                    <td>
                      <strong>@{item.username}</strong>
                      <small className="cell-sub">{item.display_name}</small>
                    </td>
                    <td>
                      {item.profile_type
                        ? `${categoryLabels[item.profile_type]} ${item.confidence ?? ''}%`
                        : '—'}
                    </td>
                    <td>{number(item.followers)}</td>
                    <td className="contact-cell">
                      {item.email || item.phone ? (
                        <>
                          {item.email && <span>{item.email}</span>}
                          {item.phone && <small>{item.phone}</small>}
                        </>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td>{item.source_username ? `@${item.source_username}` : '—'}</td>
                    <td>{statusLabels[item.status] || item.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!items.length && <p className="empty-copy">Нет лидов с такими условиями.</p>}
          </div>
          {(audience.data?.total || 0) > query.page_size && (
            <div className="pagination">
              <span>
                Страница {query.page} из {Math.ceil((audience.data?.total || 0) / query.page_size)}
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
                  disabled={query.page * query.page_size >= (audience.data?.total || 0)}
                  onClick={() => setQuery(current => ({ ...current, page: current.page + 1 }))}
                >
                  Далее
                </Button>
              </div>
            </div>
          )}
        </div>
      )}

      {step === 2 && (
        <div className="wizard-body">
          <p className="helper">
            Отправители — уже подключённые аккаунты из раздела «Аккаунты». Несколько аккаунтов делят
            получателей по очереди; лид, с которым уже есть диалог, остаётся за тем же аккаунтом.
          </p>
          {!senders.data?.length && <p className="empty-copy">Нет аккаунтов. Добавьте их в «Аккаунтах».</p>}
          <div className="sender-pick">
            {(senders.data || []).map(sender => (
              <label
                key={sender.id}
                className={`sender-option${senderIds.includes(sender.id) ? ' active' : ''}`}
              >
                <input
                  type="checkbox"
                  checked={senderIds.includes(sender.id)}
                  onChange={() => setSenderIds(current => toggleIn(current, sender.id))}
                />
                <span>
                  <strong>{sender.name}</strong>
                  <small>
                    {senderStatusLabels[sender.status]}
                    {sender.has_session ? '' : ' · нет сессии'}
                    {sender.open ? ' · окно открыто' : ' · окно закрыто'} · {sender.sent_24h}/
                    {sender.daily_limit} за 24 ч
                  </small>
                </span>
              </label>
            ))}
          </div>
          <p className="helper">
            {senderIds.length > 1
              ? `Несколько аккаунтов (${senderIds.length}): получатели распределяются по кругу.`
              : senderIds.length === 1
                ? 'Один аккаунт.'
                : 'Выберите хотя бы один аккаунт.'}
          </p>
        </div>
      )}

      {step === 3 && (
        <div className="wizard-body">
          <label className="wizard-field">
            Шаблон сообщения
            <select
              value={templateId || ''}
              onChange={event => setTemplateId(event.target.value ? Number(event.target.value) : undefined)}
            >
              <option value="">Выберите шаблон</option>
              {enabledTemplates.map(item => (
                <option key={item.id} value={item.id}>
                  {item.name}
                </option>
              ))}
            </select>
          </label>
          {!enabledTemplates.length && (
            <p className="helper">Шаблонов пока нет — создайте его на вкладке «Шаблоны».</p>
          )}
          {template && <pre className="template-body">{template.body}</pre>}
          <label className="wizard-field">
            Follow-up цепочка (необязательно)
            <select
              value={sequenceId || ''}
              onChange={event => setSequenceId(event.target.value ? Number(event.target.value) : undefined)}
            >
              <option value="">Без follow-up</option>
              {(sequences.data || [])
                .filter(item => item.enabled)
                .map(item => (
                  <option key={item.id} value={item.id}>
                    {item.name} · {item.steps.length} шаг.
                  </option>
                ))}
            </select>
          </label>
          <fieldset className="check-row">
            <legend>Когда начать</legend>
            <label className="inline-check">
              <input type="radio" checked={!scheduleLater} onChange={() => setScheduleLater(false)} />
              Сразу после нажатия «Запустить кампанию»
            </label>
            <label className="inline-check">
              <input type="radio" checked={scheduleLater} onChange={() => setScheduleLater(true)} />В
              указанное время
            </label>
            {scheduleLater && (
              <input
                type="datetime-local"
                aria-label="Время запуска"
                value={scheduledAt}
                onChange={event => setScheduledAt(event.target.value)}
              />
            )}
          </fieldset>
          <p className="helper">Время — по часам этого компьютера.</p>
        </div>
      )}

      {step === 4 && (
        <div className="wizard-body">
          {!preview && busy && <p className="helper">Проверяем аудиторию…</p>}
          {preview && <PreviewSummary preview={preview} />}
          <p className="helper">
            Создание кампании ничего не отправляет. Отправка начнётся только после кнопки «Запустить кампанию»
            на странице кампании.
          </p>
        </div>
      )}

      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      <div className="wizard-actions">
        <Button
          variant="outline"
          disabled={step === 0 || busy}
          onClick={() => {
            setPreview(undefined);
            setStep(value => value - 1);
          }}
        >
          <ArrowLeft size={15} /> Назад
        </Button>
        {step < steps.length - 1 ? (
          <Button disabled={!valid[step]} onClick={next}>
            Далее <ArrowRight size={15} />
          </Button>
        ) : (
          <Button disabled={busy || !preview} onClick={() => void create()}>
            Создать кампанию
          </Button>
        )}
      </div>
    </section>
  );
}

export function PreviewSummary({ preview }: { preview: CampaignPreview }) {
  return (
    <>
      <dl className="run-stats">
        <div>
          <dt>Получателей</dt>
          <dd>{number(preview.total)}</dd>
        </div>
        <div>
          <dt>Пройдут проверку</dt>
          <dd className="good">{number(preview.eligible)}</dd>
        </div>
        <div>
          <dt>Аккаунтов</dt>
          <dd>
            {preview.active_accounts} / {preview.accounts}
          </dd>
        </div>
        <div>
          <dt>Будет в очереди</dt>
          <dd>{number(preview.estimated_queued)}</dd>
        </div>
      </dl>
      {Object.keys(preview.skipped).length > 0 && (
        <ul className="skip-breakdown">
          {Object.entries(preview.skipped).map(([reason, count]) => (
            <li key={reason}>
              {reasonLabel(reason)} <b>{count}</b>
            </li>
          ))}
        </ul>
      )}
      {preview.active_accounts === 0 && (
        <p className="error-text">Нет активных аккаунтов: запустить кампанию не получится.</p>
      )}
      <h3>Примеры сообщений</h3>
      <div className="preview-list">
        {preview.examples.map(example => (
          <article key={example.lead_id} className="preview-card">
            <header>
              <span>
                От <strong>{example.sender_name}</strong>
              </span>
              <span>
                Кому <strong>@{example.username}</strong>
              </span>
            </header>
            <p>{example.message}</p>
            {example.fallbacks.length > 0 && (
              <small className="helper">
                Без данных, подставлено по умолчанию: {example.fallbacks.join(', ')}
              </small>
            )}
          </article>
        ))}
        {!preview.examples.length && <p className="empty-copy">Нет получателей, прошедших проверку.</p>}
      </div>
    </>
  );
}
