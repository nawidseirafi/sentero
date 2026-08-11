import { Check, Loader2, Radio, Search, ShieldCheck } from 'lucide-react';
import type { SenteroDiscoveredSensor } from '@shared/api/client';
import { SetupWifiQr } from './SetupWifiQr';

export type SensorBinding = {
  id: string;
  roomId: string;
  type: 'motion' | 'door' | 'electricity_meter' | 'water_meter' | 'gas_meter';
  sensorId: string;
  name: string;
  status: 'idle' | 'searching' | 'connected' | 'missing' | 'skipped';
  sessionId?: number;
  score?: number;
  sensorManagerId?: string;
};

export type SensorDiscoveryState = {
  sensor?: SenteroDiscoveredSensor | null;
  remainingSeconds?: number;
  error?: string;
};

type Props = {
  sensors: SensorBinding[];
  discovery: Record<string, SensorDiscoveryState>;
  devMode: boolean;
  connected: number;
  total: number;
  roomLabel: (roomId: string) => string;
  onChange: (id: string, patch: Partial<SensorBinding>) => void;
  onSearch: (sensor: SensorBinding) => void;
  onSkip: (sensor: SensorBinding) => void;
};

export function SensorWizard({ sensors, discovery, devMode, connected, total, roomLabel, onChange, onSearch, onSkip }: Props) {
  const grouped = sensors.reduce<Record<string, SensorBinding[]>>((acc, sensor) => {
    acc[sensor.roomId] = [...(acc[sensor.roomId] || []), sensor];
    return acc;
  }, {});
  const allConnected = total > 0 && connected >= total;
  const progressLabel = total === 1 ? 'Sensor verbunden' : 'Sensoren verbunden';

  return (
    <section className="sc-sensor-step">
      <div className="sc-zigbee-intro">
        <span className="sc-zigbee-intro-icon"><Radio size={24} /></span>
        <div className="sc-zigbee-intro-copy">
          <div className="sc-zigbee-intro-title">
            <h3>Sensoren verbinden</h3>
            <p>Starten Sie die Suche und verbinden Sie jeden Sensor im passenden Raum.</p>
          </div>
          <div className="sc-zigbee-intro-notes">
            <span><strong>Präsenzsensoren</strong> vorher per Setup-Hotspot ins Heim-WLAN bringen.</span>
            <span><strong>Türsensoren</strong> während der Suche 3-5 Sekunden in den Pairing-Modus setzen.</span>
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
            <SensorRow
              key={sensor.id}
              sensor={sensor}
              state={discovery[sensor.id]}
              devMode={devMode}
              onChange={onChange}
              onSearch={onSearch}
              onSkip={onSkip}
            />
          ))}
        </article>
      ))}
    </section>
  );
}

function SensorRow({ sensor, state, devMode, onChange, onSearch, onSkip }: {
  sensor: SensorBinding;
  state?: SensorDiscoveryState;
  devMode: boolean;
  onChange: (id: string, patch: Partial<SensorBinding>) => void;
  onSearch: (sensor: SensorBinding) => void;
  onSkip: (sensor: SensorBinding) => void;
}) {
  const presence = isPresenceBinding(sensor);
  const label = sensorLabel(sensor);
  const help = sensorHelp(sensor);

  return (
    <div className={`sc-sensor-row ${sensor.status === 'connected' ? 'is-connected' : ''}`}>
      <div className="sc-sensor-main">
        <span className="sc-sensor-kind"><ShieldCheck size={20} /> {label}</span>
        <strong>{sensor.name || label}</strong>
        <small>{help}</small>
        {presence && (
          sensor.status === 'connected' ? (
              <span/>
          ) : (
            <div className="sc-sensor-preflight">
              <SetupWifiQr compact details={false} />
              <span>Setup-Hotspot scannen, Captive Portal öffnen und den Sensor mit Ihrem Heimnetz verbinden. Danach kann Sentero ihn hier finden.</span>
            </div>
          )
        )}
        <input
          value={sensor.name}
          onChange={(event) => onChange(sensor.id, { name: event.target.value })}
          placeholder="Sensorname"
          disabled={sensor.status === 'connected'}
        />
      </div>
      <div className="sc-sensor-side">
        <SensorStatus status={sensor.status} remainingSeconds={state?.remainingSeconds} />
        <div className="sc-sensor-buttons">
          <button className="primary" type="button" onClick={() => void onSearch(sensor)} disabled={sensor.status === 'searching' || sensor.status === 'connected'}>
            <Search size={19} /> {sensor.status === 'connected' ? 'Verbunden' : 'Sensor suchen'}
          </button>
          <button className="secondary" type="button" onClick={() => onSkip(sensor)} disabled={sensor.status === 'connected'}>Überspringen</button>
        </div>
      </div>
      {state?.error && <p className="sc-sensor-error">{state.error}</p>}
      {devMode && <code className="sc-dev-line">Score {sensor.score ?? state?.sensor?.confidence ?? '-'} · Rest {state?.remainingSeconds ?? '-'}s</code>}
    </div>
  );
}

function SensorStatus({ status, remainingSeconds }: { status: SensorBinding['status']; remainingSeconds?: number }) {
  if (status === 'searching') return <span className="sc-sensor-state searching"><Loader2 size={18} /> Sensor wird verbunden{typeof remainingSeconds === 'number' ? ` · ${Math.ceil(remainingSeconds)}s` : ''}</span>;
  if (status === 'connected') return <span className="sc-sensor-state connected"><Check size={18} /> Sensor gefunden</span>;
  if (status === 'missing') return <span className="sc-sensor-state missing">Sensor konnte nicht verbunden werden. Bitte einschalten und erneut versuchen.</span>;
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
  if (sensor.type === 'electricity_meter') return 'Stromzähler';
  if (sensor.type === 'water_meter') return 'Wasserzähler';
  if (sensor.type === 'gas_meter') return 'Gaszähler';
  return 'Präsenzsensor';
}

function sensorHelp(sensor: SensorBinding) {
  if (sensor.type === 'door') return 'Erkennt, ob eine Tür oder ein Fenster geöffnet wurde.';
  if (sensor.type === 'electricity_meter') return 'Liefert Stromverbrauch oder aktuelle Leistung als zusätzlichen Aktivitätshinweis.';
  if (sensor.type === 'water_meter') return 'Liefert Wasserverbrauch als zusätzlichen Aktivitätshinweis.';
  if (sensor.type === 'gas_meter') return 'Liefert Gasverbrauch als zusätzlichen Aktivitätshinweis.';
  return 'Erkennt, ob sich eine Person im Raum bewegt oder anwesend ist.';
}
