import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { AlarmClock, House, Activity } from 'lucide-react';
import { api, type SenteroBehaviorAssessment, type SenteroBehaviorLearning, type SenteroSensorRole, type SenteroSetupStatus } from '@shared/api/client';

type BehaviorEvent = {
  event_time: string;
  room?: string | null;
  role?: string | null;
  state?: string | null;
  device_class?: string | null;
  entity_id?: string | null;
};

export function DashboardPage() {
  const [status, setStatus] = useState<SenteroSetupStatus | null>(null);
  const [roles, setRoles] = useState<SenteroSensorRole[]>([]);
  const [behavior, setBehavior] = useState<SenteroBehaviorAssessment | null>(null);
  const [learning, setLearning] = useState<SenteroBehaviorLearning | null>(null);
  const [timelineEvents, setTimelineEvents] = useState<BehaviorEvent[]>([]);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const [next, liveRoles, latestBehavior, timeline] = await Promise.all([
          api.senteroSetupStatus(),
          api.senteroSensorRoles(true).catch(() => ({ sensor_roles: [] })),
          api.senteroBehaviorLatest().catch(() => ({ assessment: null, learning: undefined })),
          api.senteroBehaviorTimeline().catch(() => ({ events: [], assessment: null })),
        ]);
        if (active) {
          setStatus(next);
          // The setup status intentionally contains configuration data. Current
          // presence must come from the live sensor-role endpoint; deriving it
          // from the behavior timeline makes an old "off" event look like the
          // current room state.
          setRoles(liveRoles.sensor_roles?.length ? liveRoles.sensor_roles : (next.sensor_roles || []));
          setBehavior(latestBehavior.assessment);
          setLearning(latestBehavior.learning || null);
          setTimelineEvents(timeline.events || []);
          setError('');
        }
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : 'Sentero konnte nicht geladen werden.');
      }
    }
    async function loadLiveRoles() {
      try {
        const nextRoles = await api.senteroSensorRoles(true);
        if (active && nextRoles.sensor_roles?.length) setRoles(nextRoles.sensor_roles);
      } catch {
        // Keep the last known live roles. A temporary request failure must not
        // turn an occupied room into "Nicht im Haus".
      }
    }

    void load();
    const timer = window.setInterval(() => void load(), 30000);
    const liveTimer = window.setInterval(() => void loadLiveRoles(), 3000);
    return () => {
      active = false;
      window.clearInterval(timer);
      window.clearInterval(liveTimer);
    };
  }, []);

  const configuredRoles = roles.filter((role) => role.configured);
  const hasSensors = configuredRoles.length > 0;
  const latestTimeline = useMemo(() => latestActivityEvent(timelineEvents), [timelineEvents]);
  const latestMovement = useMemo(() => latestMovementEvent(timelineEvents), [timelineEvents]);
  // Current location is a live state, not a historical event. A person may sit
  // still for a long time while presence remains true.
  const currentPresence = useMemo(() => currentPresenceRole(roles), [roles]);
  const personName = status?.profile?.name?.trim() || 'Person';
  const activitySlots = useMemo(() => activitySlotsFromTimeline(timelineEvents, roles), [timelineEvents, roles]);
  const hasActivity = activitySlots.some((slot) => slot.active);
  const firstActivity = firstWakeActivityEvent(timelineEvents, roles);
  // Primary source is the persisted behavior timeline. As a live fallback, use
  // a currently active motion state so a fresh ZG-204ZH large/small report is
  // visible immediately even before the timeline request refreshes.
  const liveMovement = useMemo(() => latestLiveMovementRole(roles), [roles]);
  // "Letzte Bewegung" must only use actual motion events. Presence-on is
  // useful for occupancy/wakeup, but it is not movement. Prefer the newest
  // persisted motion event, unless a currently active live motion report is
  // newer and has not reached the timeline yet.
  const persistedMovementTime = latestMovement?.event_time || '';
  const liveMovementTime = liveMovement?.time || '';
  const useLiveMovement = timestamp(liveMovementTime) > timestamp(persistedMovementTime);
  const lastEventTime = useLiveMovement ? liveMovementTime : persistedMovementTime;
  const lastEventRoom = useLiveMovement ? liveMovement?.room : latestMovement?.room;
  const lastSeen = lastEventTime ? movementLabel(lastEventTime, lastEventRoom) : 'Noch keine Bewegung erkannt';
  const morning = firstActivity ? formatTime(new Date(timestamp(firstActivity.time))) : '';
  const currentRoomValue = currentPresence ? roomLocationLabel(currentPresence.room) : 'Nicht im Haus';
  const dashboardState = getDashboardState({
    error,
    hasSensors,
    latest: Boolean(latestTimeline || liveMovement),
    currentPresence: Boolean(currentPresence),
    behavior,
    learning,
  });
  const currentLocation = currentPresence ? roomLocationLabel(currentPresence.room) : 'Nicht im Haus';

  return (
    <section className="sc-page sc-simple-dashboard" aria-label="Sentero Tagesstatus">
      <header className="sc-simple-hero">
        <p className="sc-simple-date">{formatHeaderDate(new Date())}</p>
        <p className={`sc-simple-person ${dashboardState.tone}`}>
          <span className="sc-simple-status-dot" aria-hidden="true" />
          {personName} · {currentLocation}
        </p>
        <h2>{dashboardState.headline}</h2>
        <p className="sc-simple-copy">
          {dashboardState.copy}
        </p>
      </header>

      <BehaviorOverviewCard
        behavior={behavior}
        learning={learning}
        hasSensors={hasSensors}
        activitySlots={activitySlots}
        hasActivity={hasActivity}
        quiet={!error}
      />

      <h3 className="sc-simple-section-title">Heute</h3>
      <section className="sc-metric-grid" aria-label="Wichtige Tagespunkte">
        <MetricCard icon={<AlarmClock size={20} />} label="Aufgestanden" value={morning || 'Noch offen'} />
        <MetricCard icon={<House size={20} />} label="Aufenthaltsort" value={currentRoomValue} muted={!currentPresence} />
        <MetricCard icon={<Activity size={20} />} label="Letzte Bewegung" value={lastSeen} highlight={Boolean(lastEventTime)} muted={!lastEventTime} />
      </section>
    </section>
  );
}

