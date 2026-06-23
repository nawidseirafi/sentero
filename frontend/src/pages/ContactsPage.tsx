import { useEffect, useState } from 'react';
import { api, type SenteroTrustedContact } from '@shared/api/client';

export function ContactsPage() {
  const [contacts, setContacts] = useState<SenteroTrustedContact[]>([]);

  useEffect(() => {
    void api.senteroSetupStatus().then((status) => setContacts(status.trusted_contacts || [])).catch(() => undefined);
  }, []);

  return (
    <section className="sc-page">
      <div className="sc-hero-copy">
        <p className="sc-kicker">Kontakte</p>
        <h1>Vertrauenspersonen.</h1>
        <p>{contacts.length ? 'Diese Personen werden bei wichtigen Hinweisen informiert.' : 'Noch keine vertraute Person hinterlegt.'}</p>
      </div>
      <div className="sc-contact-list">
        {contacts.map((contact) => (
          <article className="sc-contact-card" key={contact.id}>
            <div className="sc-contact-avatar">{contact.name.slice(0, 1)}</div>
            <div>
              <strong>{contact.name}</strong>
              <p>{contact.relationship || 'Kontakt'}</p>
              <small>{contact.email || 'Keine E-Mail hinterlegt'}</small>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
