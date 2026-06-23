import { useEffect, useState } from 'react';
import { Bell, CheckCircle2 } from 'lucide-react';
import { api, type MessageCenterItem } from '@shared/api/client';

export function NotificationsPage() {
  const [messages, setMessages] = useState<MessageCenterItem[]>([]);

  useEffect(() => {
    api.messages(20).then((result) => setMessages(result.messages)).catch(() => setMessages([]));
  }, []);

  return (
    <section className="sentero-page">
      <p className="eyebrow">Benachrichtigungen</p>
      <h2>Offene Hinweise</h2>
      <p className="sentero-page-lead">Nur betreuungsrelevante Hinweise gehoeren in diese Ansicht.</p>
      <div className="sentero-notification-list">
        {messages.length ? messages.map((message) => (
          <article className="sentero-notification" key={message.id}>
            <span><Bell size={18} /></span>
            <div>
              <strong>{message.title}</strong>
              <p>{message.message}</p>
            </div>
          </article>
        )) : (
          <article className="sentero-notification">
            <span><CheckCircle2 size={18} /></span>
            <div>
              <strong>Keine offenen Hinweise</strong>
              <p>Aktuell gibt es keine neuen Betreuungsbenachrichtigungen.</p>
            </div>
          </article>
        )}
      </div>
    </section>
  );
}
