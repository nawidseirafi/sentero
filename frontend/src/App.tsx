import { useEffect, useState } from 'react';
import { SenteroAuthProvider, useSenteroAuth } from './auth/SenteroAuthContext';
import { LoginPage } from './pages/LoginPage';
import { DashboardPage } from './pages/DashboardPage';
import { HistoryPage } from './pages/HistoryPage';
import { RoomsPage } from './pages/RoomsPage';
import { ContactsPage } from './pages/ContactsPage';
import { SettingsPage } from './pages/SettingsPage';
import { SetupWizardPage } from './pages/SetupWizardPage';
import { SenteroShell } from './components/SenteroShell';
import type { SenteroRoute, SenteroRouteName, SenteroSettingsTab } from './routes/routes';
import { parseSenteroRoute, senteroRouteToPath } from './routes/routes';
import './styles/sentero.css';

export function App() {
  return (
    <SenteroAuthProvider>
      <SenteroContent />
    </SenteroAuthProvider>
  );
}

function SenteroContent() {
  const { loading, setupRequired, isAuthenticated, logout } = useSenteroAuth();
  const [route, setRoute] = useState<SenteroRoute>(parseSenteroRoute());

  useEffect(() => {
    const onPop = () => setRoute(parseSenteroRoute());
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);

  const navigate = (name: SenteroRouteName, tab?: SenteroSettingsTab) => {
    const next = name === 'settings' ? { name, tab: tab || 'profile' } as SenteroRoute : { name } as SenteroRoute;
    window.history.pushState({}, '', senteroRouteToPath(next));
    setRoute(next);
  };

  if (loading) {
    return <main className="sc-login-page"><section className="sc-login-card"><p className="sc-muted-note">Sentero wird geladen...</p></section></main>;
  }

  if (setupRequired || !isAuthenticated) {
    return <LoginPage mode={setupRequired ? 'setup' : 'login'} onLoggedIn={(target) => {
      if (target === 'setup') {
        navigate('setup');
        return;
      }
      setRoute(parseSenteroRoute());
    }} />;
  }

  return (
    <SenteroShell route={route} onNavigate={navigate} onLogout={logout}>
      {route.name === 'setup' && <SetupWizardPage onFinish={() => navigate('dashboard')} />}
      {route.name === 'dashboard' && <DashboardPage />}
      {route.name === 'history' && <HistoryPage />}
      {route.name === 'rooms' && <RoomsPage />}
      {route.name === 'contacts' && <ContactsPage />}
      {route.name === 'settings' && <SettingsPage activeTab={route.tab || 'profile'} />}
    </SenteroShell>
  );
}
