import { useResource } from '../hooks/useResource';
import type { OutreachWorkspace, ScoutAccountRow, ScoutSourceRow } from '../services/types';
import { PageHeader } from './PageHeader';
import { plural } from '../lib/format';
import { ScoutAccounts } from './ScoutAccounts';
import { ScoutActivity } from './ScoutActivity';
import { ScoutBoard } from './ScoutBoard';

export function ScoutDiscovery() {
  const sources = useResource<ScoutSourceRow[]>('scout.source_list', {}, 5000);
  const accounts = useResource<ScoutAccountRow[]>('scout.accounts', {}, 2000);
  // Accounts ticked on the «Аккаунты» page; the board's «Парсинг» starts on them.
  const workspace = useResource<OutreachWorkspace>('outreach.workspace', {}, 5000);
  const refreshRuns = () => {
    accounts.refresh();
    sources.refresh();
  };
  return (
    <section className="scout-workspace">
      <div className="screen">
        <PageHeader
          page="discovery"
          count={
            sources.data ? plural(sources.data.length, ['источник', 'источника', 'источников']) : undefined
          }
        />
        <ScoutBoard
          rows={sources.data}
          accounts={accounts.data ?? []}
          selected={workspace.data?.sender_ids ?? []}
          refresh={refreshRuns}
        />
      </div>
      <ScoutAccounts rows={accounts.data} error={accounts.error} refresh={refreshRuns} />
      <ScoutActivity accounts={accounts.data || []} />
    </section>
  );
}
