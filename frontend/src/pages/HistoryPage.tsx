import { useEffect, useMemo, useState } from 'react';
import type React from 'react';
import { Activity, AlertTriangle, Clock3, Gauge, Radio, ShieldCheck } from 'lucide-react';
import { api, type SenteroBehaviorAssessment, type SenteroSensorRole } from '@shared/api/client';

type BehaviorEvent = { event_time: string; room?: string | null; role?: string | null; state?: string | null; data_class?: string | null };
type HistoryFilter = 'all' | 'activity' | 'alerts' | 'system';

export function HistoryPage() {
  const [sensors, setSensors] = useState<SenteroSensorRole[]>([]);
  const [events, setEvents] = useState<BehaviorEvent[]>([]);
  const [assessment, setAssessment] = useState<SenteroBehaviorAssessment | null>(null);
  const [filter, setFilter] = useState<HistoryFilter>('all');

  useEffect(() => {
    async function load() {
      const [sensorResult, timelineResult] = await Promise.all([
        api.senteroSensorRoles(true).catch(() => ({ sensor_roles: [] })),
        api.senteroBehaviorTimeline(true).catch(() => ({ events: [], assessment: null })),
      ]);
      setSensors(sensorResult.sensor_roles || []);
      setEvents(timelineResult.events || []);
      setAssessment(timelineResult.assessment);
    }
    void load();
  }, []);

  const entries = useMemo(() => buildHistoryEntries(events, sensors), [events, sensors]);
  const filtered = useMemo(() => entries.filter((entry) => filter === 'all' || entry.kind === filter), [entries, filter]);
  const grouped = useMemo(() => groupEntries(filtered), [filtered]);
  const activeSensors = sensors.filter((sensor) => sensor.reachable !== false).length;
  const latest = entries[0];
  const activityCount = entries.filter((entry) => entry.kind === 'activity').length;
  const alertCount = entries.filter((entry) => entry.kind === 'alerts').length;

  return (
    <section className="sc-page sc-history-page" aria-label="Sentero Verlauf">
      <header className="sc-history-hero">
        <div>
          <p className="sc-kicker">Verlauf</p>
          <h1>Aktivität und Systemereignisse</h1>
          <p>{latest ? `Letzter Eintrag ${relativeTime(latest.time)}.` : 'Noch kein verwertbarer Verlauf vorhanden.'}</p>
        </div>
        <BehaviorStatusPill assessment={assessment} />
      </header>

      <section className="sc-history-metrics" aria-label="Verlaufskennzahlen">
        <HistoryMetric icon={<Activity size={20} />} label="Aktivitäten" value={String(activityCount)} />
        <HistoryMetric icon={<AlertTriangle size={20} />} label="Hinweise" value={String(alertCount)} muted={!alertCount} />
        <HistoryMetric icon={<Radio size={20} />} label="Erreichbare Sensoren" value={`${activeSensors}/${sensors.length || 0}`} />
        <HistoryMetric icon={<Clock3 size={20} />} label="Letzter Eintrag" value={latest ? formatTime(latest.time) : 'Noch offen'} />
      </section>

      <nav className="sc-history-filter" aria-label="Verlauf filtern">
        {[
          ['all', 'Alle'],
          ['activity', 'Aktivität'],
          ['alerts', 'Hinweise'],
          ['system', 'System'],
        ].map(([value, label]) => (
          <button key={value} className={filter === value ? 'active' : ''} type="button" onClick={() => setFilter(value as HistoryFilter)}>
            {label}
          </button>
        ))}
      </nav>

      <section className="sc-history-timeline" aria-label="Zeitachse">
        {grouped.map((group) => (
          <div className="sc-history-day" key={group.label}>
            <h2>{group.label}</h2>
            <div>
              {group.entries.map((entry) => (
                <article className={`sc-history-entry ${entry.kind}`} key={entry.id}>
                  <time>{formatTime(entry.time)}</time>
                  <span className="sc-history-dot" aria-hidden="true">{entry.icon}</span>
                  <div>
                    <strong>{entry.title}</strong>
                    <p>{entry.description}</p>
                    <small>{entry.meta}</small>
                  </div>
                </article>
              ))}
            </div>
          </div>
        ))}
        {!grouped.length && (
          <div className="sc-history-empty">
            <ShieldCheck size={24} />
            <strong>Keine Einträge für diesen Filter</strong>
            <p>Sentero zeigt hier neue Sensorereignisse, sobald sie vorliegen.</p>
          </div>
        )}
      </section>
    </section>
  );
}

function BehaviorStatusPill({ assessment }: { assessment: SenteroBehaviorAssessment | null }) {
  const tone = String(assessment?.status || 'green');
  return (
    <aside className={`sc-history-status ${tone}`}>
      <span aria-hidden="true"><Gauge size={22} /></span>
      <div>
        <small>Verhaltensanalyse</small>
        <strong>{statusLabel(tone)}</strong>
        <p>{assessment?.summary || 'Sentero baut das Normalverhalten auf.'}</p>
      </div>
    </aside>
  );
}

function HistoryMetric({ icon, label, value, muted = false }: { icon: React.ReactNode; label: string; value: string; muted?: boolean }) {
  return (
    <article className={`sc-history-metric${muted ? ' muted' : ''}`}>
      <span aria-hidden="true">{icon}</span>
      <div>
        <small>{label}</small>
        <strong>{value}</strong>
      </div>
    </article>
  );
}

