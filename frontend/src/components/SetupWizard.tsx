import { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Cable, CheckCircle2, HeartHandshake, Mail, Plus, ShieldCheck, Smartphone, Trash2, UserRound, Wifi, X } from 'lucide-react';
import { api, type NetworkStatus, type SenteroEsp32DiscoverySensor, type SenteroSensorRole, type WifiNetwork } from '@shared/api/client';
import { SensorWizard, type SensorBinding, type SensorDiscoveryState } from './SensorWizard';

type Profile = {
  name: string;
  birthYear: string;
  notes: string;
};

type Contact = {
  id: string;
  name: string;
  relation: string;
  phone: string;
  email: string;
  channels: string[];
  primary: boolean;
};

type NotificationPreferences = {
  anomalies: boolean;
  critical: boolean;
  daily_summary: boolean;
};

type SensorPlan = { motion: boolean; door: boolean; electricity: boolean; water: boolean; gas: boolean };

const steps = ['Willkommen', 'Internetverbindung', 'Profil', 'Räume', 'Sensoren', 'Vertraute Personen', 'Benachrichtigungen', 'Abschluss'];
const SENSOR_DISCOVERY_SECONDS = 120;
const PRESENCE_DISCOVERY_TIMEOUT_MS = 8000;

const roomOptions = [
  { id: 'living_room', label: 'Wohnzimmer', door: true },
  { id: 'kitchen', label: 'Küche', door: true },
  { id: 'bathroom', label: 'Bad', door: true },
  { id: 'toilet', label: 'Toilette', door: true },
  { id: 'bedroom', label: 'Schlafzimmer', door: true },
  { id: 'hallway', label: 'Flur/Eingang', door: true },
  { id: 'office', label: 'Arbeitszimmer', door: true },
  { id: 'garden', label: 'Balkon/Garten', door: false },
];

const baseRoomLabel: Record<string, string> = { ...Object.fromEntries(roomOptions.map((room) => [room.id, room.label])), home: 'Haushalt' };

