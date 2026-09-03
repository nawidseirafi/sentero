const RAW_API_BASE = import.meta.env.VITE_API_BASE ?? '';
const API_BASE = normalizeApiBase(RAW_API_BASE);
const TOKEN_KEY = 'sentero.session-token';
export const AUTH_EXPIRED_EVENT = 'sentero:auth-expired';

export type SenteroUser = {
  id: number;
  email: string;
  display_name?: string | null;
  role: 'owner' | 'admin' | 'viewer' | string;
  aal_role?: 'resident' | 'relative' | 'care_service' | 'emergency_service' | 'housing_provider' | 'admin' | string;
  last_login_at?: string | null;
};

export type SenteroAuthStatus = {
  setup_required: boolean;
  authenticated: boolean;
  user?: SenteroUser | null;
};

export type SenteroBehaviorAssessment = {
  id?: number;
  assessment_time: string;
  status: 'green' | 'yellow' | 'orange' | 'red' | string;
  confidence: number;
  anomaly_score?: number;
  learning_completed?: boolean;
  learning_day?: number;
  learning_days?: number;
  summary: string;
  findings: string[];
  recommendation: string;
  llm_response?: string | null;
  created_at?: string;
};

export type SenteroBehaviorLearning = {
  completed: boolean;
  day: number;
  days: number;
  remaining_days: number;
  usable_days?: number;
  required_usable_days?: number;
  remaining_usable_days?: number;
  calendar_complete?: boolean;
  data_complete?: boolean;
};

export type SenteroTimelineEvent = {
  id: string;
  type: string;
  category: 'routine' | 'activity' | 'door' | 'environment' | 'anomaly' | 'safety' | string;
  severity: 'normal' | 'info' | 'notice' | 'warning' | 'critical' | string;
  title: string;
  description: string;
  room?: string | null;
  start_time: string;
  end_time?: string | null;
  duration_minutes?: number | null;
  duration?: string | null;
  status?: 'active' | 'observed' | 'resolved' | string;
  status_label?: string | null;
  baseline_comparison?: string | null;
  observations?: string[];
  source_entities?: string[];
};

export type SenteroBehaviorDaySummary = {
  date: string;
  headline: string;
  wakeup_time?: string | null;
  first_activity?: string | null;
  last_activity?: string | null;
  active_minutes: number;
  longest_inactivity_minutes?: number;
  longest_inactivity?: string | null;
  door_events: number;
  anomaly_count: number;
  critical_count: number;
  status_text: string;
  critical_text: string;
  baseline?: Record<string, unknown>;
  deviations?: Record<string, unknown>;
};

export type SenteroBehaviorDay = {
  date: string;
  events: Array<{ event_time: string; room?: string | null; role?: string | null; state?: string | null; device_class?: string | null; entity_id?: string | null; data_class?: string | null; aggregation_level?: string | null }>;
  timeline_events: SenteroTimelineEvent[];
  summary: SenteroBehaviorDaySummary;
  profile?: Record<string, unknown>;
  deviations?: Record<string, unknown>;
  data_quality?: SenteroDashboardDataQuality;
  assessment: SenteroBehaviorAssessment | null;
};

export type SenteroTrendPoint = {
  date: string;
  wakeup_time?: string | null;
  wakeup_minutes?: number | null;
  active_minutes: number;
  longest_inactivity_minutes?: number;
  night_activity_count?: number;
  away_minutes?: number;
  door_events: number;
  anomaly_score: number;
  has_data?: boolean;
};

export type SenteroTrendCard = {
  key: string;
  label: string;
  baseline?: string | null;
  previous?: string | null;
  current?: string | null;
  comparison: string;
};

export type SenteroBehaviorTrends = {
  days: number;
  profile?: Record<string, unknown>;
  data_quality?: SenteroDashboardDataQuality;
  points: SenteroTrendPoint[];
  cards: SenteroTrendCard[];
  series?: SenteroTrendSeries[];
};

export type SenteroDashboardDataQuality = {
  usable_days?: number;
  learning_completed?: boolean;
  learning_day?: number;
  learning_days?: number;
  baseline_available?: boolean;
  message?: string;
  sensor_quality?: Record<string, unknown>;
};

export type SenteroTrendSeries = {
  metric: 'wake_time' | 'activity' | 'longest_rest' | 'night_activity' | 'away_time' | string;
  label: string;
  unit: 'time' | 'minutes' | 'count' | string;
  points: Array<{ timestamp: string; value?: number | null; label?: string; has_data?: boolean }>;
  baseline: { lower?: number | null; upper?: number | null; average?: number | null };
  interpretation: string;
};

export type SenteroBehaviorHints = {
  current: SenteroTimelineEvent[];
  observed: SenteroTimelineEvent[];
  resolved: SenteroTimelineEvent[];
};

