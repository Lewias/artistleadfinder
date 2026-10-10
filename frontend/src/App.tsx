import { useEffect, useState } from 'react';
import { getVersion } from '@tauri-apps/api/app';
import {
  AudioLines,
  BarChart3,
  BookUser,
  CloudCog,
  Contact,
  Database,
  History,
  LayoutTemplate,
  MessageSquareText,
  ScanSearch,
  ScrollText,
  Settings as SettingsIcon,
  Sparkles,
  UserRound,
  UsersRound,
  type LucideIcon,
} from 'lucide-react';
import { channels, pages, type Channel, type PageId } from './navigation';
import { useResource } from './hooks/useResource';
import type {
  AccountState,
  CrmList,
  IMessageState,
  IMessageTemplate,
  SearchJob,
  ScoutAccountRow,
} from './services/types';
import { api } from './services/api';
import { number } from './lib/format';
import { Dashboard } from './pages/Dashboard';
import { Discovery } from './pages/Discovery';
import { Cloud } from './pages/Cloud';
import type { WorkTab } from './components/ScoutDiscovery';
import { Leads } from './pages/Leads';
import { SearchHistory } from './pages/SearchHistory';
import { Settings } from './pages/Settings';
import { IMessageCampaigns } from './pages/IMessageCampaigns';
import { IMessageLog } from './pages/IMessageLog';
import { IMessageTemplates } from './pages/IMessageTemplates';
import { Crm } from './pages/Crm';
import { Admin } from './pages/Admin';
import { Assistant } from './pages/Assistant';
import { BrowserProfiles } from './components/BrowserProfiles';
import { SectionTransition } from './components/SectionTransition';
import { ErrorBoundary, ErrorToast, Toaster } from './components/Toaster';
import { UpdateNotice } from './components/UpdateNotice';
import { AccountGate, AccountMenu } from './components/account/AccountGate';
import { useUpdater } from './hooks/useUpdater';

const icons: Record<PageId, LucideIcon> = {
  discovery: ScanSearch,
  cloud: CloudCog,
  profiles: UserRound,
  leads: Database,
  history: History,
  dashboard: BarChart3,
  imessage: MessageSquareText,
  'imessage-templates': LayoutTemplate,
  'imessage-log': ScrollText,
  crm: BookUser,
  'crm-users': Contact,
  'imessage-crm': BookUser,
  'imessage-crm-users': Contact,
  assistant: Sparkles,
  admin: UsersRound,
  settings: SettingsIcon,
};

function useNavCounts(): Partial<Record<PageId, number>> {
  const sources = useResource<string[]>('scout.sources', {}, 15000);
  const accounts = useResource<ScoutAccountRow[]>('scout.accounts', {}, 15000);
  const leads = useResource<{ total: number }>('leads.list', { page_size: 1 }, 15000);
  const jobs = useResource<SearchJob[]>('jobs.list', {}, 15000);
  const imessage = useResource<IMessageState>('imessage.state', {}, 15000);
  const templates = useResource<IMessageTemplate[]>('imessage.templates', {}, 15000);
  const crm = useResource<CrmList>('crm.list', { crm: 'instagram', page_size: 1 }, 15000);
  const imessageCrm = useResource<CrmList>('crm.list', { crm: 'imessage', page_size: 1 }, 15000);
  return {
    crm: crm.data?.counts.all,
    'imessage-crm': imessageCrm.data?.counts.all,
    imessage: imessage.data?.workspace.recipients.length,
    'imessage-templates': templates.data?.length,
    discovery: sources.data?.length,
    profiles: accounts.data?.length,
    leads: leads.data?.total,
    history: jobs.data?.length,
  };
}

/** The sign-in comes first when accounts are on; the workspace opens once it is ready. */
export function App() {
  const resource = useResource<AccountState>('account.state', {}, 10000);
  const [account, setAccount] = useState<AccountState>();
  useEffect(() => {
    if (resource.data) setAccount(resource.data);
  }, [resource.data]);
  const locked = account?.configured && !account.ready;
  return (
    <>
      {account && locked ? (
        <AccountGate state={account} onChange={setAccount} />
      ) : account || resource.error ? (
        <Workspace account={account} onAccount={setAccount} />
      ) : (
        <div className="gate" />
      )}
      <Toaster />
    </>
  );
}

