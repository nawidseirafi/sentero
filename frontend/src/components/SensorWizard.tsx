import { AlertTriangle, Check, Loader2, Search, ShieldCheck, Trash2 } from 'lucide-react';
import type { SenteroDiscoveredSensor } from '@shared/api/client';
import { SetupWifiQr } from './SetupWifiQr';

export type SensorBinding = {
  id: string;
  roomId: string;
  type: 'motion' | 'door' | 'smoke_detector' | 'electricity_meter' | 'water_meter' | 'gas_meter';
  sensorId: string;
  name: string;
  status: 'idle' | 'searching' | 'connected' | 'missing' | 'skipped';
  sessionId?: number;
  score?: number;
  sensorManagerId?: string;
};

export type SensorDiscoveryState = {
  sensor?: SenteroDiscoveredSensor | null;
  device?: SenteroDiscoveredSensor | null;
  devices?: SenteroDiscoveredSensor[];
  status?: string;
  detectedType?: string | null;
  requestedType?: string | null;
  remainingSeconds?: number;
  error?: string;
};

type Props = {
  sensors: SensorBinding[];
  discovery: Record<string, SensorDiscoveryState>;
  devMode: boolean;
  connected: number;
  total: number;
  presenceTransport?: 'zigbee' | 'wifi_esphome';
  roomLabel: (roomId: string) => string;
  onChange: (id: string, patch: Partial<SensorBinding>) => void;
  onSearch: (sensor: SensorBinding) => void;
  onDelete: (sensor: SensorBinding) => void;
  onSkip: (sensor: SensorBinding) => void;
  onUseDevice: (sensor: SensorBinding, device: SenteroDiscoveredSensor) => void;
  onUseAsDetected: (sensor: SensorBinding, device: SenteroDiscoveredSensor) => void;
  onIgnoreDevice: (sensor: SensorBinding, device: SenteroDiscoveredSensor) => void;
  onRemoveDevice: (sensor: SensorBinding, device: SenteroDiscoveredSensor) => void;
};

export function SensorWizard({ sensors, discovery, devMode, connected, total, presenceTransport = 'zigbee', roomLabel, onChange, onSearch, onDelete, onSkip, onUseDevice, onUseAsDetected, onIgnoreDevice, onRemoveDevice }: Props) {
  const grouped = sensors.reduce<Record<string, SensorBinding[]>>((acc, sensor) => {
    acc[sensor.roomId] = [...(acc[sensor.roomId] || []), sensor];
    return acc;
  }, {});
  const allConnected = total > 0 && connected >= total;
  const progressLabel = total === 1 ? 'Sensor verbunden' : 'Sensoren verbunden';

  return (
    <section className="sc-sensor-step">
      <div className="sc-zigbee-intro">
        <span className="sc-zigbee-intro-icon"><ShieldCheck size={24} /></span>
        <div className="sc-zigbee-intro-copy">
          <div className="sc-zigbee-intro-title">
            <h3>Sensoren verbinden</h3>
            <p>Starten Sie die Suche und verbinden Sie jeden Sensor im passenden Raum.</p>
          </div>
          <div className="sc-zigbee-intro-notes">
            <span>Versetzen Sie den jeweiligen Sensor nach dem Start der Suche in den Verbindungsmodus.</span>
            <span>Sentero ordnet gefundene Sensoren dem ausgewählten Raum zu.</span>
          </div>
        </div>
        <div className={`sc-zigbee-progress ${allConnected ? 'complete' : ''}`}>
          <strong>{connected}/{total}</strong>
          <span>{progressLabel}</span>
        </div>
      </div>

      {Object.entries(grouped).map(([roomId, items]) => (
        <article key={roomId} className="sc-sensor-room">
          <h3>{roomLabel(roomId)}</h3>
          {items.map((sensor) => (
            <SensorSetupCard
              key={sensor.id}
              sensor={sensor}
              state={discovery[sensor.id]}
              devMode={devMode}
              presenceTransport={presenceTransport}
              onChange={onChange}
              onSearch={onSearch}
              onDelete={onDelete}
              onSkip={onSkip}
              onUseDevice={onUseDevice}
              onUseAsDetected={onUseAsDetected}
              onIgnoreDevice={onIgnoreDevice}
              onRemoveDevice={onRemoveDevice}
            />
          ))}
        </article>
      ))}
    </section>
  );
}

