import { useEffect, useState } from 'react';
import { Activity, CheckCircle2, History, RefreshCw, ShieldAlert } from 'lucide-react';
import { api, type UpdateStatus, type UpdateStep } from '@shared/api/client';

export function UpdatePanel() {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  async function load() {
    setError('');
    try {
      setStatus(await api.senteroUpdateStatus());
    } catch {
      setError('Update-Status konnte nicht geladen werden.');
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function checkUpdates() {
    setBusy('check');
    setError('');
    try {
      await api.senteroCheckUpdates();
      await load();
    } catch {
      setError('Die Update-Pruefung ist fehlgeschlagen.');
    } finally {
      setBusy('');
    }
  }

  async function installUpdate() {
    setBusy('install');
    setError('');
    try {
      setStatus(await api.senteroInstallUpdate());
      await load();
    } catch {
      setError('Das Update konnte nicht vollstaendig installiert werden.');
    } finally {
      setBusy('');
    }
  }

  const product = status?.product || 'Sentero';
  const uiState = status?.status || status?.state || 'idle';
  const isRunning = busy === 'install' || uiState === 'running';
  const isSuccess = uiState === 'success' || uiState === 'completed';
  const isFailed = uiState === 'failed' || uiState === 'error';
  const updateAvailable = Boolean(status?.update_available);

  return (
    <section className="sentero-update-panel">
      <div className="update-panel-head">
        <div>
          <p className="eyebrow">Updates</p>
          <h2>{titleForState(uiState, updateAvailable)}</h2>
          <p>{textForState(product, status, uiState, updateAvailable)}</p>
        </div>
        <button className="button secondary" type="button" onClick={checkUpdates} disabled={Boolean(busy)}>
          {busy === 'check' ? <Activity size={16} /> : <RefreshCw size={16} />} Nach Updates suchen
        </button>
      </div>

      {error && <div className="update-alert error"><ShieldAlert size={18} /> {error}</div>}
      {isSuccess && <div className="update-alert success"><CheckCircle2 size={18} /> {product} wurde erfolgreich aktualisiert.</div>}
      {isFailed && <div className="update-alert error"><ShieldAlert size={18} /> Das Update konnte nicht vollstaendig installiert werden.</div>}

      <div className="update-version-grid update-version-grid-simple">
        <VersionItem label="Produkt" value={product} />
        <VersionItem label="Aktuelle Version" value={status?.current_version || '-'} />
        <VersionItem label="Update-Status" value={statusLabel(uiState, updateAvailable)} />
        <VersionItem label="Letzte Pruefung" value={formatDate(status?.last_checked)} />
      </div>

      {updateAvailable && (
        <article className="update-release-card">
          <span>Neue Version</span>
          <h3>Version {status?.latest_version}</h3>
          <h4>Was ist neu?</h4>
          <ReleaseNotes notes={status?.release_notes} />
        </article>
      )}

      {isRunning && (
        <div className="update-wizard">
          <div className="update-wizard-title"><History size={18} /> Update wird installiert</div>
          {(status?.steps?.length ? status.steps : defaultSteps()).map((step, index) => (
            <div className={`update-step ${step.status}`} key={step.key}>
              <span>{index + 1}</span>
              <div>
                <strong>{step.label}</strong>
                <small>{stepStatusLabel(step.status)}</small>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="update-action-row">
        {updateAvailable && !isRunning && (
          <button className="button primary" type="button" onClick={installUpdate} disabled={Boolean(busy)}>
            {busy === 'install' ? <Activity size={16} /> : <CheckCircle2 size={16} />} Update installieren
          </button>
        )}
      </div>
    </section>
  );
}

function VersionItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="update-version-item">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ReleaseNotes({ notes }: { notes?: string[] | string }) {
  const items = Array.isArray(notes) ? notes : typeof notes === 'string' ? notes.split('\n').filter(Boolean) : [];
  if (!items.length) return <p>Details zu diesem Update werden nach der Pruefung angezeigt.</p>;
  return <ul className="update-release-notes">{items.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul>;
}

function defaultSteps(): UpdateStep[] {
  return [
    { key: 'prepare', label: 'Vorbereitung', status: 'pending' },
    { key: 'backup', label: 'Sicherung', status: 'pending' },
    { key: 'install', label: 'Installation', status: 'pending' },
    { key: 'done', label: 'Fertig', status: 'pending' },
  ];
}

function titleForState(state: string, updateAvailable: boolean) {
  if (state === 'running') return 'Update wird installiert';
  if (state === 'success' || state === 'completed') return 'Update erfolgreich';
  if (state === 'failed' || state === 'error') return 'Update fehlgeschlagen';
  if (state === 'check_failed') return 'Update-Pruefung fehlgeschlagen';
  if (updateAvailable) return 'Update verfuegbar';
  return 'Applikation ist aktuell';
}

function textForState(product: string, status: UpdateStatus | null, state: string, updateAvailable: boolean) {
  if (state === 'running') return `${product} wird aktualisiert. Bitte warten Sie, bis der Vorgang abgeschlossen ist.`;
  if (state === 'success' || state === 'completed') return `${product} wurde erfolgreich aktualisiert.`;
  if (state === 'failed' || state === 'error') return status?.last_error || 'Das Update konnte nicht vollstaendig installiert werden.';
  if (state === 'check_failed') return status?.message || 'Die Update-Pruefung konnte nicht abgeschlossen werden.';
  if (updateAvailable) return `Eine neue Version von ${product} ist verfuegbar.`;
  return status?.message || 'Ihre Installation ist auf dem neuesten Stand.';
}

function statusLabel(state: string, updateAvailable: boolean) {
  if (state === 'running') return 'Update laeuft';
  if (state === 'success' || state === 'completed') return 'Aktualisiert';
  if (state === 'failed' || state === 'error') return 'Fehlgeschlagen';
  if (state === 'check_failed') return 'Pruefung fehlgeschlagen';
  return updateAvailable ? 'Update verfuegbar' : 'Aktuell';
}

function stepStatusLabel(status: string) {
  if (status === 'running') return 'Wird ausgefuehrt';
  if (status === 'success' || status === 'completed') return 'Abgeschlossen';
  if (status === 'failed' || status === 'error') return 'Fehlgeschlagen';
  return 'Wartet';
}

function formatDate(value?: string | null) {
  if (!value) return '-';
  try {
    return new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value));
  } catch {
    return value;
  }
}