export type SenteroSensorRole = {
  role: string;
  room?: string | null;
  label: string;
  configured: boolean;
  updated_at?: string | null;
  state?: string | null;
  reachable?: boolean | null;
  last_changed?: string | null;
  last_updated?: string | null;
  battery_level?: number | null;
  power_source?: string | null;
  temperature?: number | null;
  humidity?: number | null;
  illuminance?: number | null;
  presence?: boolean | null;
  smoke?: boolean | null;
  fall_detected?: boolean | null;
  motion?: string | null;
  motion_state?: string | null;
  stale?: boolean | null;
  stale_seconds?: number | null;
  writable_settings?: string[] | null;
  hp_led?: boolean | null;
  fall_led?: boolean | null;
  led_status?: {
    hp_led?: boolean | null;
    fall_led?: boolean | null;
    all_on?: boolean | null;
    any_on?: boolean | null;
  } | null;
  device_class?: string | null;
  domain?: string | null;
  source?: string | null;
  device_id?: string | null;
  source_ref?: string | null;
};

export type SenteroProfileData = {
  name?: string | null;
  birth_year?: number | null;
  age?: number | null;
  notes?: string | null;
};

export type SenteroTrustedContact = {
  id: number;
  name: string;
  relationship?: string | null;
  actor_role?: 'resident' | 'relative' | 'care_service' | 'emergency_service' | 'housing_provider' | 'admin' | string | null;
  email?: string | null;
  phone?: string | null;
  telegram_chat_id?: string | null;
  telegram_invite_code?: string | null;
  telegram_linked_at?: string | null;
  telegram_linked?: boolean;
  whatsapp_phone_number?: string | null;
  preferred_channels?: string | string[] | null;
  notification_enabled?: number | boolean;
  primary_contact?: number | boolean;
  email_queries_enabled?: number | boolean;
  email_permissions?: string[] | string | null;
  active?: number;
};

export type SenteroMailQueryContact = {
  id: number;
  name: string;
  email?: string | null;
  email_queries_enabled: boolean;
  email_permissions: string[];
};

export type SenteroMailQuerySettings = {
  enabled: boolean;
  contacts: SenteroMailQueryContact[];
};

export type SenteroNotifications = {
  anomalies: number | boolean;
  critical: number | boolean;
  daily_summary: number | boolean;
};

export type SenteroSetupStatus = {
  current_step: string;
  completed_steps: string[];
  selected_rooms: string[];
  is_complete: boolean;
  home: { connected: boolean; sensor_ready: boolean; system_ready: boolean };
  has_profile: boolean;
  profile?: SenteroProfileData | null;
  trusted_contacts_count: number;
  trusted_contacts?: SenteroTrustedContact[];
  notifications?: SenteroNotifications | null;
  sensor_roles: SenteroSensorRole[];
  updated_at: string;
};

export type SenteroNotificationChannel = {
  channel: 'email' | 'telegram' | 'whatsapp' | string;
  enabled: boolean;
  configured: boolean;
  config: Record<string, unknown>;
  updated_at?: string | null;
};

export type SenteroTelegramBotInfo = {
  id?: number | string | null;
  username: string;
  first_name?: string | null;
  invite_base_url: string;
};

export type MailConfig = {
  imap_host: string;
  imap_port: number;
  imap_encryption: 'SSL' | 'STARTTLS' | 'NONE' | string;
  smtp_host: string;
  smtp_port: number;
  smtp_encryption: 'SSL' | 'STARTTLS' | 'NONE' | string;
  auth_method?: string | null;
  requires_app_password: boolean;
  app_password_help_url?: string | null;
  source: 'ispdb' | 'fallback' | 'manual' | string;
};

export type SenteroConsent = {
  id: number;
  contact_id: number;
  contact_name?: string | null;
  contact_relationship?: string | null;
  contact_email?: string | null;
  recipient_type: string;
  purpose: string;
  data_classes: string[];
  valid_until?: string | null;
  revoked_at?: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
};

export type SenteroExportToken = {
  id: number;
  contact_id: number;
  contact_name?: string | null;
  contact_email?: string | null;
  actor_role?: string | null;
  purpose: string;
  data_classes: string[];
  expires_at?: string | null;
  revoked_at?: string | null;
  last_used_at?: string | null;
  active: boolean;
  created_at: string;
  updated_at: string;
};

export type SenteroTransparencyItem = {
  id: string;
  category: 'export' | 'notification' | 'mail_query' | 'consent' | 'security' | string;
  event_type: string;
  status: string;
  summary: string;
  contact_id?: number | null;
  contact_name?: string | null;
  actor_role?: string | null;
  purpose?: string | null;
  data_classes: string[];
  aggregation_level?: string | null;
  raw_data_included?: boolean;
  created_at: string;
  metadata?: Record<string, unknown>;
};