function SensorSetupCard({ sensor, state, devMode, presenceTransport, onChange, onSearch, onDelete, onSkip, onUseDevice, onUseAsDetected, onIgnoreDevice, onRemoveDevice }: {
  sensor: SensorBinding;
  state?: SensorDiscoveryState;
  devMode: boolean;
  presenceTransport: 'zigbee' | 'wifi_esphome';
  onChange: (id: string, patch: Partial<SensorBinding>) => void;
  onSearch: (sensor: SensorBinding) => void;
  onDelete: (sensor: SensorBinding) => void;
  onSkip: (sensor: SensorBinding) => void;
  onUseDevice: (sensor: SensorBinding, device: SenteroDiscoveredSensor) => void;
  onUseAsDetected: (sensor: SensorBinding, device: SenteroDiscoveredSensor) => void;
  onIgnoreDevice: (sensor: SensorBinding, device: SenteroDiscoveredSensor) => void;
  onRemoveDevice: (sensor: SensorBinding, device: SenteroDiscoveredSensor) => void;
}) {
  const label = sensorLabel(sensor);
  const help = sensorHelp(sensor);
  const showWifiPresenceSetup = isPresenceBinding(sensor) && presenceTransport === 'wifi_esphome' && sensor.status !== 'connected';
  const isEcoTracker = sensor.type === 'electricity_meter';

  return (
    <div className={`sc-sensor-row ${sensor.status === 'connected' ? 'is-connected' : ''}`}>
      <div className="sc-sensor-main">
        <span className="sc-sensor-kind"><ShieldCheck size={20} /> {label}</span>
        <strong>{sensor.name || label}</strong>
        <small>{help}</small>
        {showWifiPresenceSetup && (
          <div className="sc-sensor-preflight">
            <SetupWifiQr compact details={false} />
            <span>Setup-Hotspot scannen, Captive Portal öffnen und den Sensor mit Ihrem Heimnetz verbinden. Danach kann Sentero ihn hier finden.</span>
          </div>
        )}
        <input
          value={isEcoTracker ? sensor.sensorId : sensor.name}
          onChange={(event) => onChange(sensor.id, isEcoTracker ? { sensorId: event.target.value } : { name: event.target.value })}
          placeholder={isEcoTracker ? 'EcoTracker IP, z.B. 192.168.1.42' : 'Sensorname'}
          disabled={sensor.status === 'connected'}
        />
      </div>
      <div className="sc-sensor-side">
        <SensorStatus status={sensor.status} remainingSeconds={state?.remainingSeconds} />
        <div className="sc-sensor-buttons">
          <button className="primary" type="button" onClick={() => void onSearch(sensor)} disabled={sensor.status === 'searching' || sensor.status === 'connected'}>
            <Search size={19} /> {sensor.status === 'connected' ? 'Verbunden' : isEcoTracker ? 'EcoTracker verbinden' : 'Sensor suchen'}
          </button>
          {sensor.status === 'connected' && (
            <button className="danger" type="button" onClick={() => onDelete(sensor)}>
              <Trash2 size={18} /> Entfernen
            </button>
          )}
          <button className="secondary" type="button" onClick={() => onSkip(sensor)} disabled={sensor.status === 'connected'}>Überspringen</button>
        </div>
      </div>
      <DiscoveryDecision sensor={sensor} state={state} onUseDevice={onUseDevice} onUseAsDetected={onUseAsDetected} onIgnoreDevice={onIgnoreDevice} onRemoveDevice={onRemoveDevice} />
      {state?.error && sensor.status !== 'missing' && <p className="sc-sensor-error">{state.error}</p>}
      {devMode && <code className="sc-dev-line">Score {sensor.score ?? state?.sensor?.confidence ?? '-'} · Rest {state?.remainingSeconds ?? '-'}s</code>}
    </div>
  );
}

