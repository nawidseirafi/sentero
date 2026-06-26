import { Check, Loader2, Radio, Search, ShieldCheck, Wifi } from 'lucide-react';
import type { SenteroDiscoveredSensor } from '@shared/api/client';

export type SensorBinding = {
  id: string;
  roomId: string;
  type: 'motion' | 'door';
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

  return (
    <section className="sc-sensor-step">
      <div className="sc-zigbee-intro">
        <span><Radio size={24} /></span>
        <div>
          <h3>Sensor verbinden</h3>
         <p>
           Klicken Sie auf <strong>„Sensor suchen“</strong>.<br/>
           <b>Präsenzsensoren</b> müssen zunächst mit Ihrem Heim-WLAN verbunden werden. Danach erkennt Sentero sie automatisch.<br/>
           <b>Türsensoren</b> werden während der Suche hinzugefügt. Halten Sie dazu die Pairing-Taste 3–5 Sekunden gedrückt.
         </p>
        </div>
        <strong>{connected}/{total} Sensor verbunden</strong>
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
  const label = presence ? 'Präsenzsensor' : 'Türsensor';
  const help = presence
    ? 'Präsenzsensor einschalten. Sentero verbindet ihn automatisch.'
    : 'Erkennt, ob eine Tür oder ein Fenster geöffnet wurde.';

  return (
    <div className={`sc-sensor-row ${sensor.status === 'connected' ? 'is-connected' : ''}`}>
      <div className="sc-sensor-main">
        <span className="sc-sensor-kind"><ShieldCheck size={20} /> {label}</span>
        <strong>{sensor.name || label}</strong>
        <small>{help}</small>
        {presence && (
          <div className="sc-sensor-preflight">
            <Wifi size={17} />
            <span>Bitte verbinden Sie den Sensor zuerst mit Ihrem Heimnetz. Danach kann Sentero ihn hier finden.</span>
          </div>
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
  return type !== 'door' || id.endsWith('_presence') || id.endsWith('_motion');
}
