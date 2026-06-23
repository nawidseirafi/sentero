import type { ReactNode } from 'react';
import { Bell, Home, KeyRound, LogOut, Settings, ShieldCheck, Sparkles, UserRound, Users, Wifi } from 'lucide-react';
import type { SenteroRoute, SenteroRouteName, SenteroSettingsTab } from '../routes/routes';
import { senteroNavigation } from '../navigation/navigation';

type Props = {
  route: SenteroRoute;
  onNavigate: (route: SenteroRouteName, tab?: SenteroSettingsTab) => void;
  onLogout: () => void;
  children: ReactNode;
};

const navIcons = {
  home: Home,
  history: Sparkles,
  rooms: Home,
  more: Settings,
};

const settingsItems: Array<{ tab: SenteroSettingsTab; label: string; icon: typeof UserRound }> = [
  { tab: 'profile', label: 'Profil', icon: UserRound },
  { tab: 'sensors', label: 'Räume & Sensoren', icon: Home },
  { tab: 'contacts', label: 'Vertraute Personen', icon: Users },
  { tab: 'notifications', label: 'Benachrichtigungen', icon: Bell },
  { tab: 'account', label: 'Konto & Zugriff', icon: KeyRound },
  { tab: 'system', label: 'System', icon: Wifi },
];

export function SenteroShell({ route, onNavigate, onLogout, children }: Props) {
  return (
    <main className="sc-app-shell">
      <aside className="sc-sidebar" aria-label="Sentero Navigation">
        <button className="sc-shell-brand" type="button" onClick={() => onNavigate('dashboard')}>
          <span><ShieldCheck size={26} aria-hidden="true" /></span>
          <strong>Sentero</strong>
        </button>
        <nav className="sc-sidebar-nav">
          {senteroNavigation.map((item) => {
            const Icon = navIcons[item.icon];
            const active = route.name === item.route;
            return (
              <div key={item.route} className={item.route === 'settings' ? 'sc-nav-group' : undefined}>
                <button className={active ? 'active' : ''} type="button" onClick={() => onNavigate(item.route)}>
                  <Icon size={22} aria-hidden="true" />
                  <span>{item.label}</span>
                </button>
                {item.route === 'settings' && active && (
                  <div className="sc-settings-subnav" aria-label="Einstellungsbereiche">
                    {settingsItems.map((setting) => {
                      const SettingIcon = setting.icon;
                      return (
                        <button
                          key={setting.tab}
                          className={route.name === 'settings' && route.tab === setting.tab ? 'active' : ''}
                          type="button"
                          onClick={() => onNavigate('settings', setting.tab)}
                        >
                          <SettingIcon size={18} aria-hidden="true" />
                          <span>{setting.label}</span>
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          })}
        </nav>
        <button className="sc-logout-button" type="button" onClick={onLogout}>
          <LogOut size={20} aria-hidden="true" />
          <span>Abmelden</span>
        </button>
      </aside>

      <section className="sc-workspace">
        <div className="sc-content">{children}</div>
      </section>

      <nav className="sc-mobile-nav" aria-label="Sentero Navigation mobil">
        {senteroNavigation.map((item) => {
          const Icon = navIcons[item.icon];
          return (
            <button key={item.route} className={route.name === item.route ? 'active' : ''} type="button" onClick={() => onNavigate(item.route)}>
              <Icon size={22} aria-hidden="true" />
              <span>{item.label}</span>
            </button>
          );
        })}
        <button className="sc-mobile-logout" type="button" onClick={onLogout}>
          <LogOut size={22} aria-hidden="true" />
          <span>Abmelden</span>
        </button>
      </nav>
    </main>
  );
}
