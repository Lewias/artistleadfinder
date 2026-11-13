import { ErrorToast } from './Toaster';
import { errorText } from '../lib/errors';
import { useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { X, ExternalLink } from 'lucide-react';
import { api } from '../services/api';
import { useResource } from '../hooks/useResource';
import type { LeadDetail as Detail } from '../services/types';
import { number, activity, relativeTime, statusLabels } from '../lib/format';
import { DataState } from './DataState';
import { Button } from './ui/button';
import { categoryLabels, methodLabel } from './scoutStatus';
import { conversationLabels, dateTime, reasonLabel, recipientStatusLabels } from './outreach/outreachText';

const decidedByLabels = { local: 'локально', ai: 'AI', 'local+ai': 'локально + AI' };

export function LeadDetail({ id, close, refresh }: { id: number; close: () => void; refresh: () => void }) {
  const resource = useResource<Detail>('leads.detail', { id });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [ignored, setIgnored] = useState(false);
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
          {!lead ? (
            <DataState {...resource} retry={resource.refresh} />
          ) : (
            <>
              <div className="avatar large">{lead.display_name.slice(0, 1) || lead.username.slice(0, 1)}</div>
              <h2>{lead.display_name}</h2>
              <p>{lead.genres.join(' / ') || 'Жанр не определён'}</p>
              <p className="helper">
                {capture?.unknown_fields.includes('followers')
                  ? 'Подписчики не прочитаны'
                  : `${number(lead.followers)} подписчиков`}{' '}
                · {lead.platform === 'mock' ? 'Демонстрационный профиль' : lead.platform}
                {lead.is_private && ' · Закрытый аккаунт'}
              </p>
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
              <label>
                Статус CRM
                <select
                  value={lead.status}
                  disabled={busy}
                  onChange={event => void action(event.target.value)}
                >
                  {['new', 'reviewed', 'qualified', 'rejected', 'contacted'].map(status => (
                    <option key={status} value={status}>
                      {statusLabels[status]}
                    </option>
                  ))}
                </select>
              </label>
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
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