function BehaviorOverviewCard({
  behavior,
  learning,
  hasSensors,
  activitySlots,
  hasActivity,
  quiet,
}: {
  behavior: SenteroBehaviorAssessment | null;
  learning: SenteroBehaviorLearning | null;
  hasSensors: boolean;
  activitySlots: Array<{ hour: number; label: string; active: boolean }>;
  hasActivity: boolean;
  quiet: boolean;
}) {
  const meta = behaviorMeta(behavior?.status || 'green');
  const headline = !hasSensors ? 'Noch offen' : meta.label;
  const summary = !hasSensors
    ? 'Verbinden Sie zuerst Sensoren, damit Sentero den Alltag kennenlernen kann.'
    : behavior?.summary || (learning?.completed
      ? 'Sentero kennt den gewohnten Tagesablauf.'
      : 'Sentero baut ein persönliches Normalverhalten auf.');
  const learningProgress = learning ? learningProgressLabel(learning) : 'Lernphase';
  const learningHint = learning?.completed
    ? 'Sentero kennt den gewohnten Tagesablauf.'
    : learning?.remaining_usable_days
      ? `Sentero braucht noch ${learning.remaining_usable_days} verwertbare ${learning.remaining_usable_days === 1 ? 'Tag' : 'Tage'}.`
      : 'Sentero lernt aktuell den gewohnten Tagesablauf kennen.';

  return (
    <article className={`sc-behavior-overview-card ${meta.tone}`} aria-label="Verhaltensanalyse und Tagesverlauf">
      <section className="sc-behavior-overview-main" aria-label="Verhaltensanalyse">
        <div className="sc-behavior-overview-head">
          <span className="sc-behavior-status-dot" aria-hidden="true" />
          <div>
            <small>Verhaltensanalyse</small>
            <strong>{headline}</strong>
          </div>
        </div>
        <p>{summary}</p>
        <TimelineStrip activitySlots={activitySlots} hasActivity={hasActivity} />
      </section>

      <aside className="sc-behavior-overview-side" aria-label="Aktueller Tagesverlauf">
        <span className={`sc-quiet-badge ${quiet ? 'quiet' : 'check'}`}>{quiet ? 'Ruhig' : 'Prüfen'}</span>
        <strong>{learningProgress}</strong>
        <p>{learningHint}</p>
      </aside>
    </article>
  );
}

function learningProgressLabel(learning: SenteroBehaviorLearning) {
  const usable = learning.usable_days;
  const required = learning.required_usable_days;
  if (usable != null && required != null && !learning.completed) {
    return `${usable} von ${required} Lerntagen`;
  }
  return `Tag ${learning.day} von ${learning.days}`;
}

