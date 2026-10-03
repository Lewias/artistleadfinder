import { useEffect, useState } from 'react';
import { getVersion } from '@tauri-apps/api/app';
import {
  AudioLines,
  BarChart3,
  BookUser,
  Database,
  History,
  MessageSquareText,
  ScanSearch,
  ScrollText,
  Send,
  Settings as SettingsIcon,
  UserRound,
  type LucideIcon,
} from 'lucide-react';
import { channels, pages, type Channel, type PageId } from './navigation';
import { useResource } from './hooks/useResource';
import type { CrmList, IMessageState, OutreachWorkspace, SearchJob, ScoutAccountRow } from './services/types';
import { api } from './services/api';
import { number } from './lib/format';
import { Dashboard } from './pages/Dashboard';
import { Discovery } from './pages/Discovery';
import { Leads } from './pages/Leads';
import { SearchHistory } from './pages/SearchHistory';
import { Settings } from './pages/Settings';
import { Outreach } from './pages/Outreach';
import { IMessageCampaigns } from './pages/IMessageCampaigns';
import { IMessageLog } from './pages/IMessageLog';
import { Crm } from './pages/Crm';
import { BrowserProfiles } from './components/BrowserProfiles';
import { SectionTransition } from './components/SectionTransition';
import { UpdateNotice } from './components/UpdateNotice';
import { useUpdater } from './hooks/useUpdater';

const icons: Record<PageId, LucideIcon> = {
  discovery: ScanSearch,
  profiles: UserRound,
  leads: Database,
  outreach: Send,
  history: History,
  dashboard: BarChart3,
  imessage: MessageSquareText,
  'imessage-log': ScrollText,
  crm: BookUser,
  'imessage-crm': BookUser,
  settings: SettingsIcon,
};

function useNavCounts(): Partial<Record<PageId, number>> {
  const sources = useResource<string[]>('scout.sources', {}, 15000);
  const accounts = useResource<ScoutAccountRow[]>('scout.accounts', {}, 15000);
  const leads = useResource<{ total: number }>('leads.list', { page_size: 1 }, 15000);
  const jobs = useResource<SearchJob[]>('jobs.list', {}, 15000);
  const outreach = useResource<OutreachWorkspace>('outreach.workspace', {}, 15000);
  const imessage = useResource<IMessageState>('imessage.state', {}, 15000);
  const crm = useResource<CrmList>('crm.list', { crm: 'instagram', page_size: 1 }, 15000);
  const imessageCrm = useResource<CrmList>('crm.list', { crm: 'imessage', page_size: 1 }, 15000);
  return {
    crm: crm.data?.counts.all,
    'imessage-crm': imessageCrm.data?.counts.all,
    imessage: imessage.data?.workspace.recipients.length,
    outreach: outreach.data?.usernames.length,
    discovery: sources.data?.length,
    profiles: accounts.data?.length,
    leads: leads.data?.total,
    history: jobs.data?.length,
  };
}

export function App() {
  const core = useResource<{ version: string }>('system.info');
  const [active, setActive] = useState<PageId>('discovery');
  const [channel, setChannel] = useState<Channel>('instagram');
  // Each channel reopens on the section it was left on.
  const [lastPage, setLastPage] = useState<Record<Channel, PageId>>({
    instagram: 'discovery',
    imessage: 'imessage',
  });
  const navigate = (page: PageId) => {
    setActive(page);
    const owner = pages.find(item => item.id === page);
    if (owner?.placement === 'main') {
      setChannel(owner.channel);
      setLastPage(value => ({ ...value, [owner.channel]: page }));
    }
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
          {pages.filter(item => item.placement === 'main' && item.channel === channel).map(navButton)}
        </nav>
        <nav className="sidebar-footer" aria-label="Параметры">
          {pages.filter(item => item.placement === 'footer').map(navButton)}
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
        </header>
        {/* Board pages use the full window width, like the reference layout at 1920×1080. */}
        <div className="page">
          {/* The previous page unmounts on a switch anyway, so the key costs no form state. */}
          <SectionTransition sectionKey={active}>
            {active === 'dashboard' ? (
              <Dashboard navigate={navigate} />
            ) : active === 'discovery' ? (
              <Discovery />
            ) : active === 'leads' ? (
              <Leads
                onCampaign={async ids => {
                  await api.request('outreach.workspace_add_leads', { lead_ids: ids });
                  navigate('outreach');
                }}
              />
            ) : active === 'outreach' ? (
              <Outreach />
            ) : active === 'history' ? (
              <SearchHistory />
            ) : active === 'profiles' ? (
              <BrowserProfiles />
            ) : active === 'imessage' ? (
              <IMessageCampaigns />
            ) : active === 'imessage-log' ? (
              <IMessageLog />
            ) : active === 'crm' ? (
              <Crm crm="instagram" onWrite={() => navigate('outreach')} />
            ) : active === 'imessage-crm' ? (
              <Crm crm="imessage" onWrite={() => navigate('imessage')} />
            ) : (
              <Settings updater={{ ...updater, version }} />
            )}
          </SectionTransition>
        </div>
      </main>
    </div>
  );
}
