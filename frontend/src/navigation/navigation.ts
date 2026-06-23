import type { SenteroRouteName } from '../routes/routes';

export type SenteroNavIcon = 'home' | 'history' | 'rooms' | 'more';

export const senteroNavigation: Array<{ route: SenteroRouteName; label: string; icon: SenteroNavIcon }> = [
  { route: 'dashboard', label: 'Dashboard', icon: 'home' },
  { route: 'setup', label: 'Wizard', icon: 'history' },
  { route: 'settings', label: 'Einstellungen', icon: 'more' },
];
