import { useEffect, useMemo, useRef, useState } from 'react';
import type React from 'react';
import { Battery, Bell, ChevronDown, ChevronLeft, ChevronRight, CheckCircle2, Copy, DoorClosed, DoorOpen, Droplets, HardDrive, Home, KeyRound, Lightbulb, Mail, MessageCircle, Pencil, Plug, Plus, Save, Send, ShieldAlert, ShieldCheck, Thermometer, Trash2, UserRound, Users, Wifi, WifiOff, X} from 'lucide-react';
import QRCode from 'qrcode';
import { api, type BoxNetworkStatus, type MailConfig, type SenteroConsent, type SenteroEcoTrackerReading, type SenteroExportToken, type SenteroMailQuerySettings, type SenteroNotificationChannel, type SenteroSensorNetworkSettings, type SenteroSensorRole, type SenteroSetupStatus, type SenteroSystemStatus, type SenteroTelegramBotInfo, type SenteroTransparency, type SenteroTrustedContact } from '@shared/api/client';
import { UpdatePanel } from '../components/UpdatePanel';
import { useSenteroAuth } from '../auth/SenteroAuthContext';
import type { SenteroSettingsTab } from '../routes/routes';
import { senteroRouteToPath } from '../routes/routes';
import PersonIcon from '@mui/icons-material/Person';
import PersonOutlineIcon from '@mui/icons-material/PersonOff';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import DirectionsRunIcon from '@mui/icons-material/DirectionsRun';
import AccessibilityNewIcon from '@mui/icons-material/AccessibilityNew';
import HelpOutlineIcon from '@mui/icons-material/HelpOutlined';

type MeterAddType = 'electricity_meter' | 'water_meter' | 'gas_meter';
type AalActorRole = 'resident' | 'relative' | 'care_service' | 'emergency_service' | 'housing_provider' | 'admin';

const aalActorRoles: Array<{ value: AalActorRole; label: string }> = [
  { value: 'relative', label: 'Angehörige' },
  { value: 'care_service', label: 'Pflegedienst' },
  { value: 'emergency_service', label: 'Notfalldienst' },
  { value: 'housing_provider', label: 'Wohnungsanbieter' },
  { value: 'resident', label: 'Bewohner' },
  { value: 'admin', label: 'Administrator' },
];

const emailQueryPermissions = [
  { value: 'STATUS', label: 'Status' },
  { value: 'ACTIVITY', label: 'Aktivität' },
  { value: 'ROOM', label: 'Räume' },
  { value: 'ENVIRONMENT', label: 'Temperatur' },
  { value: 'NIGHT', label: 'Nacht' },
  { value: 'HISTORY', label: 'Historie' },
  { value: 'TECHNICAL_HEALTH', label: 'Technik' },
];

const roomLabels: Record<string, string> = {
  living_room: 'Wohnzimmer',
  kitchen: 'Küche',
  bathroom: 'Bad',
  bedroom: 'Schlafzimmer',
  hallway: 'Flur',
  entrance: 'Eingang',
};

const settingsTabs: Array<{ tab: SenteroSettingsTab; label: string; shortLabel: string; icon: React.ElementType }> = [
  { tab: 'profile', label: 'Profil', shortLabel: 'Profil', icon: UserRound },
  { tab: 'sensors', label: 'Räume & Sensoren', shortLabel: 'Räume', icon: Home },
  { tab: 'network', label: 'Netzwerk', shortLabel: 'Netz', icon: Wifi },
  { tab: 'contacts', label: 'Vertraute Personen', shortLabel: 'Personen', icon: Users },
  { tab: 'notifications', label: 'Benachrichtigungen', shortLabel: 'Benachr.', icon: Bell },
  { tab: 'transparency', label: 'Transparenz', shortLabel: 'Daten', icon: ShieldCheck },
  { tab: 'account', label: 'Konto & Zugriff', shortLabel: 'Konto', icon: KeyRound },
  { tab: 'system', label: 'System', shortLabel: 'System', icon: HardDrive },
];

