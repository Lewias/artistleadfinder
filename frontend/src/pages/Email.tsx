import { useEffect, useState } from 'react';
import { open } from '@tauri-apps/plugin-dialog';
import {
  Eye,
  FileUp,
  Inbox,
  KeyRound,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Save,
  Send,
  Square,
  Trash2,
  Users,
  X,
} from 'lucide-react';
import { useResource } from '../hooks/useResource';
import type {
  EmailAccount,
  EmailAccountStatus,
  EmailFollowup,
  EmailPreview,
  EmailState,
  EmailWorkspace,
} from '../services/types';
import { number, relativeTime } from '../lib/format';
import { PageHeader } from '../components/PageHeader';
import { Button } from '../components/ui/button';
import { StatusBadge } from '../components/DataState';
import { ErrorToast } from '../components/Toaster';
import { Modal } from '../components/Modal';
import { time, useCall } from '../components/email/emailUi';

const ACCOUNT_BADGES: Record<EmailAccountStatus, string> = {
  active: 'running',
  paused: 'paused',
  auth_failed: 'failed',
  limited: 'paused',
  bounces: 'failed',
};
const JOB_BADGES: Record<string, string> = {
  pending: 'queued',
  sending: 'sending',
  sent: 'sent',
  failed: 'failed',
  skipped: 'skipped',
  uncertain: 'failed',
  cancelled: 'cancelled',
  replied: 'completed',
  bounced: 'failed',
};
const CAMPAIGN_LABELS: Record<string, [string, string]> = {
  running: ['running', 'Идёт'],
  paused: ['paused', 'На паузе'],
  stopped: ['cancelled', 'Остановлена'],
  finished: ['completed', 'Завершена'],
};
const ZONE_LABELS: Record<string, string> = {
  'America/New_York': 'США, восток (Нью-Йорк)',
  'America/Chicago': 'США, центр (Чикаго)',
  'America/Denver': 'США, горы (Денвер)',
  'America/Los_Angeles': 'США, запад (Лос-Анджелес)',
  'Europe/London': 'Великобритания (Лондон)',
  'Europe/Berlin': 'Европа (Берлин)',
  'Europe/Moscow': 'Москва',
};
const VARIABLES = '{{firstName}} {{artistName}} {{username}} {{followers}}';

const lines = (text: string) =>
  text
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean);
/** The letter as the form edits it: subjects as lines, the texts and follow-ups as fields. */
interface Draft {
  subjects: string;
  bodies: string[];
  followups: EmailFollowup[];
  signature: string;
  interval_min_seconds: number;
  interval_max_seconds: number;
  window_zone: string;
  window_start_hour: number;
  window_end_hour: number;
  skip_previously_emailed: boolean;
}
const draftOf = (workspace: EmailWorkspace): Draft => ({
  subjects: workspace.subjects.join('\n'),
  bodies: workspace.bodies.length ? workspace.bodies : [''],
  followups: workspace.followups,
  signature: workspace.signature,
  interval_min_seconds: workspace.interval_min_seconds,
  interval_max_seconds: workspace.interval_max_seconds,
  window_zone: workspace.window_zone,
  window_start_hour: workspace.window_start_hour,
  window_end_hour: workspace.window_end_hour,
  skip_previously_emailed: workspace.skip_previously_emailed,
});
const paramsOf = (draft: Draft) => ({
  ...draft,
  subjects: lines(draft.subjects),
  bodies: draft.bodies.filter(body => body.trim()),
});

