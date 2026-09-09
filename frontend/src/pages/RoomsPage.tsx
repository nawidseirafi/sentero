import { useEffect, useMemo, useState } from 'react';
import { api, type SenteroSensorRole, type SenteroSetupStatus } from '@shared/api/client';

const roomLabels: Record<string, string> = {
  living_room: 'Wohnzimmer',
  kitchen: 'Küche',
  bathroom: 'Bad',
  bedroom: 'Schlafzimmer',
  hallway: 'Flur',
  entrance: 'Eingang',
};

export function RoomsPage() {
  const [status, setStatus] = useState<SenteroSetupStatus | null>(null);
  const [sensors, setSensors] = useState<SenteroSensorRole[]>([]);

  useEffect(() => {
    void Promise.all([api.senteroSetupStatus(), api.senteroSensorRoles(true)]).then(([nextStatus, nextSensors]) => {
      setStatus(nextStatus);
      setSensors(nextSensors.sensor_roles);
    }).catch(() => undefined);
  }, []);

  const rooms = useMemo(() => Array.from(new Set([...(status?.selected_rooms || []), ...sensors.map((sensor) => sensor.room).filter(Boolean) as string[]])), [status, sensors]);

  return (
    <section className="sc-page">
      <div className="sc-hero-copy">
        <p className="sc-kicker">Räume</p>
        <h1>Das Zuhause im Blick.</h1>
        <p>{rooms.length ? 'Die Räume werden aus der Sentero-Einrichtung geladen.' : 'Noch keine Räume eingerichtet.'}</p>
      </div>
      <div className="sc-room-map">
        {rooms.map((room) => {
          const roomSensors = sensors.filter((sensor) => sensor.room === room);
          const count = roomSensors.length;
          const unavailable = roomSensors.filter((sensor) => sensor.reachable === false).length;
          const unknown = roomSensors.filter((sensor) => sensor.reachable == null).length;
          return (
            <article className={`sc-room-card ${unavailable ? 'notice' : 'quiet'}`} key={room}>
              <div><span className="sc-room-dot" /><strong>{roomLabels[room] || room}</strong></div>
              <p>{sensorSummary(count, unavailable, unknown)}</p>
              {roomSensors.filter(isSmokeSensor).map((sensor) => (
                <small key={sensor.role} className={sensor.reachable === true && sensor.smoke === true ? 'sc-room-alert' : undefined}>
                  Rauchmelder · {smokeStatus(sensor)}{sensor.battery_level == null ? '' : ` · Akku ${sensor.battery_level}%`}
                </small>
              ))}
              <small>{lastSeen(roomSensors)}</small>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function sensorSummary(count: number, unavailable: number, unknown: number) {
  if (unavailable) return `${unavailable} von ${count} Sensoren nicht erreichbar`;
  if (unknown) return `${unknown} von ${count} Sensorstatus unbekannt`;
  return `${count} Sensoren verbunden`;
}

function smokeStatus(sensor: SenteroSensorRole) {
  if (sensor.reachable === false) return 'nicht erreichbar · Zustand unbekannt';
  if (sensor.reachable == null) return 'Verbindung unklar · Zustand unbekannt';
  return sensor.smoke === true ? 'Rauch erkannt' : sensor.smoke === false ? 'Kein Rauch erkannt' : 'Zustand unbekannt';
}

function isSmokeSensor(sensor: SenteroSensorRole) {
  const role = String(sensor.role || '').toLowerCase();
  return role.endsWith('_smoke') || String(sensor.device_class || '').toLowerCase() === 'smoke';
}

function lastSeen(sensors: SenteroSensorRole[]) {
  const latest = sensors.map((sensor) => new Date(sensor.last_changed || sensor.last_updated || sensor.updated_at || '').getTime()).filter(Number.isFinite).sort((a, b) => b - a)[0];
  if (!latest) return 'noch keine Daten';
  return new Intl.DateTimeFormat('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(latest));
}