export function SettingsPage({ activeTab }: { activeTab: SenteroSettingsTab }) {
  const { user, updateMe, changePassword } = useSenteroAuth();
  const [status, setStatus] = useState<SenteroSetupStatus | null>(null);
  const [systemStatus, setSystemStatus] = useState<SenteroSystemStatus | null>(null);
  const [systemStatusLoading, setSystemStatusLoading] = useState(false);
  const [systemHealthOpen, setSystemHealthOpen] = useState(false);
  const [sensors, setSensors] = useState<SenteroSensorRole[]>([]);
  const [saved, setSaved] = useState('');
  const [error, setError] = useState('');
  const [resetText, setResetText] = useState('');
  const [mobileShowList, setMobileShowList] = useState(true);
  const [profile, setProfile] = useState({ name: '', birthYear: '', notes: '' });
  const [contactForm, setContactForm] = useState(emptyContactForm());
  const [contactFormOpen, setContactFormOpen] = useState(false);
  const [editingContactId, setEditingContactId] = useState<number | null>(null);
  const [editContactForm, setEditContactForm] = useState(emptyContactForm());
  const [roomDraft, setRoomDraft] = useState('');
  const [notifications, setNotifications] = useState({ anomalies: true, critical: true, daily_summary: false });
  const [accountForm, setAccountForm] = useState({ display_name: '', email: '' });
  const [networkForm, setNetworkForm] = useState({ wifi_ssid: '', wifi_password: '' });
  const [networkStatus, setNetworkStatus] = useState<SenteroSensorNetworkSettings | null>(null);
  const [boxNetworkForm, setBoxNetworkForm] = useState({ ssid: '', password: '' });
  const [boxNetworkStatus, setBoxNetworkStatus] = useState<BoxNetworkStatus | null>(null);
  const [ecoTrackerHost, setEcoTrackerHost] = useState('');
  const [ecoTrackerMessage, setEcoTrackerMessage] = useState('');
  const [ecoTrackerReading, setEcoTrackerReading] = useState<SenteroEcoTrackerReading | null>(null);
  const [ecoTrackerBusy, setEcoTrackerBusy] = useState(false);
  const [passwordForm, setPasswordForm] = useState({ current_password: '', new_password: '', new_password_confirm: '' });
  const [accountEditing, setAccountEditing] = useState(false);
  const [passwordModalOpen, setPasswordModalOpen] = useState(false);
  const [ledStates, setLedStates] = useState<Record<string, boolean>>({});
  const [ledBusyRole, setLedBusyRole] = useState<string | null>(null);
  const [sensorTestBusyRole, setSensorTestBusyRole] = useState<string | null>(null);
  const [channels, setChannels] = useState<SenteroNotificationChannel[]>([]);
  const [telegramBot, setTelegramBot] = useState<SenteroTelegramBotInfo | null>(null);
  const [consents, setConsents] = useState<SenteroConsent[]>([]);
  const [exportTokens, setExportTokens] = useState<SenteroExportToken[]>([]);
  const [newExportToken, setNewExportToken] = useState<{ contactId: number; token: string } | null>(null);
  const [exportDialogContactId, setExportDialogContactId] = useState<number | null>(null);
  const [transparency, setTransparency] = useState<SenteroTransparency | null>(null);
  const [emailQueries, setEmailQueries] = useState<SenteroMailQuerySettings | null>(null);
  const [meterDiscovery, setMeterDiscovery] = useState<{ type: MeterAddType; status: 'idle' | 'searching' | 'found' | 'missing'; message: string; remainingSeconds?: number } | null>(null);
  const [setupChannel, setSetupChannel] = useState<'email' | 'telegram' | 'whatsapp' | null>(null);
  const [helpChannel, setHelpChannel] = useState<'email' | 'telegram' | 'whatsapp' | null>(null);
  const [mailDiscovery, setMailDiscovery] = useState<{ status: 'idle' | 'checking' | 'found' | 'failed'; message: string }>({ status: 'idle', message: '' });
  const [mailVerification, setMailVerification] = useState<{ busy: boolean; ok: boolean; message: string }>({ busy: false, ok: false, message: '' });
  const [emailAdvancedOpen, setEmailAdvancedOpen] = useState(false);
  const lastDiscoveredEmail = useRef('');
  const [channelForms, setChannelForms] = useState({
    email: { mail_from: '', smtp_host: '', smtp_port: '587', smtp_user: '', smtp_login: '', smtp_password: '', smtp_encryption: '', smtp_starttls: 'true', smtp_ssl: 'false', imap_host: '', imap_port: '993', imap_user: '', imap_password: '', imap_encryption: '', app_password_help_url: '', test_recipient: '' },
    telegram: { bot_token: '', default_chat_id: '', test_recipient: '' },
    whatsapp: { access_token: '', phone_number_id: '', business_account_id: '', api_version: 'v23.0', test_recipient: '' },
  });

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (setupChannel !== 'email') return;
    const email = normalizeEmail(channelForms.email.smtp_user);
    lastDiscoveredEmail.current = '';
    const savedEmailChannel = channels.find((item) => item.channel === 'email');
    const savedEmail = normalizeEmail(String(savedEmailChannel?.config?.smtp_user || ''));
    const usingSavedConfiguration = Boolean(
      savedEmailChannel?.configured && savedEmail && savedEmail === email
    );
    setMailVerification({
      busy: false,
      ok: usingSavedConfiguration,
      message: usingSavedConfiguration ? 'Gespeicherte Zugangsdaten vorhanden.' : '',
    });
    if (!isValidEmail(email)) {
      setMailDiscovery({ status: 'idle', message: '' });
      return;
    }
    if (hasEmailServerSettings(channelForms.email)) {
      setMailDiscovery({ status: 'idle', message: '' });
      return;
    }
    const timer = window.setTimeout(() => {
      void discoverEmailSettings(email);
    }, 500);
    return () => window.clearTimeout(timer);
  }, [setupChannel, channelForms.email.smtp_user, channels]);

  useEffect(() => {
    if (activeTab !== 'sensors') return;
    let active = true;
    let loading = false;

    async function refreshSensors() {
      if (loading) return;
      loading = true;
      try {
        const nextSensors = await api.senteroSensorRoles(true);
        if (active) {
          setSensors(nextSensors.sensor_roles);
          hydrateLedStates(nextSensors.sensor_roles);
        }
      } catch {
        // Keep the last known sensor state visible during transient refresh failures.
      } finally {
        loading = false;
      }
    }

    void refreshSensors();
    const timer = window.setInterval(() => void refreshSensors(), 2000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [activeTab]);

  useEffect(() => {
    if (activeTab !== 'system') return;
    let active = true;
    let loading = false;

    async function refreshSystemStatus() {
      if (loading) return;
      loading = true;
      if (active) setSystemStatusLoading(true);
      try {
        const next = await api.senteroSystemStatus();
        if (active) setSystemStatus(next);
      } catch {
        // Keep the last known status visible during short service restarts.
      } finally {
        loading = false;
        if (active) setSystemStatusLoading(false);
      }
    }

    void refreshSystemStatus();
    const timer = window.setInterval(() => void refreshSystemStatus(), 15000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [activeTab]);

  useEffect(() => {
    if (systemStatus?.overall && systemStatus.overall !== 'ok') {
      setSystemHealthOpen(true);
    }
  }, [systemStatus?.overall]);

  useEffect(() => {
    setAccountForm({ display_name: user?.display_name || '', email: user?.email || '' });
  }, [user]);

  async function load() {
    try {
      const [nextStatus, nextSensors, nextChannels, nextConsents, nextExportTokens, nextTransparency, nextNetwork, nextBoxNetwork, nextEmailQueries, nextEcoTracker] = await Promise.all([
        api.senteroSetupStatus(),
        api.senteroSensorRoles(true),
        api.senteroNotificationChannels(),
        api.senteroConsents(),
        api.senteroExportTokens(),
        api.senteroTransparency(),
        api.senteroSensorNetwork(),
        api.boxNetworkStatus(),
        api.senteroEmailQuerySettings(),
        api.senteroEcoTrackerStatus(),
      ]);
      const nextTelegramBot = nextChannels.channels.some((channel) => channel.channel === 'telegram' && channel.configured)
        ? await api.senteroTelegramBot().catch(() => null)
        : null;
      setStatus(nextStatus);
      setSensors(nextSensors.sensor_roles);
      hydrateLedStates(nextSensors.sensor_roles);
      setChannels(nextChannels.channels);
      setTelegramBot(nextTelegramBot);
      setConsents(nextConsents.consents);
      setExportTokens(nextExportTokens.tokens);
      setTransparency(nextTransparency);
      setNetworkStatus(nextNetwork);
      setNetworkForm({
        wifi_ssid: nextNetwork.wifi_ssid || '',
        wifi_password: '',
      });
      setBoxNetworkStatus(nextBoxNetwork);
      setEmailQueries(nextEmailQueries);
      setEcoTrackerHost(nextEcoTracker.host || '');
      setEcoTrackerReading(nextEcoTracker.reading || null);
      setEcoTrackerMessage(nextEcoTracker.reading ? ecoTrackerReadingMessage(nextEcoTracker.reading) : '');
      setBoxNetworkForm({ ssid: '', password: '' });
      hydrateChannelForms(nextChannels.channels);
      const sensorRooms = Array.from(new Set(nextSensors.sensor_roles.map((sensor) => sensor.room).filter(Boolean))) as string[];
      const savedRooms = nextStatus.selected_rooms || [];
      const cleanedRooms = savedRooms.filter((room) => sensorRooms.includes(room));
      if (cleanedRooms.length !== savedRooms.length) {
        void api.saveSenteroSetupRooms(cleanedRooms).catch(() => undefined);
      }
      setProfile({
        name: nextStatus.profile?.name || '',
        birthYear: nextStatus.profile?.birth_year ? String(nextStatus.profile.birth_year) : '',
        notes: nextStatus.profile?.notes || '',
      });
      setNotifications({
        anomalies: Boolean(nextStatus.notifications?.anomalies ?? true),
        critical: Boolean(nextStatus.notifications?.critical ?? true),
        daily_summary: Boolean(nextStatus.notifications?.daily_summary ?? false),
      });
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Einstellungen konnten nicht geladen werden.');
    }
  }

  async function addMeter(type: MeterAddType) {
    const meta = meterMeta(type);
    setError('');
    setSaved('');
    setMeterDiscovery({ type, status: 'searching', message: `${meta.label} wird gesucht.`, remainingSeconds: 180 });
    try {
      const started = await api.startSenteroSensorDiscovery({
        sensor_type: type,
        room_id: 'home',
        role: meta.role,
        duration: 180,
      });
      if (!started.discovery_id) throw new Error(started.message || 'Zähler konnte nicht gesucht werden.');
      await pollMeterDiscovery(type, started.discovery_id, Date.now());
    } catch (err) {
      setMeterDiscovery({ type, status: 'missing', message: err instanceof Error ? err.message : `${meta.label} konnte nicht verbunden werden.` });
    }
  }

  async function testEcoTracker() {
    const host = ecoTrackerHost.trim();
    if (!host) {
      setEcoTrackerMessage('Bitte geben Sie die IP-Adresse des EcoTrackers ein.');
      return;
    }
    setEcoTrackerBusy(true);
    setEcoTrackerMessage('EcoTracker wird geprüft ...');
    try {
      const result = await api.testSenteroEcoTracker(host);
      setEcoTrackerHost(result.host);
      setEcoTrackerReading(result.reading);
      setEcoTrackerMessage(ecoTrackerReadingMessage(result.reading));
      setError('');
    } catch (err) {
      setEcoTrackerMessage(err instanceof Error ? err.message : 'EcoTracker konnte nicht erreicht werden.');
    } finally {
      setEcoTrackerBusy(false);
    }
  }

  async function connectEcoTracker() {
    const host = ecoTrackerHost.trim();
    if (!host) {
      setEcoTrackerMessage('Bitte geben Sie die IP-Adresse des EcoTrackers ein.');
      return;
    }
    setEcoTrackerBusy(true);
    setEcoTrackerMessage('EcoTracker wird verbunden ...');
    try {
      const result = await api.connectSenteroEcoTracker(host);
      setEcoTrackerHost(host);
      setEcoTrackerReading(result.reading);
      setEcoTrackerMessage(`EcoTracker verbunden. ${ecoTrackerReadingMessage(result.reading)}`);
      setSaved('everHome EcoTracker IR wurde als Stromzähler verbunden.');
      setError('');
      await load();
    } catch (err) {
      setEcoTrackerMessage(err instanceof Error ? err.message : 'EcoTracker konnte nicht verbunden werden.');
    } finally {
      setEcoTrackerBusy(false);
    }
  }

  async function pollMeterDiscovery(type: MeterAddType, discoveryId: number, startedAt: number): Promise<void> {
    const meta = meterMeta(type);
    const result = await api.senteroDiscoveredSensors(discoveryId, false);
    if (result.status === 'found' && result.sensor) {
      await api.registerSenteroSensor(result.sensor.id, { discovery_id: discoveryId, name: meta.label, room_id: 'home' });
      setMeterDiscovery({ type, status: 'found', message: `${meta.label} wurde verbunden.`, remainingSeconds: 0 });
      setSaved(`${meta.label} wurde verbunden.`);
      await load();
      return;
    }
    const remainingSeconds = result.remaining_seconds ?? Math.max(0, 180 - Math.round((Date.now() - startedAt) / 1000));
    if (remainingSeconds <= 0 || result.status === 'not_found') {
      await api.cancelSenteroSensorDiscovery(discoveryId).catch(() => undefined);
      setMeterDiscovery({ type, status: 'missing', message: `${meta.label} wurde nicht gefunden.`, remainingSeconds: 0 });
      return;
    }
    setMeterDiscovery({ type, status: 'searching', message: `${meta.label} wird gesucht.`, remainingSeconds });
    await wait(2000);
    return pollMeterDiscovery(type, discoveryId, startedAt);
  }

  function hydrateLedStates(sensorRoles: SenteroSensorRole[]) {
    setLedStates((current) => {
      const next = { ...current };
      for (const sensor of sensorRoles) {
        const value = ledEnabledFromSensor(sensor);
        if (value != null) next[sensor.role] = value;
      }
      return next;
    });
  }

  const rooms = useMemo(() => {
    const fromSensors = sensors.map((sensor) => sensor.room).filter(Boolean) as string[];
    return Array.from(new Set(fromSensors));
  }, [sensors]);

  const availableChannels = useMemo(() => channelAvailability(channels), [channels]);

  function navigateTab(tab: SenteroSettingsTab) {
    window.history.pushState({}, '', senteroRouteToPath({ name: 'settings', tab }));
    window.dispatchEvent(new PopStateEvent('popstate'));
  }

  function toast(message = 'Gespeichert') {
    setSaved(`✓ ${message}`);
    window.setTimeout(() => setSaved(''), 2200);
  }

  async function saveProfile() {
    try {
      const calculatedAge = ageFromBirthYear(profile.birthYear);
      await api.saveSenteroProfile({ name: profile.name, birth_year: profile.birthYear ? Number.parseInt(profile.birthYear, 10) : null, age: calculatedAge, notes: profile.notes });
      toast();
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Profil konnte nicht gespeichert werden.');
    }
  }

  async function saveNetwork() {
    try {
      const result = await api.saveSenteroSensorNetwork({
        wifi_ssid: networkForm.wifi_ssid,
        wifi_password: networkForm.wifi_password,
      });
      setNetworkStatus(result.network);
      setNetworkForm((value) => ({ ...value, wifi_password: '' }));
      toast('Netzwerk gespeichert');
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Netzwerk konnte nicht gespeichert werden.');
    }
  }

  async function saveBoxNetwork() {
    try {
      const result = await api.saveBoxNetworkWifi({
        ssid: boxNetworkForm.ssid,
        password: boxNetworkForm.password,
      });
      setBoxNetworkStatus(result.status);
      setBoxNetworkForm({ ssid: '', password: '' });
      toast(result.message || 'Netzwerk gespeichert');
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Netzwerk konnte nicht gespeichert werden.');
    }
  }

  async function startNetworkRecovery() {
    if (!window.confirm('Setup-WLAN vorübergehend starten? Die bestehende Verbindung bleibt erhalten, bis eine neue Verbindung erfolgreich geprüft wurde.')) return;
    try {
      await api.startNetworkSetupAp();
      const status = await api.boxNetworkStatus();
      setBoxNetworkStatus(status);
      toast('Netzwerk-Einrichtung gestartet');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Netzwerk-Einrichtung konnte nicht gestartet werden.');
    }
  }

  async function testNetwork() {
    try {
      const result = await api.testSenteroSensorNetwork();
      if (result.ok) {
        toast(result.message || 'Netzwerk geprüft');
        setError('');
      } else {
        setError(result.message || 'Netzwerk konnte nicht geprüft werden.');
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Netzwerk konnte nicht geprüft werden.');
    }
  }

  async function saveAccount() {
    try {
      await updateMe({ displayName: accountForm.display_name, email: accountForm.email });
      toast('Konto gespeichert');
      setAccountEditing(false);
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Konto konnte nicht gespeichert werden.');
    }
  }

  async function savePassword() {
    try {
      await changePassword({
        currentPassword: passwordForm.current_password,
        newPassword: passwordForm.new_password,
        newPasswordConfirm: passwordForm.new_password_confirm,
      });
      setPasswordForm({ current_password: '', new_password: '', new_password_confirm: '' });
      setPasswordModalOpen(false);
      toast('Passwort geändert');
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Passwort konnte nicht geändert werden.');
    }
  }

  async function addContact() {
    if (!contactForm.name.trim()) {
      setError('Bitte geben Sie einen Namen ein.');
      return;
    }
    const email = normalizeEmail(contactForm.email);
    if (email && (status?.trusted_contacts || []).some((contact) => normalizeEmail(contact.email || '') === email)) {
      setError('Diese E-Mail-Adresse ist bereits hinterlegt.');
      return;
    }
    try {
      const payload = contactPayload({ ...contactForm, email }, availableChannels);
      const validation = validateContactPayload(payload);
      if (validation) {
        setError(validation);
        return;
      }
      await api.saveSenteroContact(payload);
      setContactForm(emptyContactForm());
      setContactFormOpen(false);
      toast('Person hinzugefügt');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Kontakt konnte nicht gespeichert werden.');
    }
  }

  async function deleteContact(contactId: number) {
    if (!window.confirm('Vertraute Person wirklich löschen?')) return;
    try {
      await api.deleteSenteroContact(contactId);
      toast('Person gelöscht');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Kontakt konnte nicht gelöscht werden.');
    }
  }

  async function grantContactConsent(contactId: number) {
    try {
      const result = await api.grantSenteroConsent({
        contact_id: contactId,
        recipient_type: actorRoleForContact(status?.trusted_contacts?.find((contact) => contact.id === contactId)?.actor_role),
        purpose: 'behavior_notification',
        data_classes: ['personal_behavior', 'health_adjacent', 'emergency'],
      });
      setConsents(result.consents);
      toast('Freigabe aktiv');
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Freigabe konnte nicht gespeichert werden.');
    }
  }

  async function revokeContactConsent(consentId: number) {
    if (!window.confirm('Freigabe für Verhaltensmeldungen widerrufen?')) return;
    try {
      const result = await api.revokeSenteroConsent(consentId);
      setConsents(result.consents);
      toast('Freigabe widerrufen');
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Freigabe konnte nicht widerrufen werden.');
    }
  }

  async function grantExportConsent(contactId: number) {
    try {
      const result = await api.grantSenteroConsent({
        contact_id: contactId,
        recipient_type: actorRoleForContact(status?.trusted_contacts?.find((contact) => contact.id === contactId)?.actor_role),
        purpose: 'aal_partner_export',
        data_classes: exportDataClassesForContact(status?.trusted_contacts?.find((contact) => contact.id === contactId)?.actor_role),
      });
      setConsents(result.consents);
      toast('Export freigegeben');
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Export-Freigabe konnte nicht gespeichert werden.');
    }
  }

  async function revokeExportConsent(consentId: number) {
    if (!window.confirm('Export-Freigabe widerrufen? Aktive Tokens sollten danach ebenfalls widerrufen werden.')) return;
    try {
      const result = await api.revokeSenteroConsent(consentId);
      setConsents(result.consents);
      toast('Export-Freigabe widerrufen');
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Export-Freigabe konnte nicht widerrufen werden.');
    }
  }

  async function createExportToken(contactId: number) {
    try {
      const contact = status?.trusted_contacts?.find((entry) => entry.id === contactId);
      const result = await api.createSenteroExportToken({
        contact_id: contactId,
        purpose: 'aal_partner_export',
        data_classes: exportDataClassesForContact(contact?.actor_role),
      });
      setExportTokens((tokens) => [result.record, ...tokens.filter((token) => token.id !== result.record.id)]);
      setNewExportToken({ contactId, token: result.token });
      setExportDialogContactId(contactId);
      toast('Export-Token erstellt');
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Export-Token konnte nicht erstellt werden.');
    }
  }

  async function updateEmailQueryPermission(contactId: number, permission: string, checked: boolean) {
    const contact = emailQueries?.contacts.find((item) => item.id === contactId);
    if (!contact) return;
    const permissions = new Set(contact.email_permissions || []);
    if (checked) permissions.add(permission);
    else permissions.delete(permission);
    const next = await api.updateSenteroEmailQueryContact(contactId, {
      email_queries_enabled: contact.email_queries_enabled,
      email_permissions: Array.from(permissions),
    });
    setEmailQueries(next);
  }

  async function toggleEmailQueries(contactId: number, enabled: boolean) {
    const contact = emailQueries?.contacts.find((item) => item.id === contactId);
    if (!contact) return;
      const next = await api.updateSenteroEmailQueryContact(contactId, {
      email_queries_enabled: enabled,
      email_permissions: contact.email_permissions?.length ? contact.email_permissions : ['STATUS', 'ACTIVITY', 'ROOM', 'ENVIRONMENT', 'NIGHT', 'TECHNICAL_HEALTH'],
    });
    setEmailQueries(next);
  }

  async function revokeExportToken(tokenId: number) {
    if (!window.confirm('Export-Token widerrufen? Der Partner kann ihn danach nicht mehr nutzen.')) return;
    try {
      const revokedContactId = exportTokens.find((token) => token.id === tokenId)?.contact_id || null;
      const result = await api.revokeSenteroExportToken(tokenId);
      setExportTokens(result.tokens);
      setNewExportToken((value) => value && revokedContactId === value.contactId ? null : value);
      toast('Export-Token widerrufen');
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Export-Token konnte nicht widerrufen werden.');
    }
  }

  async function copyExportToken(token: string) {
    try {
      await navigator.clipboard.writeText(token);
      toast('Token kopiert');
    } catch {
      setError('Token konnte nicht automatisch kopiert werden.');
    }
  }

  async function cleanupTransparency() {
    const days = transparency?.retention.retention_days || 180;
    if (!window.confirm(`Audit- und Transparenzdaten älter als ${days} Tage löschen?`)) return;
    try {
      await api.cleanupSenteroTransparency(days);
      const refreshed = await api.senteroTransparency();
      setTransparency(refreshed);
      toast('Alte Transparenzdaten gelöscht');
      setError('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Transparenzdaten konnten nicht gelöscht werden.');
    }
  }

  function startEditContact(contact: {
    id: number;
    name: string;
    relationship?: string | null;
    actor_role?: string | null;
    email?: string | null;
    phone?: string | null;
    telegram_chat_id?: string | null;
    whatsapp_phone_number?: string | null;
    preferred_channels?: string | string[] | null;
    notification_enabled?: number | boolean;
    primary_contact?: number | boolean;
  }) {
    setEditingContactId(contact.id);
    setEditContactForm({
      name: contact.name,
      relationship: contact.relationship || '',
      actor_role: actorRoleForContact(contact.actor_role),
      email: contact.email || '',
      phone: contact.phone || '',
      telegram_chat_id: contact.telegram_chat_id || '',
      whatsapp_phone_number: contact.whatsapp_phone_number || '',
      preferred_channels: normalizeChannels(contact.preferred_channels),
      notification_enabled: Boolean(contact.notification_enabled ?? true),
      primary_contact: Boolean(contact.primary_contact),
    });
  }

  async function saveEditedContact() {
    if (!editingContactId) return;
    if (!editContactForm.name.trim()) {
      setError('Bitte geben Sie einen Namen ein.');
      return;
    }
    const email = normalizeEmail(editContactForm.email);
    if (email && (status?.trusted_contacts || []).some((contact) => contact.id !== editingContactId && normalizeEmail(contact.email || '') === email)) {
      setError('Diese E-Mail-Adresse ist bereits hinterlegt.');
      return;
    }
    try {
      const payload = contactPayload({ ...editContactForm, email }, availableChannels);
      const validation = validateContactPayload(payload);
      if (validation) {
        setError(validation);
        return;
      }
      await api.updateSenteroContact(editingContactId, payload);
      setEditingContactId(null);
      setEditContactForm(emptyContactForm());
      toast('Person gespeichert');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Kontakt konnte nicht gespeichert werden.');
    }
  }

  async function saveRooms(nextRooms: string[]) {
    try {
      await api.saveSenteroSetupRooms(nextRooms);
      toast('Räume gespeichert');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Räume konnten nicht gespeichert werden.');
    }
  }

  async function addRoom() {
    const label = roomDraft.trim();
    if (!label) {
      setError('Bitte geben Sie einen Raumnamen ein.');
      return;
    }
    if (rooms.includes(label)) {
      setError('Dieser Raum existiert bereits.');
      return;
    }
    setRoomDraft('');
    setError('Räume werden gemeinsam mit einem Sensor eingerichtet. Bitte nutzen Sie „Sensor hinzufügen“.');
  }

  async function deleteRoom(room: string) {
    const roomSensors = sensors.filter((sensor) => sensor.room === room);
    const message = roomSensors.length
      ? 'Raum wirklich löschen? Zugeordnete Sensoren werden auch entfernt.'
      : 'Raum wirklich löschen?';
    if (!window.confirm(message)) return;
    try {
      for (const sensor of roomSensors) {
        await deleteSensorRoleWithFallback(sensor);
      }
      await api.saveSenteroSetupRooms(rooms.filter((item) => item !== room));
      toast('Raum gelöscht');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Raum konnte nicht gelöscht werden.');
    }
  }

  async function saveNotifications(nextNotifications = notifications) {
    try {
      await api.saveSenteroNotifications(nextNotifications);
      toast('Gespeichert');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Benachrichtigungen konnten nicht gespeichert werden.');
    }
  }

  function updateNotificationPreference(key: keyof typeof notifications, checked: boolean) {
    const nextNotifications = { ...notifications, [key]: checked };
    setNotifications(nextNotifications);
    void saveNotifications(nextNotifications);
  }

  function hydrateChannelForms(nextChannels: SenteroNotificationChannel[]) {
    setChannelForms((current) => {
      const byChannel = Object.fromEntries(nextChannels.map((item) => [item.channel, item.config || {}]));
      return {
        email: {
          ...current.email,
          ...stringConfig(byChannel.email),
          smtp_login: String(byChannel.email?.smtp_login || current.email.smtp_login || byChannel.email?.smtp_user || ''),
          smtp_port: String(byChannel.email?.smtp_port || current.email.smtp_port),
          imap_port: String(byChannel.email?.imap_port || current.email.imap_port),
          // The API returns secrets masked. A mask such as "••••••" is not a
          // password and must never be sent back to IMAP/SMTP.
          smtp_password: '',
          imap_password: '',
        },
        telegram: { ...current.telegram, ...stringConfig(byChannel.telegram) },
        whatsapp: { ...current.whatsapp, ...stringConfig(byChannel.whatsapp) },
      };
    });
  }

  async function saveChannel(channel: 'email' | 'telegram' | 'whatsapp') {
    try {
      if (channel === 'email' && !mailVerification.ok) {
        setMailVerification({ busy: false, ok: false, message: 'Bitte prüfen Sie zuerst die Verbindung.' });
        return;
      }
      const config = channel === 'email' ? emailChannelConfig(channelForms.email) : channelForms[channel];
      await api.saveSenteroNotificationChannel(channel, { enabled: false, config });
      toast(channel === 'email' ? 'E-Mail ist eingerichtet.' : 'Kanal gespeichert. Bitte testen, um ihn für Vertrauenspersonen freizuschalten.');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Kanal konnte nicht gespeichert werden.');
    }
  }

  async function discoverEmailSettings(email: string) {
    if (lastDiscoveredEmail.current === email) return;
    lastDiscoveredEmail.current = email;
    setMailDiscovery({ status: 'checking', message: 'Mailbox wird erkannt ...' });
    try {
      const config = await api.discoverMailSettings(email);
      setChannelForms((current) => ({
        ...current,
        email: discoveredEmailForm(current.email, config, email),
      }));
      setMailDiscovery({ status: 'found', message: 'Mailbox erkannt' });
    } catch {
      setMailDiscovery({ status: 'failed', message: 'Sentero konnte den Mailserver nicht automatisch erkennen.' });
    }
  }

  async function verifyEmailSettings(values?: { email?: string; password?: string }) {
    const email = normalizeEmail(values?.email || channelForms.email.smtp_user);
    const enteredPassword = values?.password ?? channelForms.email.smtp_password ?? channelForms.email.imap_password;
    const password = looksMaskedSecret(enteredPassword) ? '' : enteredPassword;
    const effectiveForm = {
      ...channelForms.email,
      smtp_user: email,
      smtp_login: channelForms.email.smtp_login || email,
      imap_user: channelForms.email.imap_user || email,
      smtp_password: password,
      imap_password: password,
      mail_from: channelForms.email.mail_from || 'Sentero',
    };
    if (!isValidEmail(email)) {
      setMailVerification({ busy: false, ok: false, message: 'Bitte geben Sie eine gültige E-Mail-Adresse ein.' });
      return;
    }
    const savedEmailChannel = channels.find((item) => item.channel === 'email');
    const canUseSavedPassword = Boolean(
      savedEmailChannel?.configured
      && normalizeEmail(String(savedEmailChannel?.config?.smtp_user || '')) === email
    );
    if (!password && !canUseSavedPassword) {
      setMailVerification({ busy: false, ok: false, message: 'Bitte geben Sie das Passwort oder App-Passwort ein.' });
      return;
    }
    if (!effectiveForm.smtp_host || !effectiveForm.imap_host) {
      setEmailAdvancedOpen(true);
      setMailVerification({ busy: false, ok: false, message: 'Sentero konnte den Mailserver nicht automatisch erkennen.' });
      return;
    }
    setChannelForms((current) => ({ ...current, email: effectiveForm }));
    setMailVerification({ busy: true, ok: false, message: 'Verbindung wird geprüft ...' });
    try {
      const result = await api.verifyMailSettings({
        email,
        password,
        config: mailConfigFromForm(effectiveForm),
        imap_username: effectiveForm.imap_user || email,
        smtp_username: effectiveForm.smtp_login || email,
      });
      setMailVerification({
        busy: false,
        ok: result.ok,
        message: result.ok ? 'Senden und Empfangen funktioniert.' : friendlyMailError(result.message, Boolean(channelForms.email.app_password_help_url)),
      });
    } catch (err) {
      setMailVerification({
        busy: false,
        ok: false,
        message: friendlyMailError(err instanceof Error ? err.message : '', Boolean(channelForms.email.app_password_help_url)),
      });
    }
  }

  async function testChannel(channel: 'email' | 'telegram' | 'whatsapp') {
    try {
      const config = channel === 'email' ? emailChannelConfig(channelForms.email) : channelForms[channel];
      await api.saveSenteroNotificationChannel(channel, { enabled: false, config });
      const result = await api.testSenteroNotificationChannel(channel);
      if (result.ok) {
        setError('');
        toast(result.message || 'Testnachricht gesendet');
      } else {
        setError(result.message || 'Die Testnachricht konnte nicht gesendet werden. Bitte prüfen Sie die Zugangsdaten.');
      }
      await load();
    } catch {
      setError('Die Testnachricht konnte nicht gesendet werden. Bitte prüfen Sie die Zugangsdaten.');
    }
  }

  async function deleteSensor(sensor: SenteroSensorRole) {
    const esp32Presence = isEsp32PresenceSensor(sensor);
    if (esp32Presence && sensor.reachable === false) {
      const localOnly = window.confirm(
        'Sensor ist derzeit nicht erreichbar.\n\nEr kann deshalb nicht auf Werkseinstellungen zurückgesetzt werden.\n\nNur aus Sentero entfernen?\n\nWird der Sensor später wieder eingeschaltet, muss er zunächst manuell auf Werkseinstellungen zurückgesetzt werden.',
      );
      if (!localOnly) return;
      try {
        await api.deleteSenteroSensorRole(sensor.role, { localOnly: true });
        toast('Sensor aus Sentero entfernt');
        await load();
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Sensor konnte nicht entfernt werden.');
      }
      return;
    }
    const message = esp32Presence
      ? 'Präsenzsensor wirklich entfernen?\n\nDer Sensor wird auf Werkseinstellungen zurückgesetzt und muss anschließend neu eingerichtet werden.'
      : 'Sensor aus Sentero entfernen?';
    if (!window.confirm(message)) return;
    try {
      await deleteSensorRoleWithFallback(sensor);
      toast('Sensor entfernt');
      await load();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Sensor konnte nicht entfernt werden.';
      if (canOfferLocalOnlySensorDelete(sensor, message)) {
        const localOnly = window.confirm(
          'Der Sensor konnte nicht zurückgesetzt oder aus dem Sensornetzwerk entfernt werden.\n\nNur aus Sentero entfernen?\n\nDer Sensor bleibt dann technisch unverändert und muss bei Bedarf separat zurückgesetzt oder entfernt werden.',
        );
        if (localOnly) {
          try {
            await api.deleteSenteroSensorRole(sensor.role, { localOnly: true });
            toast('Sensor aus Sentero entfernt');
            await load();
            return;
          } catch (localErr) {
            setError(localErr instanceof Error ? localErr.message : 'Sensor konnte nicht entfernt werden.');
            return;
          }
        }
      }
      setError(message);
    }
  }

  async function deleteSensorRoleWithFallback(sensor: SenteroSensorRole) {
    try {
      return await api.deleteSenteroSensorRole(sensor.role);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Sensor konnte nicht entfernt werden.';
      if (canOfferLocalOnlySensorDelete(sensor, message)) {
        const localOnly = window.confirm(
          'Der Sensor konnte nicht aus dem Sensornetzwerk entfernt werden.\n\nNur aus Sentero entfernen?\n\nDer Sensor bleibt dann in Zigbee2MQTT bzw. im Sensornetzwerk erhalten und muss dort bei Bedarf separat entfernt werden.',
        );
        if (localOnly) {
          return await api.deleteSenteroSensorRole(sensor.role, { localOnly: true });
        }
      }
      throw err;
    }
  }

  async function renameSensor(sensor: SenteroSensorRole) {
    const currentName = sensor.label || sensor.role;
    const nextName = window.prompt('Neuer Sensorname', currentName);
    if (nextName === null) return;
    const cleanName = nextName.trim();
    if (!cleanName) {
      setError('Bitte geben Sie einen Sensornamen ein.');
      return;
    }
    try {
      await api.renameSenteroSensorRole(sensor.role, cleanName);
      toast('Sensor umbenannt');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sensor konnte nicht umbenannt werden.');
    }
  }

  async function testSensor(sensor: SenteroSensorRole) {
    const wasOffline = sensor.reachable === false || sensor.stale === true;
    setSensorTestBusyRole(sensor.role);
    try {
      const result = await api.testSenteroSensorRole(sensor.role);
      if (!result.ok) {
        setError(result.message || 'Sensor ist aktuell nicht erreichbar.');
      } else if (wasOffline && result.stale !== false) {
        setError('Sensor erneut geprüft. Sentero wartet auf neue Sensordaten.');
      } else {
        setError('');
        toast(result.message || 'Sensor geprüft');
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sensor konnte nicht geprüft werden.');
    } finally {
      setSensorTestBusyRole(null);
    }
  }

  async function toggleSensorLeds(sensor: SenteroSensorRole) {
    if (!sensorSupportsLedControl(sensor)) {
      setError('Dieser Sensor unterstützt keine LED-Steuerung.');
      return;
    }
    const currentEnabled = ledEnabled(sensor, ledStates);
    const enabled = !currentEnabled;
    setLedBusyRole(sensor.role);
    try {
      const result = await api.commandSenteroSensorRole(sensor.role, {
        command: 'configure',
        settings: {
          hp_led: enabled,
          fall_led: enabled,
        },
      });
      if (!result.ok) {
        setError(result.message || 'LEDs konnten nicht geschaltet werden.');
        return;
      }
      const confirmed = ledEnabledFromCommandResult(result);
      setLedStates((current) => ({ ...current, [sensor.role]: confirmed ?? enabled }));
      toast(enabled ? 'LEDs eingeschaltet' : 'LEDs ausgeschaltet');
      setError('');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'LEDs konnten nicht geschaltet werden.');
    } finally {
      setLedBusyRole(null);
    }
  }

  const activeTabMeta = settingsTabs.find((item) => item.tab === activeTab);
  const exportDialogToken = newExportToken && exportDialogContactId === newExportToken.contactId ? newExportToken.token : null;
  const exportDialogContact = exportDialogContactId ? status?.trusted_contacts?.find((contact) => contact.id === exportDialogContactId) || null : null;

  function mobileNavigateTab(tab: SenteroSettingsTab) {
    setMobileShowList(false);
    navigateTab(tab);
  }

  return (
    <section className="sc-page sc-settings">
      {saved && <div className="sc-toast" role="status">{saved}</div>}
      {error && <div className="sc-form-errors" role="alert"><p>{error}</p></div>}

      {/* Mobile: Übersichtsliste */}
      {mobileShowList && (
        <div className="sc-settings-mobile-list sc-mobile-only">
          <h1>Einstellungen</h1>
          <nav aria-label="Einstellungsbereiche">
            {settingsTabs.map((item) => {
              const Icon = item.icon;
              return (
                <button key={item.tab} type="button" onClick={() => mobileNavigateTab(item.tab)}>
                  <Icon size={22} aria-hidden="true" />
                  <span>{item.label}</span>
                  <ChevronRight size={18} aria-hidden="true" />
                </button>
              );
            })}
          </nav>
        </div>
      )}

      {/* Mobile: Zurück-Button und Seitentitel wenn Tab aktiv */}
      {!mobileShowList && activeTabMeta && (
        <div className="sc-settings-mobile-header sc-mobile-only">
          <button
            className="sc-settings-back"
            type="button"
            onClick={() => setMobileShowList(true)}
            aria-label="Zurück zu Einstellungen"
          >
            <ChevronLeft size={20} aria-hidden="true" />
            <span>Einstellungen</span>
          </button>
        </div>
      )}

      {/* Tab-Inhalte – auf Mobile ausgeblendet wenn Liste sichtbar */}
      <div className={mobileShowList ? 'sc-settings-content sc-mobile-hidden' : 'sc-settings-content'}>

      {activeTab === 'profile' && (
        <section className="sc-panel sc-settings-panel">
          <div className="sc-section-title">
          <h2>Profil</h2></div>
          <div className="sc-form-grid">
            <label>Name der betreuten Person<input value={profile.name} onChange={(event) => setProfile((value) => ({ ...value, name: event.target.value }))} /></label>
            <label>
              Geburtsjahr
              <input inputMode="numeric" maxLength={4} value={profile.birthYear} onChange={(event) => setProfile((value) => ({ ...value, birthYear: event.target.value.replace(/\D+/g, '').slice(0, 4) }))} placeholder="1945" />
            </label>
            <label className="sc-form-wide">
              Besondere Hinweise (optional)
              <textarea value={profile.notes} onChange={(event) => setProfile((value) => ({ ...value, notes: event.target.value }))} placeholder="z.B. Eingeschränkte Mobilität, Rollator, regelmäßige Arzttermine ..." />
              <small>Diese Informationen helfen Sentero, Auffälligkeiten besser einzuordnen.</small>
            </label>
          </div>
          <button className="sc-profile-save-button" type="button" onClick={() => void saveProfile()}><Save size={18} /> Speichern</button>
        </section>
      )}

      {activeTab === 'sensors' && (
        <section className="sc-panel sc-settings-panel">
          <div className="sc-section-title"><h2>Räume & Sensoren</h2><button type="button" onClick={() => window.location.assign('/sentero/setup')}><Plus size={20} /> Sensor hinzufügen</button></div>

          <section className="sc-network-card">
            <div className="sc-network-card-head">
              <div>
                <h3>everHome EcoTracker IR</h3>
                <p>{ecoTrackerMessage || 'EcoTracker-IP eintragen, um Leistung und Stromzählerstand lokal auszulesen.'}</p>
              </div>
              <span className={`sc-network-pill ${ecoTrackerHost ? 'ready' : 'setup'}`}>
                {ecoTrackerHost ? 'Verbunden' : 'Einrichten'}
              </span>
            </div>
            <div className="sc-inline-add">
              <input value={ecoTrackerHost} onChange={(event) => setEcoTrackerHost(event.target.value)} placeholder="EcoTracker IP, z.B. 192.168.1.42" />
              <button type="button" onClick={() => void testEcoTracker()} disabled={ecoTrackerBusy}><Wifi size={18} /> Prüfen</button>
              <button type="button" onClick={() => void connectEcoTracker()} disabled={ecoTrackerBusy}><Plug size={18} /> Verbinden</button>
            </div>
          </section>

          <div className="sc-inline-add">
            <input value={roomDraft} onChange={(event) => setRoomDraft(event.target.value)} placeholder="Raum hinzufügen" />
            <button type="button" onClick={() => void addRoom()}><Plus size={20} /> Raum hinzufügen</button>
          </div>
          {rooms.length === 0 && <EmptyState text="Noch keine Räume oder Sensoren eingerichtet." action="Einrichtungsassistent starten" />}
          <div className="sc-room-settings-list">
            {rooms.map((room) => {
              const roomSensors = sensors.filter((sensor) => sensor.room === room);
              return (
                <details key={room} open>
                  <summary>
                    <div>
                      <strong>{roomLabels[room] || room}</strong>
                      <small>{roomSensors.length} Sensoren verbunden</small>
                    </div>
                    <button className="sc-room-delete" type="button" onClick={(event) => { event.preventDefault(); void deleteRoom(room); }}><Trash2 size={18} /></button>
                  </summary>
                  <div className="sc-sensor-settings-list">
                    {roomSensors.length === 0 && <p className="sc-muted-note">Für diesen Raum ist noch kein Sensor verbunden.</p>}
                    {roomSensors.map((sensor) => (
                      <div key={sensor.role}>
                        <div className="sc-sensor-settings-main">
                          <div className="sc-sensor-settings-head">
                            <div className="sc-sensor-title-line">
                              <span
                                className={`sc-sensor-connection-dot ${sensorConnectionTone(sensor)}`}
                                title={sensorConnectionLabel(sensor)}
                                aria-label={sensorConnectionLabel(sensor)}
                              />
                              <strong>{sensor.label || sensor.role}</strong>
                            </div>
                            <small>{sensorType(sensor)} · letzte Meldung {formatDateTime(sensor.last_changed || sensor.last_updated || sensor.updated_at)}</small>
                          </div>
                          <div className="sc-sensor-health">
                            {isDoorContactSensor(sensor) && <DoorContactStatus sensor={sensor} />}
                            {isSmokeSensor(sensor) && <SmokeStatus sensor={sensor} />}
                            {isEsp32PresenceSensor(sensor) && <C1001Telemetry sensor={sensor} />}
                            {!isEsp32PresenceSensor(sensor) && !isSmokeSensor(sensor) && isMotionSensor(sensor) && <MotionStatus sensor={sensor} />}
                            {isSmartMeterSensor(sensor) && <span className="battery"><Plug size={17} /> {formatMeterValue(sensor)}</span>}
                            {isEcoTrackerSensor(sensor) && ecoTrackerMeterReadingLabel(ecoTrackerReading) && <span className="battery"><Plug size={17} /> {ecoTrackerMeterReadingLabel(ecoTrackerReading)}</span>}
                            <SensorEnvironment sensor={sensor} />
                            {sensorPowerLabel(sensor) === 'USB-Strom' ? (
                              <span className="battery"><Plug size={17} /> USB-Strom</span>
                            ) : (
                              <span className={batteryClass(sensor.battery_level)}>
                                <Battery size={17} />
                                {sensor.battery_level ?? 'unbekannt'}{sensor.battery_level == null ? '' : '%'}
                              </span>
                            )}
                          </div>
                        </div>
                        <div className="sc-sensor-settings-actions">
                          {isEsp32PresenceSensor(sensor) && sensorSupportsLedControl(sensor) && (
                            <button
                              className={`led-dot-btn ${ledEnabled(sensor, ledStates) ? 'led-on' : 'led-off'}`}
                              type="button"
                              onClick={() => void toggleSensorLeds(sensor)}
                              disabled={ledBusyRole === sensor.role || sensor.reachable === false}
                              title={ledEnabled(sensor, ledStates) ? 'LEDs ausschalten' : 'LEDs einschalten'}
                              aria-label={ledEnabled(sensor, ledStates) ? 'LEDs ausschalten' : 'LEDs einschalten'}
                              aria-pressed={ledEnabled(sensor, ledStates)}
                            >
                              <span aria-hidden="true" />
                            </button>
                          )}
                          <button type="button" onClick={() => void renameSensor(sensor)} title="Sensor umbenennen" aria-label="Sensor umbenennen"><Pencil size={18} aria-hidden="true" /> </button>
                          <button
                            type="button"
                            onClick={() => void testSensor(sensor)}
                            disabled={sensorTestBusyRole === sensor.role}
                            title={sensor.reachable === false ? 'Sensor erneut prüfen' : 'Sensor prüfen'}
                            aria-label={sensor.reachable === false ? 'Sensor erneut prüfen' : 'Sensor prüfen'}
                          >
                            {sensor.reachable === false ? <WifiOff size={18} aria-hidden="true" /> : <Wifi size={18} aria-hidden="true" />}
                          </button>
                          <button type="button" onClick={() => void deleteSensor(sensor)} title="Sensor entfernen" aria-label="Sensor entfernen"><Trash2 size={18} aria-hidden="true" /> </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </details>
              );
            })}
          </div>
        </section>
      )}

      {activeTab === 'network' && (
        <section className="sc-panel sc-settings-panel sc-network-panel">
          <div className="sc-settings-hero">
             <div className="sc-section-title">
            <h2>Netzwerk</h2>
             </div>
            <p>Prüfen Sie die lokale Netzwerk- und Internetverbindung der Sentero-Box oder richten Sie WLAN bei Bedarf neu ein.</p>
          </div>
          <div className="sc-network-sections">
            <div className="sc-network-card">
              <div className="sc-network-card-head">
                <div>
                  <h3>Sentero-Box</h3>
                  <p>{boxNetworkStatus?.message || 'Netzwerkstatus wird geladen.'}</p>
                </div>
                <span className={`sc-network-pill ${boxNetworkStatus?.network_ready ? 'ready' : 'setup'}`}>
                  {boxNetworkStatus?.network_ready ? 'Verbunden' : 'Einrichten'}
                </span>
              </div>
              <div className="sc-network-facts">
                <span>Verbindung: {networkLabel(boxNetworkStatus)}</span>
                <span>Lokales Netzwerk: {boxNetworkStatus?.network_ready ? 'Verbunden' : 'Nicht verbunden'}</span>
                <span>Internet: {boxNetworkStatus?.internet_reachable ? 'Verbunden' : 'Nicht erreichbar'}</span>
                <span>Setup-WLAN: {boxNetworkStatus?.setup_ap_active ? 'Aktiv' : 'Aus'}</span>
              </div>
              {boxNetworkStatus?.network_ready && !boxNetworkStatus?.internet_reachable && (
                <p className="sc-network-note">Die Box ist im lokalen Netzwerk erreichbar und arbeitet lokal weiter. Internetdienste und Benachrichtigungen stehen wieder zur Verfügung, sobald die Internetverbindung zurückkehrt.</p>
              )}
              {!boxNetworkStatus?.network_ready && (
                <p className="sc-network-note">Keine lokale Netzwerkverbindung. Über „Netzwerk neu einrichten“ kann das Sentero-Setup-WLAN gestartet werden.</p>
              )}
              {boxNetworkStatus?.ethernet_active && (
                <p className="sc-network-note">LAN ist aktiv. Eine WLAN-Konfiguration ist optional und kann als Fallback gespeichert werden.</p>
              )}
              <div className="sc-form-grid">
                <label>
                  WLAN-Name
                  <input value={boxNetworkForm.ssid} onChange={(event) => setBoxNetworkForm((value) => ({ ...value, ssid: event.target.value }))} placeholder="Mein WLAN" />
                </label>
                <label>
                  WLAN-Passwort
                  <input type="password" value={boxNetworkForm.password} onChange={(event) => setBoxNetworkForm((value) => ({ ...value, password: event.target.value }))} placeholder={boxNetworkStatus?.wifi_configured ? 'Gespeichert' : 'Passwort'} />
                </label>
              </div>

              <footer className="sc-account-actions">
                <button className="sc-soft-action primary" type="button" onClick={() => void saveBoxNetwork()}><Save size={18} /> Verbinden</button>
                {!boxNetworkStatus?.network_ready && (
                  <button className="sc-soft-action" type="button" onClick={() => void startNetworkRecovery()}><Wifi size={18} /> Setup-WLAN starten</button>
                )}
              </footer>
            </div>
          </div>
        </section>
      )}

      {activeTab === 'contacts' && (
        <section className="sc-panel sc-settings-panel sc-contacts-panel">
          <div className="sc-section-title sc-contacts-title">
            <div>
              <h2>Vertraute Personen</h2>
              <p>Menschen, die bei Auffälligkeiten informiert werden.</p>
            </div>
            <button className="sc-round-add" type="button" onClick={() => setContactFormOpen(true)} aria-label="Person hinzufügen"><Plus size={28} /></button>
          </div>
          {contactFormOpen && (
            <div className="sc-contact-form-card">
              <div className="sc-contact-form-head">
                <strong>Person hinzufügen</strong>
                <button type="button" onClick={() => setContactFormOpen(false)} aria-label="Formular schließen"><X size={20} /></button>
              </div>
              <div className="sc-form-grid">
                <label>Name<input value={contactForm.name} onChange={(event) => setContactForm((value) => ({ ...value, name: event.target.value }))} /></label>
                <label>Beziehung<input value={contactForm.relationship} onChange={(event) => setContactForm((value) => ({ ...value, relationship: event.target.value }))} /></label>
                <label>AAL-Rolle<select value={contactForm.actor_role} onChange={(event) => setContactForm((value) => ({ ...value, actor_role: actorRoleForContact(event.target.value) }))}>{aalActorRoles.map((role) => <option key={role.value} value={role.value}>{role.label}</option>)}</select></label>
                {channelSelected(contactForm.preferred_channels, 'email', availableChannels) && <label>E-Mail<input type="email" value={contactForm.email} onChange={(event) => setContactForm((value) => ({ ...value, email: event.target.value }))} /></label>}
                {channelSelected(contactForm.preferred_channels, 'telegram', availableChannels) && <label>Telegram Chat ID<input value={contactForm.telegram_chat_id} onChange={(event) => setContactForm((value) => ({ ...value, telegram_chat_id: event.target.value }))} placeholder="Wird per Einladung automatisch gesetzt" /></label>}
                {channelSelected(contactForm.preferred_channels, 'whatsapp', availableChannels) && <label>WhatsApp Telefonnummer<input value={contactForm.whatsapp_phone_number} onChange={(event) => setContactForm((value) => ({ ...value, whatsapp_phone_number: event.target.value, phone: event.target.value }))} /></label>}
              </div>
              <ChannelChecks value={contactForm.preferred_channels} available={availableChannels} onChange={(preferred_channels) => setContactForm((value) => ({ ...value, preferred_channels }))} />
              <button className="sc-primary-button" type="button" onClick={() => void addContact()}><Save size={20} /> Speichern</button>
            </div>
          )}
          <div className="sc-settings-contact-grid">
            {(status?.trusted_contacts || []).map((contact) => (
              <article key={contact.id}>
                {editingContactId === contact.id ? (
                  <>
                    <div className="sc-contact-edit-grid">
                      <label>Name<input value={editContactForm.name} onChange={(event) => setEditContactForm((value) => ({ ...value, name: event.target.value }))} /></label>
                      <label>Beziehung<input value={editContactForm.relationship} onChange={(event) => setEditContactForm((value) => ({ ...value, relationship: event.target.value }))} /></label>
                      <label>AAL-Rolle<select value={editContactForm.actor_role} onChange={(event) => setEditContactForm((value) => ({ ...value, actor_role: actorRoleForContact(event.target.value) }))}>{aalActorRoles.map((role) => <option key={role.value} value={role.value}>{role.label}</option>)}</select></label>
                      {channelSelected(editContactForm.preferred_channels, 'email', availableChannels) && <label>E-Mail<input type="email" value={editContactForm.email} onChange={(event) => setEditContactForm((value) => ({ ...value, email: event.target.value }))} /></label>}
                      {channelSelected(editContactForm.preferred_channels, 'telegram', availableChannels) && <label>Telegram Chat ID<input value={editContactForm.telegram_chat_id} onChange={(event) => setEditContactForm((value) => ({ ...value, telegram_chat_id: event.target.value }))} placeholder="Wird per Einladung automatisch gesetzt" /></label>}
                      {channelSelected(editContactForm.preferred_channels, 'whatsapp', availableChannels) && <label>WhatsApp Telefonnummer<input value={editContactForm.whatsapp_phone_number} onChange={(event) => setEditContactForm((value) => ({ ...value, whatsapp_phone_number: event.target.value, phone: event.target.value }))} /></label>}
                    </div>
                    <ChannelChecks value={editContactForm.preferred_channels} available={availableChannels} onChange={(preferred_channels) => setEditContactForm((value) => ({ ...value, preferred_channels }))} />
                    <ContactQueryCard
                      contact={{
                        ...contact,
                        email: editContactForm.email,
                        preferred_channels: editContactForm.preferred_channels,
                        telegram_chat_id: editContactForm.telegram_chat_id,
                      }}
                      query={emailQueries?.contacts.find((item) => item.id === contact.id) || null}
                      mailEnabled={Boolean(emailQueries?.enabled)}
                      editable
                      onToggle={(enabled) => void toggleEmailQueries(contact.id, enabled)}
                      onPermission={(permission, checked) => void updateEmailQueryPermission(contact.id, permission, checked)}
                    />
                    <footer>
                      <button type="button" onClick={() => void saveEditedContact()}><Save size={18} /> Speichern</button>
                      <button type="button" onClick={() => setEditingContactId(null)}><X size={18} /> Abbrechen</button>
                    </footer>
                  </>
                ) : (
                  <>
                    <header className="sc-contact-card-head">
                      <span className="sc-avatar">{contact.name[0]}</span>
                      <div className="sc-contact-card-identity">
                        <div>
                          <h3>
                            {contact.name}
                            <span className="relationship"> · {contact.relationship || 'Kontakt'}</span>
                          </h3>
                        </div>
                        <small>{contact.email || 'Keine E-Mail hinterlegt'}</small>
                        <small className="sc-contact-role">{aalRoleLabel(contact.actor_role)}</small>
                      </div>
                    </header>
                    {normalizeChannels(contact.preferred_channels).includes('telegram') && (
                      <TelegramPairingCard
                        contact={contact}
                        bot={telegramBot}
                        onCopied={(message) => toast(message)}
                        onError={(message) => setError(message)}
                      />
                    )}
                    <ContactDataSharingControl
                      behaviorConsent={activeBehaviorConsent(contact.id, consents)}
                      revokedBehaviorConsent={latestBehaviorConsent(contact.id, consents)}
                      exportConsent={activeExportConsent(contact.id, consents)}
                      revokedExportConsent={latestExportConsent(contact.id, consents)}
                      token={activeExportToken(contact.id, exportTokens)}
                      latestToken={latestExportToken(contact.id, exportTokens)}
                      newToken={newExportToken?.contactId === contact.id ? newExportToken.token : null}
                      onGrantBehavior={() => void grantContactConsent(contact.id)}
                      onRevokeBehavior={(consentId) => void revokeContactConsent(consentId)}
                      onGrantExport={() => void grantExportConsent(contact.id)}
                      onRevokeExportConsent={(consentId) => void revokeExportConsent(consentId)}
                      onCreateToken={() => void createExportToken(contact.id)}
                      onRevokeToken={(tokenId) => void revokeExportToken(tokenId)}
                      onOpenPackage={() => setExportDialogContactId(contact.id)}
                      queryControl={(
                        <ContactQueryCard
                          contact={contact}
                          query={emailQueries?.contacts.find((item) => item.id === contact.id) || null}
                          mailEnabled={Boolean(emailQueries?.enabled)}
                          onToggle={(enabled) => void toggleEmailQueries(contact.id, enabled)}
                        />
                      )}
                    />
                    <footer>
                      <button type="button" onClick={() => startEditContact(contact)}><Pencil size={18} /> </button>
                      <button type="button" onClick={() => void deleteContact(contact.id)}><Trash2 size={18} /> </button>
                    </footer>
                  </>
                )}
              </article>
            ))}
          </div>
        </section>
      )}

      {activeTab === 'notifications' && (
        <section className="sc-panel sc-settings-panel sc-notification-settings">
          <div className="sc-settings-hero">
             <div className="sc-section-title">
            <h2>Benachrichtigungen</h2>
             </div>
            <p>Legen Sie fest, wann und wie Vertrauenspersonen informiert werden.</p>
          </div>

          <section className="sc-preference-section">
            <div className="sc-preference-list">
              <NotificationPreference
                title="Bei ungewöhnlichem Tagesablauf informieren"
                description="Wenn der Tagesablauf anders wirkt als sonst."
                checked={notifications.anomalies}
                onChange={(checked) => updateNotificationPreference('anomalies', checked)}
              />
              <NotificationPreference
                title="Wichtige Warnungen sofort senden"
                description="Wenn Sentero eine deutliche Auffälligkeit erkennt."
                checked={notifications.critical}
                onChange={(checked) => updateNotificationPreference('critical', checked)}
              />
              <NotificationPreference
                title="Tägliche Zusammenfassung erhalten"
                description="Ein kurzer Überblick über den Tag."
                checked={notifications.daily_summary}
                onChange={(checked) => updateNotificationPreference('daily_summary', checked)}
              />
            </div>
          </section>

          <section className="sc-channel-overview-section">
            <div className="sc-section-heading">
              <h3>Benachrichtigungskanäle</h3>
            </div>
            <div className="sc-channel-overview-grid">
              <NotificationChannelOverviewCard channel="email" channels={channels} onOpen={() => setSetupChannel('email')} onHelp={() => setHelpChannel('email')} />
              <NotificationChannelOverviewCard channel="telegram" channels={channels} optional onOpen={() => setSetupChannel('telegram')} onHelp={() => setHelpChannel('telegram')} />
              <NotificationChannelOverviewCard channel="whatsapp" channels={channels} optional onOpen={() => setSetupChannel('whatsapp')} onHelp={() => setHelpChannel('whatsapp')} />
            </div>
          </section>

          {setupChannel && (
            <ChannelSetupModal
              channel={setupChannel}
              form={channelForms[setupChannel]}
              recipient={primaryNotificationRecipient(status?.trusted_contacts || [])}
              onClose={() => setSetupChannel(null)}
              onFormChange={(form) => {
                if (setupChannel === 'email') {
                  setMailVerification({ busy: false, ok: false, message: '' });
                }
                setChannelForms((value) => ({ ...value, [setupChannel]: form as never }));
              }}
              discoverStatus={mailDiscovery.status}
              discoverMessage={setupChannel === 'email' ? mailDiscovery.message : ''}
              verificationBusy={mailVerification.busy}
              verificationOk={mailVerification.ok}
              verificationMessage={setupChannel === 'email' ? mailVerification.message : ''}
              advancedOpen={emailAdvancedOpen}
              onAdvancedToggle={() => setEmailAdvancedOpen((value) => !value)}
              onVerify={setupChannel === 'email' ? (values) => void verifyEmailSettings(values) : undefined}
              onSave={() => void saveChannel(setupChannel)}
              onTest={() => void testChannel(setupChannel)}
            />
          )}
          {helpChannel && <ChannelHelpModal channel={helpChannel} onClose={() => setHelpChannel(null)} />}
        </section>
      )}

      {activeTab === 'transparency' && (
        <section className="sc-panel sc-settings-panel sc-transparency-panel">
          <div className="sc-settings-hero">
            <div className="sc-section-title">
              <h2>Transparenz</h2>
            </div>
            <p>Sehen Sie, wann Sentero Daten genutzt, geteilt oder Freigaben geändert hat.</p>
          </div>

          <section className="sc-transparency-summary" aria-label="Transparenzübersicht">
            <TransparencyMetric label="Einträge" value={String(transparency?.summary.total || 0)} />
            <TransparencyMetric label="Exporte" value={String(transparency?.summary.exports || 0)} />
            <TransparencyMetric label="Benachrichtigungen" value={String(transparency?.summary.notifications || 0)} />
            <TransparencyMetric label="Anfragen" value={String(transparency?.summary.mail_queries || 0)} />
            <TransparencyMetric label="Freigaben" value={String(transparency?.summary.consents || 0)} />
          </section>

          <section className="sc-transparency-retention">
            <div>
              <strong>Aufbewahrung</strong>
              <small>{transparency?.retention.retention_days || 180} Tage fuer Audit-, Export- und Benachrichtigungslogs</small>
            </div>
            <button type="button" onClick={() => void cleanupTransparency()}><Trash2 size={18} /> Alte Daten löschen</button>
          </section>

          <section className="sc-transparency-list" aria-label="Datenverwendung">
            {(transparency?.items || []).map((item) => (
              <article className={`sc-transparency-item ${item.category}`} key={item.id}>
                <span aria-hidden="true">{transparencyIcon(item.category)}</span>
                <div>
                  <header>
                    <strong>{item.summary}</strong>
                    <time>{formatDateTime(item.created_at)}</time>
                  </header>
                  <p>{transparencyDetail(item)}</p>
                  <small>{item.data_classes.map(dataClassLabel).join(', ') || 'Metadaten'} · {item.aggregation_level || 'summary'} · {item.raw_data_included ? 'Rohdaten' : 'keine Rohdaten'}</small>
                </div>
              </article>
            ))}
            {!transparency?.items.length && (
              <div className="sc-history-empty">
                <ShieldCheck size={24} />
                <strong>Noch keine Transparenzeinträge</strong>
                <p>Neue Exporte, Benachrichtigungen und Freigaben erscheinen hier automatisch.</p>
              </div>
            )}
          </section>
        </section>
      )}

      {activeTab === 'account' && (
        <section className="sc-panel sc-settings-panel sc-account-panel">
          <div className="sc-settings-hero">
             <div className="sc-section-title">
            <h2>Konto & Zugriff</h2>
             </div>
            <p>Verwalten Sie Ihr Sentero-Konto und Ihre Sicherheit.</p>
          </div>

          <div className="sc-account-grid">
            <article className="sc-account-card">
              <header>
                <span><UserRound size={22} /></span>
                <div>
                  <h3>Mein Konto</h3>
                  <p>Persönliche Zugangsdaten</p>
                </div>
              </header>
              {!accountEditing ? (
                <>
                  <div className="sc-account-details">
                    <p><span>Name</span><strong>{user?.display_name || accountForm.display_name || 'Nicht hinterlegt'}</strong></p>
                    <p><span>E-Mail-Adresse</span><strong>{user?.email || accountForm.email || 'Nicht hinterlegt'}</strong></p>
                    <p><span>Technische Rolle</span><strong>{user?.role === 'owner' ? 'Inhaber-Konto' : user?.role === 'admin' ? 'Admin-Konto' : 'Ansichtskonto'}</strong></p>
                    <p><span>AAL-Rolle</span><strong>{aalRoleLabel(user?.aal_role || 'admin')}</strong></p>
                  </div>
                  <button className="sc-soft-action" type="button" onClick={() => setAccountEditing(true)}><Pencil size={18} /> Konto bearbeiten</button>
                </>
              ) : (
                <>
                  <div className="sc-form-grid">
                    <label>Name<input value={accountForm.display_name} onChange={(event) => setAccountForm((value) => ({ ...value, display_name: event.target.value }))} /></label>
                    <label>E-Mail-Adresse<input type="email" value={accountForm.email} onChange={(event) => setAccountForm((value) => ({ ...value, email: event.target.value }))} /></label>
                  </div>
                  <footer className="sc-account-actions">
                    <button className="sc-soft-action" type="button" onClick={() => { setAccountEditing(false); setAccountForm({ display_name: user?.display_name || '', email: user?.email || '' }); }}>Abbrechen</button>
                    <button className="sc-soft-action primary" type="button" onClick={() => void saveAccount()}><Save size={18} /> Speichern</button>
                  </footer>
                </>
              )}
            </article>

            <article className="sc-account-card">
              <header>
                <span><KeyRound size={22} /></span>
                <div>
                  <h3>Sicherheit</h3>
                  <p>Schützen Sie den Zugang zu Sentero.</p>
                </div>
              </header>
              <div className="sc-account-details">
                <p><span>Passwort</span><strong>Gespeichert</strong></p>
                <p><span>Zuletzt geändert</span><strong>Noch nicht verfügbar</strong></p>
              </div>
              <button className="sc-soft-action" type="button" onClick={() => setPasswordModalOpen(true)}><KeyRound size={18} /> Passwort ändern</button>
            </article>
          </div>

          {passwordModalOpen && (
            <div className="sc-modal-backdrop" role="presentation" onMouseDown={() => setPasswordModalOpen(false)}>
              <section className="sc-channel-modal sc-password-modal" role="dialog" aria-modal="true" aria-label="Passwort ändern" onMouseDown={(event) => event.stopPropagation()}>
                <header>
                  <span><KeyRound size={22} /></span>
                  <div>
                    <h3>Passwort ändern</h3>
                    <p>Nutzen Sie ein sicheres Passwort mit mindestens 8 Zeichen.</p>
                  </div>
                  <button type="button" onClick={() => setPasswordModalOpen(false)} aria-label="Dialog schließen"><X size={20} /></button>
                </header>
                <div className="sc-form-grid">
                  <label>Aktuelles Passwort<input type="password" value={passwordForm.current_password} onChange={(event) => setPasswordForm((value) => ({ ...value, current_password: event.target.value }))} autoComplete="current-password" /></label>
                  <label>Neues Passwort<input type="password" value={passwordForm.new_password} onChange={(event) => setPasswordForm((value) => ({ ...value, new_password: event.target.value }))} autoComplete="new-password" /></label>
                  <label className="sc-form-wide">Neues Passwort bestätigen<input type="password" value={passwordForm.new_password_confirm} onChange={(event) => setPasswordForm((value) => ({ ...value, new_password_confirm: event.target.value }))} autoComplete="new-password" /></label>
                </div>
                <footer>
                  <button type="button" onClick={() => setPasswordModalOpen(false)}>Abbrechen</button>
                  <button type="button" onClick={() => void savePassword()}><KeyRound size={18} /> Passwort ändern</button>
                </footer>
              </section>
            </div>
          )}
        </section>
      )}

      {activeTab === 'system' && (
        <section className="sc-panel sc-settings-panel">
          <div className="sc-section-title sc-system-heading">
            <div>
              <h2>System</h2>
              <p>Systemverwaltung und Status Ihrer Sentero Box.</p>
            </div>
          </div>

          <section className={`sc-system-health sc-system-health-${systemStatus?.overall || 'warning'} ${systemHealthOpen ? 'open' : ''}`}>
            <button
              className="sc-system-health-toggle"
              type="button"
              aria-expanded={systemHealthOpen}
              aria-controls="sentero-system-health-details"
              onClick={() => setSystemHealthOpen((value) => !value)}
            >
              <div className="sc-system-health-title">
                <span className={`sc-system-health-icon sc-system-health-icon-${systemStatus?.overall || 'warning'}`} aria-hidden="true">
                  <CheckCircle2 size={22} />
                </span>
                <div>
                  <strong>Systemzustand</strong>
                  <span>{systemHealthSummary(systemStatus, systemStatusLoading)}</span>
                </div>
              </div>
              <div className="sc-system-health-toggle-meta">
                <span className={`sc-system-health-pill sc-system-health-pill-${systemStatus?.overall || 'warning'}`}>
                  {systemStatus?.summary || (systemStatusLoading ? 'Wird geprüft …' : 'Status laden …')}
                </span>
                <ChevronDown size={20} aria-hidden="true" />
              </div>
            </button>

            {systemHealthOpen && (
              <div className="sc-system-health-details" id="sentero-system-health-details">
                <div className="sc-service-status-grid" aria-live="polite">
                  {(systemStatus?.services || []).map((service) => (
                    <article className="sc-service-status-card" key={service.key}>
                      <div className="sc-service-status-main">
                        <span className={`sc-service-dot sc-service-dot-${service.state}`} aria-hidden="true" />
                        <div>
                          <strong>{service.label}</strong>
                          <span>{service.detail || systemServiceLabel(service.state)}</span>
                        </div>
                      </div>
                      <span className={`sc-service-state sc-service-state-${service.state}`}>{systemServiceLabel(service.state)}</span>
                    </article>
                  ))}
                  {!systemStatus?.services?.length && (
                    <div className="sc-system-status-placeholder">Systemstatus wird geladen …</div>
                  )}
                </div>

                <div className="sc-system-soft-facts">
                  <p><span>Sensoren</span><strong>{sensors.filter((sensor) => sensor.configured).length} eingerichtet</strong></p>
                  <p><span>Erreichbarkeit</span><strong>{sensors.filter((sensor) => sensor.reachable === false).length ? `${sensors.filter((sensor) => sensor.reachable === false).length} nicht erreichbar` : 'Alles erreichbar'}</strong></p>
                  <p><span>Letzte Prüfung</span><strong>{formatDateTime(systemStatus?.checked_at || status?.updated_at)}</strong></p>
                </div>

                <div className="sc-system-query-note">
                  <div>
                    <Mail size={18} />
                    <MessageCircle size={18} />
                  </div>
                  <p><strong>Status auch unterwegs abrufen</strong><span>Per E-Mail „Systemstatus“ oder in Telegram <code>/status</code> senden.</span></p>
                </div>
              </div>
            )}
          </section>

          <UpdatePanel />
          <div className="sc-danger-zone">
            <h3><ShieldAlert size={22} /> Werkseinstellungen</h3>
            <p>Zum Zurücksetzen bitte ZURÜCKSETZEN eingeben.</p>
            <input value={resetText} onChange={(event) => setResetText(event.target.value)} placeholder="ZURÜCKSETZEN" />
            <button type="button" disabled={resetText !== 'ZURÜCKSETZEN'} onClick={() => window.confirm('Alle Sentero-Daten löschen?')}>Factory Reset</button>
          </div>
        </section>
      )}

      {exportDialogToken && (
        <PartnerExportDialog
          contactName={exportDialogContact?.name || 'Partner'}
          token={exportDialogToken}
          onClose={() => setExportDialogContactId(null)}
          onCopy={(value) => void copyExportToken(value)}
        />
      )}
      </div>
    </section>
  );
}

function systemHealthSummary(systemStatus: SenteroSystemStatus | null, loading: boolean) {
  const services = systemStatus?.services || [];
  if (!services.length) return loading ? 'Dienste werden geprüft …' : 'Status wird geladen …';
  const ready = services.filter((service) => service.state === 'ok').length;
  if (systemStatus?.overall === 'ok') return `${ready} von ${services.length} Diensten bereit`;
  const issues = services.length - ready;
  return issues === 1 ? '1 Bereich benötigt Aufmerksamkeit' : `${issues} Bereiche benötigen Aufmerksamkeit`;
}

function systemServiceLabel(state?: string) {
  if (state === 'ok') return 'Bereit';
  if (state === 'error') return 'Prüfen';
  if (state === 'inactive') return 'Nicht aktiv';
  return 'Hinweis';
}

function EmptyState({ text, action }: { text: string; action: string }) {
  return (
    <div className="sc-empty-state">
      <p>{text}</p>
      <button type="button" onClick={() => window.location.assign('/sentero/setup')}>{action}</button>
    </div>
  );
}

function ContactDataSharingControl({
  behaviorConsent,
  revokedBehaviorConsent,
  exportConsent,
  revokedExportConsent,
  token,
  latestToken,
  newToken,
  onGrantBehavior,
  onRevokeBehavior,
  onGrantExport,
  onRevokeExportConsent,
  onCreateToken,
  onRevokeToken,
  onOpenPackage,
  queryControl,
}: {
  behaviorConsent?: SenteroConsent | null;
  revokedBehaviorConsent?: SenteroConsent | null;
  exportConsent?: SenteroConsent | null;
  revokedExportConsent?: SenteroConsent | null;
  token?: SenteroExportToken | null;
  latestToken?: SenteroExportToken | null;
  newToken?: string | null;
  onGrantBehavior: () => void;
  onRevokeBehavior: (consentId: number) => void;
  onGrantExport: () => void;
  onRevokeExportConsent: (consentId: number) => void;
  onCreateToken: () => void;
  onRevokeToken: (tokenId: number) => void;
  onOpenPackage: () => void;
  queryControl?: React.ReactNode;
}) {
  const currentBehavior = behaviorConsent || revokedBehaviorConsent || null;
  const currentExport = exportConsent || revokedExportConsent || null;
  const currentToken = token || latestToken || null;
  const activeBehavior = Boolean(behaviorConsent);
  const activeExport = Boolean(exportConsent);
  const activeToken = Boolean(token);
  return (
    <section className="sc-contact-sharing" aria-label="Datenfreigaben">
      <header>
        <span><ShieldCheck size={18} /></span>
        <strong>Datenfreigaben</strong>
      </header>
      <SharingRow
        active={activeBehavior}
        icon={activeBehavior ? <ShieldCheck size={16} /> : <ShieldAlert size={16} />}
        title="Meldungen"
        detail={currentBehavior ? consentDescription(currentBehavior) : 'Verhaltensmeldungen gesperrt'}
        action={activeBehavior && behaviorConsent
          ? <button type="button" onClick={() => onRevokeBehavior(behaviorConsent.id)}>Widerrufen</button>
          : <button type="button" onClick={onGrantBehavior}>Freigeben</button>}
      />
      <SharingRow
        active={activeExport}
        icon={activeToken ? <KeyRound size={16} /> : activeExport ? <ShieldCheck size={16} /> : <ShieldAlert size={16} />}
        title="Partnerexport"
        detail={exportDetail(currentExport, currentToken, newToken)}
        action={(
          <div className="sc-sharing-actions">
            {!activeExport && <button type="button" onClick={onGrantExport}>Freigeben</button>}
            {activeExport && !activeToken && <button type="button" onClick={onCreateToken}>Token</button>}
            {newToken && <button type="button" onClick={onOpenPackage}>Paket</button>}
            {activeExport && exportConsent && <button type="button" onClick={() => onRevokeExportConsent(exportConsent.id)}>Widerrufen</button>}
            {activeToken && token && <button type="button" onClick={() => onRevokeToken(token.id)}>Token widerrufen</button>}
          </div>
        )}
      />
      {queryControl}
    </section>
  );
}

function SharingRow({ active, icon, title, detail, action }: { active: boolean; icon: React.ReactNode; title: string; detail: string; action: React.ReactNode }) {
  return (
    <div className={`sc-sharing-row ${active ? 'active' : 'inactive'}`}>
      <span>{icon}</span>
      <div>
        <strong>{title}</strong>
        <small>{detail}</small>
      </div>
      {action}
    </div>
  );
}

function exportDetail(consent?: SenteroConsent | null, token?: SenteroExportToken | null, newToken?: string | null) {
  if (newToken) return 'Partnerpaket bereit. Token nur in dieser Sitzung sichtbar.';
  if (token?.revoked_at) return `Token widerrufen am ${formatDateTime(token.revoked_at)}`;
  if (token?.active) return `Token aktiv bis ${formatDateTime(token.expires_at)}`;
  if (consent) return consentDescription(consent);
  return 'Kein Partnerexport freigegeben';
}

function PartnerExportDialog({
  contactName,
  token,
  onClose,
  onCopy,
}: {
  contactName: string;
  token: string;
  onClose: () => void;
  onCopy: (value: string) => void;
}) {
  const headerValue = `Authorization: Bearer ${token}`;
  return (
    <div className="sc-modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="sc-channel-modal sc-partner-export-modal" role="dialog" aria-modal="true" aria-label="Partnerzugang" onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <span><KeyRound size={22} /></span>
          <div>
            <h3>Partnerzugang</h3>
            <p>{contactName} kann die freigegebenen Exporte mit diesem Token abrufen.</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Dialog schließen"><X size={20} /></button>
        </header>

        <div className="sc-partner-export-warning">
          <ShieldAlert size={18} />
          <span>Der Token wird nur einmal vollständig angezeigt. Danach bleiben nur Ablaufdatum und Widerruf sichtbar.</span>
        </div>

        <section className="sc-partner-export-section">
          <div className="sc-partner-export-row">
            <span>Token</span>
            <code>{token}</code>
            <button type="button" onClick={() => onCopy(token)}><Copy size={16} /> Kopieren</button>
          </div>
          <div className="sc-partner-export-row">
            <span>Header</span>
            <code>{headerValue}</code>
            <button type="button" onClick={() => onCopy(headerValue)}><Copy size={16} /> Kopieren</button>
          </div>
        </section>

        <section className="sc-partner-export-section">
          <h4>Export-Endpunkte</h4>
          {exportExchangeEndpoints().map((endpoint) => (
            <div className="sc-partner-export-row" key={endpoint.path}>
              <span>{endpoint.label}</span>
              <code>{endpoint.url}</code>
              <button type="button" onClick={() => onCopy(`${endpoint.url}?token=${encodeURIComponent(token)}`)}><Copy size={16} /> Direktlink</button>
            </div>
          ))}
        </section>

        <footer>
          <button type="button" onClick={onClose}>Schließen</button>
        </footer>
      </section>
    </div>
  );
}

function TransparencyMetric({ label, value }: { label: string; value: string }) {
  return (
    <article>
      <small>{label}</small>
      <strong>{value}</strong>
    </article>
  );
}

function transparencyIcon(category: string) {
  if (category === 'export') return <KeyRound size={18} />;
  if (category === 'notification') return <Bell size={18} />;
  if (category === 'mail_query') return <Mail size={18} />;
  if (category === 'consent') return <ShieldCheck size={18} />;
  if (category === 'metadata') return <ShieldAlert size={18} />;
  return <ShieldAlert size={18} />;
}

function transparencyDetail(item: { contact_name?: string | null; actor_role?: string | null; purpose?: string | null; status?: string | null; category: string }) {
  const parts = [
    item.contact_name ? `Empfänger: ${item.contact_name}` : '',
    item.actor_role ? `Rolle: ${aalRoleLabel(item.actor_role)}` : '',
    item.purpose ? `Zweck: ${purposeLabel(item.purpose)}` : '',
    item.status ? `Status: ${statusLabel(item.status)}` : '',
  ].filter(Boolean);
  return parts.join(' · ') || categoryLabel(item.category);
}

function purposeLabel(value: string) {
  if (value === 'behavior_notification') return 'Verhaltensmeldung';
  if (value === 'mail_status_query') return 'E-Mail-Statusfrage';
  if (value === 'mail_auto_ignored') return 'Automatische E-Mail';
  if (value === 'aal_partner_export') return 'Partnerexport';
  return value;
}

function statusLabel(value: string) {
  if (value === 'sent') return 'gesendet';
  if (value === 'active') return 'aktiv';
  if (value === 'revoked') return 'widerrufen';
  if (value === 'completed') return 'abgeschlossen';
  if (value === 'failed') return 'fehlgeschlagen';
  if (value === 'rejected') return 'abgelehnt';
  if (value === 'ignored') return 'ignoriert';
  if (value === 'duplicate') return 'doppelt';
  if (value.startsWith('skipped')) return 'blockiert';
  return value;
}

function categoryLabel(value: string) {
  if (value === 'export') return 'Export';
  if (value === 'notification') return 'Benachrichtigung';
  if (value === 'mail_query') return 'E-Mail-Anfrage';
  if (value === 'metadata') return 'Metadaten';
  if (value === 'consent') return 'Freigabe';
  if (value === 'security') return 'Sicherheit';
  return value;
}

function NotificationPreference({ title, description, checked, onChange }: { title: string; description: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className={`sc-notification-preference${checked ? ' active' : ''}`}>
      <span>
        <strong>{title}</strong>
        <small>{description}</small>
      </span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <i aria-hidden="true" />
    </label>
  );
}

function NotificationChannelOverviewCard({
  channel,
  channels,
  optional = false,
  onOpen,
  onHelp,
}: {
  channel: 'email' | 'telegram' | 'whatsapp';
  channels: SenteroNotificationChannel[];
  optional?: boolean;
  onOpen: () => void;
  onHelp: () => void;
}) {
  const item = channels.find((entry) => entry.channel === channel);
  const state = channelState(channels, channel);
  const configured = Boolean(item?.configured);
  return (
    <article className="sc-channel-overview-card" onClick={onOpen}>
      <header>
        <span>{channelIcon(channel, 24)}</span>
        <div>
          <strong>{channelLabel(channel)}</strong>
          <small>{state}</small>
        </div>
        <button className="sc-help-icon" type="button" onClick={(event) => { event.stopPropagation(); onHelp(); }} aria-label={`${channelLabel(channel)} Hilfe öffnen`}><Lightbulb size={17} /></button>
        <em>{optional ? "Optional" : "Pflicht"}</em>
      </header>
    </article>
  );
}

function ChannelHelpModal({ channel, onClose }: { channel: 'email' | 'telegram' | 'whatsapp'; onClose: () => void }) {
  const help = channelHelpContent(channel);
  return (
    <div className="sc-modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="sc-channel-modal sc-help-modal" role="dialog" aria-modal="true" aria-label={help.title} onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <span>{channelIcon(channel, 24)}</span>
          <div>
            <h3>{help.title}</h3>
            <p>{help.intro}</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Dialog schließen"><X size={20} /></button>
        </header>
        <div className="sc-help-content">
          {help.sections.map((section) => (
            <section key={section.title}>
              <h4>{section.title}</h4>
              {section.text?.map((item) => <p key={item}>{item}</p>)}
              {section.items && <ul>{section.items.map((item) => <li key={item}>{item}</li>)}</ul>}
              {section.steps && <ol>{section.steps.map((item) => <li key={item}>{item}</li>)}</ol>}
            </section>
          ))}
        </div>
        <footer>
          <button type="button" onClick={onClose}>Verstanden</button>
        </footer>
      </section>
    </div>
  );
}

function ChannelSetupModal({
  channel,
  form,
  recipient,
  onClose,
  onFormChange,
  discoverStatus = 'idle',
  discoverMessage = '',
  verificationBusy = false,
  verificationOk = false,
  verificationMessage = '',
  advancedOpen = false,
  onAdvancedToggle,
  onVerify,
  onSave,
  onTest,
}: {
  channel: 'email' | 'telegram' | 'whatsapp';
  form: Record<string, string>;
  recipient?: { name: string; email: string; relationship?: string; primary: boolean } | null;
  onClose: () => void;
  onFormChange: (form: Record<string, string>) => void;
  discoverStatus?: 'idle' | 'checking' | 'found' | 'failed';
  discoverMessage?: string;
  verificationBusy?: boolean;
  verificationOk?: boolean;
  verificationMessage?: string;
  advancedOpen?: boolean;
  onAdvancedToggle?: () => void;
  onVerify?: (values?: { email?: string; password?: string }) => void;
  onSave: () => void;
  onTest: () => void;
}) {
  const meta = channelSetupMeta(channel);
  const appPasswordHelpUrl = form.app_password_help_url;
  const emailInputRef = useRef<HTMLInputElement | null>(null);
  const passwordInputRef = useRef<HTMLInputElement | null>(null);
  const visibleDiscoverMessage = channel === 'email' && discoverStatus === 'failed' && hasEmailServerSettings(form) ? '' : discoverMessage;
  if (channel === 'email') {
    return (
      <div className="sc-modal-backdrop" role="presentation" onMouseDown={onClose}>
        <section className="sc-channel-modal sc-mail-onboarding-modal" role="dialog" aria-modal="true" aria-label={meta.title} onMouseDown={(event) => event.stopPropagation()}>
          <header>
            <span>{channelIcon(channel, 24)}</span>
            <div>
              <h3>{meta.title}</h3>
              <p>{meta.text}</p>
            </div>
            <button type="button" onClick={onClose} aria-label="Dialog schließen"><X size={20} /></button>
          </header>

          <div className="sc-mail-simple-form">
            <label>
              E-Mail-Adresse
              <input
                ref={emailInputRef}
                type="email"
                value={form.smtp_user || ''}
                onInput={(event) => onFormChange(emailAddressForm(form, event.currentTarget.value))}
                onChange={(event) => onFormChange(emailAddressForm(form, event.target.value))}
                autoComplete="email"
              />
            </label>
            {visibleDiscoverMessage && (
              <p className={`sc-mail-status ${discoverStatus}`}>
                {discoverStatus === 'found' && <CheckCircle2 size={18} />}
                {visibleDiscoverMessage}
              </p>
            )}
            <label>
              Passwort / App-Passwort
              <input
                ref={passwordInputRef}
                type="password"
                value={form.smtp_password || ''}
                onInput={(event) => onFormChange({ ...form, smtp_password: event.currentTarget.value, imap_password: event.currentTarget.value })}
                onChange={(event) => onFormChange({ ...form, smtp_password: event.target.value, imap_password: event.target.value })}
                autoComplete="current-password"
                placeholder="Leer lassen, um das gespeicherte Passwort zu verwenden"
              />
            </label>
            {appPasswordHelpUrl && (
              <div className="sc-app-password-note">
                <small>{appPasswordProviderHint(form.smtp_user)}</small>
                <a href={appPasswordHelpUrl} target="_blank" rel="noreferrer">So erstellen Sie ein App-Passwort</a>
              </div>
            )}
            {verificationMessage && (
              <p className={`sc-mail-status ${verificationOk ? 'found' : 'failed'}`}>
                {verificationOk && <CheckCircle2 size={18} />}
                {verificationMessage}
              </p>
            )}
          </div>

          <section className="sc-advanced-mail-settings">
            <button type="button" onClick={onAdvancedToggle} aria-expanded={advancedOpen}>
              {discoverStatus === 'failed' && !advancedOpen ? 'Erweiterte Einstellungen öffnen' : 'Erweiterte Einstellungen'}
            </button>
            {advancedOpen && (
              <div className="sc-form-grid">
                {meta.fields.map(({ key, label, hint }) => (
                  <label key={key} className={key === 'mail_from' ? 'sc-form-wide' : undefined}>
                    {label}
                    {key.includes('encryption') ? (
                      <select value={form[key] || ''} onChange={(event) => onFormChange({ ...form, [key]: event.target.value })}>
                        <option value="">Automatisch</option>
                        <option value="SSL">SSL</option>
                        <option value="STARTTLS">STARTTLS</option>
                        <option value="NONE">Keine</option>
                      </select>
                    ) : (
                      <input
                        type="text"
                        value={form[key] || ''}
                        onChange={(event) => onFormChange({ ...form, [key]: event.target.value })}
                      />
                    )}
                    {hint && <small className="sc-field-hint">{hint}</small>}
                  </label>
                ))}
              </div>
            )}
          </section>

          <footer>
            <button
              type="button"
              onClick={() => onVerify?.({ email: emailInputRef.current?.value, password: passwordInputRef.current?.value })}
              disabled={verificationBusy || discoverStatus === 'checking'}
            >
              {verificationBusy ? 'Wird geprüft' : 'Verbindung prüfen'}
            </button>
            <button type="button" onClick={onTest} disabled={verificationBusy || discoverStatus === 'checking'}>
              <Send size={18} /> Testmail senden
            </button>
            <button type="button" onClick={onSave} disabled={!verificationOk}><Save size={18} /> Speichern</button>
          </footer>
        </section>
      </div>
    );
  }
  return (
    <div className="sc-modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section className="sc-channel-modal" role="dialog" aria-modal="true" aria-label={meta.title} onMouseDown={(event) => event.stopPropagation()}>
        <header>
          <span>{channelIcon(channel, 24)}</span>
          <div>
            <h3>{meta.title}</h3>
            <p>{meta.text}</p>
          </div>
          <button type="button" onClick={onClose} aria-label="Dialog schließen"><X size={20} /></button>
        </header>
        <div className="sc-form-grid">
          {meta.fields.map(({ key, label, hint }) => (
            <label key={key} className={key.includes('token') || key.includes('password') ? 'sc-form-wide' : undefined}>
              {label}
              <input
                type={key.includes('token') || key.includes('password') ? 'password' : 'text'}
                value={form[key] || ''}
                onChange={(event) => onFormChange({ ...form, [key]: event.target.value })}
              />
              {hint && <small className="sc-field-hint">{hint}</small>}
            </label>
          ))}
        </div>
        <footer>
          <button type="button" onClick={onTest}><Send size={18} /> Testen</button>
          <button type="button" onClick={onSave}><Save size={18} /> Speichern</button>
        </footer>
      </section>
    </div>
  );
}

function ChannelChecks({
  value,
  available,
  onChange,
}: {
  value: string[];
  available: Record<'email' | 'telegram' | 'whatsapp', boolean>;
  onChange: (value: string[]) => void;
}) {
  function toggle(channel: string, checked: boolean) {
    if (channel === 'email') return;
    if (!available[channel as 'email' | 'telegram' | 'whatsapp']) return;
    const next = checked ? [...value, channel] : value.filter((item) => item !== channel);
    onChange(sanitizeChannels(next, available));
  }
  const options = [
    { channel: 'email' as const, label: 'E-Mail', icon: <Mail size={20} /> },
    { channel: 'telegram' as const, label: 'Telegram', icon: <Send size={20} /> },
    { channel: 'whatsapp' as const, label: 'WhatsApp', icon: <MessageCircle size={20} /> },
  ];
  return (
    <div className="sc-channel-checks" aria-label="Benachrichtigungskanäle">
      <span>Benachrichtigung per</span>
      <div className="sc-channel-choice-row">
        {options.map((option) => {
          const selected = option.channel === 'email' || (value.includes(option.channel) && available[option.channel]);
          const disabled = option.channel === 'email' || !available[option.channel];
          return (
            <label key={option.channel} className={`sc-channel-choice${selected ? ' selected' : ''}${disabled ? ' disabled' : ''}`}>
              <input type="checkbox" checked={selected} disabled={disabled} onChange={(event) => toggle(option.channel, event.target.checked)} />
              <i>{option.icon}</i>
              <strong>{option.label}</strong>
            </label>
          );
        })}
      </div>
      <small>E-Mail bleibt als Pflichtkanal aktiv. Weitere Kanäle werden nach erfolgreichem Verbindungstest freigeschaltet.</small>
    </div>
  );
}

function TelegramPairingCard({
  contact,
  bot,
  onCopied,
  onError,
}: {
  contact: SenteroTrustedContact;
  bot: SenteroTelegramBotInfo | null;
  onCopied: (message: string) => void;
  onError: (message: string) => void;
}) {
  const inviteUrl = telegramInviteUrl(bot, contact);
  const linked = Boolean(contact.telegram_linked || contact.telegram_chat_id);
  const [qr, setQr] = useState('');

  useEffect(() => {
    let active = true;
    if (!inviteUrl) {
      setQr('');
      return;
    }
    QRCode.toDataURL(inviteUrl, { margin: 1, width: 164, color: { dark: '#16231f', light: '#ffffff' } })
      .then((value) => {
        if (active) setQr(value);
      })
      .catch(() => {
        if (active) setQr('');
      });
    return () => {
      active = false;
    };
  }, [inviteUrl]);

  async function copyInvite() {
    if (!inviteUrl) return;
    try {
      await navigator.clipboard.writeText(inviteUrl);
      onCopied('Telegram-Link kopiert');
    } catch {
      onError('Telegram-Link konnte nicht automatisch kopiert werden.');
    }
  }

  return (
    <section className={`sc-telegram-pairing${linked ? ' linked' : ''}`}>
      <header>
        <span><Send size={18} /></span>
        <div>
          <strong>{linked ? 'Telegram verbunden' : 'Telegram einladen'}</strong>
          <small>{bot?.username ? `@${bot.username}` : 'Bot noch nicht erkannt'}</small>
        </div>
      </header>
      {inviteUrl ? (
        <div className="sc-telegram-invite">
          {qr && <img src={qr} alt={`Telegram QR-Code für ${contact.name}`} />}
          <div>
            <p>{linked ? 'Dieser Chat ist gekoppelt. Der Link kann bei Gerätewechsel erneut genutzt werden.' : 'Link oder QR-Code an diese Person senden. Nach Start wird die Chat-ID automatisch gespeichert.'}</p>
            <button type="button" onClick={() => void copyInvite()}><Copy size={17} /> Link kopieren</button>
          </div>
        </div>
      ) : (
        <p>Telegram-Bot zuerst im Bereich Benachrichtigungen verbinden.</p>
      )}
    </section>
  );
}

function ContactQueryCard({
  contact,
  query,
  mailEnabled,
  editable = false,
  onToggle,
  onPermission,
}: {
  contact: SenteroTrustedContact;
  query: SenteroMailQuerySettings['contacts'][number] | null;
  mailEnabled: boolean;
  editable?: boolean;
  onToggle?: (enabled: boolean) => void;
  onPermission?: (permission: string, checked: boolean) => void;
}) {
  const channels = normalizeChannels(contact.preferred_channels);
  const hasEmail = Boolean(contact.email);
  const hasTelegram = channels.includes('telegram') || Boolean(contact.telegram_chat_id);
  if (!hasEmail && !hasTelegram) return null;
  const enabled = Boolean(query?.email_queries_enabled);
  const permissions = query?.email_permissions || [];
  const visiblePermissions = editable ? emailQueryPermissions : emailQueryPermissions.filter((permission) => permissions.includes(permission.value));
  const channelText = queryChannelText(hasEmail, hasTelegram, Boolean(contact.telegram_chat_id), mailEnabled);
  const summary = queryPermissionSummary(permissions);
  if (!editable) {
    return (
      <SharingRow
        active={enabled}
        icon={enabled ? <MessageCircle size={16} /> : <ShieldAlert size={16} />}
        title="Anfragen"
        detail={enabled ? `${summary.countLabel}: ${summary.names}` : `Aus · ${channelText}`}
        action={onToggle
          ? <button type="button" className={`sc-binary-toggle ${enabled ? 'active' : ''}`} onClick={() => onToggle(!enabled)} aria-pressed={enabled}>{enabled ? 'Ein' : 'Aus'}</button>
          : <span className={`sc-binary-status ${enabled ? 'active' : ''}`}>{enabled ? 'Ein' : 'Aus'}</span>}
      />
    );
  }
  return (
    <section className={`sc-contact-query-card sc-contact-query-card-edit${enabled ? ' active' : ''}`}>
      <header>
        <span><MessageCircle size={18} /></span>
          <div>
            <strong>Anfragen</strong>
            <small>{channelText}</small>
          </div>
        <button type="button" className={`sc-binary-toggle ${enabled ? 'active' : ''}`} onClick={() => onToggle?.(!enabled)} aria-pressed={enabled}>
          {enabled ? 'Ein' : 'Aus'}
        </button>
      </header>
      <div className="sc-email-permission-grid compact">
        {enabled && visiblePermissions.length === 0 && <span className="sc-query-empty">Keine Bereiche freigegeben</span>}
        {(enabled || editable) && visiblePermissions.map((permission) => (
          <label key={permission.value} className={permissions.includes(permission.value) ? 'active' : ''}>
            {editable && <input type="checkbox" checked={permissions.includes(permission.value)} disabled={!enabled} onChange={(event) => onPermission?.(permission.value, event.target.checked)} />}
            <span>{permission.label}</span>
          </label>
        ))}
      </div>
    </section>
  );
}

function DoorContactStatus({ sensor }: { sensor: SenteroSensorRole }) {
  const status = doorContactStatus(sensor);
  const Icon = status.open ? DoorOpen : DoorClosed;
  return (
    <div className={`sc-door-contact-status ${status.tone}`} aria-label={`Türkontakt ${status.label}`}>
      <Icon size={24} />
      <strong>{status.label}</strong>
    </div>
  );
}

function SmokeStatus({ sensor }: { sensor: SenteroSensorRole }) {
  const alarm = sensor.smoke === true || ['on', 'true', '1', 'alarm', 'detected'].includes(String(sensor.state || '').toLowerCase());
  return (
    <span className={`presence-status ${alarm ? 'alert' : 'away'}`} aria-label={alarm ? 'Rauch erkannt' : 'Kein Rauch erkannt'}>
      <ShieldAlert size={17} />
      {alarm ? 'Rauch erkannt' : 'Kein Rauch erkannt'}
    </span>
  );
}

function C1001Telemetry({ sensor }: { sensor: SenteroSensorRole }) {
  const status = presenceMotionStatus(sensor);
  return (
    <span className={`presence-status ${status.tone}`}>
      {status.icon}
      {status.label}
    </span>
  );
}

function MotionStatus({ sensor }: { sensor: SenteroSensorRole }) {
  const status = presenceMotionStatus(sensor);
  return (
    <span className={`presence-status ${status.tone}`}>
      {status.icon}
      {status.label}
    </span>
  );
}

function SensorEnvironment({ sensor }: { sensor: SenteroSensorRole }) {
  const temperature = formatMetric(sensor.temperature, '°C', 1);
  const light = lightLevel(sensor.illuminance);
  const humidity = formatMetric(sensor.humidity, '%', 0);
  return (
    <>
      {temperature && <span className="battery"><Thermometer size={17} /> {temperature}</span>}
      {light && (
        <span className={`battery light-level ${light.tone}`} title={`Helligkeit: ${light.lux} lx`}>
          <Lightbulb size={17} />
          {light.label} <small>· {light.lux} lx</small>
        </span>
      )}
      {humidity && <span className="battery"><Droplets size={17} /> {humidity}</span>}
    </>
  );
}

function lightLevel(value: unknown): { label: string; lux: number; tone: string } | null {
  const lux = Number(value);
  if (!Number.isFinite(lux)) return null;
  const rounded = Math.max(0, Math.round(lux));
  if (rounded <= 10) return { label: 'Dunkel', lux: rounded, tone: 'dark' };
  if (rounded <= 50) return { label: 'Sehr gedämpft', lux: rounded, tone: 'very-dim' };
  if (rounded <= 150) return { label: 'Gedämpft', lux: rounded, tone: 'dim' };
  if (rounded <= 300) return { label: 'Normal hell', lux: rounded, tone: 'normal' };
  if (rounded <= 700) return { label: 'Hell', lux: rounded, tone: 'bright' };
  return { label: 'Sehr hell', lux: rounded, tone: 'very-bright' };
}

function sensorConnectionTone(sensor: SenteroSensorRole) {
  if (sensor.reachable === false) return 'offline';
  if (sensor.stale === true) return 'stale';
  if (sensor.reachable == null) return 'unknown';
  return 'online';
}

function sensorConnectionLabel(sensor: SenteroSensorRole) {
  if (sensor.reachable === false) return 'Sensor nicht erreichbar';
  if (sensor.stale === true) return 'Keine frischen Sensordaten';
  if (sensor.reachable == null) return 'Verbindungsstatus unbekannt';
  return 'Sensor verbunden';
}

function sensorType(sensor: SenteroSensorRole) {
  if (isSmartMeterSensor(sensor)) return meterLabelFromRole(sensor.role);
  if (isSmokeSensor(sensor)) return 'Rauchmelder';
  if (isDoorContactSensor(sensor)) return 'Türkontakt';
  if (isMotionSensor(sensor)) return 'Präsenzsensor';
  if (String(sensor.device_class || '') === 'vibration') return 'Vibrationssensor';
  if (String(sensor.domain || '') === 'lock') return 'Türsensor';
  return 'Sensor';
}

function isSmartMeterSensor(sensor: SenteroSensorRole) {
  const role = String(sensor.role || '').toLowerCase();
  const dc = String(sensor.device_class || '').toLowerCase();
  return role.endsWith('_energy') || role.endsWith('_power') || role.endsWith('_water') || role.endsWith('_gas') || ['energy', 'power', 'water', 'gas'].includes(dc);
}

function isEcoTrackerSensor(sensor: SenteroSensorRole) {
  return String(sensor.source || '').toLowerCase() === 'ecotracker' || String(sensor.device_id || '').startsWith('ecotracker:');
}

function meterLabelFromRole(role: string) {
  if (role.endsWith('_water')) return 'Wasserzähler';
  if (role.endsWith('_gas')) return 'Gaszähler';
  return 'Stromzähler';
}

function formatMeterValue(sensor: SenteroSensorRole) {
  const value = sensor.state ?? 'unbekannt';
  const dc = String(sensor.device_class || '').toLowerCase();
  if (dc === 'power') return `${value} W`;
  if (dc === 'water' || sensor.role.endsWith('_water')) return `${value} m³`;
  if (dc === 'gas' || sensor.role.endsWith('_gas')) return `${value} m³`;
  return `${value} kWh`;
}

function ecoTrackerReadingMessage(reading: SenteroEcoTrackerReading) {
  const parts = [];
  const meterReading = reading.meter_reading_kwh ?? reading.energy_in_kwh;
  if (meterReading != null) parts.push(`Stromzählerstand ${meterReading} kWh`);
  if (reading.power_w != null) parts.push(`Leistung ${reading.power_w} W`);
  if (reading.energy_out_kwh != null) parts.push(`Einspeisung ${reading.energy_out_kwh} kWh`);
  return parts.length ? parts.join(' · ') : 'EcoTracker erreichbar.';
}

function ecoTrackerMeterReadingLabel(reading: SenteroEcoTrackerReading | null) {
  const meterReading = reading?.meter_reading_kwh ?? reading?.energy_in_kwh;
  return meterReading == null ? '' : `Zählerstand ${meterReading} kWh`;
}

function isEsp32PresenceSensor(sensor: SenteroSensorRole) {
  return String(sensor.source || '').toLowerCase() === 'mqtt' && (
    sensor.role.endsWith('_presence') ||
    String(sensor.device_class || '').toLowerCase() === 'presence' ||
    String(sensor.source_ref || '').includes('/state')
  );
}

function sensorSupportsLedControl(sensor: SenteroSensorRole) {
  const settings = Array.isArray(sensor.writable_settings) ? sensor.writable_settings.map((item) => String(item)) : [];
  return settings.includes('hp_led') || settings.includes('fall_led') || sensor.hp_led != null || sensor.fall_led != null || sensor.led_status?.hp_led != null || sensor.led_status?.fall_led != null;
}

function isDoorContactSensor(sensor: SenteroSensorRole) {
  return sensor.role === 'main_door' || sensor.role.endsWith('_door') || sensor.role.endsWith('_contact') || ['door', 'window', 'opening', 'contact'].includes(String(sensor.device_class || ''));
}

function isSmokeSensor(sensor: SenteroSensorRole) {
  const role = String(sensor.role || '').toLowerCase();
  const dc = String(sensor.device_class || '').toLowerCase();
  return role.endsWith('_smoke') || dc === 'smoke';
}

function isMotionSensor(sensor: SenteroSensorRole) {
  const dc = String(sensor.device_class || '').toLowerCase();
  return sensor.role.endsWith('_presence') || ['occupancy', 'motion', 'presence'].includes(dc);
}

function presenceMotionStatus(sensor: SenteroSensorRole) {
  if (sensor.fall_detected) {
    return { tone: 'alert', label: 'Sturz', icon: <WarningAmberIcon fontSize="small" /> };
  }
  const motion = String(sensor.motion_state || sensor.motion || '').toLowerCase();
  if (sensor.presence === false) {
    return { tone: 'away', label: 'Abwesend', icon: <PersonOutlineIcon fontSize="small" /> };
  }
  if (sensor.presence === true) {
    if (['active', 'move', 'moving', 'movement', 'motion', 'detected', 'large', 'small'].includes(motion)) {
      return { tone: 'motion', label: 'Bewegung', icon: <DirectionsRunIcon fontSize="small" /> };
    }
    if (['still', 'static', 'stationary', 'standstill', 'static_target', 'none'].includes(motion)) {
      return { tone: 'still', label: 'Still', icon: <AccessibilityNewIcon fontSize="small" /> };
    }
    return { tone: 'still', label: 'Anwesend', icon: <PersonIcon fontSize="small" /> };
  }
  if (['none', 'clear', 'off', 'false', '0', 'no_motion'].includes(motion)) {
    return { tone: 'away', label: 'Abwesend', icon: <PersonOutlineIcon fontSize="small" /> };
  }
  if (['active', 'move', 'moving', 'movement', 'motion', 'detected', 'large', 'small'].includes(motion)) {
    return { tone: 'motion', label: 'Bewegung', icon: <DirectionsRunIcon fontSize="small" /> };
  }
  const value = String(sensor.state || '').toLowerCase();
  if (['on', 'true', '1', 'active', 'occupied', 'detected'].includes(value)) {
    return { tone: 'motion', label: 'Bewegung', icon: <PersonIcon fontSize="small" /> };
  }
  if (['off', 'false', '0', 'clear', 'none'].includes(value)) {
    return { tone: 'away', label: 'Abwesend', icon: <PersonOutlineIcon fontSize="small" /> };
  }
  return { tone: 'unknown', label: 'Unbekannt', icon: <HelpOutlineIcon fontSize="small" /> };
}

function isZigbeeSensor(sensor: SenteroSensorRole) {
  return sensor.source === 'zigbee2mqtt' || String(sensor.source_ref || '').startsWith('zigbee2mqtt/') || String(sensor.device_id || '').startsWith('0x');
}

function canOfferLocalOnlySensorDelete(sensor: SenteroSensorRole, message: string) {
  const lower = message.toLowerCase();
  if (isEsp32PresenceSensor(sensor)) {
    return (
      lower.includes('nicht erreichbar') ||
      lower.includes('factory reset') ||
      lower.includes('zurückgesetzt') ||
      lower.includes('connection refused') ||
      lower.includes('mqtt') ||
      lower.includes('errno 61')
    );
  }
  if (!isZigbeeSensor(sensor)) return false;
  return (
    lower.includes('permit join') ||
    lower.includes('sensornetzwerk') ||
    lower.includes('zigbee') ||
    lower.includes('connection refused') ||
    lower.includes('mqtt') ||
    lower.includes('errno 61')
  );
}

function doorContactStatus(sensor: SenteroSensorRole) {
  const state = String(sensor.state || '').toLowerCase();
  const changedAt = sensor.last_changed || sensor.last_updated || sensor.updated_at;
  if (['open', 'on', 'opening', 'detected', 'true'].includes(state)) {
    return { open: true, tone: 'open', label: changedAt ? `Offen seit ${formatRelativeDuration(changedAt)}` : 'Offen' };
  }
  if (['closed', 'off', 'closing', 'clear', 'false'].includes(state)) {
    return { open: false, tone: 'closed', label: 'Geschlossen' };
  }
  return { open: false, tone: 'unknown', label: 'Status unbekannt' };
}

function batteryClass(value?: number | null) {
  if (value == null) return 'battery unknown';
  if (value < 30) return 'battery low';
  if (value < 50) return 'battery medium';
  return 'battery';
}

function sensorPowerLabel(sensor: SenteroSensorRole) {
  const source = String(sensor.power_source || '').toLowerCase();
  if (isEsp32PresenceSensor(sensor) && ['usb', 'mains', 'wired', 'external'].includes(source)) return 'USB-Strom';
  return '';
}

function formatBoolean(value?: boolean | null) {
  if (value == null) return 'unbekannt';
  return value ? 'true' : 'false';
}

function formatMetric(value: number | null | undefined, unit: string, digits: number) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '';
  return `${value.toFixed(digits)} ${unit}`;
}

function ledEnabled(sensor: SenteroSensorRole, localStates: Record<string, boolean>) {
  return localStates[sensor.role] ?? ledEnabledFromSensor(sensor) ?? false;
}

function ledEnabledFromSensor(sensor: SenteroSensorRole) {
  if (sensor.led_status?.all_on != null) return Boolean(sensor.led_status.all_on);
  if (sensor.hp_led != null && sensor.fall_led != null) return Boolean(sensor.hp_led && sensor.fall_led);
  if (sensor.led_status?.any_on != null) return Boolean(sensor.led_status.any_on);
  if (sensor.hp_led != null) return Boolean(sensor.hp_led);
  if (sensor.fall_led != null) return Boolean(sensor.fall_led);
  return null;
}

function ledEnabledFromCommandResult(result: { hp_led?: boolean | null; fall_led?: boolean | null; led_status?: SenteroSensorRole['led_status']; response?: Record<string, unknown> }) {
  if (result.led_status?.all_on != null) return Boolean(result.led_status.all_on);
  if (result.hp_led != null && result.fall_led != null) return Boolean(result.hp_led && result.fall_led);
  const response = result.response || {};
  const responseLedStatus = response.led_status && typeof response.led_status === 'object' ? response.led_status as { all_on?: unknown; any_on?: unknown } : null;
  if (responseLedStatus?.all_on != null) return Boolean(responseLedStatus.all_on);
  const hpLed = typeof response.hp_led === 'boolean' ? response.hp_led : null;
  const fallLed = typeof response.fall_led === 'boolean' ? response.fall_led : null;
  if (hpLed != null && fallLed != null) return hpLed && fallLed;
  return null;
}

function normalizeEmail(value: string) {
  return value.trim().toLowerCase();
}

function isValidEmail(value: string) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}

function emptyContactForm() {
  return {
    name: '',
    relationship: '',
    actor_role: 'relative' as AalActorRole,
    email: '',
    phone: '',
    telegram_chat_id: '',
    whatsapp_phone_number: '',
    preferred_channels: ['email'],
    notification_enabled: true,
    primary_contact: false,
  };
}

function normalizeChannels(value?: string | string[] | null) {
  if (Array.isArray(value)) return Array.from(new Set(value.filter((item) => ['email', 'telegram', 'whatsapp'].includes(item))));
  if (typeof value === 'string') {
    try {
      return normalizeChannels(JSON.parse(value));
    } catch {
      return ['email'];
    }
  }
  return ['email'];
}

function contactPayload(form: ReturnType<typeof emptyContactForm>, available: Record<'email' | 'telegram' | 'whatsapp', boolean>) {
  return {
    name: form.name.trim(),
    relationship: form.relationship.trim(),
    actor_role: actorRoleForContact(form.actor_role),
    email: normalizeEmail(form.email),
    phone: form.phone.trim(),
    telegram_chat_id: form.telegram_chat_id.trim(),
    whatsapp_phone_number: (form.whatsapp_phone_number || form.phone).trim(),
    preferred_channels: sanitizeChannels(normalizeChannels(form.preferred_channels), available),
    notification_enabled: form.notification_enabled,
    primary_contact: Boolean(form.primary_contact),
  };
}

function validateContactPayload(payload: ReturnType<typeof contactPayload>) {
  if (payload.preferred_channels.length === 0) return 'Bitte richten Sie zuerst mindestens einen funktionierenden Benachrichtigungskanal ein.';
  if (payload.preferred_channels.includes('email') && !payload.email) return 'Bitte geben Sie eine E-Mail-Adresse ein.';
  if (payload.preferred_channels.includes('whatsapp') && !payload.whatsapp_phone_number) return 'Bitte geben Sie die WhatsApp Telefonnummer ein.';
  return '';
}

function stringConfig(value: unknown) {
  if (!value || typeof value !== 'object') return {};
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, String(item ?? '')]));
}

function discoveredEmailForm<T extends Record<string, string>>(current: T, config: MailConfig, email: string): T {
  const smtpEncryption = String(config.smtp_encryption || '').toUpperCase();
  const imapEncryption = String(config.imap_encryption || '').toUpperCase();
  const currentImapUser = String(current.imap_user || '').trim();
  const imapUser = !currentImapUser || currentImapUser === current.imap_host ? email : currentImapUser;
  return {
    ...current,
    mail_from: current.mail_from || 'Sentero',
    smtp_host: config.smtp_host,
    smtp_port: String(config.smtp_port),
    smtp_user: email,
    smtp_login: current.smtp_login || email,
    smtp_encryption: smtpEncryption,
    smtp_starttls: smtpEncryption === 'STARTTLS' ? 'true' : 'false',
    smtp_ssl: smtpEncryption === 'SSL' ? 'true' : 'false',
    imap_host: config.imap_host,
    imap_port: String(config.imap_port),
    imap_user: imapUser,
    imap_encryption: imapEncryption,
    app_password_help_url: config.app_password_help_url || '',
  };
}

function emailAddressForm<T extends Record<string, string>>(current: T, nextEmail: string): T {
  const previousEmail = normalizeEmail(current.smtp_user || '');
  const nextLogin = normalizeEmail(nextEmail);
  const currentImapUser = String(current.imap_user || '').trim();
  const currentSmtpLogin = String(current.smtp_login || '').trim();
  const shouldUpdateSmtpLogin = !currentSmtpLogin || normalizeEmail(currentSmtpLogin) === previousEmail;
  const shouldUpdateImapUser = !currentImapUser || normalizeEmail(currentImapUser) === previousEmail;
  return {
    ...current,
    smtp_user: nextEmail,
    smtp_login: shouldUpdateSmtpLogin ? nextEmail : currentSmtpLogin,
    imap_user: shouldUpdateImapUser ? nextEmail : currentImapUser,
    mail_from: current.mail_from || 'Sentero',
  };
}

function hasEmailServerSettings(form: Record<string, string>) {
  return Boolean(String(form.smtp_host || '').trim() && String(form.imap_host || '').trim());
}

function emailChannelConfig(form: Record<string, string>) {
  const email = normalizeEmail(form.smtp_user);
  const rawPassword = form.smtp_password || form.imap_password || '';
  const password = looksMaskedSecret(rawPassword) ? '' : rawPassword;
  const smtpEncryption = normalizedEncryption(form.smtp_encryption, form.smtp_port, form.smtp_starttls, form.smtp_ssl);
  const { smtp_password: _smtpPassword, imap_password: _imapPassword, ...publicForm } = form;
  const config: Record<string, string> = {
    ...publicForm,
    mail_from: form.mail_from || 'Sentero',
    smtp_user: email,
    smtp_login: form.smtp_login || email,
    smtp_encryption: smtpEncryption,
    smtp_starttls: smtpEncryption === 'STARTTLS' ? 'true' : 'false',
    smtp_ssl: smtpEncryption === 'SSL' ? 'true' : 'false',
    imap_user: form.imap_user || email,
    imap_encryption: normalizedEncryption(form.imap_encryption, form.imap_port),
  };
  if (password) {
    config.smtp_password = password;
    config.imap_password = password;
  }
  return config;
}

function looksMaskedSecret(value: unknown) {
  const text = String(value || '');
  return text.includes('•') || text.startsWith('***');
}

function mailConfigFromForm(form: Record<string, string>): MailConfig {
  const smtpEncryption = normalizedEncryption(form.smtp_encryption, form.smtp_port, form.smtp_starttls, form.smtp_ssl);
  const imapEncryption = normalizedEncryption(form.imap_encryption, form.imap_port);
  return {
    imap_host: form.imap_host,
    imap_port: parsePort(form.imap_port, 993),
    imap_encryption: imapEncryption,
    smtp_host: form.smtp_host,
    smtp_port: parsePort(form.smtp_port, 587),
    smtp_encryption: smtpEncryption,
    auth_method: null,
    requires_app_password: Boolean(form.app_password_help_url),
    app_password_help_url: form.app_password_help_url || null,
    source: 'manual',
  };
}

function normalizedEncryption(value: string | undefined, port: string | undefined, starttls?: string, ssl?: string) {
  const encryption = String(value || '').toUpperCase();
  if (encryption === 'SSL' || encryption === 'STARTTLS' || encryption === 'NONE') return encryption;
  if (ssl === 'true' || port === '465' || port === '993') return 'SSL';
  if (starttls !== 'false') return 'STARTTLS';
  return 'NONE';
}

function parsePort(value: string | undefined, fallback: number) {
  const port = Number.parseInt(String(value || ''), 10);
  return Number.isFinite(port) && port > 0 ? port : fallback;
}

function appPasswordProviderHint(email: string) {
  const domain = normalizeEmail(email).split('@')[1] || 'diesen Anbieter';
  const label = domain.includes('gmail') ? 'Gmail' : domain.includes('icloud') ? 'iCloud' : domain.includes('yahoo') ? 'Yahoo' : domain.includes('outlook') || domain.includes('hotmail') ? 'Outlook' : 'diesen Anbieter';
  return `Für ${label} benötigen Sie normalerweise ein App-Passwort.`;
}

function friendlyMailError(message: string, hasAppPasswordHint: boolean) {
  const lower = message.toLowerCase();
  if (hasAppPasswordHint && (lower.includes('passwort') || lower.includes('anmeldung') || lower.includes('auth'))) {
    return 'Für diesen Anbieter benötigen Sie möglicherweise ein App-Passwort.';
  }
  if (lower.includes('nicht automatisch erkennen')) return 'Sentero konnte den Mailserver nicht automatisch erkennen.';
  return 'Die Anmeldung war nicht erfolgreich. Bitte prüfen Sie E-Mail-Adresse und Passwort.';
}

function channelState(channels: SenteroNotificationChannel[], channel: string) {
  const item = channels.find((entry) => entry.channel === channel);
  if (!item?.configured) return 'Nicht eingerichtet';
  return item.enabled ? 'Aktiv' : 'Test erforderlich';
}

function channelAvailability(channels: SenteroNotificationChannel[]) {
  const state = { email: false, telegram: false, whatsapp: false };
  for (const channel of channels) {
    if (channel.channel === 'email' || channel.channel === 'telegram' || channel.channel === 'whatsapp') {
      state[channel.channel] = Boolean(channel.enabled && channel.configured);
    }
  }
  return state;
}

function networkLabel(status: BoxNetworkStatus | null) {
  if (!status?.network_ready) return 'Offline';
  const value = String(status.active_connection || '');
  if (value === 'cellular') return 'Mobilfunk';
  if (value === 'wifi') return 'WLAN';
  if (value === 'ethernet') return 'LAN';
  return 'Verbunden';
}

function sanitizeChannels(channels: string[], available: Record<'email' | 'telegram' | 'whatsapp', boolean>) {
  const optional = channels.filter((channel): channel is 'telegram' | 'whatsapp' => (
    (channel === 'telegram' || channel === 'whatsapp') && available[channel]
  ));
  return ['email', ...optional.filter((channel, index) => optional.indexOf(channel) === index)];
}

function channelSelected(channels: string[], channel: 'email' | 'telegram' | 'whatsapp', available: Record<'email' | 'telegram' | 'whatsapp', boolean>) {
  if (channel === 'email') return true;
  return available[channel] && channels.includes(channel);
}

function telegramInviteUrl(bot: SenteroTelegramBotInfo | null, contact: SenteroTrustedContact) {
  const username = bot?.username?.trim();
  const code = contact.telegram_invite_code?.trim();
  if (!username || !code) return '';
  return `https://t.me/${encodeURIComponent(username)}?start=${encodeURIComponent(code)}`;
}

function queryChannelText(hasEmail: boolean, hasTelegram: boolean, telegramLinked: boolean, mailEnabled: boolean) {
  if (hasEmail && hasTelegram) return telegramLinked ? 'Antwortmail und Telegram' : 'Antwortmail, Telegram nach Kopplung';
  if (hasTelegram) return telegramLinked ? 'Telegram-Fragen' : 'Telegram nach Kopplung';
  if (hasEmail) return mailEnabled ? 'Antwortmail an Sentero' : 'E-Mail-Verbindung ausstehend';
  return 'Gilt für freigeschaltete Fragekanäle';
}

function queryPermissionSummary(permissions: string[]) {
  const labels = emailQueryPermissions.filter((permission) => permissions.includes(permission.value)).map((permission) => permission.label);
  if (!labels.length) return { countLabel: 'Keine Bereiche', names: 'Keine Bereiche freigegeben' };
  return {
    countLabel: labels.length === 1 ? '1 Bereich' : `${labels.length} Bereiche`,
    names: labels.join(', '),
  };
}

function channelLabel(channel: string) {
  if (channel === 'email') return 'E-Mail';
  if (channel === 'telegram') return 'Telegram';
  if (channel === 'whatsapp') return 'WhatsApp';
  return channel;
}

function channelIcon(channel: string, size = 20) {
  if (channel === 'telegram') return <Send size={size} />;
  if (channel === 'whatsapp') return <MessageCircle size={size} />;
  return <Mail size={size} />;
}

type ChannelSetupField = { key: string; label: string; hint?: string };
type ChannelSetupMeta = { title: string; text: string; fields: ChannelSetupField[] };

function channelSetupMeta(channel: 'email' | 'telegram' | 'whatsapp'): ChannelSetupMeta {
  if (channel === 'telegram') {
    return {
      title: 'Telegram einrichten',
      text: 'Telegram kann über persönliche Einladungslinks mit Angehörigen verbunden werden.',
      fields: [
        { key: 'bot_token', label: 'Bot Token', hint: 'Token von BotFather.' },
        { key: 'default_chat_id', label: 'Test Chat ID', hint: 'Optional für eine direkte Testnachricht.' },
      ],
    };
  }
  if (channel === 'whatsapp') {
    return {
      title: 'WhatsApp einrichten',
      text: 'WhatsApp benötigt eigene WhatsApp Cloud API Zugangsdaten.',
      fields: [
        { key: 'access_token', label: 'Access Token' },
        { key: 'phone_number_id', label: 'Phone Number ID' },
        { key: 'business_account_id', label: 'Business Account ID' },
        { key: 'api_version', label: 'Graph API Version' },
      ],
    };
  }
  return {
    title: 'E-Mail einrichten',
    text: 'Sentero verwendet diese Mailbox, um Hinweise zu senden und Anfragen von Vertrauenspersonen zu beantworten. Für eine neue Anfrage muss der E-Mail-Betreff mit „Sentero:“ beginnen. Die Frage schreiben Sie in den Nachrichtentext.',
    fields: [
      { key: 'smtp_host', label: 'SMTP-Server' },
      { key: 'smtp_port', label: 'SMTP-Port' },
      { key: 'smtp_encryption', label: 'SMTP-Verschlüsselung' },
      { key: 'smtp_login', label: 'SMTP-Login' },
      { key: 'imap_host', label: 'IMAP-Server' },
      { key: 'imap_port', label: 'IMAP-Port' },
      { key: 'imap_encryption', label: 'IMAP-Verschlüsselung' },
      { key: 'imap_user', label: 'IMAP-Login' },
      { key: 'mail_from', label: 'Anzeigename' },
    ],
  };
}

function channelHelpContent(channel: 'email' | 'telegram' | 'whatsapp') {
  if (channel === 'telegram') {
    return {
      title: 'Telegram einrichten',
      intro: 'Telegram wird über einen eigenen Bot und persönliche Einladungslinks verbunden.',
      sections: [
        { title: 'Was wird benötigt?', items: ['Bot Token von BotFather'] },
        { title: 'Schritt 1', text: ['Öffnen Sie Telegram und suchen Sie nach @BotFather.'] },
        { title: 'Schritt 2', text: ['Erstellen Sie mit BotFather einen neuen Bot. Danach erhalten Sie einen Bot Token.'] },
        { title: 'Schritt 3', text: ['Token in Sentero eintragen und Telegram testen.'] },
        { title: 'Schritt 4', text: ['Bei jeder vertrauten Person den persönlichen Link oder QR-Code teilen. Die Chat-ID wird nach dem Start automatisch gespeichert.'] },
        { title: 'Hinweis', text: ['Der Bot Token bleibt geheim. Angehörige erhalten nur den Link oder QR-Code.'] },
      ],
    };
  }
  if (channel === 'whatsapp') {
    return {
      title: 'WhatsApp einrichten',
      intro: 'WhatsApp-Benachrichtigungen benötigen die offizielle WhatsApp Cloud API von Meta.',
      sections: [
        { title: 'Was wird benötigt?', items: ['Meta Entwicklerkonto', 'WhatsApp Business Konto', 'Access Token', 'Phone Number ID', 'Business Account ID'] },
        { title: 'Wichtig', text: ['WhatsApp kann nicht einfach mit einer privaten WhatsApp-Nummer verbunden werden.', 'Diese Funktion richtet sich an fortgeschrittene Nutzer oder Unternehmen.'] },
        { title: 'Empfehlung', text: ['Nutzen Sie zuerst E-Mail. WhatsApp kann später zusätzlich eingerichtet werden.'] },
      ],
    };
  }
  return {
    title: 'E-Mail einrichten',
    intro: 'E-Mail ist der empfohlene Standardkanal für Sentero.',
    sections: [
      { title: 'Warum E-Mail?', text: ['Sentero nutzt Ihre E-Mail-Zugangsdaten, um Hinweise und Warnungen zu senden und Antworten von Vertrauenspersonen zu lesen.'] },
      { title: 'Was wird benötigt?', items: ['Server zum Senden', 'Server für Antworten', 'E-Mail-Adresse', 'App-Passwort oder E-Mail-Passwort'] },
      { title: 'Beispiel Gmail', text: ['Server zum Senden: smtp.gmail.com', 'Server für Antworten: imap.gmail.com', 'Sendeport: 587', 'Antwortport: 993'] },
      { title: 'Wichtig bei Gmail', text: ['Bei Gmail sollte ein App-Passwort verwendet werden. Das normale Google-Passwort funktioniert meistens nicht.'] },
      { title: 'So erstellen Sie ein App-Passwort', steps: ['Öffnen Sie Ihr Google-Konto.', 'Aktivieren Sie die Zwei-Faktor-Authentifizierung.', 'Öffnen Sie „App-Passwörter“.', 'Erstellen Sie ein neues App-Passwort für „Mail“.', 'Tragen Sie dieses Passwort in Sentero ein.'] },
      { title: 'Hinweis', text: ['Wenn Sie einen anderen E-Mail-Anbieter verwenden, finden Sie die Angaben zum Senden und Empfangen meist in den Hilfe-Seiten Ihres Anbieters.'] },
    ],
  };
}

function primaryNotificationRecipient(contacts: NonNullable<SenteroSetupStatus['trusted_contacts']>) {
  const contact = [...contacts]
    .filter((item) => item.email)
    .sort((a, b) => Number(Boolean(b.primary_contact)) - Number(Boolean(a.primary_contact)))[0];
  if (!contact?.email) return null;
  return {
    name: contact.name,
    email: contact.email,
    relationship: contact.relationship || undefined,
    primary: Boolean(contact.primary_contact),
  };
}

function actorRoleForContact(value?: string | null): AalActorRole {
  const role = String(value || '').trim();
  return aalActorRoles.some((item) => item.value === role) ? role as AalActorRole : 'relative';
}

function aalRoleLabel(value?: string | null) {
  const role = actorRoleForContact(value);
  return aalActorRoles.find((item) => item.value === role)?.label || 'Angehörige';
}

function activeBehaviorConsent(contactId: number, consents: SenteroConsent[]) {
  return consents.find((consent) => consent.contact_id === contactId && consent.purpose === 'behavior_notification' && consent.active) || null;
}

function latestBehaviorConsent(contactId: number, consents: SenteroConsent[]) {
  return consents.find((consent) => consent.contact_id === contactId && consent.purpose === 'behavior_notification') || null;
}

function activeExportConsent(contactId: number, consents: SenteroConsent[]) {
  return consents.find((consent) => consent.contact_id === contactId && consent.purpose === 'aal_partner_export' && consent.active) || null;
}

function latestExportConsent(contactId: number, consents: SenteroConsent[]) {
  return consents.find((consent) => consent.contact_id === contactId && consent.purpose === 'aal_partner_export') || null;
}

function activeExportToken(contactId: number, tokens: SenteroExportToken[]) {
  return tokens.find((token) => token.contact_id === contactId && token.purpose === 'aal_partner_export' && token.active) || null;
}

function latestExportToken(contactId: number, tokens: SenteroExportToken[]) {
  return tokens.find((token) => token.contact_id === contactId && token.purpose === 'aal_partner_export') || null;
}

function exportDataClassesForContact(actorRole?: string | null) {
  const role = actorRoleForContact(actorRole);
  if (role === 'housing_provider') return ['technical'];
  if (role === 'care_service') return ['personal_behavior', 'health_adjacent', 'emergency'];
  if (role === 'emergency_service') return ['emergency'];
  if (role === 'resident' || role === 'admin') return ['technical', 'utility', 'personal_behavior', 'health_adjacent', 'emergency'];
  return ['personal_behavior', 'health_adjacent', 'emergency'];
}

function exportExchangeEndpoints() {
  const origin = typeof window !== 'undefined' ? window.location.origin : '';
  return [
    { label: 'Tagesstatus', path: '/api/sentero/exchange/v1/daily-status' },
    { label: 'Ereignisse', path: '/api/sentero/exchange/v1/event-summary' },
    { label: 'Systemstatus', path: '/api/sentero/exchange/v1/system-status' },
  ].map((endpoint) => ({ ...endpoint, url: `${origin}${endpoint.path}` }));
}

function consentDescription(consent: SenteroConsent) {
  const classes = consent.data_classes.map(dataClassLabel).join(', ');
  if (consent.revoked_at) return `Widerrufen am ${formatDateTime(consent.revoked_at)}`;
  if (consent.valid_until) return `${classes} bis ${formatDateTime(consent.valid_until)}`;
  return classes || 'Verhaltensmeldungen';
}

function dataClassLabel(value: string) {
  if (value === 'personal_behavior') return 'Tagesablauf';
  if (value === 'health_adjacent') return 'AAL-Hinweise';
  if (value === 'emergency') return 'Notfall';
  if (value === 'technical') return 'Technik';
  if (value === 'environmental') return 'Umgebung';
  if (value === 'utility') return 'Verbrauch';
  return value;
}

function ageFromBirthYear(value: string) {
  const year = Number.parseInt(value, 10);
  const currentYear = new Date().getFullYear();
  if (!Number.isFinite(year) || year < 1900 || year > currentYear) return null;
  return currentYear - year;
}

function formatDateTime(value?: string | null) {
  if (!value) return 'noch keine Daten';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return 'noch keine Daten';
  return new Intl.DateTimeFormat('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }).format(date);
}

function formatRelativeDuration(value?: string | null) {
  if (!value) return '';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return '';
  const seconds = Math.max(0, Math.round((Date.now() - date.getTime()) / 1000));
  if (seconds < 60) return 'gerade eben';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} Min.`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} Std.`;
  const days = Math.round(hours / 24);
  return `${days} Tg.`;
}

function meterMeta(type: MeterAddType) {
  if (type === 'water_meter') return { label: 'Wasserzähler', role: 'home_water' };
  if (type === 'gas_meter') return { label: 'Gaszähler', role: 'home_gas' };
  return { label: 'Stromzähler', role: 'home_energy' };
}

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}