function LetterForm({
  workspace,
  zones,
  onSaved,
}: {
  workspace: EmailWorkspace;
  zones: string[];
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => draftOf(workspace));
  const [dirty, setDirty] = useState(false);
  const [preview, setPreview] = useState<EmailPreview>();
  const { call, error, notice, setNotice } = useCall(onSaved);
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => {
    setDirty(true);
    setDraft(current => ({ ...current, [key]: value }));
  };
  const save = async () => {
    const result = await call('email.workspace_update', paramsOf(draft));
    if (result) {
      setDirty(false);
      setNotice('Сохранено');
    }
    return result;
  };
  const showPreview = async () => {
    if (dirty && !(await save())) return;
    const result = await call<EmailPreview>('email.preview', { index: 0 });
    if (result) setPreview(result);
  };
  const setBody = (index: number, value: string) =>
    set(
      'bodies',
      draft.bodies.map((body, at) => (at === index ? value : body)),
    );
  const setFollowup = (index: number, value: Partial<EmailFollowup>) =>
    set(
      'followups',
      draft.followups.map((item, at) => (at === index ? { ...item, ...value } : item)),
    );
  const hours = Array.from({ length: 25 }, (_, hour) => hour);
  return (
    <section className="panel board cloud-form">
      <div className="board-heading">
        <h2>Письмо</h2>
        <span className="helper">
          Переменные: {VARIABLES}. Своё значение по умолчанию: {'{{firstName|friend}}'}.
        </span>
      </div>
      <p className="helper">
        Чтобы письма не уходили в спам: коротко, как от человека, без ссылок и вложений в первом письме, без
        слов вроде «free», «$$$», «guaranteed». Ссылку на биты отправляйте после ответа.
      </p>
      <div className="cloud-grid">
        <label className="cloud-field">
          <span>Темы — по одной на строку, раздаются по очереди</span>
          <textarea
            className="board-bulk"
            rows={4}
            placeholder={'quick q {{firstName}}\nyour last drop'}
            value={draft.subjects}
            onChange={event => set('subjects', event.target.value)}
          />
          <small>{lines(draft.subjects).length} из 10</small>
        </label>
        <label className="cloud-field">
          <span>Подпись — добавляется под каждым письмом</span>
          <textarea
            className="board-bulk"
            rows={4}
            placeholder="matt / ifeelthisbounce"
            value={draft.signature}
            onChange={event => set('signature', event.target.value)}
          />
        </label>
      </div>
      {draft.bodies.map((body, index) => (
        <label className="cloud-field" key={`body-${index}`}>
          <span>
            Текст, вариант {index + 1}
            {draft.bodies.length > 1 && (
              <Button
                variant="outline"
                icon
                aria-label="Убрать вариант"
                onClick={() =>
                  set(
                    'bodies',
                    draft.bodies.filter((_, at) => at !== index),
                  )
                }
              >
                <X size={13} />
              </Button>
            )}
          </span>
          <textarea
            className="board-bulk"
            rows={5}
            placeholder={'yo {{firstName}}\n\nheard your last track, the vocals sit crazy on that bounce...'}
            value={body}
            onChange={event => setBody(index, event.target.value)}
          />
          <small>{body.length} из 4000</small>
        </label>
      ))}
      {draft.bodies.length < 10 && (
        <div>
          <Button variant="outline" onClick={() => set('bodies', [...draft.bodies, ''])}>
            <Plus size={14} /> Ещё вариант текста
          </Button>
        </div>
      )}
      <fieldset>
        <legend>Фоллоу-апы — в той же переписке, пока нет ответа</legend>
        <div className="cloud-stack">
          {draft.followups.map((item, index) => (
            <div className="cloud-field" key={`followup-${index}`}>
              <span>
                Фоллоу-ап {index + 1}: через{' '}
                <input
                  type="number"
                  min={1}
                  max={30}
                  value={item.days}
                  onChange={event => setFollowup(index, { days: Number(event.target.value) })}
                  style={{ width: 64 }}
                />{' '}
                дн. после прошлого письма
                <Button
                  variant="outline"
                  icon
                  aria-label="Убрать фоллоу-ап"
                  onClick={() =>
                    set(
                      'followups',
                      draft.followups.filter((_, at) => at !== index),
                    )
                  }
                >
                  <X size={13} />
                </Button>
              </span>
              <textarea
                className="board-bulk"
                rows={3}
                placeholder="just bumping this {{firstName}}"
                value={item.body}
                onChange={event => setFollowup(index, { body: event.target.value })}
              />
            </div>
          ))}
          {draft.followups.length < 3 && (
            <div>
              <Button
                variant="outline"
                onClick={() =>
                  set('followups', [...draft.followups, { days: draft.followups.length ? 4 : 3, body: '' }])
                }
              >
                <Plus size={14} /> Добавить фоллоу-ап
              </Button>
            </div>
          )}
        </div>
      </fieldset>
      <div className="cloud-grid">
        <fieldset>
          <legend>Темп</legend>
          <div className="cloud-stack">
            <label>
              <span>Пауза между письмами одного ящика — от, сек</span>
              <input
                type="number"
                min={60}
                max={3600}
                value={draft.interval_min_seconds}
                onChange={event => set('interval_min_seconds', Number(event.target.value))}
              />
            </label>
            <label>
              <span>— до, сек (каждый раз случайно в этих пределах)</span>
              <input
                type="number"
                min={60}
                max={3600}
                value={draft.interval_max_seconds}
                onChange={event => set('interval_max_seconds', Number(event.target.value))}
              />
            </label>
            <label className="check-row">
              <input
                type="checkbox"
                checked={draft.skip_previously_emailed}
                onChange={event => set('skip_previously_emailed', event.target.checked)}
              />
              <span>Не писать тем, кому уже писали с любого ящика</span>
            </label>
          </div>
        </fieldset>
        <fieldset>
          <legend>Часы отправки — по времени получателей</legend>
          <div className="cloud-stack">
            <label>
              <span>Часовой пояс</span>
              <select value={draft.window_zone} onChange={event => set('window_zone', event.target.value)}>
                {zones.map(zone => (
                  <option key={zone} value={zone}>
                    {ZONE_LABELS[zone] ?? zone}
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>С</span>
              <select
                value={draft.window_start_hour}
                onChange={event => set('window_start_hour', Number(event.target.value))}
              >
                {hours.slice(0, 24).map(hour => (
                  <option key={hour} value={hour}>
                    {hour}:00
                  </option>
                ))}
              </select>
            </label>
            <label>
              <span>До</span>
              <select
                value={draft.window_end_hour}
                onChange={event => set('window_end_hour', Number(event.target.value))}
              >
                {hours.slice(1).map(hour => (
                  <option key={hour} value={hour}>
                    {hour}:00
                  </option>
                ))}
              </select>
            </label>
            <small className="helper">
              {workspace.window_open_now
                ? 'Сейчас время отправки.'
                : 'Сейчас не время отправки: письма ждут.'}
            </small>
          </div>
        </fieldset>
      </div>
      <ErrorToast message={error} />
      <div className="board-footer">
        {notice && !dirty && <span className="helper">{notice}</span>}
        {dirty && <span className="helper">Есть несохранённые изменения</span>}
        <Button variant="outline" onClick={() => void showPreview()}>
          <Eye size={15} /> Как увидит получатель
        </Button>
        <Button onClick={() => void save()}>
          <Save size={15} /> Сохранить
        </Button>
      </div>
      {preview && (
        <Modal title="Предпросмотр письма" onClose={() => setPreview(undefined)} wide>
          <p className="helper">Кому: {preview.to}</p>
          <p>
            <b>{preview.subject}</b>
          </p>
          <pre className="email-preview">{preview.body}</pre>
          {preview.followups.map((item, index) => (
            <div key={index}>
              <p className="helper">
                Фоллоу-ап {index + 1}, через {item.days} дн.
              </p>
              <pre className="email-preview">{item.body}</pre>
            </div>
          ))}
        </Modal>
      )}
    </section>
  );
}

function Recipients({ state, onChange }: { state: EmailState; onChange: () => void }) {
  const [text, setText] = useState('');
  const { call, error, notice, setNotice } = useCall(onChange);
  const recipients = state.workspace.recipients;
  const added = (result?: { added: number; skipped: number }) => {
    if (!result) return;
    setNotice(
      `Добавлено ${number(result.added)}` +
        (result.skipped
          ? `, пропущено ${number(result.skipped)}: уже писали, ответили или в стоп-листе`
          : ''),
    );
  };
  const paste = async () => {
    const result = await call<{ added: number; skipped: number }>('email.add_text', { text });
    if (result) setText('');
    added(result);
  };
  const importFile = async () => {
    const path = await open({
      title: 'Файл с адресами',
      multiple: false,
      filters: [{ name: 'Таблица', extensions: ['csv', 'xlsx', 'txt'] }],
    });
    if (typeof path === 'string')
      added(await call<{ added: number; skipped: number }>('email.import_file', { path }));
  };
  const remove = (email: string) =>
    void call('email.workspace_update', { recipients: recipients.filter(item => item.email !== email) });
  return (
    <section className="panel board cloud-form">
      <div className="board-heading">
        <h2>
          Получатели{' '}
          {recipients.length > 0 && <span className="page-count">{number(recipients.length)}</span>}
        </h2>
        <span className="helper">Повторы, стоп-лист и тех, кто уже ответил, приложение убирает само.</span>
      </div>
      <div className="cloud-grid">
        <label className="cloud-field">
          <span>Вставьте адреса — по одному на строку, можно «email, Имя»</span>
          <textarea
            className="board-bulk"
            rows={5}
            placeholder={'jay@gmail.com, Jay Carter\nmia.rose@outlook.com'}
            value={text}
            onChange={event => setText(event.target.value)}
          />
        </label>
        <div className="cloud-stack">
          <Button disabled={!text.trim()} onClick={() => void paste()}>
            <Plus size={15} /> Добавить из текста
          </Button>
          <Button variant="outline" onClick={() => void importFile()}>
            <FileUp size={15} /> Импорт CSV / XLSX
          </Button>
          <Button
            variant="outline"
            onClick={() =>
              void call<{ added: number; skipped: number }>('email.add_leads', { all: true }).then(added)
            }
          >
            <Users size={15} /> Все артисты с email из «Базы»
          </Button>
          {recipients.length > 0 && (
            <Button variant="outline" onClick={() => void call('email.workspace_update', { recipients: [] })}>
              <Trash2 size={15} /> Очистить список
            </Button>
          )}
          <small className="helper">
            В файле нужна колонка «email», имя — из колонки «name» или «имя». Email из био Instagram парсер
            собирает сам.
          </small>
        </div>
      </div>
      {notice && <p className="helper">{notice}</p>}
      <ErrorToast message={error} />
      {recipients.length > 0 && (
        <div className="crm-table cloud-table email-recipients">
          <table>
            <thead>
              <tr>
                <th>Email</th>
                <th>Имя</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {recipients.slice(0, 300).map(item => (
                <tr key={item.email}>
                  <td>{item.email}</td>
                  <td>{item.name || '—'}</td>
                  <td>
                    <Button
                      variant="outline"
                      icon
                      aria-label={`Убрать ${item.email}`}
                      onClick={() => remove(item.email)}
                    >
                      <X size={13} />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {recipients.length > 300 && <p className="helper">И ещё {number(recipients.length - 300)}.</p>}
        </div>
      )}
    </section>
  );
}

function Campaigns({ state, onChange }: { state: EmailState; onChange: () => void }) {
  const { call, error } = useCall(onChange);
  if (!state.campaigns.length) return null;
  return (
    <section className="panel cloud-detail">
      <div className="board-heading">
        <h2>Идущие кампании</h2>
      </div>
      <ErrorToast message={error} />
      {state.campaigns.map(campaign => {
        const label = CAMPAIGN_LABELS[campaign.status];
        const jobs = state.jobs.filter(job => job.campaign_id === campaign.id);
        return (
          <div key={campaign.id} className="email-campaign">
            <div className="board-heading">
              <h3>
                Кампания №{campaign.id} <StatusBadge value={label[0]} label={label[1]} />
              </h3>
              <span className="helper">
                {campaign.window} · запущена {relativeTime(campaign.created_at, '')}
              </span>
            </div>
            <div className="cloud-counters">
              <div>
                <span>Отправлено</span>
                <b>
                  {number(campaign.sent)} из {number(campaign.total)}
                </b>
              </div>
              <div>
                <span>Фоллоу-апов</span>
                <b>{number(campaign.followups_sent)}</b>
              </div>
              <div>
                <span>Ответили</span>
                <b>{number(campaign.replies)}</b>
              </div>
              <div>
                <span>Возвраты</span>
                <b>{number(campaign.bounces)}</b>
              </div>
              <div>
                <span>Ошибки / пропуски</span>
                <b>
                  {number(campaign.failed)} / {number(campaign.skipped)}
                </b>
              </div>
            </div>
            <div className="board-footer">
              {campaign.status === 'running' ? (
                <Button
                  variant="outline"
                  onClick={() => void call('email.control', { campaign_id: campaign.id, action: 'pause' })}
                >
                  <Pause size={15} /> Пауза
                </Button>
              ) : (
                <Button
                  variant="outline"
                  onClick={() => void call('email.control', { campaign_id: campaign.id, action: 'resume' })}
                >
                  <Play size={15} /> Продолжить
                </Button>
              )}
              <Button
                variant="danger"
                onClick={() => void call('email.control', { campaign_id: campaign.id, action: 'stop' })}
              >
                <Square size={15} /> Остановить
              </Button>
            </div>
            <div className="crm-table cloud-table">
              <table>
                <thead>
                  <tr>
                    <th>Кому</th>
                    <th>Письмо</th>
                    <th>Статус</th>
                    <th>Когда</th>
                    <th>Примечание</th>
                  </tr>
                </thead>
                <tbody>
                  {jobs.map(job => (
                    <tr key={job.id}>
                      <td>
                        {job.email}
                        {job.name && <small>{job.name}</small>}
                      </td>
                      <td>{job.step ? `Фоллоу-ап ${job.step}` : 'Первое'}</td>
                      <td>
                        <StatusBadge value={JOB_BADGES[job.status] ?? 'pending'} label={job.label} />
                      </td>
                      <td>
                        {job.sent_at ? time(job.sent_at) : job.due_at ? `не раньше ${time(job.due_at)}` : '—'}
                      </td>
                      <td>{job.note || ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      })}
    </section>
  );
}

/** «Email» → «Кампании»: who gets the email, the letter, and the campaigns going on. */
export function EmailCampaigns({ onAccounts }: { onAccounts: () => void }) {
  const [poll, setPoll] = useState(15000);
  const state = useResource<EmailState>('email.state', {}, poll);
  const { call, error, notice, setNotice } = useCall(state.refresh);
  const data = state.data;
  const live = !!data?.campaigns.some(item => item.status === 'running') || !!data?.inbox_pending;
  useEffect(() => setPoll(live ? 5000 : 15000), [live]);

  if (!data) return <div className="screen">{state.error || 'Загрузка…'}</div>;
  const working = data.accounts.filter(item => item.status === 'active');
  const capacity = working.reduce((sum, item) => sum + Math.max(0, item.cap_today - item.sent_24h), 0);
  const start = async () => {
    const result = await call<{ queued: number; skipped: { email: string; reason: string }[] }>(
      'email.start',
    );
    if (result)
      setNotice(
        `В очереди ${number(result.queued)}` +
          (result.skipped.length ? `, пропущено ${number(result.skipped.length)}` : '') +
          `. Сегодня ящики могут отправить ещё ${number(capacity)}: остальное уйдёт в следующие дни.`,
      );
  };
  const checkInbox = async () => {
    if (await call('email.check_inbox')) setNotice('Проверяю почту: ответы появятся здесь через минуту.');
  };
  const inbox = data.inbox;
  const stats = data.stats;
  const rate = stats.sent_total ? Math.round((stats.replies_total / stats.sent_total) * 1000) / 10 : 0;
  return (
    <div className="screen work-flow">
      <PageHeader
        page="email"
        count={`${number(stats.sent_24h)} за 24 ч`}
        actions={
          <Button variant="outline" disabled={data.inbox_pending} onClick={() => void checkInbox()}>
            <Inbox size={15} /> {data.inbox_pending ? 'Проверяю…' : 'Проверить ответы'}
          </Button>
        }
      />
      {!data.accounts.length && (
        <p className="board-warning">
          Сначала добавьте Gmail в «Ящики».{' '}
          <Button variant="outline" onClick={onAccounts}>
            Открыть «Ящики»
          </Button>
        </p>
      )}
      <section className="panel board">
        {inbox && (
          <p className={inbox.errors.length ? 'board-warning' : 'helper'}>
            Почта проверена {relativeTime(inbox.at, '')}: новых ответов {inbox.replies}, возвратов{' '}
            {inbox.bounces}, отписок {inbox.unsubscribed}
            {inbox.errors.length ? `. Ошибки: ${inbox.errors.join('; ')}` : ''}
          </p>
        )}
        <div className="cloud-counters">
          <div>
            <span>Писем отправлено</span>
            <b>{number(stats.sent_total)}</b>
          </div>
          <div>
            <span>Ответили</span>
            <b>
              {number(stats.replies_total)} <small>{rate}%</small>
            </b>
          </div>
          <div>
            <span>Возвраты</span>
            <b>{number(stats.bounces_total)}</b>
          </div>
          <div>
            <span>Стоп-лист</span>
            <b>{number(stats.suppressed)}</b>
          </div>
          <div>
            <span>Можно отправить сегодня</span>
            <b>{number(capacity)}</b>
          </div>
        </div>
      </section>
      <Recipients state={data} onChange={state.refresh} />
      <LetterForm
        key={JSON.stringify({ ...data.workspace, recipients: [], window_open_now: false })}
        workspace={data.workspace}
        zones={data.zones}
        onSaved={state.refresh}
      />
      <section className="panel board">
        <ErrorToast message={error || state.error} />
        {notice && <p className="helper">{notice}</p>}
        <div className="board-footer">
          <span className="helper">
            Сохраните письмо перед запуском. Список после запуска очищается, адреса уходят в очередь.
          </span>
          <Button
            className="autopilot-go"
            disabled={!data.workspace.recipients.length || !working.length}
            onClick={() => void start()}
          >
            <Send size={16} /> Запустить рассылку ({number(data.workspace.recipients.length)})
          </Button>
        </div>
      </section>
      <Campaigns state={data} onChange={state.refresh} />
    </div>
  );
}

function AccountRow({ account, onChange }: { account: EmailAccount; onChange: () => void }) {
  const { call, error, notice, setNotice } = useCall(onChange);
  const [password, setPassword] = useState<string>();
  const control = (action: string) => call('email.account_control', { id: account.id, action });
  return (
    <tr>
      <td>
        {account.address}
        {account.display_name && <small>{account.display_name}</small>}
      </td>
      <td>
        <StatusBadge value={ACCOUNT_BADGES[account.status]} label={account.status_label} />
        {account.reason && <small>{account.reason}</small>}
        {error && <small className="board-warning">{error}</small>}
        {notice && <small>{notice}</small>}
      </td>
      <td>
        {number(account.sent_24h)} / {number(account.cap_today)}
        {account.warmup && account.cap_today < account.daily_limit && <small>разгон</small>}
      </td>
      <td>
        <input
          type="number"
          min={1}
          max={50}
          defaultValue={account.daily_limit}
          style={{ width: 70 }}
          onBlur={event => {
            const value = Number(event.target.value);
            if (value !== account.daily_limit)
              void call('email.account_update', { id: account.id, daily_limit: value });
          }}
        />
      </td>
      <td>
        <input
          type="checkbox"
          aria-label="Разгон"
          checked={account.warmup}
          onChange={event =>
            void call('email.account_update', { id: account.id, warmup: event.target.checked })
          }
        />
      </td>
      <td>{relativeTime(account.checked_at, '—')}</td>
      <td className="email-actions">
        <Button
          variant="outline"
          onClick={() => void control('test').then(result => result && setNotice('Вход работает'))}
        >
          Проверить
        </Button>
        {account.status === 'active' ? (
          <Button variant="outline" icon aria-label="Пауза" onClick={() => void control('pause')}>
            <Pause size={14} />
          </Button>
        ) : (
          <Button variant="outline" icon aria-label="Возобновить" onClick={() => void control('resume')}>
            <Play size={14} />
          </Button>
        )}
        <Button variant="outline" icon aria-label="Новый пароль приложения" onClick={() => setPassword('')}>
          <KeyRound size={14} />
        </Button>
        <Button
          variant="outline"
          icon
          aria-label="Удалить ящик"
          onClick={() => {
            if (window.confirm(`Удалить ${account.address}? Фоллоу-апы его переписок отменятся.`))
              void call('email.account_remove', { id: account.id });
          }}
        >
          <Trash2 size={14} />
        </Button>
        {password !== undefined && (
          <Modal
            title={`Новый пароль приложения для ${account.address}`}
            onClose={() => setPassword(undefined)}
            narrow
          >
            <input
              type="password"
              autoComplete="off"
              placeholder="xxxx xxxx xxxx xxxx"
              value={password}
              onChange={event => setPassword(event.target.value)}
            />
            <Button
              disabled={!password.trim()}
              onClick={() =>
                void call('email.account_update', { id: account.id, password }).then(result => {
                  if (result) setPassword(undefined);
                })
              }
            >
              Сохранить
            </Button>
          </Modal>
        )}
      </td>
    </tr>
  );
}

/** «Email» → «Ящики»: the Gmail mailboxes, their health and today's limit. */
export function EmailAccounts() {
  const state = useResource<EmailState>('email.state', {}, 15000);
  const [form, setForm] = useState({
    address: '',
    password: '',
    display_name: '',
    daily_limit: 20,
    warmup: true,
  });
  const [busy, setBusy] = useState(false);
  const { call, error, notice, setNotice } = useCall(state.refresh);
  const add = async () => {
    setBusy(true);
    const result = await call('email.account_add', form);
    setBusy(false);
    if (result) {
      setForm(current => ({ ...current, address: '', password: '', display_name: '' }));
      setNotice('Ящик добавлен: Gmail принял вход.');
    }
  };
  const accounts = state.data?.accounts ?? [];
  return (
    <div className="screen work-flow">
      <PageHeader
        page="email-accounts"
        count={`${accounts.length}`}
        actions={
          <Button variant="outline" icon aria-label="Обновить" onClick={state.refresh}>
            <RefreshCw size={14} />
          </Button>
        }
      />
      <section className="panel board cloud-form">
        <div className="board-heading">
          <h2>Добавить Gmail</h2>
        </div>
        <ol className="helper email-steps">
          <li>
            В аккаунте Google включите двухэтапную аутентификацию (Безопасность → Двухэтапная аутентификация).
          </li>
          <li>
            Откройте myaccount.google.com/apppasswords, создайте пароль приложения с любым названием (например
            «Artist Lead Finder») и скопируйте 16 букв.
          </li>
          <li>Вставьте адрес и этот пароль ниже. Обычный пароль от Gmail не подойдёт.</li>
        </ol>
        <p className="helper">
          Новый или давно молчавший ящик начинает с 5 писем рассылки в день и прибавляет по 3 каждый день до
          лимита — это разгон. Для обычного @gmail.com держите лимит 20–30: выше растёт риск спама и
          блокировки ящика. Не берите личный ящик, к которому привязано что-то важное. Переписку ящиков между
          собой и с партнёрами включите в «Прогреве».
        </p>
        <div className="cloud-grid">
          <label className="cloud-field">
            <span>Адрес Gmail</span>
            <input
              type="email"
              autoComplete="off"
              placeholder="name@gmail.com"
              value={form.address}
              onChange={event => setForm({ ...form, address: event.target.value })}
            />
          </label>
          <label className="cloud-field">
            <span>Пароль приложения (16 букв)</span>
            <input
              type="password"
              autoComplete="off"
              placeholder="xxxx xxxx xxxx xxxx"
              value={form.password}
              onChange={event => setForm({ ...form, password: event.target.value })}
            />
          </label>
          <label className="cloud-field">
            <span>Имя отправителя — так его увидит артист</span>
            <input
              placeholder="Matt"
              value={form.display_name}
              onChange={event => setForm({ ...form, display_name: event.target.value })}
            />
          </label>
          <label className="cloud-field">
            <span>Писем в день, максимум</span>
            <input
              type="number"
              min={1}
              max={50}
              value={form.daily_limit}
              onChange={event => setForm({ ...form, daily_limit: Number(event.target.value) })}
            />
          </label>
        </div>
        <label className="check-row">
          <input
            type="checkbox"
            checked={form.warmup}
            onChange={event => setForm({ ...form, warmup: event.target.checked })}
          />
          <span>Разгон: начинать с 5 писем рассылки в день</span>
        </label>
        <ErrorToast message={error || state.error} />
        <div className="board-footer">
          {notice && <span className="helper">{notice}</span>}
          <Button disabled={busy || !form.address.trim() || !form.password.trim()} onClick={() => void add()}>
            <Plus size={15} /> {busy ? 'Проверяю вход…' : 'Добавить ящик'}
          </Button>
        </div>
      </section>
      {accounts.length > 0 && (
        <section className="panel cloud-detail">
          <div className="crm-table cloud-table">
            <table>
              <thead>
                <tr>
                  <th>Ящик</th>
                  <th>Состояние</th>
                  <th>За 24 ч / можно сегодня</th>
                  <th>Лимит в день</th>
                  <th>Разгон</th>
                  <th>Ответы проверены</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {accounts.map(account => (
                  <AccountRow key={account.id} account={account} onChange={state.refresh} />
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
