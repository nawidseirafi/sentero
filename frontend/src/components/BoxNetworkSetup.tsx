import { useEffect, useMemo, useState } from 'react';
import { Eye, EyeOff, Lock, RefreshCw } from 'lucide-react';
import { api, type BoxNetworkStatus, type WifiNetwork } from '@shared/api/client';
import logo from '../assets/logo2.png';

type Props = {
  initialStatus?: BoxNetworkStatus | null;
  onReady?: () => void;
};

type MessageMode = 'normal' | 'ok' | 'error';

const styles = `
  .sentero-provisioning {
    --sp-bg: #0b0f0c;
    --sp-card: rgba(255,255,255,.05);
    --sp-card-border: rgba(255,255,255,.09);
    --sp-ink: #f2f3ee;
    --sp-muted: #93a091;
    --sp-accent: #7fa984;
    --sp-accent-ink: #0b140c;
    --sp-line: rgba(255,255,255,.10);
    --sp-field: rgba(255,255,255,.06);
    --sp-danger: #e0796a;
    --sp-ok: #7fa984;
    --sp-radius: 18px;
    --sp-radius-sm: 12px;
    box-sizing: border-box;
    width: 100%;
    min-height: 100svh;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 20px;
    color: var(--sp-ink);
    background:
      radial-gradient(120% 90% at 15% -10%, #16241a 0%, transparent 60%),
      radial-gradient(120% 90% at 100% 110%, #17251b 0%, transparent 55%),
      var(--sp-bg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, system-ui, sans-serif;
  }
  .sentero-provisioning *, .sentero-provisioning *::before, .sentero-provisioning *::after { box-sizing: border-box; }
  .sp-wrap { width: 100%; max-width: 400px; animation: sp-rise .45s ease both; }
  @keyframes sp-rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
  .sp-brand { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; }
  .sp-brand img { width: 40px; height: 40px; border-radius: 10px; display: block; object-fit: cover; flex-shrink: 0; }
  .sp-brand h1 { margin: 0; font-size: 19px; line-height: 1.2; font-weight: 700; letter-spacing: -.01em; }
  .sp-brand p { margin: 3px 0 0; font-size: 13px; color: var(--sp-muted); }
  .sp-card { background: var(--sp-card); border: 1px solid var(--sp-card-border); backdrop-filter: blur(20px); border-radius: var(--sp-radius); padding: 18px; }
  .sp-card-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 4px; }
  .sp-card-head h2 { margin: 0; font-size: 14px; font-weight: 700; color: var(--sp-muted); text-transform: uppercase; letter-spacing: .06em; }
  .sp-badge { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; font-size: 12px; font-weight: 600; color: var(--sp-muted); border: 1px solid var(--sp-line); border-radius: 999px; padding: 4px 10px 4px 8px; }
  .sp-badge-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--sp-muted); }
  .sp-badge.busy .sp-badge-dot { background: var(--sp-accent); animation: sp-pulse 1s ease-in-out infinite; }
  .sp-badge.ok .sp-badge-dot { background: var(--sp-ok); }
  .sp-badge.error .sp-badge-dot { background: var(--sp-danger); }
  @keyframes sp-pulse { 0%,100% { opacity: 1; } 50% { opacity: .35; } }
  .sp-networks { margin-top: 10px; display: flex; flex-direction: column; gap: 6px; max-height: 228px; overflow-y: auto; padding-right: 1px; }
  .sp-network { display: flex; align-items: center; gap: 10px; width: 100%; min-height: 44px; text-align: left; color: var(--sp-ink); background: var(--sp-field); border: 1px solid transparent; border-radius: var(--sp-radius-sm); padding: 10px 12px; cursor: pointer; transition: border-color .15s, background .15s, transform .1s; }
  .sp-network:hover { background: rgba(255,255,255,.08); }
  .sp-network:active { transform: scale(.99); }
  .sp-network.selected { border-color: var(--sp-accent); background: rgba(127,169,132,.14); }
  .sp-network-name { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; font-weight: 500; }
  .sp-lock { flex-shrink: 0; color: var(--sp-muted); opacity: .75; }
  .sp-signal { width: 18px; height: 16px; display: flex; align-items: flex-end; gap: 2px; flex-shrink: 0; }
  .sp-signal span { width: 3px; border-radius: 1px; background: var(--sp-line); }
  .sp-signal span:nth-child(1) { height: 4px; }
  .sp-signal span:nth-child(2) { height: 8px; }
  .sp-signal span:nth-child(3) { height: 12px; }
  .sp-signal span:nth-child(4) { height: 16px; }
  .sp-signal span.on { background: var(--sp-accent); }
  .sp-skeleton { height: 42px; border-radius: var(--sp-radius-sm); background: linear-gradient(90deg, var(--sp-field) 25%, rgba(255,255,255,.1) 37%, var(--sp-field) 63%); background-size: 400% 100%; animation: sp-shimmer 1.4s ease infinite; }
  @keyframes sp-shimmer { 0% { background-position: 100% 0; } 100% { background-position: 0 0; } }
  .sp-hint { margin: 10px 2px 0; font-size: 12.5px; color: var(--sp-muted); line-height: 1.4; }
  .sp-scan { appearance: none; background: none; border: 0; color: var(--sp-accent); font-size: 13px; font-weight: 600; padding: 8px 2px; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; }
  .sp-scan:disabled { opacity: .5; cursor: wait; }
  .sp-scan.busy svg { animation: sp-spin 1s linear infinite; }
  @keyframes sp-spin { to { transform: rotate(360deg); } }
  .sp-sep { border: 0; border-top: 1px solid var(--sp-line); margin: 16px 0; }
  .sp-field { margin-bottom: 12px; }
  .sp-field label { display: block; font-size: 12.5px; font-weight: 600; color: var(--sp-muted); margin-bottom: 6px; }
  .sp-input { width: 100%; min-height: 46px; border-radius: var(--sp-radius-sm); border: 1px solid var(--sp-line); background: var(--sp-field); color: var(--sp-ink); padding: 0 13px; outline: none; font: inherit; transition: border-color .15s, background .15s; }
  .sp-input::placeholder { color: #6b756a; }
  .sp-input:focus { border-color: var(--sp-accent); background: rgba(127,169,132,.09); }
  .sp-password { position: relative; }
  .sp-password .sp-input { padding-right: 48px; }
  .sp-eye { position: absolute; right: 4px; top: 4px; bottom: 4px; width: 38px; display: flex; align-items: center; justify-content: center; background: none; border: 0; border-radius: 9px; color: var(--sp-muted); cursor: pointer; }
  .sp-eye:active { background: rgba(255,255,255,.08); }
  .sp-primary { width: 100%; min-height: 48px; margin-top: 4px; border: 0; border-radius: var(--sp-radius-sm); background: var(--sp-accent); color: var(--sp-accent-ink); font: inherit; font-weight: 700; font-size: 15px; cursor: pointer; display: flex; align-items: center; justify-content: center; gap: 8px; transition: opacity .15s, transform .1s; }
  .sp-primary:active { transform: scale(.99); }
  .sp-primary:disabled { opacity: .55; cursor: wait; }
  .sp-spinner { width: 15px; height: 15px; border-radius: 50%; border: 2px solid rgba(11,20,12,.3); border-top-color: var(--sp-accent-ink); animation: sp-spin .7s linear infinite; }
  .sp-message { min-height: 18px; margin: 10px 2px 0; color: var(--sp-muted); font-size: 13px; line-height: 1.45; white-space: pre-line; }
  .sp-message.error { color: var(--sp-danger); }
  .sp-message.ok { color: var(--sp-ok); }
  .sp-meta { display: flex; justify-content: space-between; gap: 10px; margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--sp-line); font-size: 11.5px; color: #6f7b6d; }
  .sp-meta span:last-child { text-align: right; overflow-wrap: anywhere; }
`;