export type SenteroTransparency = {
  items: SenteroTransparencyItem[];
  summary: { total: number; exports: number; notifications: number; mail_queries?: number; consents: number; security: number };
  retention: {
    retention_days: number;
    tables: Array<{ table: string; count: number; oldest?: string | null; newest?: string | null }>;
  };
};

export type SenteroContactPayload = {
  name: string;
  relationship?: string;
  actor_role?: string;
  email?: string;
  phone?: string;
  telegram_chat_id?: string;
  whatsapp_phone_number?: string;
  preferred_channels?: string[];
  notification_enabled?: boolean;
  primary_contact?: boolean;
  email_queries_enabled?: boolean;
  email_permissions?: string[];
};

export type SenteroCandidate = {
  label: string;
  confidence: number;
  score?: number;
  entity_id: string;
  reasons?: string[];
  device_class?: string | null;
  domain?: string | null;
};

export type SenteroPairingStart = {
  session_id: number;
  status: 'waiting_for_signal' | 'pairing_started' | 'pairing_needs_manual_action' | string;
  message: string;
  detail?: { ok?: boolean; provider?: string; reason?: string; message?: string; attempts?: unknown[] } | null;
};

export type SenteroSensorDiscoveryStart = {
  discovery_id: number;
  status: 'searching' | 'manual_action' | string;
  message: string;
  sensor_type: string;
  room_id?: string | null;
  transport?: 'zigbee' | 'wifi_esphome' | string;
  device?: SenteroDiscoveredSensor | null;
  devices?: SenteroDiscoveredSensor[];
  expires_in_seconds?: number;
};

export type SenteroDiscoveredSensor = {
  id: string;
  name: string;
  type: string;
  detected_type?: string | null;
  supported?: boolean;
  manufacturer?: string | null;
  model?: string | null;
  lifecycle_status?: string | null;
  interview_status?: string | null;
  confidence: number;
  source?: string | null;
  source_ref?: string | null;
  entities?: string[];
};

export type SenteroSensorDiscoveryResult = {
  discovery_id: number;
  status: 'found' | 'searching' | 'existing_device_found' | 'wrong_type_found' | 'unsupported_device_found' | 'not_found' | string;
  message: string;
  sensor?: SenteroDiscoveredSensor | null;
  device?: SenteroDiscoveredSensor | null;
  devices?: SenteroDiscoveredSensor[];
  requested_type?: string | null;
  detected_type?: string | null;
  remaining_seconds?: number;
};

export type SenteroSensorNetworkSettings = {
  wifi_ssid: string;
  wifi_password_set: boolean;
  configured: boolean;
};

export type SenteroEcoTrackerStatus = {
  enabled: boolean;
  configured: boolean;
  host: string;
  device: string;
  last_checked_at?: string | null;
  reading?: SenteroEcoTrackerReading | null;
};

export type SenteroEcoTrackerReading = {
  power_w?: number | null;
  power_avg_w?: number | null;
  meter_reading_kwh?: number | null;
  energy_in_kwh?: number | null;
  energy_out_kwh?: number | null;
};

export type SenteroSensorManagerStatus = {
  ready: boolean;
  mode: string;
  status_label: string;
  network: SenteroSensorNetworkSettings;
  supported_sensor_types: string[];
  wifi_sensor_setup_enabled?: boolean;
  presence_sensor_transport?: 'zigbee' | 'wifi_esphome' | string;
};

export type BoxNetworkStatus = {
  mode: 'disabled' | 'auto' | 'force' | string;
  status?: 'OFFLINE' | 'LOCAL_ONLY' | 'ONLINE_ETHERNET' | 'ONLINE_WIFI' | 'ONLINE_CELLULAR' | 'DEGRADED' | string;
  active_connection?: 'ethernet' | 'wifi' | 'cellular' | 'none' | string;
  network_ready: boolean;
  ethernet_active: boolean;
  wifi_active: boolean;
  ip_address?: string | null;
  setup_ap_active: boolean;
  setup_ap_ssid?: string | null;
  setup_url?: string | null;
  hostname: string;
  local_url: string;
  message: string;
  wifi_configured: boolean;
  internet_reachable?: boolean | null;
};

export type BoxNetworkWifiResult = {
  ok: boolean;
  applied: boolean;
  mode: string;
  message: string;
  status: BoxNetworkStatus;
};

export type NetworkStatus = BoxNetworkStatus & {
  status: 'OFFLINE' | 'LOCAL_ONLY' | 'ONLINE_ETHERNET' | 'ONLINE_WIFI' | 'ONLINE_CELLULAR' | 'DEGRADED' | string;
  active_connection: 'ethernet' | 'wifi' | 'cellular' | 'none' | string;
  cellular_active: boolean;
  capabilities: { ethernet: boolean; wifi: boolean; wifi_ap: boolean; cellular: boolean };
  cellular: {
    available: boolean;
    sim_present: boolean;
    registered: boolean;
    provider?: string | null;
    signal_percent?: number | null;
    connected: boolean;
  };
};

