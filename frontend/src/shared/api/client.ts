const RAW_API_BASE = import.meta.env.VITE_API_BASE ?? '';
const API_BASE = normalizeApiBase(RAW_API_BASE);
const TOKEN_KEY = 'sentero.session-token';
export const AUTH_EXPIRED_EVENT = 'sentero:auth-expired';

export type SenteroUser = {
  id: number;
  email: string;
  display_name?: string | null;
  role: 'owner' | 'admin' | 'viewer' | string;
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
  device_class?: string | null;
  domain?: string | null;
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
  email?: string | null;
  phone?: string | null;
  telegram_chat_id?: string | null;
  whatsapp_phone_number?: string | null;
  preferred_channels?: string | string[] | null;
  notification_enabled?: number | boolean;
  primary_contact?: number | boolean;
  active?: number;
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

export type SenteroContactPayload = {
  name: string;
  relationship?: string;
  email?: string;
  phone?: string;
  telegram_chat_id?: string;
  whatsapp_phone_number?: string;
  preferred_channels?: string[];
  notification_enabled?: boolean;
  primary_contact?: boolean;
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
};

export type SenteroDiscoveredSensor = {
  id: string;
  name: string;
  type: string;
  confidence: number;
};

export type SenteroSensorDiscoveryResult = {
  discovery_id: number;
  status: 'found' | 'searching' | 'not_found' | string;
  message: string;
  sensor?: SenteroDiscoveredSensor | null;
  remaining_seconds?: number;
};

export type SenteroSensorNetworkSettings = {
  wifi_ssid: string;
  wifi_password_set: boolean;
  configured: boolean;
};

export type BoxNetworkStatus = {
  mode: 'disabled' | 'auto' | 'force' | string;
  network_ready: boolean;
  ethernet_active: boolean;
  wifi_active: boolean;
  ip_address?: string | null;
  setup_ap_active: boolean;
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
  senteroBehaviorTimeline: () => request<{ events: Array<{ event_time: string; room?: string | null; role?: string | null; state?: string | null }>; assessment: SenteroBehaviorAssessment | null }>('/api/sentero/behavior/timeline'),
  startSenteroSetup: () => request<SenteroSetupStatus>('/api/sentero/setup/start', { method: 'POST' }),
  saveSenteroProfile: (payload: { name?: string; birth_year?: number | null; age?: number | null; notes?: string }) =>
    request<SenteroSetupStatus>('/api/sentero/setup/profile', { method: 'POST', body: JSON.stringify(payload) }),
  saveSenteroSetupRooms: (rooms: string[]) =>
    request<SenteroSetupStatus>('/api/sentero/setup/rooms', { method: 'POST', body: JSON.stringify({ rooms }) }),
  startSenteroDiscovery: (payload: { role: string; room?: string | null; pairing_code?: string }) =>
    request<SenteroPairingStart>('/api/sentero/setup/discovery/start', { method: 'POST', body: JSON.stringify(payload) }),
  startSenteroZigbeePairing: (payload: { role: string; room?: string | null; duration?: number }) =>
    request<SenteroPairingStart>('/api/sentero/setup/pairing/zigbee/start', { method: 'POST', body: JSON.stringify(payload) }),
  startSenteroSensorDiscovery: (payload: { sensor_type: string; room_id?: string | null; role?: string | null; duration?: number }) =>
    request<SenteroSensorDiscoveryStart>('/api/sentero/sensors/start-discovery', { method: 'POST', body: JSON.stringify(payload) }),
  senteroDiscoveredSensors: (discoveryId: number, dev = false) =>
    request<SenteroSensorDiscoveryResult>(`/api/sentero/sensors/discovered?discovery_id=${discoveryId}${dev ? '&dev=true' : ''}`),
  cancelSenteroSensorDiscovery: (discoveryId?: number | null) =>
    request<{ ok: boolean; provider?: string; reason?: string }>('/api/sentero/sensors/discovery/cancel', { method: 'POST', body: JSON.stringify({ discovery_id: discoveryId ?? null }) }),
  registerSenteroSensor: (sensorId: string, payload: { discovery_id: number; name?: string | null; room_id?: string | null }, dev = false) =>
    request<{ status: string; sensor: { id: string; name: string; room_id?: string | null; type: string } }>(`/api/sentero/sensors/${encodeURIComponent(sensorId)}/register${dev ? '?dev=true' : ''}`, { method: 'POST', body: JSON.stringify(payload) }),
  senteroSensorNetwork: () => request<SenteroSensorNetworkSettings>('/api/sentero/sensors/network'),
  saveSenteroSensorNetwork: (payload: { wifi_ssid?: string; wifi_password?: string }) =>
    request<{ status: string; network: SenteroSensorNetworkSettings }>('/api/sentero/sensors/network', { method: 'POST', body: JSON.stringify(payload) }),
  testSenteroSensorNetwork: () => request<{ ok: boolean; message: string }>('/api/sentero/sensors/network/test', { method: 'POST' }),
  boxNetworkStatus: () => request<BoxNetworkStatus>('/api/setup/box-network/status'),
  saveBoxNetworkWifi: (payload: { ssid: string; password: string }) =>
    request<BoxNetworkWifiResult>('/api/setup/box-network/wifi', { method: 'POST', body: JSON.stringify(payload) }),
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
  saveSenteroSetupSensors: () => request<SenteroSetupStatus>('/api/sentero/setup/sensors', { method: 'POST' }),
  saveSenteroContact: (payload: SenteroContactPayload) =>
    request<SenteroSetupStatus>('/api/sentero/setup/contact', { method: 'POST', body: JSON.stringify(payload) }),
  updateSenteroContact: (contactId: number, payload: SenteroContactPayload) =>
    request<SenteroSetupStatus>(`/api/sentero/setup/contact/${encodeURIComponent(String(contactId))}`, { method: 'PUT', body: JSON.stringify(payload) }),
  deleteSenteroContact: (contactId: number) =>
    request<SenteroSetupStatus>(`/api/sentero/setup/contact/${encodeURIComponent(String(contactId))}`, { method: 'DELETE' }),
  saveSenteroNotifications: (payload: { anomalies: boolean; critical: boolean; daily_summary: boolean }) =>
    request<SenteroSetupStatus>('/api/sentero/setup/notifications', { method: 'POST', body: JSON.stringify(payload) }),
  senteroNotificationChannels: () => request<{ channels: SenteroNotificationChannel[] }>('/api/sentero/notifications/channels'),
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
    request<{ ok: boolean; mode: string; message: string; entity_id?: string; state?: string }>(`/api/sentero/sensor-roles/${encodeURIComponent(role)}/test`, { method: 'POST' }),
  deleteSenteroSensorRole: (role: string) =>
    request<{ deleted: boolean; role: string }>(`/api/sentero/sensor-roles/${encodeURIComponent(role)}`, { method: 'DELETE' }),
};
