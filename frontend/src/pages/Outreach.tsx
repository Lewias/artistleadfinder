import { useState } from 'react';
import { Plus } from 'lucide-react';
import { useResource } from '../hooks/useResource';
import type { OutreachCampaign } from '../services/types';
import { date, number } from '../lib/format';
import { PageHeader } from '../components/PageHeader';
import { Button } from '../components/ui/button';
import { DataState, StatusBadge } from '../components/DataState';
import { CampaignWizard } from '../components/outreach/CampaignWizard';
import { CampaignView } from '../components/outreach/CampaignView';
import { TemplatesPanel } from '../components/outreach/TemplatesPanel';
import { SendersPanel } from '../components/outreach/SendersPanel';
import { campaignStatusLabels } from '../components/outreach/outreachText';

type Tab = 'campaigns' | 'templates' | 'senders';
type View = { kind: 'list' } | { kind: 'new'; leadIds?: number[] } | { kind: 'campaign'; id: number };

export function Outreach({ preset, clearPreset }: { preset?: number[]; clearPreset: () => void }) {
  const [tab, setTab] = useState<Tab>('campaigns');
  const [view, setView] = useState<View>(
    preset?.length ? { kind: 'new', leadIds: preset } : { kind: 'list' },
  );
  const campaigns = useResource<OutreachCampaign[]>('outreach.campaigns', {}, 5000);
  const show = (next: View) => {
    clearPreset();
    setView(next);
  };
  return (
    <>
      <PageHeader
        page="outreach"
        count={number(campaigns.data?.length || 0)}
        actions={
          tab === 'campaigns' &&
          view.kind === 'list' && (
            <Button onClick={() => show({ kind: 'new' })}>
              <Plus size={16} /> Новая кампания
            </Button>
          )
        }
      />
      <div className="segmented outreach-tabs" role="tablist" aria-label="Рассылки">
        {(
          [
            ['campaigns', 'Кампании'],
            ['templates', 'Шаблоны'],
            ['senders', 'Отправители'],
          ] as const
        ).map(([id, label]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            className={tab === id ? 'active' : ''}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
      {tab === 'templates' && <TemplatesPanel />}
      {tab === 'senders' && <SendersPanel />}
      {tab === 'campaigns' && view.kind === 'new' && (
        <CampaignWizard
          initialLeadIds={view.leadIds}
          onCancel={() => show({ kind: 'list' })}
          onCreated={id => {
            campaigns.refresh();
            show({ kind: 'campaign', id });
          }}
        />
      )}
      {tab === 'campaigns' && view.kind === 'campaign' && (
        <CampaignView id={view.id} back={() => show({ kind: 'list' })} />
      )}
      {tab === 'campaigns' && view.kind === 'list' && (
        <section className="panel">
          <DataState {...campaigns} retry={campaigns.refresh} />
          {campaigns.data && (
            <div className="table-container">
              <table>
                <thead>
                  <tr>
                    <th>Кампания</th>
                    <th>Статус</th>
                    <th>Получателей</th>
                    <th>Отправлено</th>
                    <th>В очереди</th>
                    <th>Пропущено</th>
                    <th>Ошибок</th>
                    <th>Ответов</th>
                    <th>Создана</th>
                  </tr>
                </thead>
                <tbody>
                  {campaigns.data.map(item => (
                    <tr key={item.id}>
                      <td>
                        <button
                          className="text-action"
                          onClick={() => show({ kind: 'campaign', id: item.id })}
                        >
                          {item.name}
                        </button>
                      </td>
                      <td>
                        <StatusBadge value={item.status} label={campaignStatusLabels[item.status]} />
                      </td>
                      <td>{number(item.total_recipients)}</td>
                      <td>{number(item.sent_count)}</td>
                      <td>{number(item.queued_count)}</td>
                      <td>{number(item.skipped_count)}</td>
                      <td>{number(item.failed_count)}</td>
                      <td>{number(item.replied_count)}</td>
                      <td>{date(item.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!campaigns.data.length && (
                <p className="empty-copy">
                  Кампаний пока нет. Создайте шаблон, затем нажмите «Новая кампания» или выберите лидов в
                  «Базе артистов».
                </p>
              )}
            </div>
          )}
        </section>
      )}
    </>
  );
}
