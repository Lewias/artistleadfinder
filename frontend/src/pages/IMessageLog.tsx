import { useState } from 'react';
import { useResource } from '../hooks/useResource';
import type { IMessageCampaign, IMessageEvent } from '../services/types';
import { PageHeader } from '../components/PageHeader';
import { DataState } from '../components/DataState';
import { campaignStatusLabel, eventLabel, eventTone } from '../components/imessage/imessageText';

const stamp = (value: string) =>
  new Date(value).toLocaleString('ru-RU', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });

export function IMessageLog() {
  const [campaignId, setCampaignId] = useState('');
  const campaigns = useResource<IMessageCampaign[]>('imessage.campaigns', {}, 10000);
  const events = useResource<IMessageEvent[]>(
    'imessage.events',
    campaignId ? { campaign_id: Number(campaignId), limit: 500 } : { limit: 300 },
    3000,
  );
  return (
    <div className="screen">
      <PageHeader page="imessage-log" count={events.data?.length} />
      <section className="panel">
        <div className="board-heading">
          <label className="log-filter">
            Рассылка
            <select value={campaignId} onChange={event => setCampaignId(event.target.value)}>
              <option value="">Все события</option>
              {campaigns.data?.map(item => (
                <option key={item.id} value={item.id}>
                  {item.is_test ? 'Тест' : 'Рассылка'} №{item.id} · {campaignStatusLabel[item.status]} ·{' '}
                  {item.counts.execution_acknowledged}/{item.total}
                </option>
              ))}
            </select>
          </label>
          <span className="helper">
            «Выполнено» — Shortcut подтвердил свои шаги; это не подтверждение доставки.
          </span>
        </div>
        {!events.data && <DataState loading={events.loading} error={events.error} retry={events.refresh} />}
        <div className="log-viewer imessage-log">
          {events.data?.map(event => (
            <pre key={event.id} className={eventTone[event.type] ?? ''}>
              <span className="log-time">{stamp(event.created_at)}</span>{' '}
              <b>{eventLabel[event.type] ?? event.type}</b>
              {event.campaign_id ? ` · #${event.campaign_id}` : ''} — {event.detail}
            </pre>
          ))}
          {events.data && !events.data.length && <p className="empty-copy">Событий пока нет.</p>}
        </div>
      </section>
    </div>
  );
}