function DiscoveryDecision({ sensor, state, onUseDevice, onUseAsDetected, onIgnoreDevice, onRemoveDevice }: {
  sensor: SensorBinding;
  state?: SensorDiscoveryState;
  onUseDevice: (sensor: SensorBinding, device: SenteroDiscoveredSensor) => void;
  onUseAsDetected: (sensor: SensorBinding, device: SenteroDiscoveredSensor) => void;
  onIgnoreDevice: (sensor: SensorBinding, device: SenteroDiscoveredSensor) => void;
  onRemoveDevice: (sensor: SensorBinding, device: SenteroDiscoveredSensor) => void;
}) {
  const status = state?.status;
  const device = state?.device || state?.sensor || state?.devices?.[0];
  if (!device) return null;
  const meta = [device.manufacturer, device.model].filter(Boolean).join(' · ');
  if (status === 'existing_device_found') {
    const multiple = (state?.devices?.length || 0) > 1;
    return (
      <div className="sc-sensor-decision">
        <AlertTriangle size={18} />
        <div>
          <strong>{multiple ? `Bereits verbundene ${sensorLabel(sensor)} gefunden.` : `Bereits verbundener ${sensorLabel(sensor)} gefunden.`}</strong>
          {meta && <small>{meta}</small>}
          <div className="sc-sensor-buttons">
            {(state?.devices || [device]).map((item) => (
              <button className="primary" type="button" key={item.id} onClick={() => onUseDevice(sensor, item)}>
                <Check size={18} /> Verwenden{multiple ? `: ${item.name}` : ''}
              </button>
            ))}
          </div>
        </div>
      </div>
    );
  }
  if (status === 'wrong_type_found') {
    return (
      <div className="sc-sensor-decision">
        <AlertTriangle size={18} />
        <div>
          <strong>Anderes Gerät erkannt</strong>
          <span>Es wurde ein {sensorTypeLabel(device.detected_type || state?.detectedType || device.type)} erkannt. Er wurde nicht als {sensorLabel(sensor)} hinzugefügt.</span>
          {meta && <small>{meta}</small>}
          <div className="sc-sensor-buttons">
            <button className="primary" type="button" onClick={() => onUseAsDetected(sensor, device)}><Check size={18} /> Als {sensorTypeLabel(device.detected_type || device.type)} einrichten</button>
            <button className="secondary" type="button" onClick={() => onIgnoreDevice(sensor, device)}>Später zuordnen</button>
          </div>
        </div>
      </div>
    );
  }
  if (status === 'unsupported_device_found') {
    return (
      <div className="sc-sensor-decision">
        <AlertTriangle size={18} />
        <div>
          <strong>Nicht unterstütztes Gerät erkannt</strong>
          <span>Dieses Gerät kann von Sentero derzeit nicht verwendet werden.</span>
          {meta && <small>{meta}</small>}
          <div className="sc-sensor-buttons">
            <button className="danger" type="button" onClick={() => onRemoveDevice(sensor, device)}><Trash2 size={18} /> Gerät entfernen</button>
            <button className="secondary" type="button" onClick={() => onIgnoreDevice(sensor, device)}>Gerät behalten</button>
          </div>
        </div>
      </div>
    );
  }
  return null;
}

function SensorStatus({ status, remainingSeconds }: { status: SensorBinding['status']; remainingSeconds?: number }) {
  if (status === 'searching') return <span className="sc-sensor-state searching"><Loader2 size={18} /> Suche läuft ...{typeof remainingSeconds === 'number' ? ` · ${Math.ceil(remainingSeconds)}s` : ''}</span>;
  if (status === 'connected') return <span className="sc-sensor-state connected"><Check size={18} /> Verbunden</span>;
  if (status === 'missing') return <span className="sc-sensor-state missing">Kein Sensor gefunden. Bitte versetzen Sie den Sensor in den Verbindungsmodus und versuchen Sie es erneut.</span>;
  if (status === 'skipped') return <span className="sc-sensor-state skipped">Übersprungen</span>;
  return <span className="sc-sensor-state idle">Bereit</span>;
}

function isPresenceBinding(sensor: SensorBinding) {
  const type = String(sensor.type || '').toLowerCase();
  const id = String(sensor.id || '').toLowerCase();
  return type === 'motion' || id.endsWith('_presence') || id.endsWith('_motion');
}

function sensorLabel(sensor: SensorBinding) {
  if (sensor.type === 'door') return 'Türsensor';
  if (sensor.type === 'smoke_detector') return 'Rauchmelder';
  if (sensor.type === 'electricity_meter') return 'everHome EcoTracker IR';
  if (sensor.type === 'water_meter') return 'Wasserzähler';
  if (sensor.type === 'gas_meter') return 'Gaszähler';
  return 'Präsenzsensor';
}

function sensorTypeLabel(type?: string | null) {
  if (type === 'door_contact' || type === 'door') return 'Türsensor';
  if (type === 'smoke_detector') return 'Rauchmelder';
  if (type === 'presence_sensor' || type === 'presence') return 'Präsenzsensor';
  if (type === 'button') return 'Taster';
  if (type === 'electricity_meter') return 'Stromzähler';
  if (type === 'water_meter') return 'Wasserzähler';
  if (type === 'gas_meter') return 'Gaszähler';
  return 'Sensor';
}

function sensorHelp(sensor: SensorBinding) {
  if (sensor.type === 'door') return 'Erkennt, ob eine Tür oder ein Fenster geöffnet wurde.';
  if (sensor.type === 'smoke_detector') return 'Warnt, wenn Rauch erkannt wird.';
  if (sensor.type === 'electricity_meter') return 'Liest den Stromzähler lokal über http://EcoTracker-IP/v1/json aus.';
  if (sensor.type === 'water_meter') return 'Liefert Wasserverbrauch als zusätzlichen Aktivitätshinweis.';
  if (sensor.type === 'gas_meter') return 'Liefert Gasverbrauch als zusätzlichen Aktivitätshinweis.';
  return 'Erkennt, ob sich eine Person im Raum bewegt oder anwesend ist.';
}
