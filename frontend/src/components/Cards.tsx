import type { ReactNode } from 'react';

type TrustedContact = {
  name: string;
  relation?: string;
  email?: string;
  note?: string;
};

export function InsightCard({ title, text, children }: { title: string; text: string; children?: ReactNode }) {
  return (
    <article className="sc-insight-card">
      <span>Hinweis</span>
      <h3>{title}</h3>
      <p>{text}</p>
      {children}
    </article>
  );
}

export function TrustedContactCard({ contact }: { contact: TrustedContact }) {
  return (
    <article className="sc-contact-card">
      <div className="sc-contact-avatar">{contact.name.slice(0, 1)}</div>
      <div>
        <strong>{contact.name}</strong>
        <p>{contact.relation || 'Kontakt'}</p>
        <small>{contact.email}</small>
      </div>
      <em>{contact.note || ''}</em>
    </article>
  );
}

export function SelectableOptionCard({ label, active, onClick }: { label: string; active?: boolean; onClick: () => void }) {
  return (
    <button className={`sc-option-card ${active ? 'active' : ''}`} type="button" onClick={onClick}>
      <span />
      {label}
    </button>
  );
}