function Signal({ value }: { value: number }) {
  const normalized = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  const bars = normalized >= 75 ? 4 : normalized >= 50 ? 3 : normalized >= 25 ? 2 : 1;
  return (
    <span className="sp-signal" aria-label={`Signal ${normalized}%`}>
      {[1, 2, 3, 4].map((bar) => <span key={bar} className={bar <= bars ? 'on' : ''} />)}
    </span>
  );
}

export function BoxNetworkSetup({ initialStatus = null, onReady }: Props) {
  const [status, setStatus] = useState<BoxNetworkStatus | null>(initialStatus);
  const [networks, setNetworks] = useState<WifiNetwork[]>([]);
  const [form, setForm] = useState({ ssid: '', password: '' });
  const [message, setMessage] = useState('');
  const [messageMode, setMessageMode] = useState<MessageMode>('normal');
  const [saving, setSaving] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  const sortedNetworks = useMemo(() => {
    const bySsid = new Map<string, WifiNetwork>();
    for (const network of networks) {
      const ssid = network.ssid?.trim();
      if (!ssid) continue;
      const current = bySsid.get(ssid);
      if (!current || network.signal > current.signal) bySsid.set(ssid, { ...network, ssid });
    }
    return [...bySsid.values()].sort((a, b) => b.signal - a.signal);
  }, [networks]);

  const selected = useMemo(
    () => sortedNetworks.find((item) => item.ssid === form.ssid),
    [sortedNetworks, form.ssid],
  );

  async function refreshStatus() {
    try {
      const next = await api.boxNetworkStatus();
      setStatus(next);
      if (next.network_ready) onReady?.();
    } catch {
      // A single-radio AP -> home WiFi switch can make this browser lose the box temporarily.
    }
  }

  async function scan() {
    setScanning(true);
    setMessage('');
    setMessageMode('normal');
    try {
      const result = await api.boxNetworkWifiNetworks();
      setNetworks(result.networks || []);
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Scan nicht verfügbar. SSID unten manuell eintragen.');
      setMessageMode('error');
    } finally {
      setScanning(false);
    }
  }

  useEffect(() => {
    void refreshStatus();
    void scan();
  }, []);

  function chooseNetwork(network: WifiNetwork) {
    setForm((value) => ({ ...value, ssid: network.ssid }));
    setMessage('');
    setMessageMode('normal');
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const ssid = form.ssid.trim();
    if (!ssid) {
      setMessage('SSID fehlt. Bitte WLAN auswählen oder den Namen manuell eintragen.');
      setMessageMode('error');
      return;
    }
    if (selected?.secured && !form.password) {
      setMessage('Bitte WLAN-Passwort eingeben.');
      setMessageMode('error');
      return;
    }

    setSaving(true);
    setMessageMode('normal');
    setMessage('Verbindung wird hergestellt …\nDas Setup-WLAN wird dabei kurz getrennt.');

    try {
      const result = await api.saveBoxNetworkWifi({ ssid, password: form.password });
      setStatus(result.status);
      if (result.ok && result.status.network_ready) {
        setMessageMode('ok');
        setMessage('Mit dem Heimnetz verbunden.\nVerbinde dieses Gerät wieder mit deinem Heim-WLAN und öffne anschließend sentero.local:8080.');
        window.setTimeout(() => onReady?.(), 2500);
      } else {
        setMessageMode('error');
        setMessage(result.message || 'WLAN konnte nicht verbunden werden. Das Sentero-Setup-WLAN bleibt verfügbar.');
      }
    } catch {
      // A successful single-radio switch often drops the HTTP response itself.
      // Do not claim success here: local NetworkManager state is the source of truth.
      setMessageMode('normal');
      setMessage('Die Box wechselt gerade das WLAN. Wenn das Setup-WLAN verschwindet, verbinde dieses Gerät wieder mit deinem Heim-WLAN und öffne sentero.local:8080. Falls die Verbindung fehlschlägt, erscheint das Sentero-Setup-WLAN erneut.');
    } finally {
      setSaving(false);
    }
  }

  const badgeMode = scanning ? 'busy' : sortedNetworks.length ? 'ok' : networks.length === 0 && !scanning ? '' : '';
  const badgeText = scanning ? 'Scan läuft' : sortedNetworks.length ? `${sortedNetworks.length} gefunden` : 'Manuell';
  const apName = status?.setup_ap_ssid || 'Sentero-Setup';

  return (
    <main className="sentero-provisioning">
      <style>{styles}</style>
      <div className="sp-wrap">
        <div className="sp-brand">
          <img src={logo} alt="Sentero" width={40} height={40} />
          <div>
            <h1>WLAN einrichten</h1>
            <p>Verbinde deine Sentero Box mit deinem Heimnetz</p>
          </div>
        </div>

        <section className="sp-card" aria-label="Sentero WLAN Setup">
          <div className="sp-card-head">
            <h2>Netzwerke</h2>
            <span className={`sp-badge ${badgeMode}`}>
              <span className="sp-badge-dot" />
              <span>{badgeText}</span>
            </span>
          </div>

          <div className="sp-networks">
            {scanning && sortedNetworks.length === 0 ? (
              <>
                <div className="sp-skeleton" />
                <div className="sp-skeleton" />
                <div className="sp-skeleton" />
              </>
            ) : sortedNetworks.length ? (
              sortedNetworks.map((network) => (
                <button
                  type="button"
                  className={`sp-network ${form.ssid === network.ssid ? 'selected' : ''}`}
                  key={network.ssid}
                  onClick={() => chooseNetwork(network)}
                  aria-pressed={form.ssid === network.ssid}
                >
                  <Signal value={network.signal} />
                  <span className="sp-network-name">{network.ssid}</span>
                  {network.secured ? <Lock className="sp-lock" size={14} aria-label="Gesichert" /> : null}
                </button>
              ))
            ) : (
              <p className="sp-hint">Keine Netzwerke gefunden. Erneut scannen oder SSID unten manuell eintragen.</p>
            )}
          </div>

          {scanning ? <p className="sp-hint">Der WLAN-Scan kann die Verbindung zum Setup-WLAN kurz beeinflussen.</p> : null}

          <button type="button" className={`sp-scan ${scanning ? 'busy' : ''}`} onClick={() => void scan()} disabled={scanning || saving}>
            <RefreshCw size={14} />
            Erneut scannen
          </button>

          <hr className="sp-sep" />

          <form onSubmit={submit} noValidate>
            <div className="sp-field">
              <label htmlFor="sentero-ssid">SSID</label>
              <input
                id="sentero-ssid"
                className="sp-input"
                type="text"
                autoComplete="username"
                required
                placeholder="Netzwerkname"
                value={form.ssid}
                onChange={(event) => setForm((value) => ({ ...value, ssid: event.target.value }))}
                disabled={saving}
              />
            </div>

            <div className="sp-field">
              <label htmlFor="sentero-wifi-password">Passwort</label>
              <div className="sp-password">
                <input
                  id="sentero-wifi-password"
                  className="sp-input"
                  type={showPassword ? 'text' : 'password'}
                  autoComplete="current-password"
                  placeholder={selected?.secured === false ? 'Kein Passwort erforderlich' : 'WLAN Passwort'}
                  value={form.password}
                  onChange={(event) => setForm((value) => ({ ...value, password: event.target.value }))}
                  disabled={saving || selected?.secured === false}
                />
                <button
                  type="button"
                  className="sp-eye"
                  onClick={() => setShowPassword((value) => !value)}
                  aria-label={showPassword ? 'Passwort verbergen' : 'Passwort anzeigen'}
                  disabled={saving || selected?.secured === false}
                >
                  {showPassword ? <EyeOff size={19} /> : <Eye size={19} />}
                </button>
              </div>
            </div>

            <button className="sp-primary" type="submit" disabled={saving || !form.ssid.trim()}>
              {saving ? <span className="sp-spinner" /> : null}
              <span>{saving ? 'Verbindung wird hergestellt …' : 'Verbinden'}</span>
            </button>

            <p className={`sp-message ${messageMode === 'normal' ? '' : messageMode}`} role="status" aria-live="polite">
              {message || status?.message || 'Nach der Einrichtung erreichst du Sentero unter sentero.local:8080.'}
            </p>
          </form>

          <div className="sp-meta">
            <span>Sentero Box</span>
            <span>{apName}</span>
          </div>
        </section>
      </div>
    </main>
  );
}