export type WifiNetwork = {
  ssid: string;
  signal: number;
  secured: boolean;
};

export type SenteroSensorProvisioningStatus = {
  implemented: boolean;
  status: string;
  message: string;
  network_configured: boolean;
  mqtt_configured?: boolean;
  available_steps: string[];
  missing_steps: string[];
  discovery?: SenteroEsp32DiscoveryStatus;
};

export type SenteroEsp32DiscoverySensor = {
  id: string;
  name: string;
  type: string;
  http_port?: number;
  model?: string | null;
  firmware?: string | null;
  capabilities: string[];
  last_seen_at: string;
};

export type SenteroEsp32DiscoveryStatus = {
  listening: boolean;
  port: number;
  pending: SenteroEsp32DiscoverySensor[];
};

export type SenteroEsp32ProvisioningResult = {
  ok: boolean;
  device: {
    id: string;
    name: string;
    type: string;
    room_id: string;
    source: string;
    capabilities?: string[];
  };
  message: string;
};

export type SenteroCandidates = {
  session_id: number;
  status: 'signal_detected' | 'no_signal_detected' | 'waiting_for_signal' | string;
  message: string;
  candidate: SenteroCandidate | null;
  candidates: SenteroCandidate[];
  elapsed_seconds?: number;
  remaining_seconds?: number;
  changed_count?: number | null;
  current_state_count?: number | null;
  baseline_state_count?: number | null;
};

export type MessageCenterItem = {
  id: number;
  source: string;
  category: string;
  severity: 'info' | 'success' | 'warning' | 'error' | string;
  title: string;
  message: string;
  payload?: Record<string, unknown>;
  read: boolean;
  created_at: string;
  read_at?: string | null;
};

export type SystemVersion = {
  edition: string;
  app_version?: string;
  version: string;
  build: string;
  commit: string;
  channel?: string;
  updated_at?: string | null;
};

export type UpdateStep = {
  key: string;
  label: string;
  status: 'pending' | 'running' | 'success' | 'failed' | 'completed' | 'error' | string;
  detail?: string;
};

export type UpdateLatest = {
  latest_version: string;
  download_url: string;
  mandatory: boolean;
  release_notes: string[] | string;
  channel: string;
  layers: string[];
};

export type SenteroSystemServiceStatus = {
  key: string;
  label: string;
  state: 'ok' | 'warning' | 'error' | 'inactive' | string;
  detail?: string;
};

export type SenteroSystemStatus = {
  ok: boolean;
  overall: 'ok' | 'warning' | 'error' | string;
  summary: string;
  checked_at?: string;
  services: SenteroSystemServiceStatus[];
  device?: {
    identity_provisioned?: boolean;
    serial_number?: string | null;
    device_id?: string | null;
    created_at?: string | null;
    setup_ssid?: string | null;
    legacy_box_id?: string | null;
    identity_error?: string | null;
  };
  network?: {
    active_connection?: string | null;
    ip_address?: string | null;
    internet_reachable?: boolean;
  };
};

export type FactoryResetState = {
  status?: 'accepted' | 'running' | 'completed' | 'failed' | string;
  phase?: string;
  message?: string;
  accepted_at?: string;
  started_at?: string;
  finished_at?: string;
  updated_at?: string;
  error?: string;
};

export type FactoryResetResult = {
  ok: boolean;
  accepted?: boolean;
  already_running?: boolean;
  message?: string;
  state?: FactoryResetState;
};

export type UpdateStatus = {
  product?: string;
  current_version?: string;
  latest_version?: string | null;
  status?: string;
  state?: string;
  last_checked?: string | null;
  release_notes?: string[] | string;
  steps?: UpdateStep[];
  message?: string;
  version?: SystemVersion;
  channel?: 'stable' | 'beta' | 'dev' | string;
  execution_mode?: string;
  update_server_url?: string;
  latest?: UpdateLatest | null;
  update_available: boolean;
  install: {
    status: string;
    layer?: string;
    target_version?: string;
    steps: UpdateStep[];
    started_at?: string;
    finished_at?: string;
  };
  rollback: {
    status?: string;
    available?: boolean;
    previous_version?: string | null;
    target_version?: string;
    steps?: UpdateStep[];
  };
  last_error?: string | null;
  backup?: { path: string; created_at: string } | null;
  dev_mode?: boolean;
};

export type UpdateCheckResult = {
  ok: boolean;
  offline: boolean;
  product?: string;
  current?: SystemVersion;
  current_version?: string;
  channel?: string;
  latest?: UpdateLatest | null;
  available?: boolean;
  update_available: boolean;
  latest_version?: string;
  release_notes?: string[] | string;
  checked_at?: string;
  last_checked?: string;
  status?: string;
  message: string;
  error?: string;
};

