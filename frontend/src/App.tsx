import { useState } from 'react';
import {
  AudioLines,
  LayoutDashboard,
  Compass,
  Users,
  History,
  Settings as SettingsIcon,
  Database,
  UserRound,
  type LucideIcon,
} from 'lucide-react';
import { pages, type PageId } from './navigation';
import { useResource } from './hooks/useResource';
import { Dashboard } from './pages/Dashboard';
import { Discovery } from './pages/Discovery';
import { Leads } from './pages/Leads';
import { SearchHistory } from './pages/SearchHistory';
import { Settings } from './pages/Settings';
import { BrowserProfiles } from './components/BrowserProfiles';

const icons: Record<PageId, LucideIcon> = {
  dashboard: LayoutDashboard,
  discovery: Compass,
  leads: Users,
  history: History,
  profiles: UserRound,
  settings: SettingsIcon,
};
export function App() {
  const core = useResource<{ version: string }>('system.info');
  const [active, setActive] = useState<PageId>('discovery');
  const page = pages.find(item => item.id === active)!;
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-symbol">
            <AudioLines size={24} />
          </div>
          <div className="brand-copy">
            <span className="brand-name">Artist Lead Finder</span>
            <small>ВАШЕ МУЗЫКАЛЬНОЕ ПРОСТРАНСТВО</small>
          </div>
        </div>
        <div className="nav-label">РАБОЧЕЕ ПРОСТРАНСТВО</div>
        <nav aria-label="Основная навигация">
          {pages.map(item => {
            const NavIcon = icons[item.id];
            return (
              <button
                key={item.id}
                className={`nav-item ${active === item.id ? 'active' : ''}`}
                aria-current={active === item.id ? 'page' : undefined}
                onClick={() => setActive(item.id)}
              >
                <NavIcon size={19} />
                {item.label}
              </button>
            );
          })}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-label">
            <Database size={15} /> Локальное приложение
          </div>
          <p>Ваши данные — на вашем устройстве.</p>
          <div className="version">
            Artist Lead Finder <span>v{core.data?.version || '0.1.0'}</span>
          </div>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <span>{page.label}</span>
          <span className={`phase-badge ${core.error ? 'offline' : core.data ? 'connected' : 'pending'}`}>
            <span />
            {core.error ? 'Ядро недоступно' : core.data ? 'Локальное ядро подключено' : 'Подключение…'}
          </span>
        </header>
        <div className="page-content">
          <div className="eyebrow">{page.eyebrow}</div>
          <h1>{page.title}</h1>
          <p className="intro">{page.description}</p>
          {active === 'dashboard' ? (
            <Dashboard navigate={setActive} />
          ) : active === 'discovery' ? (
            <Discovery />
          ) : active === 'leads' ? (
            <Leads />
          ) : active === 'history' ? (
            <SearchHistory />
          ) : active === 'profiles' ? (
            <BrowserProfiles />
          ) : (
            <Settings />
          )}
          <footer>Artist Lead Finder · От источника к новому контакту</footer>
        </div>
      </main>
    </div>
  );
}
