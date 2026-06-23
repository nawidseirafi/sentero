import { HeartPulse, Moon, Pill, ShieldCheck } from 'lucide-react';

export function SenteroPage() {
  return (
    <section className="sentero-page">
      <p className="eyebrow">Sentero</p>
      <h2>Persoenlicher Status</h2>
      <p className="sentero-page-lead">Alltag, Wohlbefinden und wichtige Erinnerungen auf einen Blick.</p>
      <div className="sentero-card-grid">
        <article className="sentero-card">
          <span><ShieldCheck size={18} /> Status</span>
          <h3>Alles normal</h3>
          <p>Keine Auffaelligkeiten im aktuellen Tagesverlauf.</p>
        </article>
        <article className="sentero-card">
          <span><HeartPulse size={18} /> Aktivitaet</span>
          <h3>Regelmaessig aktiv</h3>
          <p>Bewegung wurde in mehreren Wohnbereichen erkannt.</p>
        </article>
        <article className="sentero-card">
          <span><Pill size={18} /> Erinnerung</span>
          <h3>20:00 Medikamente</h3>
          <p>Naechste geplante Erinnerung fuer den Abend.</p>
        </article>
        <article className="sentero-card">
          <span><Moon size={18} /> Ruhephase</span>
          <h3>Noch nicht begonnen</h3>
          <p>Die Nachtruhe wird spaeter gesondert bewertet.</p>
        </article>
      </div>
    </section>
  );
}