function TimelineStrip({ activitySlots, hasActivity }: { activitySlots: Array<{ hour: number; label: string; active: boolean }>; hasActivity: boolean }) {
  return (
    <div className={`sc-overview-dayline ${hasActivity ? 'has-activity' : ''}`} aria-label="Tagesverlauf">
      <div className="sc-overview-dots" aria-hidden="true">
        {activitySlots.map((slot) => <i key={slot.label} className={slot.active ? 'active' : ''} />)}
      </div>
      <div className="sc-overview-times" aria-hidden="true">
        {activitySlots.map((slot) => <span key={slot.label}>{slot.label}</span>)}
      </div>
      {!hasActivity && <p>Noch keine Aktivität erkannt</p>}
    </div>
  );
}

function behaviorMeta(status?: string | null) {
  if (status === 'red') return { tone: 'red', dot: '🔴', label: 'Kritisch' };
  if (status === 'orange') return { tone: 'orange', dot: '🟠', label: 'Auffällig' };
  if (status === 'yellow') return { tone: 'yellow', dot: '🟡', label: 'Leichte Abweichung' };
  return { tone: 'green', dot: '🟢', label: 'Normal' };
}

function MetricCard({ icon, label, value, highlight, muted }: { icon: ReactNode; label: string; value: string; highlight?: boolean; muted?: boolean }) {
  return (
    <div className="sc-metric-card">
      <span className="sc-metric-icon" aria-hidden="true">{icon}</span>
      <span className="sc-metric-label">{label}</span>
      <strong className={`sc-metric-value${highlight ? ' highlight' : ''}${muted ? ' muted' : ''}`}>{value}</strong>
    </div>
  );
}

function getDashboardState({ error, hasSensors, latest, currentPresence, behavior, learning }: {
  error: string;
  hasSensors: boolean;
  latest: boolean;
  currentPresence: boolean;
  behavior: SenteroBehaviorAssessment | null;
  learning: SenteroBehaviorLearning | null;
}) {
  if (error) {
    return {
      tone: 'error',
      kicker: 'Datenquelle nicht erreichbar',
      headline: 'Bitte prüfen.',
      copy: 'Aktuelle Daten konnten gerade nicht geladen werden.',
    };
  }
  if (!hasSensors) {
    return {
      tone: 'neutral',
      kicker: 'Einrichtung offen',
      headline: 'Noch keine Sensoren.',
      copy: 'Verbinden Sie zuerst Sensoren, damit Sentero den Tagesablauf zuverlässig bewerten kann.',
    };
  }
  if (!latest) {
    if (learning?.completed) {
      return currentPresence
        ? {
            tone: behavior?.status || 'ok',
            kicker: 'Präsenz erkannt',
            headline: 'Alles in Ordnung.',
            copy: behavior?.summary || 'Aktuell wird Anwesenheit erkannt. Heute wurde noch keine Bewegung erfasst.',
          }
        : {
            tone: behavior?.status || 'ok',
            kicker: 'Heute',
            headline: 'Noch keine Aktivität heute.',
            copy: behavior?.summary || 'Die Lernphase ist abgeschlossen. Heute wurde bislang noch keine Bewegung erkannt.',
          };
    }
    return {
      tone: 'learning',
      kicker: 'Sensoren verbunden',
      headline: 'Sentero lernt.',
      copy: 'Sentero baut noch das persönliche Normalverhalten auf.',
    };
  }
  if (!currentPresence) {
    return {
      tone: behavior?.status || 'ok',
      kicker: 'Keine Anwesenheit',
      headline: 'Gerade keine Präsenz erkannt.',
      copy: behavior?.summary || 'Die letzte Aktivität basiert auf den verbundenen Sensoren.',
    };
  }
  const titleByStatus: Record<string, string> = {
    yellow: 'Leichte Auffälligkeit.',
    orange: 'Bitte prüfen.',
    red: 'Handlungsbedarf.',
  };
  return {
    tone: behavior?.status || 'ok',
    kicker: 'Aktivität erkannt',
    headline: titleByStatus[String(behavior?.status || '')] || 'Alles in Ordnung.',
    copy: behavior?.summary || 'Der aktuelle Verlauf basiert auf verbundenen Sensoren.',
  };
}

function isPresenceRole(role: SenteroSensorRole) {
  return role.role.endsWith('presence') || ['motion', 'occupancy', 'presence'].includes(String(role.device_class || ''));
}

