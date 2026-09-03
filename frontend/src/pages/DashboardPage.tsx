import { useEffect, useMemo, useState } from 'react';
import { Activity, AlertTriangle, CheckCircle2, ChevronDown, ChevronRight, Clock3, DoorOpen, Info, Moon, ShieldAlert, Sun } from 'lucide-react';
import { api, type SenteroBehaviorDay, type SenteroBehaviorTrends, type SenteroSensorRole, type SenteroTimelineEvent, type SenteroTrendSeries } from '@shared/api/client';
import { MainActivityTrendChart, RangeSelector, formatTrendValue } from '../components/DashboardCharts';

type LoadState = 'idle' | 'loading' | 'ready' | 'error';

export function DashboardPage() {
  const [selectedDate, setSelectedDate] = useState(todayKey());
  const [day, setDay] = useState<SenteroBehaviorDay | null>(null);
  const [liveDay, setLiveDay] = useState<SenteroBehaviorDay | null>(null);
  const [trends, setTrends] = useState<SenteroBehaviorTrends | null>(null);
  const [roles, setRoles] = useState<SenteroSensorRole[]>([]);
  const [trendRange, setTrendRange] = useState<7 | 14 | 30>(14);
  const [initialDateResolved, setInitialDateResolved] = useState(false);
  const [state, setState] = useState<LoadState>('idle');
  const [error, setError] = useState('');
  const [openEventId, setOpenEventId] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    async function loadTrends() {
      const [trendResult, roleResult] = await Promise.all([
        api.senteroBehaviorTrends(trendRange).catch(() => null),
        api.senteroSensorRoles(true).catch(() => ({ sensor_roles: [] })),
      ]);
      if (!active) return;
      setTrends(trendResult);
      setRoles(roleResult.sensor_roles || []);
      const dates = availableDates(trendResult);
      if (!initialDateResolved) {
        setSelectedDate(preferredInitialDate(trendResult));
        setInitialDateResolved(true);
      } else if (dates.length && !dates.includes(selectedDate)) {
        setSelectedDate(latestDateWithData(trendResult) || dates[dates.length - 1]);
      }
    }
    void loadTrends();
    return () => {
      active = false;
    };
  }, [trendRange, initialDateResolved, selectedDate]);

  useEffect(() => {
    let active = true;
    async function loadDay() {
      setState('loading');
      try {
        const result = await api.senteroBehaviorDay(selectedDate, selectedDate === todayKey()).catch(() => null);
        if (!active) return;
        setDay(result);
        setError(result ? '' : 'Der ausgewählte Tag konnte gerade nicht geladen werden.');
        setState(result ? 'ready' : 'error');
        setOpenEventId(null);
      } catch (err) {
        if (!active) return;
        setError(err instanceof Error ? err.message : 'Sentero konnte den Tag nicht laden.');
        setState('error');
      }
    }
    void loadDay();
    const timer = window.setInterval(() => {
      if (selectedDate === todayKey()) void loadDay();
    }, 30000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [selectedDate]);

  useEffect(() => {
    let active = true;

    async function loadLiveStatus() {
      const [todayResult, roleResult] = await Promise.all([
        api.senteroBehaviorDay(todayKey(), true).catch(() => null),
        api.senteroSensorRoles(true).catch(() => ({ sensor_roles: [] })),
      ]);
      if (!active) return;
      setLiveDay(todayResult);
      setRoles(roleResult.sensor_roles || []);
    }

    void loadLiveStatus();
    const timer = window.setInterval(loadLiveStatus, 30000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, []);

  const currentPresence = useMemo(() => currentPresenceRole(roles), [roles]);
  const latestActivity = useMemo(() => latestActivityFromDay(liveDay, roles), [liveDay, roles]);
  const topStatus = useMemo(() => topStatusLabel(liveDay, roles, currentPresence), [liveDay, roles, currentPresence]);
  const liveHealth = useMemo(() => dashboardHealthState(liveDay, roles), [liveDay, roles]);
  const activitySeries = useMemo(() => trends?.series?.find((item) => item.metric === 'activity'), [trends]);
  const notableEvents = (day?.timeline_events || []).filter(isNotableEvent);

  return (
    <section className="sc-page sc-dashboard-compact" aria-label="Sentero Dashboard">
      <header className="sc-today-statusbar">
        <div className="sc-dashboard-brand">
          <div className="sc-dashboard-brand-line">
            <span
              className={`sc-brand-status-dot ${liveHealth}`}
              aria-label={dashboardHealthLabel(liveHealth)}
              role="img"
            />
            <strong>Sentero</strong>
          </div>
          <span>Heute · {formatTime(new Date())}</span>
        </div>

        <div className="sc-live-overview" aria-label="Aktueller Sentero Status">
          <div className="sc-live-primary">
            <span className={`sc-live-icon ${currentPresence ? 'home' : ''}`} aria-hidden="true">
              <Activity size={18} />
            </span>
            <span>
              <strong>{topStatus}</strong>
              <small>{latestActivity.label}</small>
            </span>
          </div>
        </div>
      </header>

      {error && <p className="sc-inline-error" role="status">{error}</p>}

      <section className="sc-dashboard-card sc-activity-overview" aria-label="Aktivitätsverlauf">
        <div className="sc-chart-head">
          <div>
            <p>Verlauf sehen</p>
            <h1>Aktivitätsverlauf</h1>
            <span>{activitySeries?.interpretation || 'Noch nicht genügend Verlaufsdaten für einen persönlichen Vergleich.'}</span>
          </div>
          <RangeSelector value={trendRange} onChange={setTrendRange} />
        </div>

        <div className="sc-baseline-legend">
          <span><i aria-hidden="true" /> Persönlicher Bereich</span>
          <strong>{baselineLabel(activitySeries)}</strong>
        </div>

        <MainActivityTrendChart
          series={activitySeries}
          points={trends?.points || []}
          selectedDate={selectedDate}
          onSelectDate={setSelectedDate}
        />
      </section>

      <section className="sc-selected-day" aria-label="Ausgewählter Tag">
        <div className="sc-selected-day-head">
          <div>
            <p>Tag verstehen</p>
            <h2>{day ? formatFullDay(day.date) : formatFullDay(selectedDate)}</h2>
            <strong>{day?.summary?.headline || emptyDayHeadline(state)}</strong>
          </div>
          <span>Tagesüberblick</span>
        </div>

        <div className="sc-day-understand-grid">
          <DayMetrics day={day} trends={trends} selectedDate={selectedDate} />
          <CompactDayEvents events={day?.timeline_events || []} />
        </div>

        <section className="sc-day-interpretation" aria-label="Sentero Tagesinterpretation">
          <div>
            <Info size={20} aria-hidden="true" />
            <h3>Tageszusammenfassung</h3>
          </div>
          <p>{dayInterpretation(day)}</p>
          <small>{dataQualityMessage(day, trends)}</small>
        </section>

        <section className="sc-day-anomalies" aria-label="Auffälligkeiten des ausgewählten Tages">
          <div className="sc-subsection-title">
            <h3>Auffälligkeiten</h3>
            <span>{notableEvents.length || 'Keine'}</span>
          </div>
          {notableEvents.length ? (
            <div className="sc-anomaly-list">
              {notableEvents.map((event) => (
                <DayAnomaly
                  key={event.id}
                  event={event}
                  open={openEventId === event.id}
                  onToggle={() => setOpenEventId(openEventId === event.id ? null : event.id)}
                />
              ))}
            </div>
          ) : (
            <p className="sc-empty-copy">Keine Auffälligkeiten erkannt.</p>
          )}
        </section>

      </section>
    </section>
  );
}

function DayMetrics({ day, trends, selectedDate }: { day: SenteroBehaviorDay | null; trends: SenteroBehaviorTrends | null; selectedDate: string }) {
  const summary = day?.summary;
  const point = trends?.points?.find((item) => item.date === selectedDate);
  const awaySeries = trends?.series?.find((item) => item.metric === 'away_time');
  const awayPoint = awaySeries?.points.find((item) => item.timestamp === selectedDate);
  const metrics = [
    { label: 'Aufgestanden', value: summary?.wakeup_time || (selectedDate === todayKey() ? 'Noch nicht erkannt' : 'Keine Daten'), note: wakeupNote(summary), icon: Sun, tone: 'normal' },
    { label: 'Zeit außer Haus', value: awayPoint?.has_data === false ? 'Keine Daten' : formatTrendValue(awayPoint?.value ?? point?.away_minutes, 'minutes'), note: 'Aus Türereignissen abgeleitet', icon: DoorOpen, tone: 'normal' },
    { label: 'Längste Ruhephase', value: summary?.longest_inactivity || 'Nicht erkannt', note: restNote(summary), icon: Clock3, tone: summary?.longest_inactivity_minutes && summary.longest_inactivity_minutes >= 180 ? 'notice' : 'normal' },
    { label: 'Nachtruhe', value: sleepValue(summary, selectedDate), note: summary?.last_activity ? 'Aus letzter Aktivität erkannt' : 'Noch nicht erkannt', icon: Moon, tone: 'normal' },
    { label: 'Auffälligkeiten', value: summary ? String(summary.anomaly_count) : 'Keine Daten', note: anomalyNote(summary), icon: AlertTriangle, tone: summary?.anomaly_count ? 'notice' : 'normal' },
  ];
  return (
    <dl className="sc-day-metrics">
      {metrics.map((item) => {
        const Icon = item.icon;
        return (
          <div key={item.label} className={item.tone}>
            <dt><span><Icon size={26} aria-hidden="true" /></span>{item.label}</dt>
            <dd className={String(item.value).length > 8 ? 'compact' : undefined}>{item.value}</dd>
            <small>{item.note}</small>
            <ChevronRight size={24} aria-hidden="true" />
          </div>
        );
      })}
    </dl>
  );
}

function DayAnomaly({ event, open, onToggle }: { event: SenteroTimelineEvent; open: boolean; onToggle: () => void }) {
  return (
    <article className={`sc-day-anomaly ${event.severity}`}>
      <button type="button" onClick={onToggle} aria-expanded={open} aria-controls={`event-${event.id}`}>
        <SeverityIcon severity={event.severity} />
        <span>
          <time>{formatRange(event)}</time>
          <strong>{event.title}</strong>
          <small>{[roomLabel(event.room), event.duration, event.baseline_comparison].filter(Boolean).join(' · ') || event.description}</small>
          <em>{event.status_label || statusLabel(event.status)}</em>
        </span>
        <ChevronDown className={open ? 'open' : ''} size={20} aria-hidden="true" />
      </button>
      {open && (
        <div className="sc-event-detail" id={`event-${event.id}`}>
          <dl>
            <div><dt>Wann</dt><dd>{formatRange(event)}</dd></div>
            {event.room && <div><dt>Wo</dt><dd>{roomLabel(event.room)}</dd></div>}
            {event.duration && <div><dt>Dauer</dt><dd>{event.duration}</dd></div>}
            <div><dt>Status</dt><dd>{event.status_label || statusLabel(event.status)}</dd></div>
            {event.baseline_comparison && <div><dt>Vergleich</dt><dd>{event.baseline_comparison}</dd></div>}
          </dl>
          <details>
            <summary>Warum wurde das erkannt?</summary>
            {event.observations?.length ? (
              <ul>{event.observations.map((item) => <li key={item}>{item}</li>)}</ul>
            ) : <p>Sentero hat dieses Ereignis aus den normalisierten Tagesdaten abgeleitet.</p>}
          </details>
        </div>
      )}
    </article>
  );
}

function CompactDayEvents({ events }: { events: SenteroTimelineEvent[] }) {
  const compact = events.filter((event) => event.category !== 'anomaly').slice(0, 6);
  return (
    <section className="sc-compact-events" aria-label="Kompakter Tagesverlauf">
      <div className="sc-subsection-title">
        <h3>Tagesverlauf</h3>
      </div>
      {compact.length ? (
        <ol>
          {compact.map((event) => (
            <li key={event.id} className={event.severity}>
              <time>{formatClock(event.start_time)}</time>
              <span className="sc-compact-event-marker"><SeverityIcon severity={event.severity} /></span>
              <span>
                <strong>{event.title}</strong>
                <small>{event.description}</small>
              </span>
            </li>
          ))}
        </ol>
      ) : (
        <p className="sc-empty-copy">Für diesen Tag liegen keine semantischen Tagesereignisse vor.</p>
      )}
    </section>
  );
}

function availableDates(trends: SenteroBehaviorTrends | null) {
  return (trends?.points || [])
    .filter((point) => point.date <= todayKey())
    .map((point) => point.date);
}

function preferredInitialDate(trends: SenteroBehaviorTrends | null) {
  const today = trends?.points.find((point) => point.date === todayKey());
  if (today?.has_data) return today.date;
  return latestDateWithData(trends) || todayKey();
}

function latestDateWithData(trends: SenteroBehaviorTrends | null) {
  return [...(trends?.points || [])].reverse().find((point) => point.date <= todayKey() && point.has_data)?.date;
}

function baselineLabel(series?: SenteroTrendSeries) {
  if (!series) return 'wird gelernt';
  const lower = series.baseline.lower;
  const upper = series.baseline.upper;
  if (lower == null || upper == null) return 'wird gelernt';
  return `${formatTrendValue(lower, series.unit)} bis ${formatTrendValue(upper, series.unit)}`;
}

function dataQualityMessage(day: SenteroBehaviorDay | null, trends: SenteroBehaviorTrends | null) {
  const dayMessage = stringFromRecord(day?.data_quality, 'message');
  if (dayMessage) return dayMessage;
  const trendMessage = stringFromRecord(trends?.data_quality, 'message');
  if (trendMessage) return trendMessage;
  return 'Noch nicht genügend Verlaufsdaten für einen persönlichen Vergleich.';
}

function dayInterpretation(day: SenteroBehaviorDay | null) {
  if (!day) return 'Für diesen Tag liegen noch keine ausreichenden Informationen vor.';
  const summary = day.summary;
  const assessment = day.assessment?.assessment_time?.slice(0, 10) === day.date ? day.assessment?.summary : '';
  if (summary.anomaly_count === 0 && summary.active_minutes > 0) {
    return assessment || 'Der Tagesverlauf lag insgesamt im persönlichen Bereich. Es wurden keine kritischen Ereignisse erkannt.';
  }
  if (summary.critical_count > 0) {
    return assessment || 'Sentero hat ein kritisches Ereignis im Tagesverlauf erkannt. Bitte die Details prüfen.';
  }
  if (summary.anomaly_count > 0) {
    return assessment || 'Sentero hat einzelne Auffälligkeiten erkannt. Der Status der Ereignisse ist unten zusammengefasst.';
  }
  return 'Für diesen Tag liegen noch nicht genügend Daten für einen zuverlässigen Vergleich mit dem persönlichen Verhalten vor.';
}

function sleepValue(summary: SenteroBehaviorDay['summary'] | undefined, selectedDate: string) {
  if (!summary?.last_activity) return selectedDate === todayKey() ? 'Noch nicht erkannt' : 'Keine Daten';
  const hour = new Date(summary.last_activity).getHours();
  if (hour < 20) return selectedDate === todayKey() ? 'Noch nicht erkannt' : formatClock(summary.last_activity);
  return formatClock(summary.last_activity);
}

function wakeupNote(summary: SenteroBehaviorDay['summary'] | undefined) {
  const comparison = stringFromRecord(summary?.deviations, 'wakeup_comparison');
  if (comparison) return comparison;
  return summary?.wakeup_time ? 'Im persönlichen Bereich' : 'Keine Aktivitätsdaten';
}

function restNote(summary: SenteroBehaviorDay['summary'] | undefined) {
  const minutes = summary?.longest_inactivity_minutes || 0;
  if (!minutes) return 'Keine längere Ruhephase erkannt';
  if (minutes >= 180) return 'Etwas länger als gewöhnlich';
  return 'Im üblichen Bereich';
}

function anomalyNote(summary: SenteroBehaviorDay['summary'] | undefined) {
  if (!summary) return 'Keine Daten';
  if (summary.critical_count > 0) return `${summary.critical_count} kritische Ereignisse`;
  if (summary.anomaly_count === 1) return '1 kleinere Abweichung';
  if (summary.anomaly_count > 1) return `${summary.anomaly_count} kleinere Abweichungen`;
  return 'Keine Auffälligkeiten erkannt';
}

function isNotableEvent(event: SenteroTimelineEvent) {
  return ['notice', 'warning', 'critical'].includes(String(event.severity));
}

function emptyDayHeadline(state: LoadState) {
  if (state === 'loading') return 'Tagesdaten werden geladen';
  return 'Noch keine Auswertung';
}

function SeverityIcon({ severity }: { severity?: string }) {
  if (severity === 'critical') return <ShieldAlert size={18} aria-hidden="true" />;
  if (severity === 'warning') return <AlertTriangle size={18} aria-hidden="true" />;
  if (severity === 'notice') return <Info size={18} aria-hidden="true" />;
  return <CheckCircle2 size={18} aria-hidden="true" />;
}


function dashboardHealthState(day: SenteroBehaviorDay | null, roles: SenteroSensorRole[]): 'green' | 'gray' | 'red' {
  const configured = roles.filter((role) => role.configured);
  if (!configured.length || !day) return 'gray';

  const reachable = configured.filter((role) => role.reachable !== false);
  if (!reachable.length) return 'red';
  if ((day.summary?.critical_count || 0) > 0) return 'red';

  return 'green';
}

function dashboardHealthLabel(state: 'green' | 'gray' | 'red') {
  if (state === 'red') return 'Sentero Status: Aufmerksamkeit erforderlich';
  if (state === 'gray') return 'Sentero Status: noch keine belastbare Bewertung';
  return 'Sentero Status: normal';
}

function currentPresenceRole(roles: SenteroSensorRole[]) {
  return roles
    .filter((role) => role.configured && role.reachable !== false && isPresenceRole(role) && roleSignalsPresence(role))
    .sort((a, b) => timestamp(b.last_updated || b.last_changed || b.updated_at) - timestamp(a.last_updated || a.last_changed || a.updated_at))[0];
}

function latestActivityFromDay(day: SenteroBehaviorDay | null, roles: SenteroSensorRole[]) {
  const latestEvent = [...(day?.timeline_events || [])].reverse().find((event) => event.category !== 'anomaly');
  const latestRole = roles
    .filter((role) => role.configured && role.reachable !== false && isPresenceRole(role))
    .map((role) => ({ time: role.last_changed || role.last_updated || role.updated_at || '', room: role.room }))
    .sort((a, b) => timestamp(b.time) - timestamp(a.time))[0];
  const time = latestEvent?.start_time || latestRole?.time;
  const room = latestEvent?.room || latestRole?.room;
  return { label: time ? `Letzte Aktivität ${relativeTime(time)}${room ? ` · ${roomLabel(room)}` : ''}` : 'Noch keine Aktivität erkannt' };
}

function topStatusLabel(day: SenteroBehaviorDay | null, roles: SenteroSensorRole[], currentPresence?: SenteroSensorRole) {
  if (currentPresence) return `Aktivität erkannt${currentPresence.room ? ` · ${roomLabel(currentPresence.room)}` : ''}`;
  const configured = roles.filter((role) => role.configured);
  const reachable = configured.filter((role) => role.reachable !== false);
  if (!configured.length) return 'Noch keine Sensoren eingerichtet';
  if (!reachable.length) return 'Sensordaten derzeit nicht verfügbar';
  if (!day) return 'Aktueller Tagesstatus wird geladen';

  const latestEvent = [...(day.timeline_events || [])]
    .reverse()
    .find((event) => event.category !== 'anomaly');

  if (!latestEvent) return 'Aktuell keine Bewegung erkannt';
  const minutes = Math.max(0, Math.round((Date.now() - timestamp(latestEvent.start_time)) / 60000));
  if (minutes < 60) return 'Aktuell keine Bewegung erkannt';
  if (minutes < 6 * 60) return `Seit ${durationLabel(minutes)} ruhig`;
  return 'Seit längerer Zeit keine Aktivität erkannt';
}

function isPresenceRole(role: SenteroSensorRole) {
  return role.role.endsWith('presence') || ['motion', 'occupancy', 'presence'].includes(String(role.device_class || ''));
}

function roleSignalsPresence(role: SenteroSensorRole) {
  if (role.presence === true) return true;
  const motion = String(role.motion_state || role.motion || role.state || '').toLowerCase();
  return ['moving', 'move', 'movement', 'motion', 'active', 'detected', 'large', 'small', 'static', 'static_target', 'still', 'stationary', 'present', 'presence', 'on', 'true'].includes(motion);
}

function roomLabel(room?: string | null) {
  const labels: Record<string, string> = { living_room: 'Wohnzimmer', kitchen: 'Küche', bathroom: 'Badezimmer', bedroom: 'Schlafzimmer', hallway: 'Flur', entrance: 'Eingang' };
  return room ? labels[room] || room : '';
}

function statusLabel(status?: string | null) {
  if (status === 'active') return 'Aktuell';
  if (status === 'resolved') return 'Normalisiert';
  return 'Beobachtet';
}

function formatRange(event: SenteroTimelineEvent) {
  if (!event.end_time) return formatClock(event.start_time);
  return `${formatClock(event.start_time)}-${formatClock(event.end_time)}`;
}

function formatClock(value: string) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value.slice(11, 16) || value;
  return new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit' }).format(date);
}

function formatTime(date: Date) {
  return new Intl.DateTimeFormat('de-DE', { hour: '2-digit', minute: '2-digit' }).format(date);
}

function formatFullDay(value: string) {
  const date = localDate(value);
  return new Intl.DateTimeFormat('de-DE', { weekday: 'long', day: 'numeric', month: 'long' }).format(date);
}

function relativeTime(value: string) {
  const time = timestamp(value);
  if (!time) return 'unbekannt';
  const minutes = Math.max(0, Math.round((Date.now() - time) / 60000));
  if (minutes < 1) return 'gerade eben';
  if (minutes < 24 * 60) return `vor ${durationLabel(minutes)}`;
  const date = new Date(time);
  return `am ${new Intl.DateTimeFormat('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }).format(date)}`;
}

function durationLabel(minutes: number) {
  if (minutes < 60) return `${minutes} Min.`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} Std. ${rest} Min.` : `${hours} Std.`;
}

function timestamp(value?: string | null) {
  if (!value) return 0;
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? date.getTime() : 0;
}

function todayKey() {
  return toLocalKey(new Date());
}

function localDate(value: string) {
  const [year, month, day] = value.split('-').map(Number);
  return new Date(year, (month || 1) - 1, day || 1);
}

function toLocalKey(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function stringFromRecord(value: unknown, key: string) {
  if (!value || typeof value !== 'object') return '';
  const item = (value as Record<string, unknown>)[key];
  return typeof item === 'string' ? item : '';
}
