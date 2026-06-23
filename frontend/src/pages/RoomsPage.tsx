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
          const count = sensors.filter((sensor) => sensor.room === room).length;
          return (
            <article className="sc-room-card quiet" key={room}>
              <div><span className="sc-room-dot" /><strong>{roomLabels[room] || room}</strong></div>
              <p>{count} Sensoren verbunden</p>
              <small>{lastSeen(sensors.filter((sensor) => sensor.room === room))}</small>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function lastSeen(sensors: SenteroSensorRole[]) {
  const latest = sensors.map((sensor) => new Date(sensor.last_changed || sensor.last_updated || sensor.updated_at || '').getTime()).filter(Number.isFinite).sort((a, b) => b - a)[0];
  if (!latest) return 'noch keine Daten';
  return new Intl.DateTimeFormat('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(latest));
}