function currentPresenceRole(roles: SenteroSensorRole[]) {
  return roles
    .filter((role) => role.configured && role.reachable !== false && isPresenceRole(role) && roleSignalsPresence(role))
    .sort((a, b) => roleStateTimestamp(b) - roleStateTimestamp(a))[0];
}

function roleSignalsPresence(role: SenteroSensorRole) {
  if (role.presence === true) return true;

  // Combined mmWave/PIR devices sometimes publish a contradictory presence=false
  // together with a motion state that still explicitly means a person is in the
  // room. Keep this frontend guard in addition to the backend resolver so the
  // dashboard never converts "still/static/moving" into "Nicht im Haus".
  const motion = normalizeState(role.motion_state || role.motion);
  return ['moving', 'move', 'movement', 'motion', 'active', 'detected', 'large', 'small', 'static', 'static_target', 'still', 'stationary', 'standstill', 'present', 'presence'].includes(motion);
}

function latestLiveMovementRole(roles: SenteroSensorRole[]) {
  return roles
    .filter((role) => role.configured && role.reachable !== false && roleSignalsMovement(role))
    .map((role) => ({
      time: role.last_changed || role.last_updated || role.updated_at || '',
      room: role.room,
    }))
    .filter((item) => timestamp(item.time))
    .sort((a, b) => timestamp(b.time) - timestamp(a.time))[0];
}

function roleSignalsMovement(role: SenteroSensorRole) {
  const motion = normalizeState(role.motion_state || role.motion || role.state);
  return ['large', 'small', 'moving', 'move', 'movement', 'motion', 'active', 'detected', 'on', 'true', '1'].includes(motion);
}

function roleStateTimestamp(role: SenteroSensorRole) {
  return timestamp(role.last_updated || role.last_changed || role.updated_at);
}

function firstWakeActivityEvent(events: BehaviorEvent[], roles: SenteroSensorRole[]) {
  // Prefer the first real movement of the day. If there is no motion event, a
  // presence transition to on is an acceptable fallback for "Aufgestanden".
  // This keeps wakeup and last-movement semantics separate.
  const movementEvents = todayMovementEvents(events)
    .map((event) => ({ time: event.event_time, room: event.room }))
    .filter((event) => timestamp(event.time));
  if (movementEvents.length > 0) {
    return movementEvents.sort((a, b) => timestamp(a.time) - timestamp(b.time))[0];
  }

  const presenceEvents = todayPresenceOnEvents(events)
    .map((event) => ({ time: event.event_time, room: event.room }))
    .filter((event) => timestamp(event.time));
  if (presenceEvents.length > 0) {
    return presenceEvents.sort((a, b) => timestamp(a.time) - timestamp(b.time))[0];
  }

  const today = new Date();
  return roles
    .filter((role) => role.configured && role.reachable !== false && isPresenceRole(role) && roleSignalsPresence(role))
    .map((role) => ({ time: role.last_changed || role.last_updated || role.updated_at || '', room: role.room }))
    .filter((event) => {
      const value = timestamp(event.time);
      return value && new Date(value).toDateString() === today.toDateString();
    })
    .sort((a, b) => timestamp(a.time) - timestamp(b.time))[0];
}

function roomLocationLabel(room?: string | null) {
  const labels: Record<string, string> = {
    living_room: 'Im Wohnzimmer',
    kitchen: 'In der Küche',
    bathroom: 'Im Bad',
    bedroom: 'Im Schlafzimmer',
    hallway: 'Im Flur',
    entrance: 'Am Eingang',
  };
  return room ? labels[room] || room : 'Raum unbekannt';
}

function latestMovementEvent(events: BehaviorEvent[]) {
  return todayMovementEvents(events)
    .sort((a, b) => timestamp(b.event_time) - timestamp(a.event_time))[0];
}

function todayMovementEvents(events: BehaviorEvent[]) {
  const today = new Date();
  return events.filter((event) => {
    if (!isMotionEvent(event)) return false;
    if (!activeMotionStates.has(normalizeState(event.state))) return false;
    const value = timestamp(event.event_time);
    return Boolean(value) && new Date(value).toDateString() === today.toDateString();
  });
}

function todayPresenceOnEvents(events: BehaviorEvent[]) {
  const today = new Date();
  return events.filter((event) => {
    if (!isPresenceEvent(event)) return false;
    if (!activeStates.has(normalizeState(event.state))) return false;
    const value = timestamp(event.event_time);
    return Boolean(value) && new Date(value).toDateString() === today.toDateString();
  });
}

