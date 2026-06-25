import { useEffect, useMemo, useState } from 'react';
import type React from 'react';
import { Battery, Bell, ChevronLeft, ChevronRight, CheckCircle2, DoorClosed, DoorOpen, HardDrive, Home, KeyRound, Lightbulb, Mail, MessageCircle, Pencil, Plus, Save, Send, ShieldAlert, Trash2, UserRound, Users, Wifi, WifiOff, X} from 'lucide-react';
import { api, type SenteroNotificationChannel, type SenteroSensorNetworkSettings, type SenteroSensorRole, type SenteroSetupStatus } from '@shared/api/client';
import { UpdatePanel } from '../components/UpdatePanel';
import { useSenteroAuth } from '../auth/SenteroAuthContext';
import type { SenteroSettingsTab } from '../routes/routes';
import { senteroRouteToPath } from '../routes/routes';

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
  { tab: 'account', label: 'Konto & Zugriff', shortLabel: 'Konto', icon: KeyRound },
  { tab: 'system', label: 'System', shortLabel: 'System', icon: HardDrive },
];

export function SettingsPage({ activeTab }: { activeTab: SenteroSettingsTab }) {
  const { user, updateMe, changePassword } = useSenteroAuth();
  const [status, setStatus] = useState<SenteroSetupStatus | null>(null);
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
  const [passwordForm, setPasswordForm] = useState({ current_password: '', new_password: '', new_password_confirm: '' });
  const [accountEditing, setAccountEditing] = useState(false);
  const [passwordModalOpen, setPasswordModalOpen] = useState(false);
  const [channels, setChannels] = useState<SenteroNotificationChannel[]>([]);
  const [setupChannel, setSetupChannel] = useState<'email' | 'telegram' | 'whatsapp' | null>(null);
  const [helpChannel, setHelpChannel] = useState<'email' | 'telegram' | 'whatsapp' | null>(null);
  const [channelForms, setChannelForms] = useState({
    email: { smtp_host: '', smtp_port: '587', smtp_user: '', smtp_password: '', test_recipient: '' },
    telegram: { bot_token: '', default_chat_id: '', test_recipient: '' },
    whatsapp: { access_token: '', phone_number_id: '', business_account_id: '', test_recipient: '' },
  });

  useEffect(() => {
    void load();
  }, []);

  useEffect(() => {
    if (activeTab !== 'sensors') return;
    let active = true;
    let loading = false;

    async function refreshSensors() {
      if (loading) return;
      loading = true;
      try {
        const nextSensors = await api.senteroSensorRoles(true);
        if (active) setSensors(nextSensors.sensor_roles);
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
    setAccountForm({ display_name: user?.display_name || '', email: user?.email || '' });
  }, [user]);

  async function load() {
    try {
      const [nextStatus, nextSensors, nextChannels, nextNetwork] = await Promise.all([
        api.senteroSetupStatus(),
        api.senteroSensorRoles(true),
        api.senteroNotificationChannels(),
        api.senteroSensorNetwork(),
      ]);
      setStatus(nextStatus);
      setSensors(nextSensors.sensor_roles);
      setChannels(nextChannels.channels);
      setNetworkStatus(nextNetwork);
      setNetworkForm({
        wifi_ssid: nextNetwork.wifi_ssid || '',
        wifi_password: '',
      });
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

  function startEditContact(contact: {
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
  }) {
    setEditingContactId(contact.id);
    setEditContactForm({
      name: contact.name,
      relationship: contact.relationship || '',
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
        await api.deleteSenteroSensorRole(sensor.role);
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
        email: { ...current.email, ...stringConfig(byChannel.email), smtp_port: String(byChannel.email?.smtp_port || current.email.smtp_port) },
        telegram: { ...current.telegram, ...stringConfig(byChannel.telegram) },
        whatsapp: { ...current.whatsapp, ...stringConfig(byChannel.whatsapp) },
      };
    });
  }

  async function saveChannel(channel: 'email' | 'telegram' | 'whatsapp') {
    try {
      const config = channelForms[channel];
      await api.saveSenteroNotificationChannel(channel, { enabled: false, config });
      toast('Kanal gespeichert. Bitte testen, um ihn für Vertrauenspersonen freizuschalten.');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Kanal konnte nicht gespeichert werden.');
    }
  }

  async function testChannel(channel: 'email' | 'telegram' | 'whatsapp') {
    try {
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

  async function deleteSensor(role: string) {
    if (!window.confirm('Sensor aus Sentero entfernen? Das Gerät bleibt im Sensornetzwerk bestehen.')) return;
    try {
      await api.deleteSenteroSensorRole(role);
      toast('Sensor entfernt');
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sensor konnte nicht entfernt werden.');
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

  async function testSensor(role: string) {
    try {
      const result = await api.testSenteroSensorRole(role);
      if (!result.ok) {
        setError(result.message || 'Sensor ist aktuell nicht erreichbar.');
      } else {
        setError('');
        toast(result.message || 'Sensor geprüft');
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sensor konnte nicht geprüft werden.');
    }
  }

  const activeTabMeta = settingsTabs.find((item) => item.tab === activeTab);

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
          <h1>{activeTabMeta.label}</h1>
        </div>
      )}

      {/* Tab-Inhalte – auf Mobile ausgeblendet wenn Liste sichtbar */}
      <div className={mobileShowList ? 'sc-settings-content sc-mobile-hidden' : 'sc-settings-content'}>


      {activeTab === 'profile' && (
        <section className="sc-panel sc-settings-panel">
          <h2>Profil</h2>
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
                    <button className="sc-room-delete" type="button" onClick={(event) => { event.preventDefault(); void deleteRoom(room); }}><Trash2 size={18} /> Löschen</button>
                  </summary>
                  <div className="sc-sensor-settings-list">
                    {roomSensors.length === 0 && <p className="sc-muted-note">Für diesen Raum ist noch kein Sensor verbunden.</p>}
                    {roomSensors.map((sensor) => (
                      <div key={sensor.role}>
                        <div className="sc-sensor-settings-main">
                          <div className="sc-sensor-settings-head">
                            <strong>{sensor.label || sensor.role}</strong>
                            <small>{sensorType(sensor)} · zuletzt {formatDateTime(sensor.last_changed || sensor.last_updated || sensor.updated_at)}</small>
                          </div>
                          <div className="sc-sensor-health">
                            {isDoorContactSensor(sensor) && <DoorContactStatus sensor={sensor} />}
                            <span className={sensor.reachable === false ? 'offline' : sensor.reachable == null ? 'unknown' : 'online'}>
                              {sensor.reachable === false ? <WifiOff size={17} /> : <CheckCircle2 size={17} />}
                              {sensor.reachable === false ? 'Nicht erreichbar' : sensor.reachable == null ? 'In HA vorhanden' : 'Erreichbar'}
                            </span>
                            <span className={batteryClass(sensor.battery_level)}>
                              <Battery size={17} />
                              Akku {sensor.battery_level ?? 'unbekannt'}{sensor.battery_level == null ? '' : '%'}
                            </span>
                          </div>
                        </div>
                        <div className="sc-sensor-settings-actions">
                          <button type="button" onClick={() => void renameSensor(sensor)}><Pencil size={18} /> Name</button>
                          <button type="button" onClick={() => void testSensor(sensor.role)}><Wifi size={18} /> Test</button>
                          <button type="button" onClick={() => void deleteSensor(sensor.role)}><Trash2 size={18} /> Löschen</button>
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
            <h2>Netzwerk</h2>
            <p>Diese Angaben nutzt Sentero, um WLAN-Sensoren automatisch mit Ihrem Zuhause zu verbinden.</p>
          </div>
          <div className="sc-form-grid">
            <label>
              WLAN-Name
              <input value={networkForm.wifi_ssid} onChange={(event) => setNetworkForm((value) => ({ ...value, wifi_ssid: event.target.value }))} placeholder="Mein WLAN" />
            </label>
            <label>
              WLAN-Passwort
              <input type="password" value={networkForm.wifi_password} onChange={(event) => setNetworkForm((value) => ({ ...value, wifi_password: event.target.value }))} placeholder={networkStatus?.wifi_password_set ? 'Gespeichert' : 'Passwort'} />
            </label>
          </div>
          <footer className="sc-account-actions">
            <button className="sc-soft-action" type="button" onClick={() => void testNetwork()}><Wifi size={18} /> Testen</button>
            <button className="sc-soft-action primary" type="button" onClick={() => void saveNetwork()}><Save size={18} /> Speichern</button>
          </footer>
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
                {channelSelected(contactForm.preferred_channels, 'email', availableChannels) && <label>E-Mail<input type="email" value={contactForm.email} onChange={(event) => setContactForm((value) => ({ ...value, email: event.target.value }))} /></label>}
                {channelSelected(contactForm.preferred_channels, 'telegram', availableChannels) && <label>Telegram Chat ID<input value={contactForm.telegram_chat_id} onChange={(event) => setContactForm((value) => ({ ...value, telegram_chat_id: event.target.value }))} /></label>}
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
                      {channelSelected(editContactForm.preferred_channels, 'email', availableChannels) && <label>E-Mail<input type="email" value={editContactForm.email} onChange={(event) => setEditContactForm((value) => ({ ...value, email: event.target.value }))} /></label>}
                      {channelSelected(editContactForm.preferred_channels, 'telegram', availableChannels) && <label>Telegram Chat ID<input value={editContactForm.telegram_chat_id} onChange={(event) => setEditContactForm((value) => ({ ...value, telegram_chat_id: event.target.value }))} /></label>}
                      {channelSelected(editContactForm.preferred_channels, 'whatsapp', availableChannels) && <label>WhatsApp Telefonnummer<input value={editContactForm.whatsapp_phone_number} onChange={(event) => setEditContactForm((value) => ({ ...value, whatsapp_phone_number: event.target.value, phone: event.target.value }))} /></label>}
                    </div>
                    <ChannelChecks value={editContactForm.preferred_channels} available={availableChannels} onChange={(preferred_channels) => setEditContactForm((value) => ({ ...value, preferred_channels }))} />
                    <footer>
                      <button type="button" onClick={() => void saveEditedContact()}><Save size={18} /> Speichern</button>
                      <button type="button" onClick={() => setEditingContactId(null)}><X size={18} /> Abbrechen</button>
                    </footer>
                  </>
                ) : (
                  <>
                    <span className="sc-avatar">{contact.name[0]}</span>
                    <h3>{contact.name}</h3>
                    <p>{contact.relationship || 'Kontakt'}</p>
                    <small>{contact.email || 'Keine E-Mail hinterlegt'}</small>
                    <div className="sc-contact-channel-list">{normalizeChannels(contact.preferred_channels).map((channel) => <span key={channel}>{channelLabel(channel)}</span>)}</div>
                    <footer>
                      <button type="button" onClick={() => startEditContact(contact)}><Pencil size={18} /> Bearbeiten</button>
                      <button type="button" onClick={() => void deleteContact(contact.id)}><Trash2 size={18} /> Löschen</button>
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
            <h2>Benachrichtigungen</h2>
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
              onFormChange={(form) => setChannelForms((value) => ({ ...value, [setupChannel]: form as never }))}
              onSave={() => void saveChannel(setupChannel)}
              onTest={() => void testChannel(setupChannel)}
            />
          )}
          {helpChannel && <ChannelHelpModal channel={helpChannel} onClose={() => setHelpChannel(null)} />}
        </section>
      )}

      {activeTab === 'account' && (
        <section className="sc-panel sc-settings-panel sc-account-panel">
          <div className="sc-settings-hero">
            <h2>Konto & Zugriff</h2>
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
                    <p><span>Rolle</span><strong>{user?.role === 'owner' ? 'Inhaber-Konto' : user?.role === 'admin' ? 'Admin-Konto' : 'Ansichtskonto'}</strong></p>
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
          <h2>System</h2>
          <div className="sc-system-grid">
            <p><strong>Home verbunden</strong><span>{status?.home.connected ? 'Ja' : 'Nein'}</span></p>
            <p><strong>Sensoren verbunden</strong><span>{sensors.filter((sensor) => sensor.configured).length}</span></p>
            <p><strong>Sensoren offline</strong><span>{sensors.filter((sensor) => sensor.reachable === false).length}</span></p>
            <p><strong>Letzte Aktualisierung</strong><span>{formatDateTime(status?.updated_at)}</span></p>
          </div>
          <UpdatePanel />
          <div className="sc-danger-zone">
            <h3><ShieldAlert size={22} /> Werkseinstellungen</h3>
            <p>Zum Zurücksetzen bitte ZURÜCKSETZEN eingeben.</p>
            <input value={resetText} onChange={(event) => setResetText(event.target.value)} placeholder="ZURÜCKSETZEN" />
            <button type="button" disabled={resetText !== 'ZURÜCKSETZEN'} onClick={() => window.confirm('Alle Sentero-Daten löschen?')}>Factory Reset</button>
          </div>
        </section>
      )}
      </div>
    </section>
  );
}

function EmptyState({ text, action }: { text: string; action: string }) {
  return (
    <div className="sc-empty-state">
      <p>{text}</p>
      <button type="button" onClick={() => window.location.assign('/sentero/setup')}>{action}</button>
    </div>
  );
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
  onSave,
  onTest,
}: {
  channel: 'email' | 'telegram' | 'whatsapp';
  form: Record<string, string>;
  recipient?: { name: string; email: string; relationship?: string; primary: boolean } | null;
  onClose: () => void;
  onFormChange: (form: Record<string, string>) => void;
  onSave: () => void;
  onTest: () => void;
}) {
  const meta = channelSetupMeta(channel);
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
          {meta.fields.map(([key, label]) => (
            <label key={key} className={key.includes('token') || key.includes('password') ? 'sc-form-wide' : undefined}>
              {label}
              <input
                type={key.includes('token') || key.includes('password') ? 'password' : 'text'}
                value={form[key] || ''}
                onChange={(event) => onFormChange({ ...form, [key]: event.target.value })}
              />
            </label>
          ))}
        </div>
        {channel === 'email' && (
          <div className="sc-channel-recipient">
            <span>Empfänger</span>
            {recipient ? (
              <>
                <strong>{recipient.name}</strong>
                <small>{recipient.email}</small>
                {recipient.primary && <em>Hauptansprechpartner</em>}
              </>
            ) : (
              <small>Bitte hinterlegen Sie zuerst eine Vertrauensperson mit E-Mail-Adresse.</small>
            )}
          </div>
        )}
        {channel === 'email' && <p className="sc-modal-help">Diese Angaben werden benötigt, damit Sentero E-Mails versenden kann.</p>}
        <footer>
          <button type="button" onClick={onTest}><Send size={18} /> {channel === 'email' ? 'Test senden' : 'Testen'}</button>
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
          const selected = value.includes(option.channel) && available[option.channel];
          const disabled = !available[option.channel];
          return (
            <label key={option.channel} className={`sc-channel-choice${selected ? ' selected' : ''}${disabled ? ' disabled' : ''}`}>
              <input type="checkbox" checked={selected} disabled={disabled} onChange={(event) => toggle(option.channel, event.target.checked)} />
              <i>{option.icon}</i>
              <strong>{option.label}</strong>
            </label>
          );
        })}
      </div>
      <small>Nicht verfügbare Kanäle werden nach erfolgreichem Verbindungstest freigeschaltet.</small>
    </div>
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

function sensorType(sensor: SenteroSensorRole) {
  if (isDoorContactSensor(sensor)) return 'Türkontakt';
  if (String(sensor.device_class || '') === 'vibration') return 'Vibrationssensor';
  if (String(sensor.domain || '') === 'lock') return 'Türsensor';
  return 'Bewegung';
}

function isDoorContactSensor(sensor: SenteroSensorRole) {
  return sensor.role === 'main_door' || sensor.role.endsWith('_door') || sensor.role.endsWith('_contact') || ['door', 'window', 'opening', 'contact'].includes(String(sensor.device_class || ''));
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

function normalizeEmail(value: string) {
  return value.trim().toLowerCase();
}

function emptyContactForm() {
  return {
    name: '',
    relationship: '',
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
  if (Array.isArray(value)) return Array.from(new Set(['email', ...value.filter((item) => ['email', 'telegram', 'whatsapp'].includes(item))]));
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
  if (payload.preferred_channels.includes('telegram') && !payload.telegram_chat_id) return 'Bitte geben Sie die Telegram Chat ID ein.';
  if (payload.preferred_channels.includes('whatsapp') && !payload.whatsapp_phone_number) return 'Bitte geben Sie die WhatsApp Telefonnummer ein.';
  return '';
}

function stringConfig(value: unknown) {
  if (!value || typeof value !== 'object') return {};
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, String(item ?? '')]));
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

function sanitizeChannels(channels: string[], available: Record<'email' | 'telegram' | 'whatsapp', boolean>) {
  return channels.filter((channel): channel is 'email' | 'telegram' | 'whatsapp' => (
    (channel === 'email' || channel === 'telegram' || channel === 'whatsapp') && available[channel]
  ));
}

function channelSelected(channels: string[], channel: 'email' | 'telegram' | 'whatsapp', available: Record<'email' | 'telegram' | 'whatsapp', boolean>) {
  return available[channel] && channels.includes(channel);
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

function channelSetupMeta(channel: 'email' | 'telegram' | 'whatsapp') {
  if (channel === 'telegram') {
    return {
      title: 'Telegram einrichten',
      text: 'Telegram kann zusätzlich zur E-Mail genutzt werden.',
      fields: [
        ['bot_token', 'Bot Token'],
        ['default_chat_id', 'Chat ID'],
      ] as Array<[string, string]>,
    };
  }
  if (channel === 'whatsapp') {
    return {
      title: 'WhatsApp einrichten',
      text: 'WhatsApp benötigt eigene WhatsApp Cloud API Zugangsdaten.',
      fields: [
        ['access_token', 'Access Token'],
        ['phone_number_id', 'Phone Number ID'],
        ['business_account_id', 'Business Account ID'],
      ] as Array<[string, string]>,
    };
  }
  return {
    title: 'E-Mail einrichten',
    text: 'E-Mail bleibt der Standardkanal für Sentero-Benachrichtigungen.',
    fields: [
      ['smtp_host', 'SMTP Host'],
      ['smtp_port', 'SMTP Port'],
      ['smtp_user', 'SMTP Benutzer'],
      ['smtp_password', 'SMTP Passwort'],
      ['test_recipient', 'Testempfänger'],
    ] as Array<[string, string]>,
  };
}

function channelHelpContent(channel: 'email' | 'telegram' | 'whatsapp') {
  if (channel === 'telegram') {
    return {
      title: 'Telegram einrichten',
      intro: 'Telegram kann zusätzlich zu E-Mail verwendet werden.',
      sections: [
        { title: 'Was wird benötigt?', items: ['Bot Token', 'Chat ID'] },
        { title: 'Schritt 1', text: ['Öffnen Sie Telegram und suchen Sie nach @BotFather.'] },
        { title: 'Schritt 2', text: ['Erstellen Sie mit BotFather einen neuen Bot. Danach erhalten Sie einen Bot Token.'] },
        { title: 'Schritt 3', text: ['Senden Sie Ihrem neuen Bot mindestens eine Nachricht.'] },
        { title: 'Schritt 4', text: ['Ermitteln Sie Ihre Chat ID. Diese kann über die Telegram Bot API oder über einen Telegram-ID-Helfer ermittelt werden.'] },
        { title: 'Hinweis', text: ['Telegram ist optional. Für die meisten Sentero-Installationen reicht E-Mail aus.'] },
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
      { title: 'Warum E-Mail?', text: ['Sentero nutzt Ihre E-Mail-Zugangsdaten, um Hinweise und Warnungen an Ihre Vertrauenspersonen zu senden.'] },
      { title: 'Was wird benötigt?', items: ['SMTP Host', 'SMTP Port', 'E-Mail-Adresse oder Benutzername', 'App-Passwort oder E-Mail-Passwort'] },
      { title: 'Beispiel Gmail', text: ['SMTP Host: smtp.gmail.com', 'SMTP Port: 587', 'Verschlüsselung: STARTTLS'] },
      { title: 'Wichtig bei Gmail', text: ['Bei Gmail sollte ein App-Passwort verwendet werden. Das normale Google-Passwort funktioniert meistens nicht.'] },
      { title: 'So erstellen Sie ein App-Passwort', steps: ['Öffnen Sie Ihr Google-Konto.', 'Aktivieren Sie die Zwei-Faktor-Authentifizierung.', 'Öffnen Sie „App-Passwörter“.', 'Erstellen Sie ein neues App-Passwort für „Mail“.', 'Tragen Sie dieses Passwort in Sentero ein.'] },
      { title: 'Hinweis', text: ['Wenn Sie einen anderen E-Mail-Anbieter verwenden, finden Sie die SMTP-Daten meist in den Hilfe-Seiten Ihres Anbieters.'] },
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
