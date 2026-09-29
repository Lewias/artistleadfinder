import { useState } from 'react';
import { ArrowLeft, Pause, Play, Send, Square } from 'lucide-react';
import { api } from '../../services/api';
import { useResource } from '../../hooks/useResource';
import type { CampaignDetail, CampaignPreview, CampaignRecipient, OutreachEvent } from '../../services/types';
import { number } from '../../lib/format';
import { Button } from '../ui/button';
import { DataState, StatusBadge } from '../DataState';
import { categoryLabels } from '../scoutStatus';
import { PreviewSummary } from './CampaignWizard';
import {
  campaignStatusLabels,
  dateTime,
  outreachActivity,
  reasonLabel,
  recipientStatusLabels,
  senderStatusLabels,
  type OutreachActivity,
} from './outreachText';

const recipientFilters = ['', 'queued', 'sent', 'replied', 'skipped', 'failed', 'cancelled'];

export function CampaignView({ id, back }: { id: number; back: () => void }) {
  const campaign = useResource<CampaignDetail>('outreach.campaign', { id }, 3000);
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const table = useResource<{ total: number; items: CampaignRecipient[] }>(
    'outreach.recipients',
    { id, status, page, page_size: 50 },
    4000,
  );
  const events = useResource<OutreachEvent[]>('outreach.events', { campaign_id: id, limit: 200 }, 3000);
  const draft = campaign.data?.status === 'draft';
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const run = async (method: string, params: object) => {
    setBusy(true);
    setError('');
    try {
      await api.request(method, params);
      campaign.refresh();
      table.refresh();
      events.refresh();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };
  const data = campaign.data;
  if (!data)
    return (
      <section className="panel">
        <DataState {...campaign} retry={campaign.refresh} />
      </section>
    );
  const activity = (events.data || [])
    .map(outreachActivity)
    .filter((item): item is OutreachActivity => item !== null)
    .reverse()
    .slice(0, 40);
  const control = (action: string) => void run('outreach.campaign_control', { id, action });
  return (
    <div className="campaign-view">
      <section className="panel">
        <div className="section-heading">
          <div>
            <button className="text-action" onClick={back}>
              <ArrowLeft size={14} /> Все кампании
            </button>
            <h2>{data.name}</h2>
            <p className="helper">
              {data.template ? `Шаблон «${data.template.name}»` : 'Шаблон удалён'}
              {data.sequence ? ` · follow-up «${data.sequence.name}»` : ' · без follow-up'}
              {data.scheduled_at ? ` · старт ${dateTime(data.scheduled_at)}` : ''}
            </p>
          </div>
          <div className="actions">
            <StatusBadge value={data.status} label={campaignStatusLabels[data.status]} />
            {data.status === 'draft' && (
              <Button disabled={busy} onClick={() => void run('outreach.campaign_start', { id })}>
                <Send size={15} /> Запустить кампанию
              </Button>
            )}
            {(data.status === 'running' || data.status === 'scheduled') && (
              <Button variant="outline" disabled={busy} onClick={() => control('pause')}>
                <Pause size={15} /> Пауза
              </Button>
            )}
            {data.status === 'paused' && (
              <Button disabled={busy} onClick={() => control('resume')}>
                <Play size={15} /> Продолжить
              </Button>
            )}
            {['draft', 'scheduled', 'running', 'paused'].includes(data.status) && (
              <Button variant="outline" disabled={busy} onClick={() => control('cancel')}>
                <Square size={14} /> Отменить
              </Button>
            )}
          </div>
        </div>
        <dl className="run-stats">
          <div>
            <dt>Получателей</dt>
            <dd>{number(data.total_recipients)}</dd>
          </div>
          <div>
            <dt>Отправлено</dt>
            <dd className="good">{number(data.sent_count)}</dd>
          </div>
          <div>
            <dt>В очереди</dt>
            <dd>{number(data.queued_count)}</dd>
          </div>
          <div>
            <dt>Пропущено</dt>
            <dd>{number(data.skipped_count)}</dd>
          </div>
          <div>
            <dt>Ошибок</dt>
            <dd className={data.failed_count ? 'bad' : ''}>{number(data.failed_count)}</dd>
          </div>
          <div>
            <dt>Ответов</dt>
            <dd className="good">{number(data.replied_count)}</dd>
          </div>
        </dl>
        {Object.keys(data.reasons).length > 0 && (
          <ul className="skip-breakdown">
            {Object.entries(data.reasons).map(([reason, count]) => (
              <li key={reason}>
                {reasonLabel(reason)} <b>{count}</b>
              </li>
            ))}
          </ul>
        )}
        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
      </section>

      {draft && <DraftPreview id={id} />}

      <section className="panel">
        <h2>Аккаунты-отправители</h2>
        <p className="helper">
          Сообщения уходят из открытого окна браузера аккаунта (раздел «Аккаунты»). Пока идёт отправка, не
          пользуйтесь этим окном и не запускайте в нём Lead Scout.
        </p>
        <div className="sender-rows">
          {data.senders.map(sender => (
            <div key={sender.id} className="sender-row">
              <strong>{sender.name}</strong>
              <StatusBadge
                value={sender.status === 'active' ? 'completed' : 'failed'}
                label={senderStatusLabels[sender.status]}
              />
              <span className="helper">
                {sender.sent_24h}/{sender.daily_limit} за 24 ч{sender.reason ? ` · ${sender.reason}` : ''}
                {sender.until ? ` · до ${dateTime(sender.until)}` : ''}
              </span>
              {sender.status !== 'active' && (
                <Button
                  variant="outline"
                  disabled={busy}
                  title="Сначала устраните причину в окне браузера аккаунта"
                  onClick={() => void run('outreach.sender_status', { id: sender.id, status: 'active' })}
                >
                  Возобновить
                </Button>
              )}
            </div>
          ))}
        </div>
      </section>

      <section className="panel">
        <h2>Недавнее</h2>
        {activity.length ? (
          <ul className="activity-list outreach-activity">
            {activity.map(item => (
              <li key={item.id} className={`activity-${item.tone}`}>
                <strong>{item.username}</strong>
                <span>{item.outcome}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty-copy">Пока нет событий.</p>
        )}
      </section>

      <section className="panel">
        <div className="section-heading">
          <h2>Получатели</h2>
          <div className="segmented" role="tablist" aria-label="Статус получателя">
            {recipientFilters.map(value => (
              <button
                key={value || 'all'}
                type="button"
                role="tab"
                aria-selected={status === value}
                className={status === value ? 'active' : ''}
                onClick={() => {
                  setStatus(value);
                  setPage(1);
                }}
              >
                {value ? recipientStatusLabels[value] : 'Все'}
              </button>
            ))}
          </div>
        </div>
        <div className="table-container recipient-table">
          <table>
            <thead>
              <tr>
                <th>Instagram · имя · тип</th>
                <th>Подписчики</th>
                <th>Отправитель</th>
                <th>Статус</th>
                <th>Сообщение</th>
                <th>Отправлено</th>
                <th>Причина</th>
              </tr>
            </thead>
            <tbody>
              {(table.data?.items || []).map(row => (
                <tr key={row.id}>
                  <td>
                    <strong>@{row.username}</strong>
                    <small className="cell-sub">
                      {[row.display_name, row.profile_type ? categoryLabels[row.profile_type] : '']
                        .filter(Boolean)
                        .join(' · ') || '—'}
                    </small>
                  </td>
                  <td>{number(row.followers)}</td>
                  <td>{row.sender_name || '—'}</td>
                  <td>
                    <StatusBadge
                      value={
                        row.status === 'sent' ? 'completed' : row.status === 'queued' ? 'queued' : row.status
                      }
                      label={row.replied_at ? 'Ответил' : recipientStatusLabels[row.status]}
                    />
                  </td>
                  <td className="message-cell" title={row.rendered_message || undefined}>
                    {row.rendered_message || '—'}
                  </td>
                  <td>{dateTime(row.sent_at)}</td>
                  <td className="reason-cell">
                    {row.failure_reason || row.skip_reason ? (
                      <span title={row.reason_details || undefined}>
                        {reasonLabel(row.failure_reason || row.skip_reason)}
                        {row.reason_details && <small className="cell-sub">{row.reason_details}</small>}
                      </span>
                    ) : (
                      '—'
                    )}
                    {row.needs_review && (
                      <span className="review-actions">
                        <Button
                          variant="outline"
                          disabled={busy}
                          title="В диалоге Instagram сообщение есть"
                          onClick={() =>
                            void run('outreach.resolve_review', { recipient_id: row.id, sent: true })
                          }
                        >
                          Ушло
                        </Button>
                        <Button
                          variant="outline"
                          disabled={busy}
                          title="В диалоге Instagram сообщения нет"
                          onClick={() =>
                            void run('outreach.resolve_review', { recipient_id: row.id, sent: false })
                          }
                        >
                          Не ушло
                        </Button>
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!table.data?.items.length && <p className="empty-copy">Нет получателей с таким статусом.</p>}
        </div>
        {(table.data?.total || 0) > 50 && (
          <div className="pagination">
            <span>
              Страница {page} из {Math.ceil((table.data?.total || 0) / 50)}
            </span>
            <div className="actions">
              <Button variant="outline" disabled={page <= 1} onClick={() => setPage(value => value - 1)}>
                Назад
              </Button>
              <Button
                variant="outline"
                disabled={page * 50 >= (table.data?.total || 0)}
                onClick={() => setPage(value => value + 1)}
              >
                Далее
              </Button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}

/** Preview of a draft before Start: senders, recipients and rendered messages. */
function DraftPreview({ id }: { id: number }) {
  const preview = useResource<CampaignPreview>('outreach.preview', { campaign_id: id, limit: 5 });
  return (
    <section className="panel">
      <h2>Проверка перед запуском</h2>
      {preview.data ? (
        <PreviewSummary preview={preview.data} />
      ) : (
        <DataState {...preview} retry={preview.refresh} />
      )}
    </section>
  );
}