function isMotionEvent(event: BehaviorEvent) {
  const role = String(event.role || '').toLowerCase();
  const deviceClass = String(event.device_class || '').toLowerCase();
  const entityId = String(event.entity_id || '').toLowerCase();
  return role.endsWith('_motion') || deviceClass === 'motion' || entityId.endsWith('#motion');
}

function isPresenceEvent(event: BehaviorEvent) {
  const role = String(event.role || '').toLowerCase();
  const deviceClass = String(event.device_class || '').toLowerCase();
  const entityId = String(event.entity_id || '').toLowerCase();
  return !isMotionEvent(event) && (role.endsWith('presence') || deviceClass === 'presence' || entityId.endsWith('#presence'));
}

function latestActivityEvent(events: BehaviorEvent[]) {
  return todayActivityEvents(events)
    .sort((a, b) => timestamp(b.event_time) - timestamp(a.event_time))[0];
}

function todayActivityEvents(events: BehaviorEvent[]) {
  const today = new Date();
  return events.filter((event) => {
    if (!isActivityEvent(event)) return false;
    const value = timestamp(event.event_time);
    if (!value) return false;
    return new Date(value).toDateString() === today.toDateString();
  });
}

function isActivityEvent(event: BehaviorEvent) {
  const state = normalizeState(event.state);
  if (inactiveStates.has(state)) return false;
  const role = String(event.role || '').toLowerCase();
  if (role.endsWith('_motion')) return activeMotionStates.has(state);
  return activeStates.has(state);
}

function activitySlotsFromTimeline(events: BehaviorEvent[], roles: SenteroSensorRole[]) {
  const slots = [0, 6, 12, 18, 24].map((hour) => ({ hour, label: String(hour).padStart(2, '0'), active: false }));
  const today = new Date();
  const values = todayActivityEvents(events).map((event) => timestamp(event.event_time));
  if (values.length === 0) {
    values.push(...roles.filter((role) => isRoleActive(role)).map((role) => timestamp(role.last_changed || role.last_updated)).filter(Boolean));
  }
  for (const value of values) {
    if (!value) continue;
    const date = new Date(value);
    if (date.toDateString() !== today.toDateString()) continue;
    const index = slots.findIndex((slot, slotIndex) => {
      const next = slots[slotIndex + 1]?.hour ?? 24;
      return date.getHours() >= slot.hour && date.getHours() < next;
    });
    if (index >= 0) slots[index].active = true;
  }
  return slots;
}

const activeStates = new Set(['active', 'on', 'open', 'opening', 'detected', 'true', '1', 'yes', 'present', 'presence']);
const activeMotionStates = new Set(['active', 'motion', 'moving', 'large', 'small', 'detected', 'on', 'true', '1', 'yes']);
const inactiveStates = new Set(['', 'unknown', 'unavailable', 'none', 'off', 'false', '0', 'no', 'inactive', 'still', 'not_ready', 'nicht bereit', 'lesefehler', 'ok', 'closed', 'closing', 'clear']);

function isRoleActive(role: SenteroSensorRole) {
  if (role.presence != null) return Boolean(role.presence);
  const state = normalizeState(role.state);
  if (inactiveStates.has(state)) return false;
  if (activeStates.has(state)) return true;
  const motion = normalizeState(role.motion);
  if (role.role.endsWith('_motion') || String(role.device_class || '').toLowerCase() === 'motion') return activeMotionStates.has(motion || state);
  return false;
}

function normalizeState(value?: string | null) {
  return String(value || '').trim().toLowerCase();
}

function timestamp(value?: string | null) {
  if (!value) return 0;
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}

function relativeTime(value?: string | null) {
  const time = timestamp(value);
  if (!time) return 'noch keine Daten';
  const minutes = Math.max(0, Math.round((Date.now() - time) / 60000));
  if (minutes < 1) return 'gerade eben';
  if (minutes < 60) return `vor ${minutes} Min.`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `vor ${hours} Std.`;
  return formatDateTime(new Date(time));
}

function movementLabel(value?: string | null, room?: string | null) {
  if (!value) return 'Noch keine Bewegung erkannt';
  const location = room ? ` · ${roomLocationLabel(room)}` : '';
  return `${relativeTime(value)}${location}`;
}

function formatTime(date: Date) {
  return new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit' }).format(date);
}

function formatDateTime(date: Date) {
  return new Intl.DateTimeFormat('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }).format(date);
}

function formatHeaderDate(date: Date) {
  return new Intl.DateTimeFormat('de-DE', { weekday: 'long', hour: '2-digit', minute: '2-digit' }).format(date);
}