function buildHistoryEntries(events: BehaviorEvent[], sensors: SenteroSensorRole[]) {
  const eventEntries = events
    .map((event, index) => {
      const time = new Date(event.event_time).getTime();
      if (!Number.isFinite(time)) return null;
      const kind = eventKind(event);
      return {
        id: `event-${event.event_time}-${event.role || index}`,
        time,
        kind,
        icon: kind === 'alerts' ? <AlertTriangle size={16} /> : kind === 'system' ? <Radio size={16} /> : <Activity size={16} />,
        title: roomLabel(event.room) || roleLabel(event.role) || 'Sensoraktivität',
        description: eventDescription(event),
        meta: [roleLabel(event.role), dataClassLabel(event.data_class)].filter(Boolean).join(' · ') || 'Sensorereignis',
      };
    })
    .filter(Boolean) as HistoryEntry[];

  const sensorEntries = sensors
    .filter((sensor) => sensor.last_changed || sensor.last_updated || sensor.updated_at)
    .map((sensor) => ({
      id: `sensor-${sensor.role}`,
      time: stamp(sensor),
      kind: sensor.reachable === false ? 'alerts' as const : 'system' as const,
      icon: sensor.reachable === false ? <AlertTriangle size={16} /> : <Radio size={16} />,
      title: sensor.label || roleLabel(sensor.role) || 'Sensor',
      description: sensor.reachable === false ? 'Sensor ist aktuell nicht erreichbar.' : sensorStateDescription(sensor),
      meta: [roomLabel(sensor.room), sensor.source || sensor.device_class || sensor.domain].filter(Boolean).join(' · ') || 'Sensorstatus',
    }))
    .filter((entry) => entry.time);

  const byKey = new Map<string, HistoryEntry>();
  for (const entry of [...eventEntries, ...sensorEntries].sort((a, b) => b.time - a.time)) {
    const minuteKey = `${entry.title}-${Math.floor(entry.time / 60000)}-${entry.kind}`;
    if (!byKey.has(minuteKey)) byKey.set(minuteKey, entry);
  }
  return Array.from(byKey.values()).sort((a, b) => b.time - a.time).slice(0, 60);
}

type HistoryEntry = {
  id: string;
  time: number;
  kind: Exclude<HistoryFilter, 'all'>;
  icon: React.ReactNode;
  title: string;
  description: string;
  meta: string;
};

function groupEntries(entries: HistoryEntry[]) {
  const groups = new Map<string, HistoryEntry[]>();
  for (const entry of entries) {
    const label = formatDay(entry.time);
    groups.set(label, [...(groups.get(label) || []), entry]);
  }
  return Array.from(groups.entries()).map(([label, groupEntries]) => ({ label, entries: groupEntries }));
}

function eventKind(event: BehaviorEvent): Exclude<HistoryFilter, 'all'> {
  const state = normalize(event.state);
  const dataClass = normalize(event.data_class);
  const role = normalize(event.role);
  if (dataClass === 'emergency' || state.includes('fall') || state.includes('sturz')) return 'alerts';
  if (dataClass === 'technical' || role.includes('battery') || role.includes('meter')) return 'system';
  return 'activity';
}

function eventDescription(event: BehaviorEvent) {
  const state = normalize(event.state);
  if (state === 'open') return 'Geöffnet erkannt.';
  if (state === 'closed') return 'Geschlossen erkannt.';
  if (['on', 'active', 'detected', 'present', 'presence', 'motion'].includes(state)) return 'Aktivität erkannt.';
  if (['off', 'inactive', 'clear', 'still'].includes(state)) return 'Ruhephase erkannt.';
  return event.state ? `Status: ${event.state}` : 'Sensorereignis erkannt.';
}

function sensorStateDescription(sensor: SenteroSensorRole) {
  if (sensor.presence) return 'Anwesenheit erkannt.';
  if (sensor.motion && !['off', 'inactive', 'clear', 'still'].includes(normalize(sensor.motion))) return 'Bewegung erkannt.';
  if (sensor.state) return `Status: ${sensor.state}`;
  return 'Sensor wurde aktualisiert.';
}

function statusLabel(status: string) {
  if (status === 'red') return 'Kritisch';
  if (status === 'orange') return 'Auffällig';
  if (status === 'yellow') return 'Leichte Abweichung';
  return 'Normal';
}

function roleLabel(role?: string | null) {
  if (!role) return '';
  return role.replace(/_/g, ' ').replace(/\b\w/g, (value) => value.toUpperCase());
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

function dataClassLabel(value?: string | null) {
  if (value === 'technical') return 'Technik';
  if (value === 'utility') return 'Verbrauch';
  if (value === 'personal_behavior') return 'Tagesablauf';
  if (value === 'health_adjacent') return 'AAL-Hinweis';
  if (value === 'emergency') return 'Notfall';
  return '';
}

function stamp(sensor: SenteroSensorRole) {
  const parsed = new Date(sensor.last_changed || sensor.last_updated || sensor.updated_at || '').getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}

function normalize(value?: string | null) {
  return String(value || '').trim().toLowerCase();
}

function formatTime(value: number) {
  if (!value) return 'Noch offen';
  return new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit' }).format(new Date(value));
}

function formatDay(value: number) {
  const date = new Date(value);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  if (date.toDateString() === today.toDateString()) return 'Heute';
  if (date.toDateString() === yesterday.toDateString()) return 'Gestern';
  return new Intl.DateTimeFormat('de-DE', { weekday: 'long', day: '2-digit', month: '2-digit' }).format(date);
}

function relativeTime(value: number) {
  const diffMinutes = Math.max(0, Math.round((Date.now() - value) / 60000));
  if (diffMinutes < 1) return 'gerade eben';
  if (diffMinutes < 60) return `vor ${diffMinutes} Min.`;
  const diffHours = Math.round(diffMinutes / 60);
  if (diffHours < 24) return `vor ${diffHours} Std.`;
  return formatDay(value);
}