export function SetupWizard({ onFinish }: { onFinish: () => void }) {
  const [step, setStep] = useState(0);
  const [profile, setProfile] = useState<Profile>({ name: '', birthYear: '', notes: '' });
  const [selectedRooms, setSelectedRooms] = useState<string[]>([]);
  const [customRooms, setCustomRooms] = useState<Record<string, string>>({});
  const [sensorPlan, setSensorPlan] = useState<Record<string, SensorPlan>>({});
  const [lockedSensorPlan, setLockedSensorPlan] = useState<Record<string, SensorPlan>>({});
  const [customRoom, setCustomRoom] = useState('');
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [contactForm, setContactForm] = useState<Contact>({ id: '', name: '', relation: 'Tochter', phone: '', email: '', channels: ['E-Mail'], primary: true });
  const [contactFormOpen, setContactFormOpen] = useState(false);
  const [notification, setNotification] = useState<NotificationPreferences>({ anomalies: true, critical: true, daily_summary: false });
  const [confirmed, setConfirmed] = useState(false);
  const [emailSetupRequired, setEmailSetupRequired] = useState(false);
  const [networkStatus, setNetworkStatus] = useState<NetworkStatus | null>(null);
  const [wifiNetworks, setWifiNetworks] = useState<WifiNetwork[]>([]);
  const [networkChoice, setNetworkChoice] = useState<'wifi' | 'cellular' | 'ethernet'>('wifi');
  const [wifiForm, setWifiForm] = useState({ ssid: '', password: '' });
  const [networkMessage, setNetworkMessage] = useState('');
  const [networkBusy, setNetworkBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [sensorBindings, setSensorBindings] = useState<SensorBinding[]>([]);
  const [discovery, setDiscovery] = useState<Record<string, SensorDiscoveryState>>({});
  const [presenceTransport, setPresenceTransport] = useState<'zigbee' | 'wifi_esphome'>('zigbee');
  const timers = useRef<Record<string, number>>({});
  const activeDiscoverySessions = useRef<Set<number>>(new Set());
  const devMode = new URLSearchParams(window.location.search).get('dev') === '1';

  useEffect(() => {
    setSensorBindings((current) => buildBindings(selectedRooms, sensorPlan, customRooms, current));
  }, [selectedRooms, sensorPlan, customRooms]);

  useEffect(() => {
    void api.senteroSetupStatus().then((status) => {
      const existingSensorRooms = Array.from(new Set((status.sensor_roles || []).map((role) => role.room).filter(Boolean))) as string[];
      const nextRooms = uniqueValues([...status.selected_rooms, ...existingSensorRooms]);
      if (nextRooms.length) setSelectedRooms(nextRooms);
      const unknownRooms = Object.fromEntries(
        nextRooms
          .filter((room) => !baseRoomLabel[room])
          .map((room) => [room, room]),
      );
      if (Object.keys(unknownRooms).length) setCustomRooms((current) => ({ ...unknownRooms, ...current }));
      if (status.sensor_roles?.length) {
        setLockedSensorPlan(lockedPlanFromRoles(status.sensor_roles));
        setSensorPlan((current) => mergeSensorPlan(current, status.sensor_roles));
        setSensorBindings((current) => mergeExistingSensorBindings(current, status.sensor_roles, unknownRooms));
      }
      if (status.profile?.name) {
        setProfile((value) => ({
          ...value,
          name: status.profile?.name || '',
          birthYear: status.profile?.birth_year ? String(status.profile.birth_year) : '',
          notes: status.profile?.notes || '',
        }));
      }
      if (status.trusted_contacts?.length) {
        setContacts(status.trusted_contacts.map((contact) => ({
          id: String(contact.id),
          name: contact.name,
          relation: contact.relationship || '',
          phone: '',
          email: contact.email || '',
          channels: contact.email ? ['E-Mail'] : [],
          primary: Boolean(contact.primary_contact),
        })));
      }
    }).catch(() => undefined);
    void api.senteroSensorManagerStatus().then((status) => {
      setPresenceTransport(status.presence_sensor_transport === 'wifi_esphome' ? 'wifi_esphome' : 'zigbee');
    }).catch(() => undefined);
    void api.senteroEcoTrackerStatus().then((status) => {
      if (!status.host) return;
      setSensorBindings((current) => current.map((sensor) => sensor.type === 'electricity_meter' ? { ...sensor, sensorId: status.host } : sensor));
    }).catch(() => undefined);
    void refreshNetwork();
    return () => {
      Object.values(timers.current).forEach((timer) => window.clearTimeout(timer));
      activeDiscoverySessions.current.forEach((sessionId) => {
        void api.cancelSenteroSensorDiscovery(sessionId).catch(() => undefined);
      });
      activeDiscoverySessions.current.clear();
    };
  }, []);

  const calculatedAge = useMemo(() => {
    return ageFromBirthYear(profile.birthYear);
  }, [profile.birthYear]);

  const connectedSensors = sensorBindings.filter((sensor) => sensor.status === 'connected').length;

  async function next() {
    const validation = validateStep();
    setErrors(validation);
    if (validation.length) return;

    if (step === 0) {
      await safeBackend(() => api.startSenteroSetup());
    }
    if (step === 1) {
      const ok = await saveNetworkStep();
      if (!ok) return;
    }
    if (step === 2) {
      await safeBackend(() => api.saveSenteroProfile({ name: profile.name.trim(), birth_year: Number.parseInt(profile.birthYear, 10), age: calculatedAge, notes: profile.notes }));
    }
    if (step === 3) {
      const roomsWithSensors = selectedRoomsWithSensors(selectedRooms, sensorPlan);
      setSelectedRooms(roomsWithSensors);
      await safeBackend(() => api.saveSenteroSetupRooms(roomsWithSensors));
    }
    if (step === 5 && contacts.length) {
      await safeBackend(() => Promise.all(
        contacts.map((contact) => {
          const email = normalizeEmail(contact.email);
          return api.saveSenteroContact({
            name: contact.name.trim(),
            relationship: contact.relation,
            email,
            preferred_channels: ['email'],
            notification_enabled: true,
            primary_contact: contact.primary,
          });
        }),
      ));
    }
    if (step === 6) {
      await safeBackend(() => api.saveSenteroNotifications(notification));
    }
    if (step === steps.length - 1) {
      const ok = await completeSetup();
      if (ok) onFinish();
      return;
    }
    setStep((value) => Math.min(value + 1, steps.length - 1));
  }

  function back() {
    setErrors([]);
    setStep((value) => Math.max(value - 1, 0));
  }

  function validateStep() {
    if (step === 1 && !networkStatus?.network_ready && networkChoice === 'wifi' && !wifiForm.ssid.trim()) return ['Bitte wählen Sie ein WLAN aus.'];
    if (step === 1 && !networkStatus?.network_ready && networkChoice === 'wifi' && !wifiForm.password) return ['Bitte geben Sie das WLAN-Passwort ein.'];
    if (step === 2 && !profile.name.trim()) return ['Bitte geben Sie den Namen ein.'];
    if (step === 2 && !validBirthYear(profile.birthYear)) return ['Bitte geben Sie ein gültiges Geburtsjahr ein.'];
    if (step === 3 && selectedRooms.length === 0) return ['Bitte wählen Sie mindestens einen Raum aus.'];
    if (step === 3 && selectedRoomsWithSensors(selectedRooms, sensorPlan).length === 0) return ['Bitte wählen Sie mindestens einen Raum mit Sensor aus.'];
    if (step === 5 && contacts.length === 0) return ['Bitte fügen Sie mindestens eine vertraute Person hinzu.'];
    if (step === 5 && !contacts.some((contact) => isValidEmail(normalizeEmail(contact.email)))) return ['Bitte hinterlegen Sie mindestens eine gültige E-Mail-Adresse.'];
    if (step === 7 && !confirmed) return ['Bitte bestätigen Sie die Zusammenfassung.'];
    return [];
  }

  async function refreshNetwork() {
    try {
      const status = await api.networkStatus();
      setNetworkStatus(status);
      if (status.active_connection === 'wifi' || status.active_connection === 'ethernet' || status.active_connection === 'cellular') {
        setNetworkChoice(status.active_connection);
      }
      if (status.capabilities.wifi) {
        const scan = await api.networkWifiNetworks();
        setWifiNetworks(scan.networks);
      }
    } catch {
      // Network setup remains optional in development.
    }
  }

  async function saveNetworkStep() {
    setNetworkBusy(true);
    setNetworkMessage('');
    try {
      if (networkStatus?.network_ready) {
        return true;
      }
      if (networkChoice === 'ethernet') {
        const status = await api.networkStatus();
        setNetworkStatus(status);
        if (!status.internet_reachable) {
          setNetworkMessage('Sentero erkennt noch keine Internetverbindung. Sie können fortfahren, Benachrichtigungen werden später versendet.');
        }
        return true;
      }
      if (networkChoice === 'cellular') {
        const result = await api.connectNetworkCellular();
        setNetworkStatus(result.status);
        setNetworkMessage(result.message);
        return result.ok;
      }
      const result = await api.connectNetworkWifi(wifiForm);
      setNetworkStatus(result.status);
      setNetworkMessage(result.message);
      if (!result.ok) setErrors([result.message || 'WLAN konnte nicht verbunden werden.']);
      return result.ok;
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Internetverbindung konnte nicht eingerichtet werden.';
      setNetworkMessage(message);
      setErrors([message]);
      return false;
    } finally {
      setNetworkBusy(false);
    }
  }

  async function completeSetup() {
    try {
      const channels = await api.senteroNotificationChannels();
      const email = channels.channels.find((channel) => channel.channel === 'email');
      if (!email?.enabled || !email?.configured) {
        setEmailSetupRequired(true);
        setErrors([]);
        return false;
      }
      await api.completeSenteroSetup();
      return true;
    } catch {
      setEmailSetupRequired(true);
      return false;
    }
  }

  async function safeBackend(action: () => Promise<unknown>) {
    try {
      await action();
    } catch {
      // The wizard remains usable with mock data when the local backend is not reachable.
    }
  }

  function toggleRoom(roomId: string) {
    if (selectedRooms.includes(roomId) && roomHasLockedSensor(lockedSensorPlan, roomId)) return;
    setSelectedRooms((current) => {
      if (current.includes(roomId)) return current.filter((id) => id !== roomId);
      setSensorPlan((plans) => ({ ...plans, [roomId]: plans[roomId] || emptySensorPlan() }));
      return [...current, roomId];
    });
  }

  function addCustomRoom() {
    const label = customRoom.trim();
    if (!label) return;
    const id = label;
    setCustomRooms((current) => ({ ...current, [id]: label }));
    setSelectedRooms((current) => current.includes(id) ? current : [...current, id]);
    setSensorPlan((current) => ({ ...current, [id]: current[id] || emptySensorPlan() }));
    setCustomRoom('');
  }

  function roomLabel(roomId: string) {
    return customRooms[roomId] || baseRoomLabel[roomId] || roomId;
  }

  function toggleSensorType(roomId: string, type: keyof SensorPlan) {
    if (lockedSensorPlan[roomId]?.[type]) return;
    setSensorPlan((current) => {
      const fallback = emptySensorPlan();
      const next = { ...(current[roomId] || fallback), [type]: !(current[roomId] || fallback)[type] };
      return { ...current, [roomId]: next };
    });
  }

  function updateSensor(id: string, patch: Partial<SensorBinding>) {
    setSensorBindings((current) => current.map((sensor) => sensor.id === id ? { ...sensor, ...patch } : sensor));
  }

  async function searchSensor(sensor: SensorBinding) {
    updateSensor(sensor.id, { status: 'searching' });
    setDiscovery((current) => ({ ...current, [sensor.id]: { remainingSeconds: SENSOR_DISCOVERY_SECONDS } }));
    if (sensor.type === 'electricity_meter') {
      await connectEcoTracker(sensor);
      return;
    }
    const activePresenceTransport = isPresenceBinding(sensor) ? await loadPresenceTransport() : presenceTransport;
    if (isPresenceBinding(sensor) && activePresenceTransport === 'wifi_esphome') {
      await searchWifiPresenceSensor(sensor);
      return;
    }
    try {
      const result = await api.startSenteroSensorDiscovery({
        sensor_type: sensorTypeForDiscovery(sensor.type),
        room_id: sensor.roomId,
        role: sensor.id,
        transport: isPresenceBinding(sensor) ? activePresenceTransport : 'zigbee',
        duration: SENSOR_DISCOVERY_SECONDS,
      });
      if (result.status === 'manual_action') {
        throw new Error(result.message || 'Die Sensor-Einrichtung ist noch nicht bereit.');
      }
      activeDiscoverySessions.current.add(result.discovery_id);
      updateSensor(sensor.id, { sessionId: result.discovery_id });
      setDiscovery((current) => ({ ...current, [sensor.id]: { ...(current[sensor.id] || {}), remainingSeconds: result.expires_in_seconds || SENSOR_DISCOVERY_SECONDS } }));
      pollSensor(sensor.id, result.discovery_id, Date.now(), sensor.name, sensor.roomId);
    } catch (err) {
      updateSensor(sensor.id, { status: 'missing' });
      setDiscovery((current) => ({ ...current, [sensor.id]: { error: userFacingSensorError(err) } }));
    }
  }

  async function connectEcoTracker(sensor: SensorBinding) {
    const host = sensor.sensorId.trim();
    if (!host) {
      updateSensor(sensor.id, { status: 'missing' });
      setDiscovery((current) => ({ ...current, [sensor.id]: { error: 'Bitte geben Sie die IP-Adresse des EcoTrackers ein.' } }));
      return;
    }
    try {
      const result = await api.connectSenteroEcoTracker(host);
      const test = await api.testSenteroSensorRole(sensor.id);
      if (!test.ok) throw new Error(test.message || 'EcoTracker ist aktuell nicht erreichbar.');
      updateSensor(sensor.id, { status: 'connected', score: 100, sensorManagerId: result.sensor.id, name: result.sensor.name, sensorId: host });
      setDiscovery((current) => ({ ...current, [sensor.id]: { sensor: { id: result.sensor.id, name: result.sensor.name, type: result.sensor.type, confidence: 100 }, remainingSeconds: 0 } }));
    } catch (err) {
      updateSensor(sensor.id, { status: 'missing' });
      setDiscovery((current) => ({ ...current, [sensor.id]: { error: err instanceof Error ? err.message : 'EcoTracker konnte nicht verbunden werden.' } }));
    }
  }

  async function loadPresenceTransport() {
    try {
      const status = await api.senteroSensorManagerStatus();
      const nextTransport = status.presence_sensor_transport === 'wifi_esphome' ? 'wifi_esphome' : 'zigbee';
      setPresenceTransport(nextTransport);
      return nextTransport;
    } catch {
      return presenceTransport;
    }
  }

  async function searchWifiPresenceSensor(sensor: SensorBinding) {
    try {
      const name = sensor.name || `${roomLabel(sensor.roomId)} Präsenzsensor`;
      await api.startSenteroPresenceDiscovery();
      const discovered = await waitForPresenceSensor();
      setDiscovery((current) => ({
        ...current,
        [sensor.id]: {
          sensor: { id: discovered.id, name: discovered.name, type: discovered.type, confidence: 100 },
          remainingSeconds: Math.ceil(PRESENCE_DISCOVERY_TIMEOUT_MS / 1000),
        },
      }));
      const result = await api.startSenteroPresenceProvisioning({ room_id: sensor.roomId, display_name: name, device_id: discovered.id });
      updateSensor(sensor.id, { status: 'connected', score: 100, sensorManagerId: result.device.id, name: result.device.name });
      setDiscovery((current) => ({ ...current, [sensor.id]: { sensor: { id: result.device.id, name: result.device.name, type: result.device.type, confidence: 100 }, remainingSeconds: 0 } }));
    } catch (err) {
      updateSensor(sensor.id, { status: 'missing' });
      setDiscovery((current) => ({ ...current, [sensor.id]: { error: userFacingSensorError(err) } }));
    }
  }

  function pollSensor(sensorId: string, sessionId: number, startedAt: number, sensorName: string, roomId: string) {
    window.clearTimeout(timers.current[sensorId]);
    timers.current[sensorId] = window.setTimeout(async () => {
      try {
        const result = await api.senteroDiscoveredSensors(sessionId, devMode);
        const done = await applyCandidate(sensorId, sessionId, result, sensorName, roomId);
        if (!done && Date.now() - startedAt < SENSOR_DISCOVERY_SECONDS * 1000) {
          pollSensor(sensorId, sessionId, startedAt, sensorName, roomId);
        }
      } catch (err) {
        activeDiscoverySessions.current.delete(sessionId);
        void api.cancelSenteroSensorDiscovery(sessionId).catch(() => undefined);
        updateSensor(sensorId, { status: 'missing' });
        setDiscovery((current) => ({ ...current, [sensorId]: { error: err instanceof Error ? err.message : 'Sensor nicht gefunden.' } }));
      }
    }, 2000);
  }

  async function applyCandidate(sensorId: string, sessionId: number, result: { status: string; sensor?: { id: string; name: string; type: string; confidence: number } | null; remaining_seconds?: number }, sensorName: string, roomId: string) {
    const score = result.sensor?.confidence || 0;
    const found = Boolean(result.sensor && result.status === 'found' && score >= 50);
    const timedOut = result.remaining_seconds === 0 || result.status === 'not_found';
    setDiscovery((current) => ({ ...current, [sensorId]: { sensor: result.sensor || null, remainingSeconds: result.remaining_seconds } }));
    if (found && result.sensor) {
      const name = sensorName || result.sensor.name || 'Sensor';
      try {
        await api.registerSenteroSensor(result.sensor.id, { discovery_id: sessionId, name, room_id: roomId }, devMode);
        const test = await api.testSenteroSensorRole(sensorId);
        if (!test.ok) throw new Error(test.message || 'Sensor ist aktuell nicht erreichbar.');
        activeDiscoverySessions.current.delete(sessionId);
        updateSensor(sensorId, { status: 'connected', sessionId, score, sensorManagerId: result.sensor.id, name });
      } catch (err) {
        activeDiscoverySessions.current.delete(sessionId);
        void api.cancelSenteroSensorDiscovery(sessionId).catch(() => undefined);
        updateSensor(sensorId, { status: 'missing' });
        setDiscovery((current) => ({ ...current, [sensorId]: { ...(current[sensorId] || {}), error: userFacingSensorError(err) } }));
      }
      return true;
    }
    if (timedOut) {
      activeDiscoverySessions.current.delete(sessionId);
      void api.cancelSenteroSensorDiscovery(sessionId).catch(() => undefined);
      updateSensor(sensorId, { status: 'missing' });
      return true;
    }
    return false;
  }

  function skipSensor(sensor: SensorBinding) {
    if (sensor.sessionId) {
      activeDiscoverySessions.current.delete(sensor.sessionId);
      window.clearTimeout(timers.current[sensor.id]);
      void api.cancelSenteroSensorDiscovery(sensor.sessionId).catch(() => undefined);
    }
    updateSensor(sensor.id, { status: 'skipped' });
  }

  async function deleteSensor(sensor: SensorBinding) {
    if (sensor.sessionId) {
      activeDiscoverySessions.current.delete(sensor.sessionId);
      window.clearTimeout(timers.current[sensor.id]);
      void api.cancelSenteroSensorDiscovery(sensor.sessionId).catch(() => undefined);
    }
    const message = isPresenceBinding(sensor)
      ? 'Präsenzsensor wirklich entfernen?\n\nWenn der Sensor erreichbar ist, wird er zurückgesetzt. Falls das nicht möglich ist, kann er nur aus Sentero entfernt werden.'
      : 'Sensor aus Sentero entfernen?';
    if (!window.confirm(message)) return;
    try {
      await deleteSensorRoleWithLocalFallback(sensor);
      updateSensor(sensor.id, { status: 'idle', sessionId: undefined, score: undefined, sensorManagerId: undefined });
      setDiscovery((current) => ({ ...current, [sensor.id]: {} }));
      setLockedSensorPlan((current) => unlockSensorPlan(current, sensor));
    } catch (err) {
      setDiscovery((current) => ({
        ...current,
        [sensor.id]: { ...(current[sensor.id] || {}), error: err instanceof Error ? err.message : 'Sensor konnte nicht entfernt werden.' },
      }));
    }
  }

  async function deleteSensorRoleWithLocalFallback(sensor: SensorBinding) {
    try {
      return await api.deleteSenteroSensorRole(sensor.id);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Sensor konnte nicht entfernt werden.';
      const localOnly = window.confirm(
        `${message}\n\nNur aus Sentero entfernen?\n\nDer Sensor bleibt dann im Sensornetzwerk erhalten und muss bei Bedarf separat zurückgesetzt oder entfernt werden.`,
      );
      if (!localOnly) throw err;
      return api.deleteSenteroSensorRole(sensor.id, { localOnly: true });
    }
  }

  function addContact() {
    const nextErrors = [];
    if (!contactForm.name.trim()) nextErrors.push('Bitte geben Sie einen Namen ein.');
    if (!contactForm.relation.trim()) nextErrors.push('Bitte geben Sie die Beziehung ein.');
    const email = normalizeEmail(contactForm.email);
    if (!isValidEmail(email)) nextErrors.push('Bitte geben Sie eine gültige E-Mail-Adresse ein.');
    if (email && contacts.some((contact) => normalizeEmail(contact.email) === email)) nextErrors.push('Diese E-Mail-Adresse ist bereits hinterlegt.');
    if (nextErrors.length) {
      setErrors(nextErrors);
      return;
    }
    setContacts((current) => {
      const primary = contactForm.primary || current.length === 0;
      const existing = primary ? current.map((contact) => ({ ...contact, primary: false })) : current;
      return [...existing, { ...contactForm, email, channels: ['E-Mail'], primary, id: crypto.randomUUID() }];
    });
    setContactForm({ id: '', name: '', relation: 'Tochter', phone: '', email: '', channels: ['E-Mail'], primary: false });
    setContactFormOpen(false);
    setErrors([]);
  }

  return (
    <section className="sc-wizard">
      <WizardProgress step={step} />
      {errors.length > 0 && <div className="sc-form-errors" role="alert">{errors.map((error) => <p key={error}>{error}</p>)}</div>}
      <div className="sc-wizard-card">
        {step === 0 && <WelcomeStep />}
        {step === 1 && <NetworkStep status={networkStatus} choice={networkChoice} networks={wifiNetworks} wifiForm={wifiForm} busy={networkBusy} message={networkMessage} onChoice={setNetworkChoice} onWifiForm={setWifiForm} onRefresh={() => void refreshNetwork()} />}
        {step === 2 && <ProfileStep profile={profile} calculatedAge={calculatedAge} onChange={setProfile} />}
        {step === 3 && <RoomsStep selected={selectedRooms} customRooms={customRooms} sensorPlan={sensorPlan} lockedSensorPlan={lockedSensorPlan} customRoom={customRoom} onToggle={toggleRoom} onCustomChange={setCustomRoom} onCustomAdd={addCustomRoom} onToggleSensorType={toggleSensorType} />}
        {step === 4 && <SensorWizard sensors={sensorBindings} discovery={discovery} roomLabel={roomLabel} devMode={devMode} connected={connectedSensors} total={sensorBindings.length} presenceTransport={presenceTransport} onChange={updateSensor} onSearch={searchSensor} onDelete={(sensor) => void deleteSensor(sensor)} onSkip={skipSensor} />}
        {step === 5 && <ContactsStep contacts={contacts} form={contactForm} formOpen={contactFormOpen} onOpen={() => setContactFormOpen(true)} onClose={() => setContactFormOpen(false)} onFormChange={setContactForm} onAdd={addContact} onDelete={(id) => setContacts((current) => {
          const nextContacts = current.filter((contact) => contact.id !== id);
          if (nextContacts.length && !nextContacts.some((contact) => contact.primary)) {
            return nextContacts.map((contact, index) => ({ ...contact, primary: index === 0 }));
          }
          return nextContacts;
        })} />}
        {step === 6 && <NotificationStep value={notification} onChange={setNotification} />}
        {step === 7 && <SummaryStep profile={profile} age={calculatedAge} rooms={selectedRooms} roomLabel={roomLabel} contacts={contacts} sensors={connectedSensors} totalSensors={sensorBindings.length} notification={notification} confirmed={confirmed} onConfirm={setConfirmed} emailSetupRequired={emailSetupRequired} />}
      </div>
      <footer className="sc-wizard-actions">
        <button type="button" onClick={back} disabled={step === 0}><ArrowLeft size={20} /> Zurück</button>
        <button className="primary" type="button" onClick={() => void next()} disabled={networkBusy}>
          {step === 0 ? 'Einrichtung starten' : step === steps.length - 1 ? 'Einrichtung abschließen' : 'Weiter'}
          <ArrowRight size={20} />
        </button>
      </footer>
    </section>
  );
}

function WizardProgress({ step }: { step: number }) {
  return (
    <header className="sc-wizard-progress">
      <div className="sc-stepper-scroll" aria-label={`Schritt ${step + 1} von ${steps.length}`}>
        <ol className="sc-stepper">
          {steps.map((label, index) => {
            const state = index < step ? 'completed' : index === step ? 'current' : 'future';
            return (
              <li key={label} className={state}>
                <span aria-current={index === step ? 'step' : undefined}>{index + 1}</span>
              </li>
            );
          })}
        </ol>
      </div>
      <div className="sc-wizard-step-title">
        <p>Schritt {step + 1} von {steps.length}</p>
        <h2>{steps[step]}</h2>
      </div>
    </header>
  );
}

function WelcomeStep() {
  return (
    <section className="sc-wizard-welcome">
      <span className="sc-hero-illustration"><HeartHandshake size={58} /><ShieldCheck size={66} /></span>
      <h1>Willkommen bei Sentero</h1>
      <p>Sentero achtet leise im Hintergrund auf vertraute Tagesabläufe. Wenn etwas ungewöhnlich wirkt, werden vertraute Personen behutsam informiert.</p>
      <p>Die Einrichtung dauert nur wenige Minuten und kann später jederzeit angepasst werden.</p>
    </section>
  );
}

function NetworkStep({ status, choice, networks, wifiForm, busy, message, onChoice, onWifiForm, onRefresh }: {
  status: NetworkStatus | null;
  choice: 'wifi' | 'cellular' | 'ethernet';
  networks: WifiNetwork[];
  wifiForm: { ssid: string; password: string };
  busy: boolean;
  message: string;
  onChoice: (choice: 'wifi' | 'cellular' | 'ethernet') => void;
  onWifiForm: (value: { ssid: string; password: string }) => void;
  onRefresh: () => void;
}) {
  const capabilities = status?.capabilities || { ethernet: true, wifi: true, cellular: false, wifi_ap: false };
  const isConnected = Boolean(status?.network_ready);
  const connectionLabel = status?.active_connection === 'wifi'
    ? 'WLAN'
    : status?.active_connection === 'ethernet'
      ? 'Ethernet'
      : status?.active_connection === 'cellular'
        ? 'Mobilfunk'
        : 'Internet';
  return (
    <section className="sc-network-step">
      <div className="sc-wizard-section-copy">
        <h3>Internetverbindung</h3>
        <p>Sentero benötigt eine Internetverbindung, um Benachrichtigungen und Updates zu senden. Ihre Sensordaten bleiben weiterhin lokal auf der Sentero-Box.</p>
      </div>
      {isConnected && (
        <div className="sc-network-ready-card">
          <CheckCircle2 size={28} />
          <div>
            <strong>Internet ist bereits verbunden</strong>
            <span>{connectionLabel}{status?.ip_address ? ` · ${status.ip_address}` : ''}</span>
          </div>
        </div>
      )}
      <div className="sc-network-choice-grid">
        {capabilities.wifi && (
          <button type="button" className={choice === 'wifi' ? 'active' : ''} onClick={() => onChoice('wifi')}>
            <Wifi size={22} /><strong>WLAN verwenden</strong>
          </button>
        )}
        {capabilities.cellular && (
          <button type="button" className={choice === 'cellular' ? 'active' : ''} onClick={() => onChoice('cellular')}>
            <Smartphone size={22} /><strong>Mobilfunk verwenden</strong>
          </button>
        )}
        {capabilities.ethernet && (
          <button type="button" className={choice === 'ethernet' ? 'active' : ''} onClick={() => onChoice('ethernet')}>
            <Cable size={22} /><strong>Ethernet verbunden</strong>
          </button>
        )}
      </div>
      {choice === 'wifi' && !isConnected && (
        <div className="sc-network-wifi-form">
          <div className="sc-network-card-head">
            <div>
              <h3>WLAN auswählen</h3>
              <p>Wählen Sie das Netzwerk der Wohnung aus.</p>
            </div>
            <button type="button" onClick={onRefresh} disabled={busy}>Aktualisieren</button>
          </div>
          <div className="sc-wifi-list">
            {networks.map((network) => (
              <button key={network.ssid} type="button" className={wifiForm.ssid === network.ssid ? 'active' : ''} onClick={() => onWifiForm({ ...wifiForm, ssid: network.ssid })}>
                <span>{network.ssid}</span>
                <small>{network.signal}% {network.secured ? 'gesichert' : ''}</small>
              </button>
            ))}
            {networks.length === 0 && <p className="sc-muted-note">Noch keine WLAN-Netze gefunden.</p>}
          </div>
          <label>
            WLAN-Name
            <input value={wifiForm.ssid} onChange={(event) => onWifiForm({ ...wifiForm, ssid: event.target.value })} placeholder="WLAN-Name eingeben" />
          </label>
          <label>
            WLAN-Passwort
            <input type="password" value={wifiForm.password} onChange={(event) => onWifiForm({ ...wifiForm, password: event.target.value })} placeholder="Passwort eingeben" />
          </label>
        </div>
      )}
      {choice === 'wifi' && isConnected && (
        <p className="sc-network-note">Wenn Sie das WLAN ändern möchten, trennen Sie die aktuelle Verbindung oder aktualisieren Sie die Netzwerkeinstellungen später in den Einstellungen.</p>
      )}
      {choice === 'cellular' && !isConnected && (
        <p className="sc-network-note">Sentero verwendet die Mobilfunkverbindung nur für die Box. Die Box wird dadurch kein WLAN-Router für andere Geräte.</p>
      )}
      {choice === 'ethernet' && !isConnected && (
        <p className="sc-network-note">Wenn das Netzwerkkabel verbunden ist, prüft Sentero die Internetverbindung automatisch.</p>
      )}
      {message && <p className="sc-network-note">{message}</p>}
      {status && <p className="sc-muted-note">{status.message}</p>}
    </section>
  );
}

function ProfileStep({ profile, calculatedAge, onChange }: { profile: Profile; calculatedAge: number | null; onChange: (profile: Profile) => void }) {
  return (
    <section className="sc-form-grid">
      <label>Name der betreuten Person<input required value={profile.name} onChange={(event) => onChange({ ...profile, name: event.target.value })} /></label>
      <label>
        Geburtsjahr
        <input inputMode="numeric" maxLength={4} value={profile.birthYear} onChange={(event) => onChange({ ...profile, birthYear: event.target.value.replace(/\D+/g, '').slice(0, 4) })} placeholder="1945" />
      </label>
      <label className="sc-form-wide">
        Besondere Hinweise (optional)
        <textarea value={profile.notes} onChange={(event) => onChange({ ...profile, notes: event.target.value })} placeholder="z.B. Eingeschränkte Mobilität, Rollator, regelmäßige Arzttermine ..." />
        <small>Diese Informationen helfen Sentero, Auffälligkeiten besser einzuordnen. Zum Beispiel eingeschränkte Mobilität, Rollator, Hörgerät, regelmäßige Arzttermine, Demenz, Parkinson oder Sehbeeinträchtigung.</small>
      </label>
      {calculatedAge && <p className="sc-muted-note">Das Alter wird intern automatisch berechnet: {calculatedAge} Jahre.</p>}
    </section>
  );
}

function RoomsStep({ selected, customRooms, sensorPlan, lockedSensorPlan, customRoom, onToggle, onCustomChange, onCustomAdd, onToggleSensorType }: {
  selected: string[];
  customRooms: Record<string, string>;
  sensorPlan: Record<string, SensorPlan>;
  lockedSensorPlan: Record<string, SensorPlan>;
  customRoom: string;
  onToggle: (id: string) => void;
  onCustomChange: (value: string) => void;
  onCustomAdd: () => void;
  onToggleSensorType: (roomId: string, type: keyof SensorPlan) => void;
}) {
  const visibleRooms = [...roomOptions, ...Object.entries(customRooms).map(([id, label]) => ({ id, label, door: false }))];
  return (
    <section className="sc-room-select">
      <p>Wählen Sie die Räume in der Wohnung.</p>
      <small className="sc-muted-note">Aktive Sensoren werden in diesem Raum verwendet.</small>
      <div className="sc-room-choice-grid">
        {visibleRooms.map((room) => {
          const active = selected.includes(room.id);
          const plan = sensorPlan[room.id] || emptySensorPlan();
          const locked = lockedSensorPlan[room.id] || emptySensorPlan();
          const roomLocked = roomHasLockedSensor(lockedSensorPlan, room.id);
          return (
            <div key={room.id} className={`sc-room-choice-card ${active ? 'active' : ''}${roomLocked ? ' has-bound-sensor' : ''}`}>
              <button type="button" onClick={() => onToggle(room.id)} disabled={active && roomLocked}>
                <strong>{room.label}</strong>
                {active && roomLocked && <small>Sensor verbunden</small>}
              </button>
              {active && (
                <div className="sc-room-sensor-toggles">
                  <label className={`${plan.motion ? 'active' : ''}${locked.motion ? ' locked' : ''}`}>
                    <input type="checkbox" checked={plan.motion} disabled={locked.motion} onChange={() => onToggleSensorType(room.id, 'motion')} />
                    <i aria-hidden="true" /> <span>Präsenzsensor</span>
                  </label>
                  <label className={`${plan.door ? 'active' : ''}${locked.door ? ' locked' : ''}`}>
                    <input type="checkbox" checked={plan.door} disabled={locked.door} onChange={() => onToggleSensorType(room.id, 'door')} />
                    <i aria-hidden="true" /> <span>Türsensor</span>
                  </label>
                </div>
              )}
            </div>
          );
        })}
      </div>
      <div className="sc-inline-add">
        <input value={customRoom} onChange={(event) => onCustomChange(event.target.value)} placeholder="Eigenen Raum hinzufügen" />
        <button type="button" onClick={onCustomAdd}><Plus size={20} /> Hinzufügen</button>
      </div>
      <strong>{selected.length} Räume ausgewählt</strong>
      <div className="sc-wizard-section-copy">
        <h3>Verbrauchszähler</h3>
        <p>Optional: Verbrauchsdaten können als zusätzlicher Hinweis auf Alltagsaktivität genutzt werden.</p>
      </div>
      <div className="sc-meter-choice-card">
        <div className="sc-meter-choice-head">
          <strong>Haushalt</strong>
          <small>Strom</small>
        </div>
        <div className="sc-room-sensor-toggles">
          <label className={`${(sensorPlan.home || emptySensorPlan()).electricity ? 'active' : ''}${lockedSensorPlan.home?.electricity ? ' locked' : ''}`}>
            <input type="checkbox" checked={(sensorPlan.home || emptySensorPlan()).electricity} disabled={lockedSensorPlan.home?.electricity} onChange={() => onToggleSensorType('home', 'electricity')} />
            <i aria-hidden="true" /> <span>Stromzähler{lockedSensorPlan.home?.electricity}</span>
          </label>
          <label style={{ display: 'none' }} className={`${(sensorPlan.home || emptySensorPlan()).water ? 'active' : ''}${lockedSensorPlan.home?.water ? ' locked' : ''}`}>
            <input type="checkbox" checked={(sensorPlan.home || emptySensorPlan()).water} disabled={lockedSensorPlan.home?.water} onChange={() => onToggleSensorType('home', 'water')} />
            <i aria-hidden="true" /> <span>Wasserzähler{lockedSensorPlan.home?.water}</span>
          </label>
          <label style={{ display: 'none' }} className={`${(sensorPlan.home || emptySensorPlan()).gas ? 'active' : ''}${lockedSensorPlan.home?.gas ? ' locked' : ''}`}>
            <input type="checkbox" checked={(sensorPlan.home || emptySensorPlan()).gas} disabled={lockedSensorPlan.home?.gas} onChange={() => onToggleSensorType('home', 'gas')} />
            <i aria-hidden="true" /> <span>Gaszähler{lockedSensorPlan.home?.gas}</span>
          </label>
        </div>
      </div>
    </section>
  );
}

function ContactsStep({ contacts, form, formOpen, onOpen, onClose, onFormChange, onAdd, onDelete }: { contacts: Contact[]; form: Contact; formOpen: boolean; onOpen: () => void; onClose: () => void; onFormChange: (contact: Contact) => void; onAdd: () => void; onDelete: (id: string) => void }) {
  return (
    <section className="sc-contacts-step">
      <div className="sc-contacts-wizard-head">
        <div>
          <p>Wer soll benachrichtigt werden?</p>
          <small>Hinterlegen Sie mindestens eine Vertrauensperson, die bei wichtigen Hinweisen oder Warnungen informiert werden kann.</small>
        </div>
        <button className="sc-round-add" type="button" onClick={onOpen} aria-label="Person hinzufügen"><Plus size={28} /></button>
      </div>
      {formOpen && (
        <div className="sc-contact-form-card">
          <div className="sc-contact-form-head">
            <strong>Person hinzufügen</strong>
            <button type="button" onClick={onClose} aria-label="Formular schließen"><X size={20} /></button>
          </div>
          <div className="sc-form-grid">
            <label>Name *<input value={form.name} onChange={(event) => onFormChange({ ...form, name: event.target.value })} /></label>
            <label>Beziehung *<input value={form.relation} onChange={(event) => onFormChange({ ...form, relation: event.target.value })} /></label>
            <label className="sc-form-wide">E-Mail-Adresse *<input type="email" value={form.email} onChange={(event) => onFormChange({ ...form, email: event.target.value })} /></label>
          </div>
          <label className={`sc-large-check${form.primary ? ' active' : ''}`}><span>Diese Person als Hauptansprechpartner verwenden</span><input type="checkbox" checked={form.primary} onChange={(event) => onFormChange({ ...form, primary: event.target.checked })} /><i aria-hidden="true" /></label>
          <button className="sc-primary-button" type="button" onClick={onAdd}><Plus size={20} /> Person hinzufügen</button>
        </div>
      )}
      <div className="sc-contact-list-editor">
        {contacts.length === 0 && <p className="sc-muted-note">Noch keine vertraute Person hinterlegt.</p>}
        {contacts.map((contact) => <div key={contact.id} className={contact.primary ? 'primary' : ''}><span className="sc-avatar">{contact.name[0]}</span><strong>{contact.primary ? '✓ Hauptansprechpartner eingerichtet' : contact.name}</strong><small>{contact.primary ? `${contact.name}\n${contact.email}\n${contact.relation}` : `${contact.email} · ${contact.relation}`}</small><button type="button" onClick={() => onDelete(contact.id)} aria-label={`${contact.name} löschen`}><Trash2 size={18} /></button></div>)}
      </div>
    </section>
  );
}

function NotificationStep({ value, onChange }: { value: NotificationPreferences; onChange: (value: NotificationPreferences) => void }) {
  const options = [
    {
      key: 'anomalies' as const,
      title: 'Über ungewöhnliche Veränderungen informieren',
      description: 'Wenn der Tagesablauf deutlich vom Gewohnten abweicht.',
    },
    {
      key: 'critical' as const,
      title: 'Wichtige Warnungen sofort senden',
      description: 'Wenn Sentero eine potenziell kritische Situation erkennt.',
    },
    {
      key: 'daily_summary' as const,
      title: 'Tägliche Zusammenfassung erhalten',
      description: 'Ein kurzer Überblick über den Tag.',
    },
  ];
  return (
    <section className="sc-notification-step">
      <div className="sc-wizard-section-copy">
        <h3>Benachrichtigungen</h3>
        <p>Legen Sie fest, wie Sentero Sie über wichtige Veränderungen informiert.</p>
      </div>
      <div className="sc-preference-list">
        {options.map((option) => {
          const active = value[option.key];
          return (
            <label key={option.key} className={`sc-notification-preference${active ? ' active' : ''}`}>
              <span>
                <strong>{option.title}</strong>
                <small>{option.description}</small>
              </span>
              <input type="checkbox" checked={active} onChange={(event) => onChange({ ...value, [option.key]: event.target.checked })} />
              <i aria-hidden="true" />
            </label>
          );
        })}
      </div>
    </section>
  );
}

function SummaryStep({ profile, age, rooms, roomLabel, contacts, sensors, totalSensors, notification, confirmed, onConfirm, emailSetupRequired }: { profile: Profile; age: number | null; rooms: string[]; roomLabel: (room: string) => string; contacts: Contact[]; sensors: number; totalSensors: number; notification: NotificationPreferences; confirmed: boolean; onConfirm: (value: boolean) => void; emailSetupRequired: boolean }) {
  const primary = contacts.find((contact) => contact.primary) || contacts[0];
  const notificationSummary = [
    notification.anomalies ? 'Ungewöhnliche Veränderungen' : '',
    notification.critical ? 'Wichtige Warnungen' : '',
    notification.daily_summary ? 'Tägliche Zusammenfassung' : '',
  ].filter(Boolean).join(', ') || 'Keine Benachrichtigungen ausgewählt';
  return (
    <section className="sc-summary-step">
      {emailSetupRequired && (
        <div className="sc-setup-blocker">
          <h3>Fast geschafft.</h3>
          <p>Damit Sentero wichtige Hinweise versenden kann, richten Sie jetzt den E-Mail-Versand ein.</p>
          <button type="button" onClick={() => window.location.assign('/sentero/settings/notifications')}><Mail size={18} /> E-Mail-Versand einrichten</button>
        </div>
      )}
      <div className="sc-summary-card">
        <UserRound size={28} /><strong>{profile.name}</strong><span>{age ? `${age} Jahre` : 'Geburtsjahr offen'}</span>
      </div>
      <div className="sc-summary-grid">
        <p><strong>Räume</strong>{rooms.map((room) => roomLabel(room)).join(', ')}</p>
        <p><strong>Sensoren</strong>{sensors} von {totalSensors} verbunden</p>
        <p><strong>Hauptansprechpartner</strong>{primary ? `${primary.name} (${primary.email})` : 'Noch nicht eingerichtet'}</p>
        <p><strong>Benachrichtigungen</strong>{notificationSummary}</p>
      </div>
      <label className={`sc-large-check${confirmed ? ' active' : ''}`}><span>Ich bestätige, dass alle Angaben korrekt sind.</span><input type="checkbox" checked={confirmed} onChange={(event) => onConfirm(event.target.checked)} /><i aria-hidden="true" /></label>
    </section>
  );
}


function buildBindings(roomIds: string[], sensorPlan: Record<string, SensorPlan>, customRooms: Record<string, string>, current: SensorBinding[]) {
  const byId = Object.fromEntries(current.map((sensor) => [sensor.id, sensor]));
  return roomIds.flatMap((roomId) => {
    const label = customRooms[roomId] || baseRoomLabel[roomId] || roomId;
    const plan = sensorPlan[roomId] || emptySensorPlan();
    const bindings: SensorBinding[] = [];
    if (plan.motion) {
      const motionId = `${roomId}_presence`;
      bindings.push(byId[motionId] || { id: motionId, roomId, type: 'motion', sensorId: '', name: `${label} Präsenz`, status: 'idle' });
    }
    if (plan.door) {
      const doorId = `${roomId}_door`;
      bindings.push(byId[doorId] || { id: doorId, roomId, type: 'door', sensorId: '', name: `${label} Türkontakt`, status: 'idle' });
    }
    if (roomId === 'home' && plan.electricity) bindings.push(byId.home_energy || { id: 'home_energy', roomId, type: 'electricity_meter', sensorId: '', name: 'Stromzähler', status: 'idle' });
    if (roomId === 'home' && plan.water) bindings.push(byId.home_water || { id: 'home_water', roomId, type: 'water_meter', sensorId: '', name: 'Wasserzähler', status: 'idle' });
    if (roomId === 'home' && plan.gas) bindings.push(byId.home_gas || { id: 'home_gas', roomId, type: 'gas_meter', sensorId: '', name: 'Gaszähler', status: 'idle' });
    return bindings;
  });
}

function mergeExistingSensorBindings(current: SensorBinding[], roles: SenteroSensorRole[], customRooms: Record<string, string>) {
  const byId = Object.fromEntries(current.map((sensor) => [sensor.id, sensor]));
  for (const role of roles) {
    const type = sensorTypeFromRole(role.role);
    const roomId = role.room || roomFromRole(role.role);
    if (!type || !roomId) continue;
    const label = customRooms[roomId] || baseRoomLabel[roomId] || roomId;
    byId[role.role] = {
      ...(byId[role.role] || {
        id: role.role,
        roomId,
        type,
        sensorId: '',
        name: defaultSensorName(label, type),
        status: 'idle' as const,
      }),
      roomId,
      type,
      name: role.label || byId[role.role]?.name || defaultSensorName(label, type),
      status: role.configured ? 'connected' : byId[role.role]?.status || 'idle',
    };
  }
  return Object.values(byId);
}

function mergeSensorPlan(current: Record<string, SensorPlan>, roles: SenteroSensorRole[]) {
  const next = { ...current };
  for (const role of roles) {
    const type = sensorTypeFromRole(role.role);
    const roomId = role.room || roomFromRole(role.role);
    if (!type || !roomId) continue;
    const plan = next[roomId] || emptySensorPlan();
    next[roomId] = { ...plan, [sensorPlanKey(type)]: true };
  }
  return next;
}

function lockedPlanFromRoles(roles: SenteroSensorRole[]) {
  const plan: Record<string, SensorPlan> = {};
  for (const role of roles) {
    if (!role.configured) continue;
    const type = sensorTypeFromRole(role.role);
    const roomId = role.room || roomFromRole(role.role);
    if (!type || !roomId) continue;
    plan[roomId] = { ...(plan[roomId] || emptySensorPlan()), [sensorPlanKey(type)]: true };
  }
  return plan;
}

function roomHasLockedSensor(lockedSensorPlan: Record<string, SensorPlan>, roomId: string) {
  const locked = lockedSensorPlan[roomId];
  return Boolean(locked?.motion || locked?.door || locked?.electricity || locked?.water || locked?.gas);
}

function unlockSensorPlan(current: Record<string, SensorPlan>, sensor: SensorBinding) {
  const key = sensorPlanKey(sensor.type);
  const plan = current[sensor.roomId];
  if (!plan || !key) return current;
  return { ...current, [sensor.roomId]: { ...plan, [key]: false } };
}

function sensorTypeFromRole(role: string): SensorBinding['type'] | null {
  if (role.endsWith('_presence') || role.endsWith('_motion')) return 'motion';
  if (role.endsWith('_door') || role.endsWith('_contact')) return 'door';
  if (role.endsWith('_energy') || role.endsWith('_power')) return 'electricity_meter';
  if (role.endsWith('_water')) return 'water_meter';
  if (role.endsWith('_gas')) return 'gas_meter';
  return null;
}

function roomFromRole(role: string) {
  return role.replace(/_(presence|motion|door|contact|energy|power|water|gas)$/, '');
}

function defaultSensorName(roomLabel: string, type: SensorBinding['type']) {
  if (type === 'motion') return `${roomLabel} Präsenz`;
  if (type === 'door') return `${roomLabel} Türkontakt`;
  if (type === 'electricity_meter') return 'Stromzähler';
  if (type === 'water_meter') return 'Wasserzähler';
  return 'Gaszähler';
}

function isPresenceBinding(sensor: SensorBinding) {
  const type = String(sensor.type || '').toLowerCase();
  const id = String(sensor.id || '').toLowerCase();
  return type === 'motion' || id.endsWith('_presence') || id.endsWith('_motion');
}

async function waitForPresenceSensor(): Promise<SenteroEsp32DiscoverySensor> {
  const startedAt = Date.now();
  while (Date.now() - startedAt < PRESENCE_DISCOVERY_TIMEOUT_MS) {
    const status = await api.senteroPresenceDiscovered();
    const sensor = status.pending[0];
    if (sensor) return sensor;
    await wait(600);
  }
  throw new Error('Präsenzsensor wurde noch nicht gefunden.');
}

function wait(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function uniqueValues(values: string[]) {
  return values.filter((value, index) => value && values.indexOf(value) === index);
}

function selectedRoomsWithSensors(roomIds: string[], sensorPlan: Record<string, SensorPlan>) {
  const selected = roomIds.filter((roomId) => {
    const plan = sensorPlan[roomId] || emptySensorPlan();
    return plan.motion || plan.door || plan.electricity || plan.water || plan.gas;
  });
  const homePlan = sensorPlan.home || emptySensorPlan();
  return homePlan.electricity || homePlan.water || homePlan.gas ? uniqueValues([...selected, 'home']) : selected;
}

function ageFromBirthYear(value: string) {
  const year = Number.parseInt(value, 10);
  const currentYear = new Date().getFullYear();
  if (!Number.isFinite(year) || year < 1900 || year > currentYear) return null;
  return currentYear - year;
}

function validBirthYear(value: string) {
  return ageFromBirthYear(value) !== null;
}

function emptySensorPlan(): SensorPlan {
  return { motion: false, door: false, electricity: false, water: false, gas: false };
}

function sensorTypeForDiscovery(type: SensorBinding['type']) {
  if (type === 'door') return 'door_contact';
  if (type === 'electricity_meter') return 'electricity_meter';
  if (type === 'water_meter') return 'water_meter';
  if (type === 'gas_meter') return 'gas_meter';
  return 'presence_sensor';
}

function userFacingSensorError(err: unknown) {
  const message = err instanceof Error ? err.message : '';
  if (message === 'wifi_sensor_setup_disabled') return 'Kein Sensor gefunden. Bitte versetzen Sie den Sensor in den Verbindungsmodus und versuchen Sie es erneut.';
  return 'Kein Sensor gefunden. Bitte versetzen Sie den Sensor in den Verbindungsmodus und versuchen Sie es erneut.';
}

function sensorPlanKey(type: SensorBinding['type']): keyof SensorPlan {
  if (type === 'electricity_meter') return 'electricity';
  if (type === 'water_meter') return 'water';
  if (type === 'gas_meter') return 'gas';
  return type;
}

function normalizeEmail(value: string) {
  return value.trim().toLowerCase();
}

function isValidEmail(value: string) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value);
}
