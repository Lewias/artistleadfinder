import { ErrorToast } from '../components/Toaster';
import { useEffect, useState, type KeyboardEvent, type ReactNode } from 'react';
import { open, save as saveDialog } from '@tauri-apps/plugin-dialog';
import {
  AtSign,
  CornerDownLeft,
  Eye,
  FlaskConical,
  FolderOpen,
  LayoutGrid,
  LayoutTemplate,
  ListOrdered,
  MessageSquare,
  Paperclip,
  Pause,
  Play,
  Plus,
  Send,
  SlidersHorizontal,
  Smartphone,
  Square,
  SquarePen,
  Tag,
  Trash2,
  Users,
  X,
} from 'lucide-react';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import type { IMessageJob, IMessagePart, IMessagePreview, IMessageState } from '../services/types';
import { number, plural } from '../lib/format';
import { PageHeader } from '../components/PageHeader';
import { Button } from '../components/ui/button';
import { Modal } from '../components/Modal';
import { errorText } from '../components/accountRuns';
import { CrmModal } from '../components/outreach/OutreachModals';
import { PartsEditor, PlanNote, TemplateEditor, TemplatePicker } from '../components/imessage/Templates';
import { parseMessages } from '../components/outreach/outreachList';
import { PhoneModal, type PhoneStage } from '../components/imessage/PhoneModal';
import {
  ATTACHMENT_EXTENSIONS,
  campaignStatusLabel,
  fileSize,
  jobStatusDetail,
  jobStatusLabel,
  jobStatusTone,
  parseRecipients,
  partsPayload,
  recipientsText,
} from '../components/imessage/imessageText';

const RECIPIENTS_HINT = 'Один телефон или email на строку: +15555550123 или name@example.com.';
const MESSAGES_HINT =
  'Разделяйте варианты пустой строкой. Для телефона или email доступна подстановка {Phone}.';
type Dialog =
  | 'phone'
  | 'settings'
  | 'preview'
  | 'test'
  | 'stop'
  | 'crm'
  | 'templates'
  | 'clear-users'
  | 'clear-messages'
  | 'clear-chain'
  | 'save-template'
  | null;

const time = (value: string | null) =>
  value
    ? new Date(value).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : '—';

/** Inner card of the board: icon, title, hint and its content. */
function Card({
  icon,
  title,
  hint,
  children,
}: {
  icon: ReactNode;
  title: string;
  hint: string;
  children: ReactNode;
}) {
  return (
    <section className="im-card">
      <header className="im-card-head">
        <span className="im-card-icon">{icon}</span>
        <div>
          <h3>{title}</h3>
          <p>{hint}</p>
        </div>
      </header>
      {children}
    </section>
  );
}

