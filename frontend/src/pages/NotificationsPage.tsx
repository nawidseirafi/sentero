import { useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, Info, ShieldAlert } from 'lucide-react';
import { api, type SenteroBehaviorHints, type SenteroTimelineEvent } from '@shared/api/client';

export function NotificationsPage() {
  const [hints, setHints] = useState<SenteroBehaviorHints | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    api.senteroBehaviorHints(14)
      .then((result) => {
        if (!active) return;
        setHints(result);
        setError('');
      })
      .catch((err) => {
        if (!active) return;
        setHints(null);
        setError(err instanceof Error ? err.message : 'Hinweise konnten nicht geladen werden.');
      });
    return () => { active = false; };
  }, []);

  return (
    <section className="sc-page sc-hints-page" aria-label="Sentero Hinweise">
      <header className="sc-page-header-quiet">
        <p>Hinweise</p>
        <h1>Auffälligkeiten und Normalisierung</h1>
      </header>
      {error && <p className="sc-inline-error" role="status">{error}</p>}
      <HintGroup title="Aktuell" empty="Keine kritischen Hinweise" items={hints?.current || []} />
      <HintGroup title="Beobachtet" empty="Keine offenen Beobachtungen" items={hints?.observed || []} />
      <HintGroup title="Erledigt / normalisiert" empty="Keine normalisierten Hinweise im Zeitraum" items={hints?.resolved || []} />
    </section>
  );
}

function HintGroup({ title, empty, items }: { title: string; empty: string; items: SenteroTimelineEvent[] }) {
  return (
    <section className="sc-soft-panel sc-hint-group">
      <div className="sc-panel-title-row">
        <h2>{title}</h2>
        <span>{items.length || 'Keine'}</span>
      </div>
      <div className="sc-hint-list">
        {items.length ? items.map((item) => <HintItem key={item.id} item={item} />) : (
          <article className="sc-hint-row normal">
            <CheckCircle2 size={20} aria-hidden="true" />
            <div><strong>{empty}</strong><p>Sentero zeigt nur betreuungsrelevante Auffälligkeiten in dieser Ansicht.</p></div>
          </article>
        )}
      </div>
    </section>
  );
}

function HintItem({ item }: { item: SenteroTimelineEvent }) {
  return (
    <article className={`sc-hint-row ${item.severity}`}>
      <SeverityIcon severity={item.severity} />
      <div>
        <time>{formatClock(item.start_time)}</time>
        <strong>{item.title}</strong>
        <p>{[item.description, item.status_label].filter(Boolean).join(' ')}</p>
        {item.baseline_comparison && <small>{item.baseline_comparison}</small>}
      </div>
    </article>
  );
}

function SeverityIcon({ severity }: { severity?: string }) {
  if (severity === 'critical') return <ShieldAlert size={20} aria-hidden="true" />;
  if (severity === 'warning') return <AlertTriangle size={20} aria-hidden="true" />;
  return <Info size={20} aria-hidden="true" />;
}

function formatClock(value?: string | null) {
  const date = new Date(value || '');
  if (!Number.isFinite(date.getTime())) return '';
  return new Intl.DateTimeFormat('de-DE', { weekday: 'short', hour: '2-digit', minute: '2-digit' }).format(date);
}
