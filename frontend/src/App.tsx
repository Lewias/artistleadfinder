import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from 'react';
import { getVersion } from '@tauri-apps/api/app';
import {
  AudioLines,
  BarChart3,
  BookUser,
  FolderOpen,
  Wrench,
  LayoutGrid,
  PanelLeftClose,
  PanelLeftOpen,
  Contact,
  Database,
  Disc3,
  Flame,
  History,
  LayoutTemplate,
  Mail,
  MailPlus,
  MessageCircle,
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
import { Spotify } from './pages/Spotify';
import type { WorkTab } from './components/ScoutDiscovery';
import { ArtistDatabase } from './pages/ArtistDatabase';
import { Dropdown, NavGroup, NavigationItem, useTabIndicators } from './components/ui/desktop';
import { SearchHistory } from './pages/SearchHistory';
import { Settings } from './pages/Settings';
import { IMessageCampaigns } from './pages/IMessageCampaigns';
import { IMessageLog } from './pages/IMessageLog';
import { IMessageTemplates } from './pages/IMessageTemplates';
import { Crm } from './pages/Crm';
import { Dialogs } from './pages/Dialogs';
import { Admin } from './pages/Admin';
import { Assistant } from './pages/Assistant';
import { EmailAccounts, EmailCampaigns } from './pages/Email';
import { EmailWarmup } from './pages/EmailWarmup';
import { BrowserProfiles } from './components/BrowserProfiles';
import { SectionTransition } from './components/SectionTransition';
import { ErrorBoundary, ErrorToast, Toaster } from './components/Toaster';
import { UpdateNotice } from './components/UpdateNotice';
import { AccountGate, AccountMenu } from './components/account/AccountGate';
import { useUpdater } from './hooks/useUpdater';
import { reportError } from './lib/toast';

const icons: Record<PageId, LucideIcon> = {
  discovery: ScanSearch,
  spotify: Disc3,
  profiles: UserRound,
  leads: Database,
  history: History,
  dashboard: BarChart3,
  imessage: MessageSquareText,
  'imessage-templates': LayoutTemplate,
  'imessage-log': ScrollText,
  'imessage-dialogs': MessageCircle,
  crm: BookUser,
  'crm-users': Contact,
  'imessage-crm': BookUser,
  'imessage-crm-users': Contact,
  email: Mail,
  'email-accounts': MailPlus,
  'email-warmup': Flame,
  'email-crm': BookUser,
  assistant: Sparkles,
  admin: UsersRound,
  settings: SettingsIcon,
};

// Shown to admins and moderators only; a plain user does not see them at all.
const staffPages: PageId[] = ['crm-users', 'imessage-crm-users', 'assistant'];
// Kept in the code but not offered in the sidebar.
const hiddenPages: PageId[] = ['dashboard'];
const toolPages: PageId[] = [
  'profiles',
  'history',
  'crm-users',
  'imessage-log',
  'imessage-crm-users',
  'email-accounts',
  'email-warmup',
];

function useNavCounts(): Partial<Record<PageId, number>> {
  const sources = useResource<string[]>('scout.sources', {}, 15000);
  const accounts = useResource<ScoutAccountRow[]>('scout.accounts', {}, 15000);
  const leads = useResource<{ total: number }>('leads.list', { page_size: 1 }, 15000);
  const jobs = useResource<SearchJob[]>('jobs.list', {}, 15000);
  const imessage = useResource<IMessageState>('imessage.state', {}, 15000);
  const templates = useResource<IMessageTemplate[]>('imessage.templates', {}, 15000);
  const crm = useResource<CrmList>('crm.list', { crm: 'instagram', page_size: 1 }, 15000);
  const imessageCrm = useResource<CrmList>('crm.list', { crm: 'imessage', page_size: 1 }, 15000);
  const emailCrm = useResource<CrmList>('crm.list', { crm: 'email', page_size: 1 }, 15000);
  return {
    crm: crm.data?.counts.all,
    'imessage-crm': imessageCrm.data?.counts.all,
    'email-crm': emailCrm.data?.counts.all,
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
  const [account, setAccount] = useState<AccountState>();
  // The first check runs in the background on start: look again soon while it is on its way.
  const resource = useResource<AccountState>('account.state', {}, account?.checking ? 1000 : 10000);
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
  // Admin and moderator see every user's CRM, signed with the owner, and the assistant.
  const staff = (account?.role === 'admin' || account?.role === 'moderator') && account.configured;
  const visible = (id: PageId) => !hiddenPages.includes(id) && (staff || !staffPages.includes(id));
  const [active, setActive] = useState<PageId>('discovery');
  const [channel, setChannel] = useState<Channel>('instagram');
  // Each channel reopens on the section it was left on.
  const [lastPage, setLastPage] = useState<Record<Channel, PageId>>({
    instagram: 'discovery',
    imessage: 'imessage',
    email: 'email',
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
  useTabIndicators();
  // Ctrl/Cmd+B folds the sidebar to an icon rail; the choice survives a restart.
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem('alf.sidebar') === 'collapsed';
    } catch {
      return false;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem('alf.sidebar', collapsed ? 'collapsed' : 'open');
    } catch {
      /* storage may be unavailable; the toggle still works for this session */
    }
  }, [collapsed]);
  // The grid column snaps to its new width and the page reflows once; the page is then slid
  // from where it was with a transform while the sidebar animates its own width above it.
  // Only the sidebar is laid out per frame, so the fold stays smooth on heavy pages.
  const mainRef = useRef<HTMLElement>(null);
  const mainLeft = useRef<number | null>(null);
  const toggleSidebar = () => {
    mainLeft.current = mainRef.current?.getBoundingClientRect().left ?? null;
    setCollapsed(value => !value);
  };
  const toggleRef = useRef(toggleSidebar);
  useLayoutEffect(() => {
    toggleRef.current = toggleSidebar;
  });
  useLayoutEffect(() => {
    const main = mainRef.current;
    const before = mainLeft.current;
    mainLeft.current = null;
    if (!main || before === null || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const shift = before - main.getBoundingClientRect().left;
    if (!shift) return;
    const motion = main.animate([{ transform: `translateX(${shift}px)` }, { transform: 'translateX(0)' }], {
      duration: 320,
      easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
    });
    return () => motion.cancel();
  }, [collapsed]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && !event.altKey && event.code === 'KeyB') {
        event.preventDefault();
        toggleRef.current();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);
  const navButton = (item: (typeof pages)[number], index: number) => {
    const Icon = icons[item.id];
    const count = counts[item.id];
    return (
      <NavigationItem
        key={item.id}
        title={item.label}
        active={active === item.id}
        onClick={() => navigate(item.id)}
        style={{ '--i': index } as CSSProperties}
      >
        <Icon size={18} strokeWidth={1.8} />
        <span>{item.label}</span>
        {count !== undefined && <span className="nav-count">{number(count)}</span>}
      </NavigationItem>
    );
  };
  return (
    <div
      className={`app-shell alf-ui${active === 'leads' ? ' alf-database-shell' : ''}${collapsed ? ' sidebar-collapsed' : ''}`}
    >
      <aside className="sidebar">
        <div className="brand" title={`Artist Lead Finder · v${version || core.data?.version || '0.1.0'}`}>
          <span className="brand-logo">
            <AudioLines size={17} strokeWidth={2.4} />
          </span>
          <span className="brand-name">Artist Lead Finder</span>
          <span className="version-pill">v{version || core.data?.version || '0.1.0'}</span>
        </div>
        <Dropdown
          label="Канал"
          className="alf-channel-dropdown"
          value={channel}
          options={channels.map(item => ({ value: item.id, label: item.label }))}
          onChange={value => switchChannel(value as Channel)}
        />
        <div className="nav-section">Навигация</div>
        <NavGroup
          title="Рабочая область"
          icon={<LayoutGrid />}
          label="Основная навигация"
          contentKey={channel}
        >
          {pages
            .filter(
              item =>
                item.placement === 'main' &&
                item.channel === channel &&
                visible(item.id) &&
                !toolPages.includes(item.id),
            )
            .map(navButton)}
        </NavGroup>
        <NavGroup title="Инструменты" icon={<Wrench />} label="Инструменты канала" contentKey={channel}>
          {pages
            .filter(
              item =>
                item.placement === 'main' &&
                item.channel === channel &&
                visible(item.id) &&
                toolPages.includes(item.id),
            )
            .map(navButton)}
        </NavGroup>
        <nav className="sidebar-footer" aria-label="Параметры">
          {pages
            .filter(item => item.placement === 'footer' && visible(item.id) && (item.id !== 'admin' || admin))
            .map(navButton)}
          <button
            type="button"
            className="nav-item"
            title="Журнал логов: папка с журналами приложения"
            onClick={() => void api.openLogs().catch(reportError)}
          >
            <FolderOpen size={18} />
            <span>Журнал логов</span>
          </button>
          <button
            type="button"
            className="nav-item alf-sidebar-toggle"
            title={collapsed ? 'Развернуть панель (Ctrl+B)' : 'Свернуть панель (Ctrl+B)'}
            aria-pressed={collapsed}
            onClick={toggleSidebar}
          >
            {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
            <span>{collapsed ? 'Развернуть панель' : 'Свернуть панель'}</span>
            <kbd>Ctrl B</kbd>
          </button>
        </nav>
        <header className="topbar alf-sidebar-state">
          <div className="alf-location">
            <span>{section.label}</span>
            <span>/</span>
            <strong>{pages.find(item => item.id === active)?.label}</strong>
          </div>
          <UpdateNotice state={updater.state} install={updater.install} />
          <span
            className={`core-status ${core.error ? 'offline' : core.data ? 'connected' : 'pending'}`}
            title={core.error || undefined}
          >
            <i />
            {core.error ? 'Ядро недоступно' : core.data ? 'Ядро подключено' : 'Подключение…'}
          </span>
          {account?.configured && !account.online && account.offline_until && (
            // The server is unreachable: the app works on its offline pass until then.
            <span className="core-status pending" title={account.reason || undefined}>
              <i />
              Без сервера до{' '}
              {new Date(account.offline_until).toLocaleString('ru-RU', {
                day: '2-digit',
                month: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
              })}
            </span>
          )}
          {account?.configured && <AccountMenu state={account} onChange={onAccount} />}
        </header>
      </aside>
      <main className="main" ref={mainRef}>
        {/* Board pages use the full window width, like the reference layout at 1920×1080. */}
        <div className="page">
          {/* The previous page unmounts on a switch anyway, so the key costs no form state. */}
          <SectionTransition sectionKey={active}>
            <ErrorBoundary key={active}>
              {active === 'dashboard' ? (
                <Dashboard navigate={navigate} />
              ) : active === 'discovery' ? (
                <Discovery tab={workTab} onTab={setWorkTab} />
              ) : active === 'spotify' ? (
                <Spotify onWrite={writeTo} />
              ) : active === 'imessage-dialogs' ? (
                <Dialogs />
              ) : active === 'leads' ? (
                <ArtistDatabase
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
              ) : active === 'email' ? (
                <EmailCampaigns onAccounts={() => navigate('email-accounts')} />
              ) : active === 'email-accounts' ? (
                <EmailAccounts />
              ) : active === 'email-warmup' ? (
                <EmailWarmup onAccounts={() => navigate('email-accounts')} />
              ) : active === 'email-crm' ? (
                <Crm crm="email" onWrite={() => navigate('email')} />
              ) : active === 'crm' ? (
                <Crm crm="instagram" onWrite={writeTo} />
              ) : active === 'imessage-crm' ? (
                <Crm crm="imessage" onWrite={() => navigate('imessage')} />
              ) : active === 'crm-users' && staff ? (
                <Crm key="crm-users" crm="instagram" others onWrite={writeTo} />
              ) : active === 'imessage-crm-users' && staff ? (
                <Crm key="imessage-crm-users" crm="imessage" others onWrite={() => navigate('imessage')} />
              ) : active === 'assistant' && staff ? (
                <Assistant />
              ) : active === 'admin' && admin ? (
                <Admin />
              ) : (
                <Settings updater={{ ...updater, version }} staff={staff} />
              )}
            </ErrorBoundary>
          </SectionTransition>
        </div>
      </main>
      <ErrorToast message={core.error} title="Ядро недоступно" />
    </div>
  );
}
