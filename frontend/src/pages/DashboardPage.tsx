import { useEffect, useMemo, useState } from 'react';
import { api, type SenteroBehaviorAssessment, type SenteroBehaviorLearning, type SenteroSensorRole, type SenteroSetupStatus } from '@shared/api/client';

export function DashboardPage() {
  const [status, setStatus] = useState<SenteroSetupStatus | null>(null);
  const [behavior, setBehavior] = useState<SenteroBehaviorAssessment | null>(null);
  const [learning, setLearning] = useState<SenteroBehaviorLearning | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const [next, latestBehavior] = await Promise.all([
          api.senteroSetupStatus(),
          api.senteroBehaviorLatest().catch(() => ({ assessment: null, learning: undefined })),
        ]);
        if (active) {
          setStatus(next);
          setBehavior(latestBehavior.assessment);
          setLearning(latestBehavior.learning || null);
          setError('');
        }
      } catch (err) {
        if (active) setError(err instanceof Error ? err.message : 'Sentero konnte nicht geladen werden.');
      }
    }
    void load();
    const timer = window.setInterval(() => void load(), 30000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const roles = status?.sensor_roles ?? [];
  const configuredRoles = roles.filter((role) => role.configured);
  const hasSensors = configuredRoles.length > 0;
  const latest = useMemo(() => latestPresenceRole(roles), [roles]);
  const personName = status?.profile?.name?.trim() || 'Person';
  const activitySlots = useMemo(() => activitySlotsFromRoles(roles), [roles]);
  const hasActivity = activitySlots.some((slot) => slot.active);
  const lastUpdate = latest ? formatTime(new Date(timestamp(latest.last_changed || latest.last_updated || latest.updated_at))) : '';
  const lastSeen = latest ? relativeTime(latest.last_changed || latest.last_updated || latest.updated_at) : 'Noch keine Daten';
  const morning = firstActivityTime(roles);
  const currentRoom = latest ? roomLocationLabel(latest.room) : 'Aktueller Raum';
  const dashboardState = getDashboardState({ error, hasSensors, latest: Boolean(latest), behavior });
  const currentLocation = latest ? roomLocationLabel(latest.room) : dashboardState.kicker;

  return (
    <section className="sc-page sc-simple-dashboard" aria-label="Sentero Tagesstatus">
      <header className="sc-simple-hero">
        <p className="sc-simple-date">{formatHeaderDate(new Date())}</p>
        <p className={`sc-simple-person ${dashboardState.tone}`}><span aria-hidden="true" /> {personName} · {currentLocation}</p>
        <h2>{dashboardState.headline}</h2>
        <p className="sc-simple-copy">
          {dashboardState.copy}
        </p>
      </header>

      <BehaviorAnalysisCard behavior={behavior} learning={learning} hasSensors={hasSensors} />

      <article className="sc-simple-day-card" aria-label="Tagesverlauf">
        <div className="sc-simple-day-head">
          <strong>Tagesverlauf</strong>
          <span>{error ? 'Prüfen' : 'Ruhig'}</span>
        </div>
        <div className={`sc-simple-dayline ${hasActivity ? 'has-activity' : ''}`}>
          <div className="sc-simple-dots" aria-hidden="true">
            {activitySlots.map((slot) => <i key={slot.label} className={slot.active ? 'active' : ''} />)}
          </div>
          <div className="sc-simple-times" aria-hidden="true">
            {activitySlots.map((slot) => <span key={slot.label}>{slot.label}</span>)}
          </div>
          {!hasActivity && <p>Noch keine Aktivität erkannt</p>}
        </div>
      </article>

      <h3 className="sc-simple-section-title">Heute</h3>
      <section className="sc-simple-facts" aria-label="Wichtige Tagespunkte">
        <Fact label="Aufgestanden" value={morning || 'Noch offen'} />
        <Fact label={currentRoom} value={lastUpdate || 'Keine Aktivität'} />
        <Fact label="Letzte Bewegung" value={lastSeen} highlight={Boolean(latest)} />
      </section>
    </section>
  );
}

