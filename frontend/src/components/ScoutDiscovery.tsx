import type { ReactNode } from 'react';
import { Inbox, ListChecks, Repeat, Rocket } from 'lucide-react';
import { useResource } from '../hooks/useResource';
import type {
  FollowUpRuns,
  InboxState,
  OutreachSender,
  OutreachWorkspace,
  ScoutAccountRow,
  ScoutSourceRow,
} from '../services/types';
import { number, plural } from '../lib/format';
import { PageHeader } from './PageHeader';
import { ScoutAccounts } from './ScoutAccounts';
import { ScoutActivity } from './ScoutActivity';
import { ScoutBoard } from './ScoutBoard';
import { MessagesBoard } from './outreach/MessagesBoard';
import { AutopilotBar } from './outreach/AutopilotBar';
import { OutreachListTab } from './outreach/OutreachListTab';
import { InboxPanel } from './outreach/InboxPanel';
import { FollowUpPanel } from './outreach/FollowUps';

export type WorkTab = 'auto' | 'list' | 'followups' | 'replies';

/** «Парсер и рассылка»: sources, messages and one button; the hand-made list and the replies beside. */
export function ScoutDiscovery({
  tab,
  onTab,
  extra,
}: {
  tab: WorkTab;
  onTab: (tab: WorkTab) => void;
  /** Rarely used tools under the main flow. */
  extra?: ReactNode;
}) {
  const sources = useResource<ScoutSourceRow[]>('scout.source_list', {}, 5000);
  const accounts = useResource<ScoutAccountRow[]>('scout.accounts', {}, 2000);
  // Accounts ticked in «Настройки»; the parser and the outreach both run on them.
  const workspace = useResource<OutreachWorkspace>('outreach.workspace', {}, 5000);
  const senders = useResource<OutreachSender[]>('outreach.senders', {}, 5000);
  const inbox = useResource<InboxState>('inbox.state', {}, 10000);
  const followups = useResource<FollowUpRuns>('outreach.followups', { group: 'open', page_size: 1 }, 15000);
  const review = followups.data?.counts.review ?? 0;
  const active = followups.data?.counts.active ?? 0;
  const refreshRuns = () => {
    accounts.refresh();
    sources.refresh();
  };
  const tabs: [WorkTab, ReactNode][] = [
    [
      'auto',
      <>
        <Rocket size={14} /> Найти и написать
      </>,
    ],
    [
      'list',
      <>
        <ListChecks size={14} /> Рассылка по списку
        {!!workspace.data?.usernames.length && (
          <span className="crm-tab-count">{number(workspace.data.usernames.length)}</span>
        )}
      </>,
    ],
    [
      'followups',
      <>
        <Repeat size={14} /> Follow-up
        {!!(review || active) && (
          <span
            className={`crm-tab-count${review ? ' attention' : ''}`}
            title={review ? 'Проверьте отправку' : undefined}
          >
            {number(review || active)}
          </span>
        )}
      </>,
    ],
    [
      'replies',
      <>
        <Inbox size={14} /> Ответы
        {!!inbox.data?.counts.new && <span className="crm-tab-count">{number(inbox.data.counts.new)}</span>}
      </>,
    ],
  ];
  return (
    <section className="scout-workspace">
      <div className="screen work-flow">
        <PageHeader
          page="discovery"
          count={
            sources.data ? plural(sources.data.length, ['источник', 'источника', 'источников']) : undefined
          }
        />
        <div className="crm-tabs-row">
          <div className="crm-tabs" role="tablist" aria-label="Парсер и рассылка">
            {tabs.map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={tab === id}
                className={tab === id ? 'active' : ''}
                onClick={() => onTab(id)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        {tab === 'auto' && (
          <>
            <ScoutBoard
              rows={sources.data}
              accounts={accounts.data ?? []}
              selected={workspace.data?.sender_ids ?? []}
              refresh={refreshRuns}
            />
            <MessagesBoard />
            <AutopilotBar accounts={accounts.data ?? []} />
          </>
        )}
        {tab === 'list' && <OutreachListTab />}
        {tab === 'followups' && <FollowUpPanel />}
        {tab === 'replies' && <InboxPanel senders={senders.data} />}
      </div>
      {tab === 'auto' && (
        <>
          <ScoutAccounts rows={accounts.data} error={accounts.error} refresh={refreshRuns} />
          <ScoutActivity accounts={accounts.data || []} />
          {extra}
        </>
      )}
    </section>
  );
}
