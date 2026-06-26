import { useEffect, useState } from 'react';
import { Wifi } from 'lucide-react';
import { api, type BoxNetworkStatus } from '@shared/api/client';

export function BoxNetworkSetup() {
  const [status, setStatus] = useState<BoxNetworkStatus | null>(null);
  const [form, setForm] = useState({ ssid: '', password: '' });
  const [message, setMessage] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    void api.boxNetworkStatus().then(setStatus).catch(() => undefined);
  }, []);

  async function submit() {
    setSaving(true);
    setMessage('Sentero verbindet sich jetzt mit Ihrem WLAN.');
    try {
      const result = await api.saveBoxNetworkWifi(form);
      setStatus(result.status);
      setMessage(`${result.message} Danach erreichen Sie Sentero unter sentero.local.`);
      setForm({ ssid: '', password: '' });
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'WLAN konnte nicht gespeichert werden.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <main className="sc-box-setup">
      <section className="sc-box-setup-card">
        <span className="sc-box-setup-icon"><Wifi size={30} /></span>
        <h1>Sentero Netzwerk einrichten</h1>
        <p>Bitte verbinden Sie Sentero mit Ihrem Heim-WLAN.</p>
        <label>
          WLAN-Name
          <input value={form.ssid} onChange={(event) => setForm((value) => ({ ...value, ssid: event.target.value }))} placeholder="Mein WLAN" />
        </label>
        <label>
          WLAN-Passwort
          <input type="password" value={form.password} onChange={(event) => setForm((value) => ({ ...value, password: event.target.value }))} placeholder="WLAN-Passwort" />
        </label>
        <button type="button" onClick={() => void submit()} disabled={saving}>
          Verbinden
        </button>
        <small>{message || status?.message || 'Danach erreichen Sie Sentero unter sentero.local.'}</small>
      </section>
    </main>
  );
}
