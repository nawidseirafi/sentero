import { useEffect, useMemo, useState } from 'react';
import { api, type SenteroBehaviorAssessment, type SenteroSensorRole } from '@shared/api/client';

type BehaviorEvent = { event_time: string; room?: string | null; role?: string | null; state?: string | null };

export function HistoryPage() {
  const [sensors, setSensors] = useState<SenteroSensorRole[]>([]);
  const [events, setEvents] = useState<BehaviorEvent[]>([]);
  const [assessment, setAssessment] = useState<SenteroBehaviorAssessment | null>(null);

  useEffect(() => {
    void api.senteroSensorRoles(true).then((result) => setSensors(result.sensor_roles)).catch(() => undefined);
    void api.senteroBehaviorTimeline().then((result) => {
      setEvents(result.events || []);
      setAssessment(result.assessment);
    }).catch(() => undefined);
  }, []);

  const items = useMemo(() => sensors
    .filter((sensor) => sensor.last_changed || sensor.last_updated || sensor.updated_at)
    .sort((a, b) => stamp(b) - stamp(a))
    .slice(0, 12), [sensors]);

  return (
    <section className="sc-page">
      <div className="sc-hero-copy">
        <p className="sc-kicker">Verlauf</p>
        <h1>Letzte Sensoraktivität.</h1>
        <p>{items.length ? 'Der Verlauf basiert auf echten Sensor-Zeitstempeln.' : 'Noch kein Sensorverlauf verfügbar.'}</p>
      </div>
      {assessment && (
        <article className={`sc-behavior-card ${assessment.status}`}>
          <div><span aria-hidden="true">{behaviorIcon(assessment.status)}</span><div><small>KI Bewertung</small><strong>{assessment.summary}</strong></div></div>
          <p>{assessment.recommendation}</p>
        </article>
      )}
      <div className="sc-timeline">
        {(events.length ? events : []).map((event, index) => (
          <article className="sc-timeline-item calm" key={`${event.event_time}-${event.role}-${index}`}>
            <time>{format(new Date(event.event_time).getTime())}</time>
            <div>
              <strong>{roomLabel(event.room) || event.role || 'Sensor'}</strong>
              <p>{event.state ? `Aktivität erkannt (${event.state}).` : 'Aktivität erkannt.'}</p>
            </div>
          </article>
        ))}
        {!events.length && items.map((sensor) => (
          <article className="sc-timeline-item calm" key={sensor.role}>
            <time>{format(stamp(sensor))}</time>
            <div>
              <strong>{sensor.label || sensor.role}</strong>
              <p>{sensor.reachable === false ? 'Sensor ist nicht erreichbar.' : 'Sensor wurde aktualisiert.'}</p>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}

function behaviorIcon(status?: string | null) {
  if (status === 'yellow') return '🟡';
  if (status === 'orange') return '🟠';
  if (status === 'red') return '🔴';
  return '🟢';
}

function roomLabel(room?: string | null) {
  const labels: Record<string, string> = {
    kitchen: 'Küche',
    living_room: 'Wohnzimmer',
    bathroom: 'Bad',
    bedroom: 'Schlafzimmer',
    hallway: 'Flur',
    entrance: 'Eingang',
  };
  return room ? labels[room] || room : '';
}

function stamp(sensor: SenteroSensorRole) {
  const parsed = new Date(sensor.last_changed || sensor.last_updated || sensor.updated_at || '').getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}

function format(value: number) {
  if (!value) return 'Noch offen';
  return new Intl.DateTimeFormat('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }).format(new Date(value));
}