function BehaviorAnalysisCard({ behavior, learning, hasSensors }: { behavior: SenteroBehaviorAssessment | null; learning: SenteroBehaviorLearning | null; hasSensors: boolean }) {
  const status = behavior?.status || 'green';
  const meta = behaviorMeta(status);
  const learningText = !hasSensors
    ? 'Noch keine Sensoren eingerichtet'
    : learning?.completed
      ? 'Verhaltensprofil vollständig gelernt'
      : learning
        ? `Sentero lernt aktuell den gewohnten Tagesablauf kennen. Tag ${learning.day} von ${learning.days}`
        : 'Sentero lernt aktuell den gewohnten Tagesablauf kennen.';
  const headline = !hasSensors ? 'Noch keine Bewertung möglich' : meta.label;
  const summary = !hasSensors
    ? 'Verbinden Sie zuerst Sensoren, damit Sentero persönliche Routinen erkennen kann.'
    : behavior?.summary || 'Sentero baut ein persönliches Normalverhalten auf.';
  return (
    <article className={`sc-behavior-overview ${meta.tone}`} aria-label="Verhaltensanalyse">
      <div>
        <span aria-hidden="true">{meta.dot}</span>
        <div>
          <small>Verhaltensanalyse</small>
          <strong>{headline}</strong>
        </div>
      </div>
      <p>{summary}</p>
      <footer>
        <span>{learningText}</span>
        {typeof behavior?.anomaly_score === 'number' && <em>Score {behavior.anomaly_score}/100</em>}
      </footer>
    </article>
  );
}

function behaviorMeta(status?: string | null) {
  if (status === 'red') return { tone: 'red', dot: '🔴', label: 'Kritisch' };
  if (status === 'orange') return { tone: 'orange', dot: '🟠', label: 'Auffällig' };
  if (status === 'yellow') return { tone: 'yellow', dot: '🟡', label: 'Leichte Abweichung' };
  return { tone: 'green', dot: '🟢', label: 'Normal' };
}

function Fact({ label, value, highlight }: { label: string; value: string; highlight?: boolean }) {
  return (
    <div className="sc-simple-fact">
      <span>{label}</span>
      <strong className={highlight ? 'highlight' : ''}>{value}</strong>
    </div>
  );
}

function getDashboardState({ error, hasSensors, latest, behavior }: { error: string; hasSensors: boolean; latest: boolean; behavior: SenteroBehaviorAssessment | null }) {
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
    return {
      tone: 'learning',
      kicker: 'Sensoren verbunden',
      headline: 'Sentero lernt.',
      copy: 'Sensoren sind eingerichtet, aber heute wurde noch keine verwertbare Aktivität erkannt.',
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

function latestPresenceRole(roles: SenteroSensorRole[]) {
  return roles
    .filter((role) => role.configured && role.reachable !== false && isPresenceRole(role))
    .sort((a, b) => timestamp(b.last_changed || b.last_updated || b.updated_at) - timestamp(a.last_changed || a.last_updated || a.updated_at))[0];
}

function isPresenceRole(role: SenteroSensorRole) {
  return role.role.endsWith('presence') || ['motion', 'occupancy', 'presence'].includes(String(role.device_class || ''));
}

function firstActivityTime(roles: SenteroSensorRole[]) {
  const value = roles
    .map((role) => timestamp(role.last_changed || role.last_updated || role.updated_at))
    .filter(Boolean)
    .sort((a, b) => a - b)[0];
  return value ? formatTime(new Date(value)) : '';
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

function activitySlotsFromRoles(roles: SenteroSensorRole[]) {
  const slots = [6, 9, 12, 15, 18, 21].map((hour) => ({ hour, label: String(hour).padStart(2, '0'), active: false }));
  const today = new Date();
  for (const role of roles) {
    const value = timestamp(role.last_changed || role.last_updated || role.updated_at);
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

function formatTime(date: Date) {
  return new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit' }).format(date);
}

function formatDateTime(date: Date) {
  return new Intl.DateTimeFormat('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }).format(date);
}

function formatHeaderDate(date: Date) {
  return new Intl.DateTimeFormat('de-DE', { weekday: 'long', hour: '2-digit', minute: '2-digit' }).format(date);
}