export function IMessageCampaigns() {
  const [poll, setPoll] = useState(5000);
  const resource = useResource<IMessageState>('imessage.state', {}, poll);
  const [state, setState] = useState<IMessageState>();
  const [userDraft, setUserDraft] = useState('');
  const [userBulk, setUserBulk] = useState<string | null>(null);
  const [messageDraft, setMessageDraft] = useState('');
  const [messageBulk, setMessageBulk] = useState<string | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [preview, setPreview] = useState<IMessagePreview>();
  const [testPhone, setTestPhone] = useState('');
  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<PhoneStage>('ready');
  const [ip, setIp] = useState('');
  const [port, setPort] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => {
    if (resource.data) setState(resource.data);
  }, [resource.data]);
  // A running campaign changes every few seconds; an idle list does not.
  useEffect(() => setPoll(state?.active ? 2000 : 5000), [state?.active]);

  const workspace = state?.workspace;
  const recipients = workspace?.recipients ?? [];
  const messages = workspace?.messages ?? [];
  const attachments = workspace?.attachments ?? [];
  const campaign = state?.campaign;
  const active = !!state?.active;
  // The chain is edited locally; a new version from the core replaces the draft.
  const sequenceKey = JSON.stringify(workspace?.sequence ?? []);
  const [chainSource, setChainSource] = useState('[]');
  const [chain, setChain] = useState<IMessagePart[]>([]);
  if (sequenceKey !== chainSource) {
    setChainSource(sequenceKey);
    setChain(JSON.parse(sequenceKey) as IMessagePart[]);
  }
  const chained = (workspace?.sequence.length ?? 0) > 0;

  const call = async (method: string, params: object = {}) => {
    setBusy(true);
    setError('');
    try {
      const next = await api.request<IMessageState>(method, params);
      setState(next);
      return next;
    } catch (err) {
      setError(errorText(err));
      return null;
    } finally {
      setBusy(false);
    }
  };
  const save = (params: object) => call('imessage.workspace_update', params);
  const plain = () => recipients.map(({ phone, message }) => ({ phone, message }));
  const invalidText = (invalid: string[]) =>
    `Телефон с «+» и кодом страны или email: ${invalid.slice(0, 3).join(', ')}`;

  const addRecipients = async (text: string) => {
    const { recipients: parsed, invalid } = parseRecipients(text.replace(/[,]/g, '\n'));
    if (invalid.length) {
      setError(invalidText(invalid));
      return false;
    }
    if (!parsed.length) return false;
    return !!(await save({ recipients: [...plain(), ...parsed] }));
  };
  const addMessages = async (texts: string[]) =>
    texts.length ? !!(await save({ messages: [...messages, ...texts] })) : false;
  const saveChain = (parts: IMessagePart[]) => void save({ sequence: partsPayload(parts) });
  // «Несколько сообщений»: the first variant and the list's files open the chain.
  const startChain = () =>
    void save({
      sequence: [
        { text: messages[0] ?? '', attachment_ids: attachments.map(item => item.id) },
        { text: '', attachment_ids: [] },
      ],
    });
  const insertTemplate = async (id: number) => {
    if (await call('imessage.template_use', { id })) {
      setDialog(null);
      setNotice('Шаблон вставлен.');
    }
  };

  const onUserKey = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key !== 'Enter') return;
    event.preventDefault();
    void addRecipients(userDraft).then(ok => ok && setUserDraft(''));
  };
  const onMessageKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey) return;
    event.preventDefault();
    const text = messageDraft.trim();
    if (text) void addMessages([text]).then(ok => ok && setMessageDraft(''));
  };
  // «Массовый» edits the whole list as text; the second press saves it back.
  const toggleUserBulk = () => {
    if (userBulk === null) return setUserBulk(recipientsText(recipients));
    const { recipients: parsed, invalid } = parseRecipients(userBulk);
    if (invalid.length) return setError(invalidText(invalid));
    if (!parsed.length && recipients.length) return setDialog('clear-users');
    void save({ recipients: parsed }).then(next => next && setUserBulk(null));
  };
  const toggleMessageBulk = () => {
    if (messageBulk === null) return setMessageBulk(messages.join('\n\n'));
    const parsed = parseMessages(messageBulk);
    if (!parsed.length && messages.length) return setDialog('clear-messages');
    void save({ messages: parsed }).then(next => next && setMessageBulk(null));
  };
  const addAttachment = async () => {
    try {
      const path = await open({
        title: 'Файлы для отправки',
        multiple: true,
        filters: [{ name: 'Фото, видео, аудио, PDF', extensions: ATTACHMENT_EXTENSIONS }],
      });
      for (const item of !path ? [] : Array.isArray(path) ? path : [path]) {
        if (!(await call('imessage.attachment_add', { path: item }))) break;
      }
    } catch (err) {
      setError(errorText(err));
    }
  };
  const openPreview = async () => {
    try {
      setPreview(await api.request<IMessagePreview>('imessage.preview'));
      setDialog('preview');
    } catch (err) {
      setError(errorText(err));
    }
  };
  // «Рассылка через телефон»: the bridge starts by itself, then the campaign, then the
  // window with the launch QR. Nothing is opened on the phone from here.
  const start = async (test?: string) => {
    setDialog('phone');
    setStage('starting');
    setError('');
    if (!state?.bridge.running || !state.bridge.token_valid) {
      const bridged = await call('imessage.bridge_start', {});
      if (!bridged) return setStage('error');
    }
    const next = await call('imessage.start', test ? { test_phone: test } : {});
    if (!next) return setStage('error');
    setStage('ready');
    setNotice(next.skipped ? `Пропущено уже отправленных: ${next.skipped}` : '');
  };
  const saveShortcut = async () => {
    if (!workspace) return;
    try {
      const path = await saveDialog({
        title: 'Файл команды для iPhone',
        defaultPath: `${workspace.legacy_shortcut_name}.shortcut`,
        filters: [{ name: 'Shortcut', extensions: ['shortcut'] }],
      });
      if (!path) return;
      const result = await api.request<{ state: IMessageState }>('imessage.shortcut_save', {
        path,
      });
      setState(result.state);
      setNotice('Файл команды сохранён. Перешлите его на iPhone и откройте.');
    } catch (err) {
      setError(errorText(err));
    }
  };
  const restartBridge = async () => {
    await call('imessage.bridge_stop');
    await call('imessage.bridge_start', { ip, port: Number(port) || undefined });
  };
  const control = (action: 'pause' | 'resume' | 'stop') => void call('imessage.control', { action });
  const resolve = (job: IMessageJob, resolution: 'sent' | 'not_sent' | 'resend') =>
    void call('imessage.resolve', { job_id: job.id, resolution });

  const counts = campaign?.counts;
  const acknowledged = counts?.execution_acknowledged ?? 0;
  let status: ReactNode = `${number(acknowledged)} / ${number(campaign?.total ?? 0)} отправлено`;
  if (active && campaign && counts) {
    status = (
      <>
        {campaign.is_test ? 'Тест' : 'Рассылка'} · {campaignStatusLabel[campaign.status].toLowerCase()} ·{' '}
        {number(acknowledged)} / {number(campaign.total)} выполнено на iPhone
        {counts.issued > 0 && ` · у телефона ${counts.issued}`}
        {counts.uncertain > 0 && <span className="board-warning"> · неизвестно {counts.uncertain}</span>}
      </>
    );
  }

  return (
    <div className="screen">
      <PageHeader page="imessage" count={`${number(acknowledged)} / ${number(campaign?.total ?? 0)}`} />

      <section className="panel board im-board">
        <header className="im-board-head">
          <span className="im-board-icon">
            <Send size={16} />
          </span>
          <div>
            <span className="eyebrow">IMESSAGE</span>
            <h2>Рассылка</h2>
          </div>
          <div className="im-counts">
            <span>{plural(recipients.length, ['получатель', 'получателя', 'получателей'])}</span>
            {chained ? (
              <span>
                цепочка: {plural(workspace?.sequence.length ?? 0, ['сообщение', 'сообщения', 'сообщений'])}
              </span>
            ) : (
              <>
                <span>{plural(messages.length, ['сообщение', 'сообщения', 'сообщений'])}</span>
                <span>{plural(attachments.length, ['вложение', 'вложения', 'вложений'])}</span>
              </>
            )}
          </div>
        </header>

        <Card icon={<Users size={15} />} title="Получатели" hint={RECIPIENTS_HINT}>
          {userBulk === null ? (
            <div className="chip-cloud board-chips im-chips" aria-label="Получатели">
              {recipients.map(item => (
                <span
                  key={item.phone}
                  className={`chip board-chip ${item.status ? jobStatusTone[item.status] : ''}`}
                  title={[
                    item.status ? jobStatusLabel[item.status] : '',
                    item.message ? `Свой текст: ${item.message}` : '',
                  ]
                    .filter(Boolean)
                    .join('\n')}
                >
                  {item.phone}
                  {item.message && <MessageSquare size={12} aria-label="свой текст" />}
                  <button
                    type="button"
                    aria-label={`Убрать ${item.phone}`}
                    disabled={busy}
                    onClick={() => void save({ recipients: plain().filter(row => row.phone !== item.phone) })}
                  >
                    <X size={13} />
                  </button>
                </span>
              ))}
              {!recipients.length && <p className="im-empty">{RECIPIENTS_HINT}</p>}
            </div>
          ) : (
            <textarea
              className="board-bulk"
              autoFocus
              aria-label="Получатели, по одному в строке"
              placeholder={'+15555550123\nname@example.com\n+15555550124; свой текст для этого номера'}
              value={userBulk}
              disabled={busy}
              onChange={event => setUserBulk(event.target.value)}
            />
          )}
          {userBulk === null && (
            <label className="chip-input">
              <AtSign size={17} aria-hidden="true" />
              <input
                aria-label="Новый получатель"
                placeholder={RECIPIENTS_HINT}
                value={userDraft}
                disabled={busy}
                onChange={event => setUserDraft(event.target.value)}
                onKeyDown={onUserKey}
              />
              <kbd aria-hidden="true">
                <CornerDownLeft size={13} />
              </kbd>
            </label>
          )}
          <div className="board-toolbar">
            <Button variant="outline" disabled={busy} onClick={toggleUserBulk}>
              {userBulk === null ? <SquarePen size={15} /> : <LayoutGrid size={15} />}
              {userBulk === null ? 'Массовый' : 'Карточки'}
            </Button>
            <Button variant="outline" onClick={() => setDialog('crm')}>
              <Tag size={15} /> CRM Метки
            </Button>
            <Button
              variant="danger"
              icon
              aria-label="Очистить получателей"
              title="Очистить получателей"
              disabled={!recipients.length}
              onClick={() => setDialog('clear-users')}
            >
              <Trash2 size={15} />
            </Button>
          </div>
        </Card>

        {chained ? (
          <Card
            icon={<ListOrdered size={15} />}
            title="Цепочка сообщений"
            hint="Каждый получатель получит сообщения по порядку. В каждом — текст, файлы или и то и другое; {Phone} подставит получателя."
          >
            <PartsEditor
              parts={chain}
              busy={busy}
              onChange={setChain}
              onCommit={saveChain}
              onError={setError}
            />
            <PlanNote parts={chain} />
            <div className="board-toolbar">
              <Button variant="outline" onClick={() => setDialog('templates')}>
                <LayoutTemplate size={15} /> Шаблоны
              </Button>
              <Button variant="outline" onClick={() => setDialog('save-template')}>
                <Plus size={15} /> Сохранить как шаблон
              </Button>
              <Button variant="outline" disabled={busy} onClick={() => setDialog('clear-chain')}>
                <MessageSquare size={15} /> Одно сообщение
              </Button>
            </div>
          </Card>
        ) : (
          <Card icon={<MessageSquare size={15} />} title="Сообщения" hint={MESSAGES_HINT}>
            {messageBulk === null ? (
              <>
                {messages.length ? (
                  <div className="message-grid im-messages">
                    {messages.map((text, index) => (
                      <div className="message-card" key={text}>
                        <p title={text}>{text}</p>
                        <button
                          type="button"
                          aria-label={`Удалить сообщение ${index + 1}`}
                          disabled={busy}
                          onClick={() => void save({ messages: messages.filter(item => item !== text) })}
                        >
                          <X size={13} />
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="im-empty im-messages-empty">{MESSAGES_HINT}</p>
                )}
                <div className="im-compose">
                  <textarea
                    className="message-input"
                    rows={2}
                    aria-label="Новое сообщение"
                    placeholder={MESSAGES_HINT}
                    value={messageDraft}
                    disabled={busy}
                    onChange={event => setMessageDraft(event.target.value)}
                    onKeyDown={onMessageKey}
                  />
                  <Button
                    variant="outline"
                    icon
                    aria-label="Добавить сообщение"
                    title="Добавить сообщение"
                    disabled={busy || !messageDraft.trim()}
                    onClick={() =>
                      void addMessages([messageDraft.trim()]).then(ok => ok && setMessageDraft(''))
                    }
                  >
                    <Plus size={16} />
                  </Button>
                </div>
              </>
            ) : (
              <textarea
                className="board-bulk message-bulk"
                autoFocus
                aria-label="Сообщения, отделяйте пустой строкой"
                placeholder={'Первый вариант, {Phone}\n\nВторой вариант — отделяйте пустой строкой'}
                value={messageBulk}
                disabled={busy}
                onChange={event => setMessageBulk(event.target.value)}
              />
            )}
            <div className="board-toolbar">
              <Button variant="outline" disabled={busy} onClick={toggleMessageBulk}>
                {messageBulk === null ? <SquarePen size={15} /> : <LayoutGrid size={15} />}
                {messageBulk === null ? 'Массовый' : 'Карточки'}
              </Button>
              <Button
                variant="outline"
                disabled={messageBulk !== null}
                onClick={() => setDialog('templates')}
              >
                <LayoutTemplate size={15} /> Шаблоны
              </Button>
              <Button
                variant="outline"
                disabled={busy || messageBulk !== null}
                title="Каждый получатель получит несколько сообщений по порядку"
                onClick={startChain}
              >
                <ListOrdered size={15} /> Несколько сообщений
              </Button>
              <Button
                variant="danger"
                icon
                aria-label="Удалить все сообщения"
                title="Удалить все сообщения"
                disabled={!messages.length}
                onClick={() => setDialog('clear-messages')}
              >
                <Trash2 size={15} />
              </Button>
            </div>
          </Card>
        )}

        {!chained && (
          <Card
            icon={<Paperclip size={15} />}
            title="Вложения"
            hint={
              attachments.length ? 'Каждый файл уходит отдельным сообщением после текста' : 'Файлы не выбраны'
            }
          >
            {attachments.length ? (
              <div className="chip-cloud attachment-chips">
                {attachments.map(item => (
                  <span className="chip board-chip" key={item.id} title={item.mime}>
                    <Paperclip size={13} aria-hidden="true" />
                    {item.filename} · {fileSize(item.size)}
                    <button
                      type="button"
                      aria-label={`Удалить ${item.filename}`}
                      disabled={busy}
                      onClick={() => void call('imessage.attachment_remove', { id: item.id })}
                    >
                      <X size={13} />
                    </button>
                  </span>
                ))}
              </div>
            ) : (
              <p className="im-files-empty">Файлы не выбраны</p>
            )}
            <div className="board-toolbar">
              <Button variant="outline" disabled={busy} onClick={() => void addAttachment()}>
                <FolderOpen size={15} /> Выбрать файлы
              </Button>
            </div>
          </Card>
        )}

        <ErrorToast message={error} />
        {notice && !error && <p className="helper">{notice}</p>}

        <footer className="im-footer">
          <Button variant="outline" onClick={() => setDialog('settings')}>
            <SlidersHorizontal size={15} /> Настройки
          </Button>
          <span className="board-status" role="status">
            {status}
          </span>
          {active && campaign?.status === 'running' && (
            <Button variant="outline" disabled={busy} onClick={() => control('pause')}>
              <Pause size={14} /> Пауза
            </Button>
          )}
          {active && campaign?.status === 'paused' && (
            <Button variant="outline" disabled={busy} onClick={() => control('resume')}>
              <Play size={14} /> Продолжить
            </Button>
          )}
          {active && (
            <Button variant="outline" disabled={busy} onClick={() => setDialog('stop')}>
              <Square size={14} /> Остановить
            </Button>
          )}
          {active ? (
            <Button
              onClick={() => {
                setStage('ready');
                setDialog('phone');
              }}
            >
              <Smartphone size={15} /> Окно рассылки
            </Button>
          ) : (
            <Button
              disabled={busy || !recipients.length || !(chained || messages.length)}
              onClick={() => void start()}
            >
              <Smartphone size={15} /> Рассылка через телефон
            </Button>
          )}
        </footer>
      </section>

      {campaign && (state?.jobs.length ?? 0) > 0 && (
        <section className="panel">
          <div className="board-heading">
            <h2>
              {campaign.is_test ? 'Тест' : 'Рассылка'} №{campaign.id} · {campaignStatusLabel[campaign.status]}
            </h2>
            <span className="helper">
              «Выполнено на iPhone» — Shortcut прошёл свои шаги. Доставку iOS не сообщает.
            </span>
          </div>
          <div className="table-container">
            <table>
              <thead>
                <tr>
                  <th>№</th>
                  <th>Получатель</th>
                  <th>Статус</th>
                  <th>Попыток</th>
                  <th>Выдано</th>
                  <th>Подтверждено</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {state?.jobs.map(job => (
                  <tr key={job.id}>
                    <td>{job.position}</td>
                    <td title={job.message}>
                      {job.phone}
                      {campaign.messages > 1 && (
                        <small className="job-detail">сообщение {job.step + 1}</small>
                      )}
                    </td>
                    <td>
                      <span className={`status-badge job-${job.status}`}>{jobStatusLabel[job.status]}</span>
                      <small className="job-detail">{job.note ?? jobStatusDetail(job)}</small>
                    </td>
                    <td>{job.attempts}</td>
                    <td>{time(job.issued_at)}</td>
                    <td>{time(job.acked_at)}</td>
                    <td>
                      {(job.status === 'uncertain' || job.status === 'failed') && (
                        <div className="actions job-actions">
                          {job.status === 'uncertain' && (
                            <Button variant="outline" disabled={busy} onClick={() => resolve(job, 'sent')}>
                              Ушло
                            </Button>
                          )}
                          {job.status === 'uncertain' && (
                            <Button
                              variant="outline"
                              disabled={busy}
                              onClick={() => resolve(job, 'not_sent')}
                            >
                              Не ушло
                            </Button>
                          )}
                          {campaign.status !== 'stopped' && (
                            <Button variant="outline" disabled={busy} onClick={() => resolve(job, 'resend')}>
                              В очередь снова
                            </Button>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {dialog === 'settings' && workspace && (
        <Modal title="Настройки рассылки" onClose={() => setDialog(null)}>
          <p className="helper">
            Команда «{workspace.legacy_shortcut_name}» берёт весь список за раз и сама ждёт 20–50 с между
            получателями. Пауза и остановка не действуют на уже выданный список; подтверждается только текст.
          </p>

          <div className="actions">
            <Button variant="outline" disabled={!recipients.length} onClick={() => void openPreview()}>
              <Eye size={15} /> Предпросмотр
            </Button>
            <Button
              variant="outline"
              disabled={busy || active || !recipients.length || !(chained || messages.length)}
              onClick={() => {
                setTestPhone(recipients[0]?.phone ?? '');
                setDialog('test');
              }}
            >
              <FlaskConical size={15} /> Тест на один номер
            </Button>
          </div>
          <div className="im-settings-bridge">
            <h3>Мост к iPhone</h3>
            <p className="helper">
              {state?.bridge.running
                ? `Включён: ${state.bridge.ip}:${state.bridge.port}` +
                  (state.bridge.token_expires_at
                    ? ` · токен до ${new Date(state.bridge.token_expires_at).toLocaleString('ru-RU', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}`
                    : '')
                : 'Выключен. Включится сам по кнопке «Рассылка через телефон».'}
            </p>
            <div className="bridge-fields">
              <label>
                Адрес компьютера в сети
                <select value={ip || state?.bridge.ip || ''} onChange={event => setIp(event.target.value)}>
                  {(state?.bridge.addresses ?? []).map(address => (
                    <option key={address} value={address}>
                      {address}
                      {/^172\.(1[6-9]|2\d|3[01])\./.test(address) ? ' — похоже на VPN' : ''}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Порт
                <input
                  type="number"
                  min={1024}
                  max={65535}
                  value={port || String(state?.bridge.port ?? '')}
                  onChange={event => setPort(event.target.value)}
                />
              </label>
              <Button variant="outline" disabled={busy} onClick={() => void restartBridge()}>
                Применить
              </Button>
            </div>
            <p className="helper">
              Нужен адрес Wi-Fi или Ethernet (обычно 192.168.x.x), не VPN. Отвечает только устройствам
              локальной сети.
            </p>
            <div className="actions">
              <Button variant="outline" disabled={busy} onClick={() => void call('imessage.bridge_rotate')}>
                Новый токен
              </Button>
              {state?.bridge.running && (
                <Button
                  variant="outline"
                  disabled={busy || active}
                  onClick={() => void call('imessage.bridge_stop')}
                >
                  Выключить мост
                </Button>
              )}
            </div>
            <div className="shortcut-names">
              <label>
                Имя команды на iPhone
                <input
                  key={workspace.legacy_shortcut_name}
                  defaultValue={workspace.legacy_shortcut_name}
                  disabled={busy}
                  onBlur={event =>
                    event.target.value.trim() !== workspace.legacy_shortcut_name &&
                    void save({ legacy_shortcut_name: event.target.value })
                  }
                />
              </label>
            </div>
          </div>
        </Modal>
      )}
      {dialog === 'phone' && (
        <PhoneModal
          state={state}
          stage={stage}
          error={error}
          busy={busy}
          onControl={action => (action === 'stop' ? setDialog('stop') : control(action))}
          onSave={() => void saveShortcut()}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog === 'preview' && preview && (
        <Modal title="Предпросмотр" wide onClose={() => setDialog('settings')}>
          <div className="preview-list">
            {preview.items.map(item => (
              <div className="preview-item" key={item.phone}>
                <strong>{item.phone}</strong>
                {item.messages ? (
                  item.messages.map((message, index) => (
                    <div className="preview-message" key={index}>
                      <span className="helper">
                        Сообщение {index + 1}
                        {index === 0 && item.individual ? ' · свой текст' : ''} · запуск {message.launch}
                      </span>
                      <p>{message.text}</p>
                      {message.files > 0 && <span className="helper">+ файлов: {message.files}</span>}
                    </div>
                  ))
                ) : (
                  <>
                    <span className="helper">{item.individual ? 'свой текст' : 'вариант из списка'}</span>
                    <p>{item.text || <span className="error-text">Нет текста — добавьте сообщение</span>}</p>
                    {attachments.length > 0 && <span className="helper">+ файлов: {attachments.length}</span>}
                  </>
                )}
              </div>
            ))}
          </div>
          <h3>
            Что получит команда (GET /task{preview.items.some(item => item.messages) ? ', первый запуск' : ''}
            )
          </h3>
          <div className="log-viewer">
            <pre>{JSON.stringify(preview.payload, null, 2)}</pre>
          </div>
        </Modal>
      )}
      {dialog === 'test' && (
        <Modal title="Тест на один номер" onClose={() => setDialog(null)}>
          <p className="helper">
            {chained
              ? 'Отправится вся цепочка сообщений по порядку.'
              : 'Отправится одно сообщение (свой текст получателя или первый подходящий вариант) и вложения.'}{' '}
            Тест не отмечает получателя как получившего рассылку.
          </p>
          <label>
            Телефон или email
            <input
              list="imessage-test-phones"
              value={testPhone}
              onChange={event => setTestPhone(event.target.value)}
              placeholder="+15555550123"
            />
            <datalist id="imessage-test-phones">
              {recipients.map(item => (
                <option key={item.phone} value={item.phone} />
              ))}
            </datalist>
          </label>

          <div className="actions">
            <Button disabled={busy || !testPhone.trim()} onClick={() => void start(testPhone.trim())}>
              <FlaskConical size={15} /> Запустить тест
            </Button>
            <Button variant="outline" onClick={() => setDialog(null)}>
              Отмена
            </Button>
          </div>
        </Modal>
      )}
      {dialog === 'stop' && (
        <Modal title="Остановить рассылку?" onClose={() => setDialog(null)}>
          <p className="helper">
            Телефон больше не получит новых заданий. То, что уже на iPhone, может отправиться до конца:
            отменить действие на телефоне приложение не может. Оставшиеся получатели останутся без сообщения.
          </p>
          <div className="actions">
            <Button
              variant="danger"
              onClick={() => {
                control('stop');
                setDialog(null);
              }}
            >
              <Square size={14} /> Остановить
            </Button>
            <Button variant="outline" onClick={() => setDialog(null)}>
              Отмена
            </Button>
          </div>
        </Modal>
      )}
      {dialog === 'crm' && (
        <CrmModal
          method="imessage.add_leads"
          note="Из «Базы артистов» с выбранными статусами добавятся найденные контакты: первый телефон лида, иначе email. Отмеченные «Не связываться» не добавляются; тем, кому уже отправлялось, рассылка повторно не отправит."
          onClose={() => setDialog(null)}
          onAdded={added => {
            setNotice(`Добавлено из CRM: ${added}`);
            resource.refresh();
          }}
        />
      )}
      {dialog === 'templates' && (
        <TemplatePicker
          chain={chained}
          onClose={() => setDialog(null)}
          onUse={template => void insertTemplate(template.id)}
        />
      )}
      {dialog === 'save-template' && (
        <TemplateEditor
          initial={chain}
          folders={[]}
          onClose={() => setDialog(null)}
          onSaved={template => {
            setDialog(null);
            setNotice(`Шаблон «${template.name}» сохранён.`);
          }}
        />
      )}
      {dialog === 'clear-chain' && (
        <Modal title="Вернуться к одному сообщению?" onClose={() => setDialog(null)}>
          <p className="helper">
            Цепочка будет удалена из рассылки, получатели снова получат по одному сообщению из вариантов.
            Сохранённые шаблоны останутся.
          </p>
          <div className="actions">
            <Button
              variant="danger"
              onClick={() => {
                void save({ sequence: [] });
                setDialog(null);
              }}
            >
              <Trash2 size={15} /> Удалить цепочку
            </Button>
            <Button variant="outline" onClick={() => setDialog(null)}>
              Отмена
            </Button>
          </div>
        </Modal>
      )}
      {(dialog === 'clear-users' || dialog === 'clear-messages') && (
        <Modal
          title={dialog === 'clear-users' ? 'Очистить получателей?' : 'Удалить все сообщения?'}
          onClose={() => setDialog(null)}
        >
          <p className="helper">
            {dialog === 'clear-users'
              ? 'Список получателей будет очищен. История рассылок останется в логах.'
              : 'Все варианты сообщений будут удалены из списка. Сохранённые шаблоны останутся.'}
          </p>
          <div className="actions">
            <Button
              variant="danger"
              onClick={() => {
                void save(dialog === 'clear-users' ? { recipients: [] } : { messages: [] });
                if (dialog === 'clear-users') setUserBulk(null);
                else setMessageBulk(null);
                setDialog(null);
              }}
            >
              <Trash2 size={15} /> Очистить
            </Button>
            <Button variant="outline" onClick={() => setDialog(null)}>
              Отмена
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