export function getAuthToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function clearAuthToken() {
  localStorage.removeItem(TOKEN_KEY);
}

export function notifyAuthExpired() {
  clearAuthToken();
  window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
}

export function handleUnauthorizedResponse(response: Response) {
  if (response.status !== 401) return false;
  notifyAuthExpired();
  return true;
}

function normalizeApiBase(value: string) {
  const trimmed = value.trim();
  if (!trimmed) return '';
  if (trimmed.startsWith('http://') || trimmed.startsWith('https://')) return trimmed;
  if (trimmed.startsWith('//')) return `${window.location.protocol}${trimmed}`;
  if (trimmed.startsWith('/')) return trimmed;
  if (/^[a-z0-9.-]+(?::\d+)?(?:\/.*)?$/i.test(trimmed)) return `http://${trimmed}`;
  return trimmed;
}

function apiUrl(path: string) {
  const normalizedPath = path.startsWith('/') ? path : `/${path}`;
  if (!API_BASE) return normalizedPath;
  try {
    return new URL(normalizedPath, API_BASE).toString();
  } catch {
    return normalizedPath;
  }
}

function queryString(params: Record<string, string | undefined>) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value);
  }
  const value = search.toString();
  return value ? `?${value}` : '';
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getAuthToken();
  const response = await fetch(apiUrl(path), {
    ...options,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers ?? {}),
    },
  });
  if (!response.ok) {
    handleUnauthorizedResponse(response);
    const text = await response.text();
    let detail = '';
    try {
      detail = (JSON.parse(text) as { detail?: string }).detail || '';
    } catch {
      detail = '';
    }
    throw new Error(detail || text || `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  messages: async (_limit = 100) => ({ messages: [] as MessageCenterItem[] }),
  senteroSystemStatus: () => request<SenteroSystemStatus>('/api/sentero/system/status'),
  senteroFactoryResetStatus: () => request<FactoryResetResult>('/api/sentero/system/factory-reset/status'),
  senteroFactoryReset: (confirm: string) =>
    request<FactoryResetResult>('/api/sentero/system/factory-reset', { method: 'POST', body: JSON.stringify({ confirm }) }),
  senteroUpdateStatus: () => request<UpdateStatus>('/api/sentero/system/update/status'),
  senteroCheckUpdates: () => request<UpdateCheckResult>('/api/sentero/system/update/check'),
  senteroInstallUpdate: () => request<UpdateStatus>('/api/sentero/system/update/install', { method: 'POST', body: JSON.stringify({}) }),
  senteroAuthStatus: () => request<SenteroAuthStatus>('/api/sentero/auth/status'),
  senteroSetup: (payload: { name: string; email: string; password: string; password_confirm: string }) =>
    request<{ authenticated: boolean; user: SenteroUser }>('/api/sentero/auth/setup', { method: 'POST', body: JSON.stringify(payload) }),
  senteroLogin: (email: string, password: string) =>
    request<{ authenticated: boolean; user: SenteroUser }>('/api/sentero/auth/login', { method: 'POST', body: JSON.stringify({ email, password }) }),
  senteroLogout: () => request<{ ok: boolean }>('/api/sentero/auth/logout', { method: 'POST' }),
  updateSenteroMe: (payload: { display_name: string; email: string }) =>
    request<{ user: SenteroUser }>('/api/sentero/auth/me', { method: 'PUT', body: JSON.stringify(payload) }),
  changeSenteroPassword: (payload: { current_password: string; new_password: string; new_password_confirm: string }) =>
    request<{ ok: boolean }>('/api/sentero/auth/change-password', { method: 'POST', body: JSON.stringify(payload) }),
  senteroForgotPassword: (email: string) =>
    request<{ message: string }>('/api/sentero/auth/forgot-password', { method: 'POST', body: JSON.stringify({ email }) }),
  senteroResetPassword: (payload: { token: string; password: string; password_confirm: string }) =>
    request<{ ok: boolean }>('/api/sentero/auth/reset-password', { method: 'POST', body: JSON.stringify(payload) }),
  senteroSetupStatus: () => request<SenteroSetupStatus>('/api/sentero/setup/status'),
  senteroBehaviorLatest: () => request<{ assessment: SenteroBehaviorAssessment | null; learning?: SenteroBehaviorLearning }>('/api/sentero/behavior/latest'),
  senteroBehaviorTimeline: (live = false, date?: string) => request<SenteroBehaviorDay>(`/api/sentero/behavior/timeline${queryString({ live: live ? 'true' : '', date })}`),
  senteroBehaviorDay: (date?: string, live = false) => request<SenteroBehaviorDay>(`/api/sentero/behavior/day${queryString({ date, live: live ? 'true' : '' })}`),
  senteroBehaviorTrends: (days = 14) => request<SenteroBehaviorTrends>(`/api/sentero/behavior/trends?days=${encodeURIComponent(String(days))}`),
  senteroBehaviorHints: (days = 14) => request<SenteroBehaviorHints>(`/api/sentero/behavior/hints?days=${encodeURIComponent(String(days))}`),
  startSenteroSetup: () => request<SenteroSetupStatus>('/api/sentero/setup/start', { method: 'POST' }),
  saveSenteroProfile: (payload: { name?: string; birth_year?: number | null; age?: number | null; notes?: string }) =>
    request<SenteroSetupStatus>('/api/sentero/setup/profile', { method: 'POST', body: JSON.stringify(payload) }),
  saveSenteroSetupRooms: (rooms: string[]) =>
    request<SenteroSetupStatus>('/api/sentero/setup/rooms', { method: 'POST', body: JSON.stringify({ rooms }) }),
  startSenteroDiscovery: (payload: { role: string; room?: string | null; pairing_code?: string }) =>
    request<SenteroPairingStart>('/api/sentero/setup/discovery/start', { method: 'POST', body: JSON.stringify(payload) }),
  startSenteroZigbeePairing: (payload: { role: string; room?: string | null; duration?: number }) =>
    request<SenteroPairingStart>('/api/sentero/setup/pairing/zigbee/start', { method: 'POST', body: JSON.stringify(payload) }),
  startSenteroSensorDiscovery: (payload: { sensor_type: string; room_id?: string | null; role?: string | null; transport?: 'zigbee' | 'wifi_esphome' | string; duration?: number }) =>
    request<SenteroSensorDiscoveryStart>('/api/sentero/sensors/start-discovery', { method: 'POST', body: JSON.stringify(payload) }),
  senteroDiscoveredSensors: (discoveryId: number, dev = false) =>
    request<SenteroSensorDiscoveryResult>(`/api/sentero/sensors/discovered?discovery_id=${discoveryId}${dev ? '&dev=true' : ''}`),
  cancelSenteroSensorDiscovery: (discoveryId?: number | null) =>
    request<{ ok: boolean; provider?: string; reason?: string }>('/api/sentero/sensors/discovery/cancel', { method: 'POST', body: JSON.stringify({ discovery_id: discoveryId ?? null }) }),
  registerSenteroSensor: (sensorId: string, payload: { discovery_id: number; name?: string | null; room_id?: string | null }, dev = false) =>
    request<{ status: string; sensor: { id: string; name: string; room_id?: string | null; type: string } }>(`/api/sentero/sensors/${encodeURIComponent(sensorId)}/register${dev ? '?dev=true' : ''}`, { method: 'POST', body: JSON.stringify(payload) }),
  senteroUnassignedSensors: () => request<{ devices: SenteroDiscoveredSensor[] }>('/api/sentero/sensors/unassigned'),
  assignSenteroUnassignedSensor: (deviceId: string, payload: { sensor_type: string; room_id?: string | null; role?: string | null; name?: string | null }, dev = false) =>
    request<{ status: string; sensor: { id: string; name: string; room_id?: string | null; type: string } }>(`/api/sentero/sensors/unassigned/${encodeURIComponent(deviceId)}/assign${dev ? '?dev=true' : ''}`, { method: 'POST', body: JSON.stringify(payload) }),
  ignoreSenteroUnassignedSensor: (deviceId: string) =>
    request<{ status: string; device?: SenteroDiscoveredSensor }>(`/api/sentero/sensors/unassigned/${encodeURIComponent(deviceId)}/ignore`, { method: 'POST' }),
  removeSenteroUnassignedSensor: (deviceId: string) =>
    request<{ deleted: boolean; device_id: string }>(`/api/sentero/sensors/unassigned/${encodeURIComponent(deviceId)}`, { method: 'DELETE' }),
  senteroSensorNetwork: () => request<SenteroSensorNetworkSettings>('/api/sentero/sensors/network'),
  saveSenteroSensorNetwork: (payload: { wifi_ssid?: string; wifi_password?: string }) =>
    request<{ status: string; network: SenteroSensorNetworkSettings }>('/api/sentero/sensors/network', { method: 'POST', body: JSON.stringify(payload) }),
  testSenteroSensorNetwork: () => request<{ ok: boolean; message: string }>('/api/sentero/sensors/network/test', { method: 'POST' }),
  senteroEcoTrackerStatus: () => request<SenteroEcoTrackerStatus>('/api/sentero/sensors/ecotracker'),
  testSenteroEcoTracker: (host: string) =>
    request<{ ok: boolean; message: string; host: string; reading: SenteroEcoTrackerReading }>('/api/sentero/sensors/ecotracker/test', { method: 'POST', body: JSON.stringify({ host }) }),
  connectSenteroEcoTracker: (host: string) =>
    request<{ status: string; sensor: { id: string; name: string; room_id?: string | null; type: string }; reading: SenteroEcoTrackerReading }>('/api/sentero/sensors/ecotracker/connect', { method: 'POST', body: JSON.stringify({ host }) }),
  boxNetworkStatus: () => request<BoxNetworkStatus>('/api/setup/box-network/status'),
  boxNetworkWifiNetworks: () => request<{ networks: WifiNetwork[] }>('/api/setup/network/wifi/networks'),
  saveBoxNetworkWifi: (payload: { ssid: string; password: string }) =>
    request<BoxNetworkWifiResult>('/api/setup/box-network/wifi', { method: 'POST', body: JSON.stringify(payload) }),
  networkStatus: (diagnostics = false) => request<NetworkStatus>(`/api/sentero/network/status${diagnostics ? '?diagnostics=true' : ''}`),
  networkWifiNetworks: () => request<{ networks: WifiNetwork[] }>('/api/sentero/network/wifi/networks'),
  connectNetworkWifi: (payload: { ssid: string; password: string }) =>
    request<{ ok: boolean; applied: boolean; message: string; status: NetworkStatus }>('/api/sentero/network/wifi/connect', { method: 'POST', body: JSON.stringify(payload) }),
  testNetworkWifi: () => request<{ ok: boolean; message: string }>('/api/sentero/network/wifi/test', { method: 'POST' }),
  connectNetworkCellular: (payload: { apn?: string; username?: string; password?: string; pin?: string } = {}) =>
    request<{ ok: boolean; applied: boolean; message: string; status: NetworkStatus }>('/api/sentero/network/cellular/connect', { method: 'POST', body: JSON.stringify(payload) }),
  startNetworkSetupAp: () => request<{ ok: boolean; message: string }>('/api/sentero/network/setup-ap/start', { method: 'POST' }),
  senteroSensorProvisioningStatus: () => request<SenteroSensorProvisioningStatus>('/api/sentero/sensors/provisioning/status'),
  startSenteroPresenceDiscovery: () =>
    request<{ ok: boolean; message: string; discovery: SenteroEsp32DiscoveryStatus }>('/api/sentero/sensors/provisioning/esp32/discovery/start', { method: 'POST' }),
  senteroPresenceDiscovered: () =>
    request<SenteroEsp32DiscoveryStatus>('/api/sentero/sensors/provisioning/esp32/discovered'),
  startSenteroPresenceProvisioning: (payload: { room_id: string; display_name: string; device_id?: string | null }) =>
    request<SenteroEsp32ProvisioningResult>('/api/sentero/sensors/provisioning/esp32/start', { method: 'POST', body: JSON.stringify(payload) }),
  senteroDiscoveryCandidates: (sessionId: number, dev = false) =>
    request<SenteroCandidates>(`/api/sentero/setup/discovery/${sessionId}/candidates${dev ? '?dev=true' : ''}`),
  confirmSenteroDiscovery: (sessionId: number, entityId: string, payload?: { name?: string; room?: string }) =>
    request<{ status: string; role: SenteroSensorRole }>(`/api/sentero/setup/discovery/${sessionId}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ entity_id: entityId, ...(payload || {}) }),
    }),
  senteroSensorManagerStatus: () => request<SenteroSensorManagerStatus>('/api/sentero/sensors/status'),
  saveSenteroSetupSensors: () => request<SenteroSetupStatus>('/api/sentero/setup/sensors', { method: 'POST' }),
  saveSenteroContact: (payload: SenteroContactPayload) =>
    request<SenteroSetupStatus>('/api/sentero/setup/contact', { method: 'POST', body: JSON.stringify(payload) }),
  updateSenteroContact: (contactId: number, payload: SenteroContactPayload) =>
    request<SenteroSetupStatus>(`/api/sentero/setup/contact/${encodeURIComponent(String(contactId))}`, { method: 'PUT', body: JSON.stringify(payload) }),
  deleteSenteroContact: (contactId: number) =>
    request<SenteroSetupStatus>(`/api/sentero/setup/contact/${encodeURIComponent(String(contactId))}`, { method: 'DELETE' }),
  senteroEmailQuerySettings: () => request<SenteroMailQuerySettings>('/api/sentero/setup/email-queries'),
  updateSenteroEmailQueryContact: (contactId: number, payload: { email_queries_enabled: boolean; email_permissions: string[] }) =>
    request<SenteroMailQuerySettings>(`/api/sentero/setup/contact/${encodeURIComponent(String(contactId))}/email-queries`, { method: 'PUT', body: JSON.stringify(payload) }),
  discoverMailSettings: (email: string) =>
    request<MailConfig>('/api/mail/discover', { method: 'POST', body: JSON.stringify({ email }) }),
  verifyMailSettings: (payload: { email: string; password: string; config: MailConfig; imap_username?: string; smtp_username?: string }) =>
    request<{ ok: boolean; message: string }>('/api/mail/verify', { method: 'POST', body: JSON.stringify(payload) }),
  saveSenteroNotifications: (payload: { anomalies: boolean; critical: boolean; daily_summary: boolean }) =>
    request<SenteroSetupStatus>('/api/sentero/setup/notifications', { method: 'POST', body: JSON.stringify(payload) }),
  senteroNotificationChannels: () => request<{ channels: SenteroNotificationChannel[] }>('/api/sentero/notifications/channels'),
  senteroTelegramBot: () => request<SenteroTelegramBotInfo>('/api/sentero/notifications/telegram/bot'),
  senteroConsents: () => request<{ consents: SenteroConsent[] }>('/api/sentero/consents'),
  grantSenteroConsent: (payload: { contact_id: number; recipient_type?: string; purpose?: string; data_classes?: string[]; valid_until?: string | null }) =>
    request<{ consents: SenteroConsent[] }>('/api/sentero/consents', { method: 'POST', body: JSON.stringify(payload) }),
  revokeSenteroConsent: (consentId: number) =>
    request<{ consents: SenteroConsent[] }>(`/api/sentero/consents/${encodeURIComponent(String(consentId))}/revoke`, { method: 'POST' }),
  senteroExportTokens: () => request<{ tokens: SenteroExportToken[] }>('/api/sentero/exports/tokens'),
  createSenteroExportToken: (payload: { contact_id: number; purpose?: string; data_classes?: string[]; expires_at?: string | null }) =>
    request<{ token: string; record: SenteroExportToken }>('/api/sentero/exports/tokens', { method: 'POST', body: JSON.stringify(payload) }),
  revokeSenteroExportToken: (tokenId: number) =>
    request<{ tokens: SenteroExportToken[] }>(`/api/sentero/exports/tokens/${encodeURIComponent(String(tokenId))}/revoke`, { method: 'POST' }),
  senteroTransparency: (limit = 100) => request<SenteroTransparency>(`/api/sentero/transparency?limit=${encodeURIComponent(String(limit))}`),
  cleanupSenteroTransparency: (days = 180) =>
    request<{ deleted: Record<string, number>; retention: SenteroTransparency['retention'] }>('/api/sentero/transparency/retention/cleanup', { method: 'POST', body: JSON.stringify({ days }) }),
  saveSenteroNotificationChannel: (channel: 'email' | 'telegram' | 'whatsapp', payload: { enabled: boolean; config: Record<string, unknown> }) =>
    request<{ channels: SenteroNotificationChannel[] }>(`/api/sentero/notifications/channels/${channel}`, { method: 'POST', body: JSON.stringify(payload) }),
  testSenteroNotificationChannel: (channel: 'email' | 'telegram' | 'whatsapp') =>
    request<{ ok: boolean; message: string; detail?: string }>(`/api/sentero/notifications/test/${channel}`, { method: 'POST' }),
  completeSenteroSetup: () => request<SenteroSetupStatus>('/api/sentero/setup/complete', { method: 'POST' }),
  senteroSensorRoles: (includeState = false) => request<{ sensor_roles: SenteroSensorRole[] }>(`/api/sentero/sensor-roles${includeState ? '?include_state=true' : ''}`),
  renameSenteroSensorRole: (role: string, name: string) =>
    request<{ status: string; role: SenteroSensorRole }>(`/api/sentero/sensor-roles/${encodeURIComponent(role)}/name`, {
      method: 'PUT',
      body: JSON.stringify({ name }),
    }),
  testSenteroSensorRole: (role: string) =>
    request<{ ok: boolean; mode: string; message: string; entity_id?: string; state?: string; stale?: boolean }>(`/api/sentero/sensor-roles/${encodeURIComponent(role)}/test`, { method: 'POST' }),
  commandSenteroSensorRole: (role: string, payload: { command: string; enabled?: boolean; value?: unknown; settings?: Record<string, unknown> }) =>
    request<{ ok: boolean; message: string; hp_led?: boolean | null; fall_led?: boolean | null; led_status?: SenteroSensorRole['led_status']; response?: Record<string, unknown> }>(`/api/sentero/sensor-roles/${encodeURIComponent(role)}/command`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  deleteSenteroSensorRole: (role: string, options?: { localOnly?: boolean }) =>
    request<{ deleted: boolean; role: string; removal?: { reason?: string; message?: string; provider?: string } }>(
      `/api/sentero/sensor-roles/${encodeURIComponent(role)}${options?.localOnly ? '?local_only=true' : ''}`,
      { method: 'DELETE' },
    ),
};
