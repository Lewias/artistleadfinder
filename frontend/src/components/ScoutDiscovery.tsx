import type { ReactNode } from 'react';
import { Inbox, ListChecks, Rocket } from 'lucide-react';
import { useResource } from '../hooks/useResource';
import type {
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

export type WorkTab = 'auto' | 'list' | 'replies';

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
