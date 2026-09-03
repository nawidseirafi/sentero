import type { SenteroRouteName } from '../routes/routes';

export type SenteroNavIcon = 'home' | 'wizard' | 'hints' | 'more';

export const senteroNavigation: Array<{ route: SenteroRouteName; label: string; icon: SenteroNavIcon }> = [
  { route: 'dashboard', label: 'Dashboard', icon: 'home' },
  { route: 'setup', label: 'Wizard', icon: 'wizard' },
  { route: 'notifications', label: 'Hinweise', icon: 'hints' },
  { route: 'settings', label: 'Einstellungen', icon: 'more' },
];
