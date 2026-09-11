import { useEffect, useState } from 'react';
import { LogIn, Unplug } from 'lucide-react';
import { api, MicrosoftMailStatus } from '../shared/api/client';

export function isMicrosoftMail(form: Record<string, string>) {
  return form.auth_method === 'microsoft_oauth2' || form.auth_method === 'OAuth2/Modern Auth'
    || ['outlook.office365.com', 'outlook.office.com', 'imap-mail.outlook.com', 'smtp.office365.com', 'smtp-mail.outlook.com'].some(host => host === form.imap_host?.toLowerCase() || host === form.smtp_host?.toLowerCase())
    || /@(outlook\.(com|de)|hotmail\.(com|de)|live\.(com|de)|msn\.com)$/i.test(form.smtp_user || '');
}

export default function MicrosoftMailConnect({ email }: { email: string }) {
  const [state, setState] = useState<MicrosoftMailStatus>({ status: 'reconnect_required' });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    if (state.status !== 'pending') return;
    const timer = setTimeout(() => setState({ status: 'expired' }), Math.max(0, state.expires_in || 0) * 1000);
    return () => clearTimeout(timer);
  }, [state.status, state.expires_in, state.user_code]);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const next = await api.microsoftMailStatus();
        if (active) { setState(next); setError(''); }
      } catch { if (active) setError('Verbindungsstatus momentan nicht erreichbar.'); }
      if (active) timer = setTimeout(poll, 3000);
    }
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [email]);
  async function connect() {
    setBusy(true); setError('');
    try { setState(await api.startMicrosoftMail(email)); }
    catch (err) { setError(err instanceof Error ? err.message : 'Anmeldung momentan nicht möglich.'); }
    finally { setBusy(false); }
  }
  async function disconnect() {
    setBusy(true);
    try { setState(await api.disconnectMicrosoftMail()); }
    catch { setError('Verbindung konnte nicht getrennt werden.'); }
    finally { setBusy(false); }
  }
  const connected = state.status === 'connected' && state.account?.toLowerCase() === email.trim().toLowerCase();
  return <div className="sc-microsoft-mail" aria-live="polite">
    {connected ? <p>Microsoft-Konto verbunden</p> : state.status === 'pending' ? <p>
      Öffnen Sie <a href="https://microsoft.com/devicelogin" target="_blank" rel="noreferrer">microsoft.com/devicelogin</a> und geben Sie diesen Code ein: <strong>{state.user_code}</strong>
    </p> : <p>{state.status === 'expired' ? 'Der Code ist abgelaufen. Bitte erneut verbinden.' : state.status === 'failed' ? 'Anmeldung fehlgeschlagen. Bitte das angegebene Microsoft-Konto verwenden.' : state.account ? 'Microsoft-Konto erneut verbinden.' : 'Microsoft-Konto verbinden.'}</p>}
    {error && <p role="alert">{error}</p>}
    <button type="button" onClick={connect} disabled={busy || state.status === 'pending'}><LogIn size={18} /> Mit Microsoft verbinden</button>
    {(connected || state.status === 'pending') && <button type="button" onClick={disconnect} disabled={busy}><Unplug size={18} /> Verbindung trennen</button>}
  </div>;
}
