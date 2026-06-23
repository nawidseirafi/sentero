import { FormEvent, useState } from 'react';
import { LockKeyhole } from 'lucide-react';
import { useSenteroAuth } from '../auth/SenteroAuthContext';
import senteroLogo from '../assets/logo.png';
import '../styles/sentero.css';

export function LoginPage({ mode, onLoggedIn }: { mode: 'setup' | 'login'; onLoggedIn: (target?: 'setup') => void }) {
  const { setup, login, forgotPassword, resetPassword } = useSenteroAuth();
  const resetToken = new URLSearchParams(window.location.search).get('reset_token') || '';
  const [view, setView] = useState<'login' | 'setup' | 'forgot' | 'reset'>(resetToken ? 'reset' : mode);
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [passwordConfirm, setPasswordConfirm] = useState('');
  const [remember, setRemember] = useState(true);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    setMessage('');
    setBusy(true);
    try {
      if (view === 'setup') {
        const ok = await setup({ name, email, password, passwordConfirm });
        if (ok) onLoggedIn('setup');
        return;
      }
      if (view === 'forgot') {
        const text = await forgotPassword(email);
        setMessage(text);
        return;
      }
      if (view === 'reset') {
        const ok = await resetPassword(resetToken, password, passwordConfirm);
        if (ok) {
          setMessage('Ihr Passwort wurde aktualisiert. Sie können sich jetzt anmelden.');
          setView('login');
          setPassword('');
          setPasswordConfirm('');
        }
        return;
      }
      const ok = await login({ email, password, remember });
      if (!ok) {
        setError('E-Mail oder Passwort ist nicht korrekt.');
        return;
      }
      onLoggedIn();
    } catch (err) {
      const detail = err instanceof Error ? err.message : '';
      setError(view === 'login' ? 'E-Mail oder Passwort ist nicht korrekt.' : detail || 'Die Eingaben konnten nicht gespeichert werden. Bitte prüfen Sie die Angaben.');
    } finally {
      setBusy(false);
    }
  };

  const isSetup = view === 'setup';
  const isForgot = view === 'forgot';
  const isReset = view === 'reset';
  const intro = isSetup
    ? 'Richten Sie Ihr persönliches Sentero-Konto ein.'
    : isForgot
    ? 'Geben Sie Ihre E-Mail-Adresse ein, um das Zurücksetzen vorzubereiten.'
    : isReset
    ? 'Wählen Sie ein neues Passwort für Ihr Sentero-Konto.'
    : 'Melden Sie sich an, um den Alltag Ihrer Angehörigen im Blick zu behalten.';

  return (
    <main className="sc-login-page">
      <section className="sc-login-card">
        <div className="sc-hero-copy">
          <div className="sc-login-brand">
            <img src={senteroLogo} alt="Sentero" />
          </div>
          <p>{intro}</p>
        </div>
        <form className="sc-login-form" onSubmit={submit}>
          {isSetup && (
            <label className="sc-floating-field">
              <input autoFocus value={name} onChange={(event) => setName(event.target.value)} autoComplete="name" placeholder=" " />
              <span>Name</span>
            </label>
          )}
          {!isReset && (
            <label className="sc-floating-field">
              <input autoFocus={!isSetup} type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" placeholder=" " />
              <span>E-Mail</span>
            </label>
          )}
          {!isForgot && (
            <label className="sc-floating-field">
              <input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={isSetup || isReset ? 'new-password' : 'current-password'} placeholder=" " />
              <span>{isReset ? 'Neues Passwort' : 'Passwort'}</span>
            </label>
          )}
          {(isSetup || isReset) && (
            <label className="sc-floating-field">
              <input type="password" value={passwordConfirm} onChange={(event) => setPasswordConfirm(event.target.value)} autoComplete="new-password" placeholder=" " />
              <span>Passwort bestätigen</span>
            </label>
          )}
          {view === 'login' && (
            <>
              <div className="sc-login-form-row">
                <a className="sc-login-link" href="/sentero/password-forgotten" onClick={(event) => { event.preventDefault(); setError(''); setView('forgot'); }}>Passwort vergessen?</a>
              </div>
              <label className={`sc-check-row${remember ? ' active' : ''}`}>
                <span>Dieses Gerät merken</span>
                <input type="checkbox" checked={remember} onChange={(event) => setRemember(event.target.checked)} />
                <i aria-hidden="true" />
              </label>
            </>
          )}
          {error && <div className="sc-form-note" role="alert">{error}</div>}
          {message && <div className="sc-form-note success" role="status">{message}</div>}
          <button className="sc-primary-action" type="submit" disabled={busy}>
            <LockKeyhole size={18} />
            {busy ? 'Bitte warten...' : isSetup ? 'Konto erstellen' : isForgot ? 'Link vorbereiten' : isReset ? 'Passwort speichern' : 'Anmelden'}
          </button>
          {(isForgot || isReset) && <button className="sc-login-secondary" type="button" onClick={() => { setError(''); setMessage(''); setView('login'); }}>Zur Anmeldung</button>}
        </form>
      </section>
      <footer className="sc-login-footer">
        <a href="https://www.mma-plus.com/datenschutz" target="_blank" rel="noreferrer">Datenschutz</a>
        <span aria-hidden="true">·</span>
        <a href="https://www.mma-plus.com/impressum" target="_blank" rel="noreferrer">Impressum</a>
      </footer>
    </main>
  );
}