function Workspace({
  account,
  onAccount,
}: {
  account?: AccountState;
  onAccount: (next: AccountState) => void;
}) {
  const core = useResource<{ version: string }>('system.info');
  const admin = account?.role === 'admin' && account.configured;
  // Admin and moderator see every user's CRM, signed with the owner.
  const allCrm = (account?.role === 'admin' || account?.role === 'moderator') && account.configured;
  const [active, setActive] = useState<PageId>('discovery');
  const [channel, setChannel] = useState<Channel>('instagram');
  // Each channel reopens on the section it was left on.
  const [lastPage, setLastPage] = useState<Record<Channel, PageId>>({
    instagram: 'discovery',
    imessage: 'imessage',
  });
  const [workTab, setWorkTab] = useState<WorkTab>('auto');
  const navigate = (page: PageId) => {
    setActive(page);
    const owner = pages.find(item => item.id === page);
    if (owner?.placement === 'main') {
      setChannel(owner.channel);
      setLastPage(value => ({ ...value, [owner.channel]: page }));
    }
  };
  // «Написать» from the base or the CRM opens the hand-made outreach list.
  const writeTo = () => {
    setWorkTab('list');
    navigate('discovery');
  };
  const switchChannel = (next: Channel) => {
    if (next === channel) return;
    setChannel(next);
    setActive(lastPage[next]);
  };
  const section = channels.find(item => item.id === channel)!;
  const counts = useNavCounts();
  const updater = useUpdater();
  const [version, setVersion] = useState<string>();
  useEffect(() => {
    getVersion()
      .then(setVersion)
      .catch(() => undefined);
  }, []);
  const navButton = (item: (typeof pages)[number]) => {
    const Icon = icons[item.id];
    const count = counts[item.id];
    return (
      <button
        key={item.id}
        className={`nav-item${active === item.id ? ' active' : ''}`}
        aria-current={active === item.id ? 'page' : undefined}
        onClick={() => navigate(item.id)}
      >
        <Icon size={18} strokeWidth={1.8} />
        <span>{item.label}</span>
        {count !== undefined && <span className="nav-count">{number(count)}</span>}
      </button>
    );
  };
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-logo">
            <AudioLines size={17} strokeWidth={2.4} />
          </span>
          <span className="brand-name">Artist Lead Finder</span>
          <span className="version-pill">v{version || core.data?.version || '0.1.0'}</span>
        </div>
        <div className="nav-section">{section.section}</div>
        <nav aria-label="Основная навигация">
          {pages
            .filter(
              item =>
                item.placement === 'main' &&
                item.channel === channel &&
                (allCrm || (item.id !== 'crm-users' && item.id !== 'imessage-crm-users')),
            )
            .map(navButton)}
        </nav>
        <nav className="sidebar-footer" aria-label="Параметры">
          {pages.filter(item => item.placement === 'footer' && (item.id !== 'admin' || admin)).map(navButton)}
        </nav>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="channel-switch" role="tablist" aria-label="Канал">
            {channels.map(item => (
              <button
                key={item.id}
                type="button"
                role="tab"
                aria-selected={channel === item.id}
                className={`channel-pill${channel === item.id ? ' active' : ''}`}
                onClick={() => switchChannel(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
          <UpdateNotice state={updater.state} install={updater.install} />
          <span
            className={`core-status ${core.error ? 'offline' : core.data ? 'connected' : 'pending'}`}
            title={core.error || undefined}
          >
            <i />
            {core.error ? 'Ядро недоступно' : core.data ? 'Ядро подключено' : 'Подключение…'}
          </span>
          {account?.configured && <AccountMenu state={account} onChange={onAccount} />}
        </header>
        {/* Board pages use the full window width, like the reference layout at 1920×1080. */}
        <div className="page">
          {/* The previous page unmounts on a switch anyway, so the key costs no form state. */}
          <SectionTransition sectionKey={active}>
            <ErrorBoundary key={active}>
              {active === 'dashboard' ? (
                <Dashboard navigate={navigate} />
              ) : active === 'discovery' ? (
                <Discovery tab={workTab} onTab={setWorkTab} />
              ) : active === 'cloud' ? (
                <Cloud />
              ) : active === 'leads' ? (
                <Leads
                  onCampaign={async ids => {
                    await api.request('outreach.workspace_add_leads', { lead_ids: ids });
                    writeTo();
                  }}
                />
              ) : active === 'history' ? (
                <SearchHistory />
              ) : active === 'profiles' ? (
                <BrowserProfiles />
              ) : active === 'imessage' ? (
                <IMessageCampaigns />
              ) : active === 'imessage-templates' ? (
                <IMessageTemplates onUsed={() => navigate('imessage')} />
              ) : active === 'imessage-log' ? (
                <IMessageLog />
              ) : active === 'crm' ? (
                <Crm crm="instagram" onWrite={writeTo} />
              ) : active === 'imessage-crm' ? (
                <Crm crm="imessage" onWrite={() => navigate('imessage')} />
              ) : active === 'crm-users' && allCrm ? (
                <Crm key="crm-users" crm="instagram" others onWrite={writeTo} />
              ) : active === 'imessage-crm-users' && allCrm ? (
                <Crm key="imessage-crm-users" crm="imessage" others onWrite={() => navigate('imessage')} />
              ) : active === 'assistant' ? (
                <Assistant />
              ) : active === 'admin' && admin ? (
                <Admin />
              ) : (
                <Settings updater={{ ...updater, version }} />
              )}
            </ErrorBoundary>
          </SectionTransition>
        </div>
      </main>
      <ErrorToast message={core.error} title="Ядро недоступно" />
    </div>
  );
}
