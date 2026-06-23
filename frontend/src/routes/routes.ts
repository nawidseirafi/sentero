export type SenteroSettingsTab = 'profile' | 'sensors' | 'contacts' | 'notifications' | 'account' | 'system';

export type SenteroRoute =
  | { name: 'setup' }
  | { name: 'dashboard' }
  | { name: 'history' }
  | { name: 'rooms' }
  | { name: 'contacts' }
  | { name: 'settings'; tab?: SenteroSettingsTab };

export type SenteroRouteName = SenteroRoute['name'];

const routeNames: SenteroRouteName[] = ['setup', 'dashboard', 'history', 'rooms', 'contacts', 'settings'];
const settingsTabs: SenteroSettingsTab[] = ['profile', 'sensors', 'contacts', 'notifications', 'account', 'system'];

export function parseSenteroRoute(): SenteroRoute {
  const parts = window.location.pathname.split('/').filter(Boolean);
  const candidate = parts[0] === 'sentero' ? parts[1] : parts[0];
  const name = routeNames.includes(candidate as SenteroRouteName) ? candidate as SenteroRouteName : 'dashboard';
  if (name === 'settings') {
    const tabCandidate = parts[0] === 'sentero' ? parts[2] : parts[1];
    const tab = settingsTabs.includes(tabCandidate as SenteroSettingsTab) ? tabCandidate as SenteroSettingsTab : 'profile';
    return { name, tab };
  }
  return { name };
}

export function senteroRouteToPath(route: SenteroRoute): string {
  if (route.name === 'settings') {
    return `/sentero/settings/${route.tab || 'profile'}`;
  }
  return `/sentero/${route.name}`;
}
