import { Check, Loader2, Radio, Search, ShieldCheck } from 'lucide-react';
import type { SenteroCandidate } from '@shared/api/client';

export type SensorBinding = {
  id: string;
  roomId: string;
  type: 'motion' | 'door';
  sensorId: string;
  name: string;
  status: 'idle' | 'searching' | 'connected' | 'missing' | 'skipped';
  sessionId?: number;
  score?: number;
  entityId?: string;
};

export type SensorDiscoveryState = {
  candidate?: SenteroCandidate | null;
  candidates?: SenteroCandidate[];
  remainingSeconds?: number;
  error?: string;
  provider?: string;
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
};

export function SensorWizard({ sensors, discovery, devMode, connected, total, roomLabel, onChange, onSearch }: Props) {
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
          <p>Klicken Sie auf ‚Sensor suchen‘. Versetzen Sie anschließend den Sensor in den Kopplungsmodus, z. B. indem Sie die Pairing-Taste 3–5 Sekunden gedrückt halten.</p>
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
            />
          ))}
        </article>
      ))}
    </section>
  );
}

function SensorRow({ sensor, state, devMode, onChange, onSearch }: {
  sensor: SensorBinding;
  state?: SensorDiscoveryState;
  devMode: boolean;
  onChange: (id: string, patch: Partial<SensorBinding>) => void;
  onSearch: (sensor: SensorBinding) => void;
}) {
  const label = sensor.type === 'motion' ? 'Präsenzsensor' : 'Türsensor';
  const help = sensor.type === 'motion'
    ? 'Erkennt Bewegungen oder Anwesenheit im Raum.'
    : 'Erkennt, ob eine Tür oder ein Fenster geöffnet wurde.';

  return (
    <div className={`sc-sensor-row ${sensor.status === 'connected' ? 'is-connected' : ''}`}>
      <div className="sc-sensor-main">
        <span className="sc-sensor-kind"><ShieldCheck size={20} /> {label}</span>
        <strong>{sensor.name || label}</strong>
        <small>{help}</small>
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
          <button className="secondary" type="button" onClick={() => onChange(sensor.id, { status: 'skipped' })} disabled={sensor.status === 'connected'}>Überspringen</button>
        </div>
      </div>
      {state?.error && <p className="sc-sensor-error">{state.error}</p>}
      {devMode && (
        <code className="sc-dev-line">
          {sensor.entityId || state?.candidate?.entity_id || 'Keine Entity'} · Score {sensor.score ?? state?.candidate?.score ?? '-'} · Rest {state?.remainingSeconds ?? '-'}s · {state?.provider || 'zigbee'}
        </code>
      )}
    </div>
  );
}

function SensorStatus({ status, remainingSeconds }: { status: SensorBinding['status']; remainingSeconds?: number }) {
  if (status === 'searching') return <span className="sc-sensor-state searching"><Loader2 size={18} /> Suche läuft... Warte auf Sensor{typeof remainingSeconds === 'number' ? ` · ${Math.ceil(remainingSeconds)}s` : ''}</span>;
  if (status === 'connected') return <span className="sc-sensor-state connected"><Check size={18} /> Sensor gefunden und verbunden</span>;
  if (status === 'missing') return <span className="sc-sensor-state missing">Kein Sensor gefunden. Prüfen Sie, ob der Sensor im Kopplungsmodus ist.</span>;
  if (status === 'skipped') return <span className="sc-sensor-state skipped">Übersprungen</span>;
  return <span className="sc-sensor-state idle">Bereit</span>;
}
