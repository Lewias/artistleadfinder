import { useEffect, useState } from 'react';
import { getVersion } from '@tauri-apps/api/app';
import {
  AudioLines,
  BarChart3,
  Database,
  History,
  ScanSearch,
  Send,
  Settings as SettingsIcon,
  UserRound,
  type LucideIcon,
} from 'lucide-react';
import { pages, type PageId } from './navigation';
import { useResource } from './hooks/useResource';
import type { OutreachWorkspace, SearchJob, ScoutAccountRow } from './services/types';
import { api } from './services/api';
import { number } from './lib/format';
import { Dashboard } from './pages/Dashboard';
import { Discovery } from './pages/Discovery';
import { Leads } from './pages/Leads';
import { SearchHistory } from './pages/SearchHistory';
import { Settings } from './pages/Settings';
import { Outreach } from './pages/Outreach';
import { BrowserProfiles } from './components/BrowserProfiles';
import { UpdateNotice } from './components/UpdateNotice';
import { useUpdater } from './hooks/useUpdater';

const icons: Record<PageId, LucideIcon> = {
  discovery: ScanSearch,
  profiles: UserRound,
  leads: Database,
  outreach: Send,
  history: History,
  dashboard: BarChart3,
  settings: SettingsIcon,
};

function useNavCounts(): Partial<Record<PageId, number>> {
  const sources = useResource<string[]>('scout.sources', {}, 15000);
  const accounts = useResource<ScoutAccountRow[]>('scout.accounts', {}, 15000);
  const leads = useResource<{ total: number }>('leads.list', { page_size: 1 }, 15000);
  const jobs = useResource<SearchJob[]>('jobs.list', {}, 15000);
  const outreach = useResource<OutreachWorkspace>('outreach.workspace', {}, 15000);
  return {
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
        onClick={() => setActive(item.id)}
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
        <div className="nav-section">INSTAGRAM</div>
        <nav aria-label="Основная навигация">
          {pages.filter(item => item.placement === 'main').map(navButton)}
        </nav>
        <nav className="sidebar-footer" aria-label="Параметры">
          {pages.filter(item => item.placement === 'footer').map(navButton)}
        </nav>
      </aside>
      <main className="main">
        <header className="topbar">
          <span className="channel-pill active">Instagram</span>
          <UpdateNotice state={updater.state} install={updater.install} />
          <span
            className={`core-status ${core.error ? 'offline' : core.data ? 'connected' : 'pending'}`}
            title={core.error || undefined}
          >
            <i />
            {core.error ? 'Ядро недоступно' : core.data ? 'Ядро подключено' : 'Подключение…'}
          </span>
        </header>
        <div className="page">
          {active === 'dashboard' ? (
            <Dashboard navigate={setActive} />
          ) : active === 'discovery' ? (
            <Discovery />
          ) : active === 'leads' ? (
            <Leads
              onCampaign={async ids => {
                await api.request('outreach.workspace_add_leads', { lead_ids: ids });
                setActive('outreach');
              }}
            />
          ) : active === 'outreach' ? (
            <Outreach />
          ) : active === 'history' ? (
            <SearchHistory />
          ) : active === 'profiles' ? (
            <BrowserProfiles />
          ) : (
            <Settings updater={{ ...updater, version }} />
          )}
        </div>
      </main>
    </div>
  );
}
