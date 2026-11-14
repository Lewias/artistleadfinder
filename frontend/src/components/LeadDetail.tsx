import { ErrorToast } from './Toaster';
import { errorText } from '../lib/errors';
import { useRef, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import {
  X,
  ExternalLink,
  UserRound,
  ScanSearch,
  MessageSquareText,
  CalendarDays,
  Settings2,
} from 'lucide-react';
import appIcon from '../../../src-tauri/icons/icon.png';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import type { LeadDetail as Detail } from '../services/types';
import { number, date, activity, relativeTime, statusLabels } from '../lib/format';
import { DataState } from './DataState';
import { Button } from './ui/button';
import { Dropdown, Panel, Tabs, StatusIndicator } from './ui/desktop';
import { categoryLabels, methodLabel } from './scoutStatus';
import { conversationLabels, dateTime, reasonLabel, recipientStatusLabels } from './outreach/outreachText';

const decidedByLabels = { local: 'локально', ai: 'AI', 'local+ai': 'локально + AI' };

export function LeadDetail({
  id,
  close,
  refresh,
  embedded = false,
  artists,
  onChooseArtist,
  onBrowse,
}: {
  id: number;
  close: () => void;
  refresh: () => void;
  embedded?: boolean;
  artists?: { value: string; label: string }[];
  onChooseArtist?: (id: number) => void;
  onBrowse?: () => void;
}) {
  const resource = useResource<Detail>('leads.detail', { id });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [ignored, setIgnored] = useState(false);
  const [tab, setTab] = useState('profile');
  const [expanded, setExpanded] = useState(false);
  const fullDetails = useRef<HTMLDivElement>(null);
  const lead = resource.data;
  const ignore = async () => {
    if (!lead) return;
    setError('');
    try {
      await api.request('scout.ignore', { username: lead.username });
      setIgnored(true);
    } catch (err) {
      setError(errorText(err));
    }
  };
  const capture = lead?.analysis?.extracted_signals?.browser_capture;
  const outreachAction = async (method: string, params: object) => {
    setBusy(true);
    setError('');
    try {
      await api.request(method, params);
      resource.refresh();
      refresh();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  const outreach = lead?.outreach;
  const action = async (status: string) => {
    setBusy(true);
    setError('');
    try {
      await api.request('leads.status', { id, status });
      resource.refresh();
      refresh();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };
  const open = async (address: string) => {
    try {
      await api.openProfile(address);
    } catch (err) {
      setError(errorText(err));
    }
  };
  const content = (
    <>
      {!lead ? (
        <DataState {...resource} retry={resource.refresh} />
      ) : (
        <>
          <div className="alf-profile-hero">
            <span className="alf-profile-avatar">
              {lead.display_name.slice(0, 1) || lead.username.slice(0, 1)}
            </span>
            <div className="alf-profile-name">
              <span className="alf-overline">
                {lead.platform} /{' '}
                {lead.classification ? categoryLabels[lead.classification.category] : 'Профиль'}
              </span>
              <h2>{lead.display_name || `@${lead.username}`}</h2>
              <p>
                @{lead.username} · {lead.genres.join(' / ') || 'Жанр не определён'}
              </p>
              <StatusIndicator
                tone={lead.do_not_contact ? 'danger' : lead.status === 'qualified' ? 'positive' : 'neutral'}
              >
                {lead.do_not_contact ? 'Не связываться' : statusLabels[lead.status]}
              </StatusIndicator>
            </div>
            <div className="alf-profile-score">
              <strong>{lead.lead_score}</strong>
              <span>из 100</span>
            </div>
          </div>
          <div className="alf-profile-facts">
            <span>
              Подписчики
              <strong>
                {lead.unknown_fields?.includes('followers') || capture?.unknown_fields.includes('followers')
                  ? 'Не прочитаны'
                  : number(lead.followers)}
              </strong>
            </span>
            <span>
              Активность<strong>{activity(lead.last_activity_at)}</strong>
            </span>
            <span>
              Доступ<strong>{lead.is_private ? 'Закрытый профиль' : 'Открытый профиль'}</strong>
            </span>
          </div>
          <div className="alf-profile-command">
            <Dropdown
              label="Статус CRM"
              value={lead.status}
              disabled={busy}
              options={['new', 'reviewed', 'qualified', 'rejected', 'contacted'].map(value => ({
                value,
                label: statusLabels[value],
              }))}
              onChange={value => void action(value)}
            />
            <Button
              variant="outline"
              disabled={!lead.profile_url}
              onClick={() => void open(lead.profile_url)}
            >
              <ExternalLink size={14} />
              Открыть профиль
            </Button>
          </div>
          <Tabs
            label="Разделы профиля"
            value={tab}
            onChange={setTab}
            options={[
              { value: 'profile', label: 'Профиль' },
              { value: 'analysis', label: 'Анализ и оценка' },
              { value: 'history', label: 'Общение' },
            ]}
          />
          <div hidden={tab !== 'analysis'}>
            <Panel title="Анализ и соответствие" icon={<ScanSearch />}>
              {capture && (
                <>
                  <h3>Данные со страницы</h3>
                  <p className="helper">
                    Проверено: {new Date(capture.captured_at).toLocaleString('ru-RU')}. Счётчики на сайте
                    могут быть округлены.
                  </p>
                  {capture.unknown_fields.length > 0 && (
                    <p className="helper">
                      Не прочитаны:{' '}
                      {capture.unknown_fields
                        .map(
                          field =>
                            ({
                              followers: 'подписчики',
                              following: 'подписки',
                              bio: 'биография',
                              last_activity_at: 'последняя активность',
                            })[field] || field,
                        )
                        .join(', ')}
                      . Для нового профиля отсутствие данных снижает оценку.
                    </p>
                  )}
                  <details>
                    <summary>Сведения, использованные при анализе</summary>
                    <p className="bio">{capture.description}</p>
                    <p className="bio">{capture.header}</p>
                  </details>
                </>
              )}
              {lead.classification && (
                <>
                  <h3>Тип профиля</h3>
                  <p>
                    <strong>{categoryLabels[lead.classification.category]}</strong> ·{' '}
                    {lead.classification.confidence}% · {decidedByLabels[lead.classification.decided_by]}
                    {lead.classification.ai_model && <small> ({lead.classification.ai_model})</small>}
                  </p>
                  {lead.classification.ai_category && lead.classification.local_category && (
                    <p className="helper">
                      Локально: {categoryLabels[lead.classification.local_category]}{' '}
                      {lead.classification.local_confidence}%
                      {lead.classification.ai_category &&
                        ` · AI: ${categoryLabels[lead.classification.ai_category]} ${lead.classification.ai_confidence}%`}
                    </p>
                  )}
                  {lead.classification.reasons.length > 0 && (
                    <ul className="helper">
                      {lead.classification.reasons.map(reason => (
                        <li key={reason}>{reason}</li>
                      ))}
                    </ul>
                  )}
                </>
              )}
              {lead.scout && (
                <>
                  <h3>Что предложить</h3>
                  <p className="helper">
                    {lead.scout.explanation} Оценка соответствия услуге, не вероятность покупки.
                  </p>
                  {Object.entries(lead.scout.services).map(([key, value]) => (
                    <div key={key}>
                      <strong>
                        {{ beats: 'Биты', mixing: 'Сведение / мастеринг', promotion: 'Продвижение' }[key] ||
                          key}
                        : {value.score}/100
                      </strong>
                      {value.reasons.map((reason, i) => (
                        <p className="helper" key={i}>
                          {reason.text}
                        </p>
                      ))}
                    </div>
                  ))}
                </>
              )}
              <div className="detail-score">
                <span>ОБЩАЯ ОЦЕНКА ПРОФИЛЯ</span>
                <strong>
                  {lead.lead_score}
                  <small> / 100</small>
                </strong>
              </div>
              <h3>Почему этот профиль?</h3>
              {lead.breakdown.map(item => (
                <div className="breakdown" key={item.rule}>
                  <span>+{item.points}</span>
                  {item.reason}
                </div>
              ))}
              <h3>Сигналы анализа</h3>
              {lead.analysis?.signals.map(signal => (
                <p className="helper" key={signal}>
                  {signal}
                </p>
              ))}
              <p className="helper">Вероятность артиста: {Math.round(lead.artist_probability * 100)}%</p>
            </Panel>
          </div>
          <div hidden={tab !== 'profile'}>
            <Panel title="Информация о профиле" icon={<UserRound />}>
              <h3>Контактные данные</h3>
              <p className="helper">
                {[...(lead.scout_profile?.emails || []), ...(lead.scout_profile?.phones || [])].join(' · ') ||
                  'Контакты не найдены в профиле'}
              </p>
              <h3>{capture?.bio_method === 'header_excerpt' ? 'Текст шапки профиля' : 'Биография'}</h3>
              <p className="bio">{lead.bio || 'Не указана'}</p>
              {lead.external_url && (
                <>
                  <h3>Внешняя ссылка</h3>
                  <button className="text-action link-wrap" onClick={() => void open(lead.external_url)}>
                    {lead.external_url} <ExternalLink size={12} />
                  </button>
                </>
              )}
              {!!lead.found_via?.length && (
                <>
                  <h3>Найден через</h3>
                  {lead.found_via.map(item => (
                    <div
                      className="source-line"
                      key={`${item.source_username}-${item.discovery_method}`}
                      title={item.origin_url || undefined}
                    >
                      <span>
                        @{item.source_username} — {methodLabel(item.discovery_method)}
                      </span>
                      <strong>
                        {item.times_seen > 1 ? `${item.times_seen} раза · ` : ''}
                        {relativeTime(item.last_seen_at, '')}
                      </strong>
                    </div>
                  ))}
                </>
              )}
              <h3>Обнаружен из</h3>
              {lead.sources.map(source => (
                <div className="source-line" key={source.id}>
                  <span>
                    {source.source_provider} / {source.source_type}
                  </span>
                  <strong>{source.source_value}</strong>
                </div>
              ))}
              <h3>Последняя активность</h3>
              <p className="helper">{activity(lead.last_activity_at)}</p>
            </Panel>
          </div>
          <div hidden={tab !== 'history'}>
            <Panel title="История общения" icon={<MessageSquareText />}>
              {!outreach && <p className="helper">История общения пока недоступна для этого профиля.</p>}
              {outreach && (
                <>
                  <h3>Рассылка</h3>
                  <div className="outreach-card">
                    <div className="source-line">
                      <span>Статус</span>
                      <strong>{outreach.contacted ? 'Написали' : 'Ещё не писали'}</strong>
                    </div>
                    {outreach.last_contacted_at && (
                      <div className="source-line">
                        <span>Последнее сообщение</span>
                        <strong>{dateTime(outreach.last_contacted_at)}</strong>
                      </div>
                    )}
                    {outreach.sender_name && (
                      <div className="source-line">
                        <span>Отправитель</span>
                        <strong>{outreach.sender_name}</strong>
                      </div>
                    )}
                    {outreach.campaign && (
                      <div className="source-line">
                        <span>Кампания</span>
                        <strong>
                          {outreach.campaign.name} · {recipientStatusLabels[outreach.campaign.status]}
                          {outreach.campaign.reason ? ` · ${reasonLabel(outreach.campaign.reason)}` : ''}
                        </strong>
                      </div>
                    )}
                    {outreach.conversation_status && (
                      <div className="source-line">
                        <span>Диалог</span>
                        <strong>{conversationLabels[outreach.conversation_status]}</strong>
                      </div>
                    )}
                    {outreach.pending_followups > 0 && (
                      <p className="helper">Запланировано follow-up: {outreach.pending_followups}</p>
                    )}
                    {outreach.campaign?.needs_review && (
                      <>
                        <p className="error-text">
                          Отправка не подтверждена — проверьте диалог в Instagram и отметьте результат.
                        </p>
                        <div className="actions">
                          <Button
                            variant="outline"
                            disabled={busy}
                            onClick={() =>
                              void outreachAction('outreach.resolve_review', {
                                recipient_id: outreach.campaign!.recipient_id,
                                sent: true,
                              })
                            }
                          >
                            Сообщение ушло
                          </Button>
                          <Button
                            variant="outline"
                            disabled={busy}
                            onClick={() =>
                              void outreachAction('outreach.resolve_review', {
                                recipient_id: outreach.campaign!.recipient_id,
                                sent: false,
                              })
                            }
                          >
                            Не ушло
                          </Button>
                        </div>
                      </>
                    )}
                    {outreach.do_not_contact && (
                      <p className="error-text">Не связываться: этот лид не получит ни одного сообщения.</p>
                    )}
                    {outreach.messages.length > 0 && (
                      <details>
                        <summary>История сообщений ({outreach.messages.length})</summary>
                        <ul className="message-history">
                          {outreach.messages.map((message, index) => (
                            <li key={index} className={`message-${message.direction}`}>
                              <small>
                                {message.direction === 'outbound' ? 'Мы' : 'Лид'} ·{' '}
                                {dateTime(message.sent_at)}
                              </small>
                              <p>{message.body || '—'}</p>
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                    <div className="actions">
                      <Button
                        variant="outline"
                        disabled={busy}
                        onClick={() =>
                          void outreachAction('leads.do_not_contact', {
                            id: lead.id,
                            value: !outreach.do_not_contact,
                          })
                        }
                      >
                        {outreach.do_not_contact ? 'Снять «Не связываться»' : 'Не связываться'}
                      </Button>
                      {outreach.conversation_status === 'waiting_reply' && (
                        <Button
                          variant="outline"
                          disabled={busy}
                          title="Лид ответил в Instagram: follow-up отменятся"
                          onClick={() => void outreachAction('outreach.mark_replied', { lead_id: lead.id })}
                        >
                          Получен ответ
                        </Button>
                      )}
                      {outreach.conversation_status && outreach.conversation_status !== 'stopped' && (
                        <Button
                          variant="outline"
                          disabled={busy}
                          title="Больше не писать этому лиду"
                          onClick={() =>
                            void outreachAction('outreach.stop_conversation', { lead_id: lead.id })
                          }
                        >
                          Остановить общение
                        </Button>
                      )}
                    </div>
                  </div>
                </>
              )}
            </Panel>
          </div>
          <p className="helper">
            «Написали» ставится автоматически после отправки из «Первичной рассылки» или вручную.
          </p>
          <div className="actions">
            <Button
              variant="outline"
              disabled={!lead.profile_url}
              onClick={() => void open(lead.profile_url)}
            >
              Открыть профиль
            </Button>
            <Button disabled={busy} onClick={() => void action('qualified')}>
              Подходит
            </Button>
            <Button variant="outline" disabled={busy} onClick={() => void action('rejected')}>
              Отклонить
            </Button>
            <Button
              variant="outline"
              disabled={ignored}
              title="Парсер больше не будет анализировать этот профиль"
              onClick={() => void ignore()}
            >
              {ignored ? 'В игноре парсера' : 'Игнорировать в парсере'}
            </Button>
          </div>
        </>
      )}
      <ErrorToast message={error} />
    </>
  );
  if (embedded) {
    if (!lead) return <DataState {...resource} retry={resource.refresh} />;
    const reveal = (next: string) => {
      setTab(next);
      setExpanded(true);
      requestAnimationFrame(() => fullDetails.current?.scrollIntoView({ behavior: 'auto', block: 'start' }));
    };
    const emails = lead.scout_profile?.emails || [];
    const phones = lead.scout_profile?.phones || [];
    return (
      <section className="alf-reference-profile" aria-label="Профиль артиста">
        <div className="alf-reference-top">
          <Panel title="Информация об артисте" icon={<UserRound />} className="alf-reference-information">
            <p className="alf-reference-description">Основная информация о выбранном артисте</p>
            <div className="alf-reference-identity">
              <img
                className="alf-reference-image"
                src={lead.avatar_url?.startsWith('data:') ? lead.avatar_url : appIcon}
                alt={lead.avatar_url?.startsWith('data:') ? lead.display_name : 'Artist Lead Finder'}
              />
              <div className="alf-reference-fields">
                <label>
                  Имя артиста
                  {artists && onChooseArtist ? (
                    <Dropdown
                      label="Выбранный артист"
                      value={String(id)}
                      options={artists}
                      onChange={value => onChooseArtist(Number(value))}
                    />
                  ) : (
                    <input readOnly value={lead.display_name || lead.username} />
                  )}
                </label>
                <label>
                  Биография
                  <textarea readOnly value={lead.bio || 'Не указана'} rows={3} />
                </label>
                <label>
                  Профиль
                  <input readOnly value={lead.profile_url || `@${lead.username}`} />
                </label>
              </div>
            </div>
            <label className="alf-reference-field">
              Контакты
              <input readOnly value={[...emails, ...phones].join(' · ') || 'Контакты не найдены в профиле'} />
            </label>
            {!!emails.length && (
              <div className="alf-reference-tags">
                {emails.map(email => (
                  <span key={email}>{email}</span>
                ))}
              </div>
            )}
            <label className="alf-reference-field">
              Жанры
              <input readOnly value={lead.genres.join(', ') || 'Жанр не определён'} />
            </label>
            {!!lead.genres.length && (
              <div className="alf-reference-tags">
                {lead.genres.map(genre => (
                  <span key={genre}>{genre}</span>
                ))}
              </div>
            )}
          </Panel>
          <Panel title="Данные профиля" icon={<CalendarDays />} className="alf-reference-statistics">
            <dl>
              <div>
                <dt>Добавлен</dt>
                <dd>{date(lead.created_at)}</dd>
              </div>
              <div>
                <dt>Активность</dt>
                <dd>{activity(lead.last_activity_at)}</dd>
              </div>
              <div>
                <dt>Подписчики</dt>
                <dd>
                  {lead.unknown_fields?.includes('followers') || capture?.unknown_fields.includes('followers')
                    ? 'Не прочитаны'
                    : number(lead.followers)}
                </dd>
              </div>
              <div>
                <dt>Подписки</dt>
                <dd>
                  {lead.unknown_fields?.includes('following') ? 'Не прочитаны' : number(lead.following)}
                </dd>
              </div>
              <div>
                <dt>Оценка</dt>
                <dd>{lead.lead_score} / 100</dd>
              </div>
              <div>
                <dt>Статус</dt>
                <dd>{statusLabels[lead.status] || lead.status}</dd>
              </div>
            </dl>
          </Panel>
        </div>
        <div className="alf-reference-tools">
          <Panel title="Анализ профиля" icon={<ScanSearch />}>
            <p className="alf-reference-description">Классификация и объяснение оценки</p>
            <label className="alf-reference-field">
              Тип профиля
              <input
                readOnly
                value={lead.classification ? categoryLabels[lead.classification.category] : 'Не определён'}
              />
            </label>
            <Button variant="outline" onClick={() => reveal('analysis')}>
              Открыть анализ
            </Button>
          </Panel>
          <Panel title="Рассылка" icon={<MessageSquareText />}>
            <p className="alf-reference-description">История общения и следующие шаги</p>
            <label className="alf-reference-field">
              Статус CRM
              <Dropdown
                label="Статус CRM"
                value={lead.status}
                disabled={busy}
                options={['new', 'reviewed', 'qualified', 'rejected', 'contacted'].map(value => ({
                  value,
                  label: statusLabels[value],
                }))}
                onChange={value => void action(value)}
              />
            </label>
            <Button variant="outline" onClick={() => reveal('history')}>
              История общения
            </Button>
          </Panel>
          <Panel title="Работа с базой" icon={<Settings2 />}>
            <p className="alf-reference-description">Артисты, фильтры и действия с профилем</p>
            <div className="alf-reference-action-list">
              <Button variant="outline" onClick={onBrowse}>
                Все артисты
              </Button>
              <Button
                variant="outline"
                disabled={!lead.profile_url}
                onClick={() => void open(lead.profile_url)}
              >
                <ExternalLink />
                Открыть профиль
              </Button>
              <Button variant="outline" disabled={busy} onClick={() => void action('qualified')}>
                Подходит
              </Button>
              <Button variant="outline" onClick={() => reveal('profile')}>
                Все сведения и действия
              </Button>
            </div>
          </Panel>
        </div>
        <div ref={fullDetails} hidden={!expanded} className="alf-reference-details">
          <div className="alf-profile">{content}</div>
        </div>
        <ErrorToast message={error} />
      </section>
    );
  }
  return (
    <Dialog.Root
      open
      onOpenChange={value => {
        if (!value) close();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="drawer-overlay" />
        <Dialog.Content className="drawer">
          <Dialog.Close className="drawer-close" aria-label="Закрыть профиль">
            <X size={20} />
          </Dialog.Close>
          <Dialog.Title className="drawer-title">{lead ? `@${lead.username}` : 'Профиль'}</Dialog.Title>
          <Dialog.Description className="helper">Анализ профиля и причины оценки</Dialog.Description>
          {content}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
