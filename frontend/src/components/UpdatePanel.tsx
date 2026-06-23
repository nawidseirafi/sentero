import { useEffect, useState } from 'react';
import { Activity, CheckCircle2, History, RefreshCw, ShieldAlert } from 'lucide-react';
import { api, type UpdateStatus, type UpdateStep } from '@shared/api/client';

type Props = {
  variant?: 'sentero';
};

export function UpdatePanel({ variant = 'sentero' }: Props) {
  const [status, setStatus] = useState<UpdateStatus | null>(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const load = async () => {
    setError('');
    try {
      setStatus(await api.senteroUpdateStatus());
    } catch {
      setError('Update-Status konnte nicht geladen werden.');
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const checkUpdates = async () => {
    setBusy('check');
    setError('');
    try {
      await api.senteroCheckUpdates();
      await load();
    } catch {
      setError('Die Update-Pruefung ist fehlgeschlagen. Bitte versuchen Sie es spaeter erneut.');
    } finally {
      setBusy('');
    }
  };

  const installUpdate = async () => {
    setBusy('install');
    setError('');
    try {
      const result = await api.senteroInstallUpdate();
      setStatus(result);
      await load();
    } catch {
      setError('Das Update konnte nicht vollstaendig installiert werden. Bitte versuchen Sie es erneut oder kontaktieren Sie den Support.');
    } finally {
      setBusy('');
    }
  };

  const product = status?.product || 'Sentero';
  const uiState = status?.status || status?.state || 'idle';
  const isRunning = busy === 'install' || uiState === 'running';
  const isSuccess = uiState === 'success' || uiState === 'completed';
  const isFailed = uiState === 'failed' || uiState === 'error';
  const updateAvailable = Boolean(status?.update_available);
  const title = titleForState(uiState, updateAvailable);
  const text = textForState(product, status, uiState, updateAvailable);
  const rootClass = `panel settings-card update-panel ${variant === 'sentero' ? 'sentero-update-panel' : ''}`;

  return (
    <section className={rootClass}>
      <div className="update-panel-head">
        <div>
          <p className="eyebrow">Updates</p>
          <h2>{title}</h2>
          <p>{text}</p>
        </div>
        <button className="button secondary" type="button" onClick={checkUpdates} disabled={Boolean(busy)}>
          {busy === 'check' ? <Activity size={16} /> : <RefreshCw size={16} />} Nach Updates suchen
        </button>
      </div>

      {error && <div className="update-alert error"><ShieldAlert size={18} /> {error}</div>}
      {isSuccess && <div className="update-alert success"><CheckCircle2 size={18} /> {product} wurde erfolgreich aktualisiert.</div>}
      {isFailed && <div className="update-alert error"><ShieldAlert size={18} /> Das Update konnte nicht vollstaendig installiert werden. Bitte kontaktieren Sie den Support.</div>}

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

      {status?.dev_mode && <DeveloperDetails status={status} />}
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
  const items = Array.isArray(notes)
    ? notes
    : typeof notes === 'string'
      ? notes.split('\n').filter(Boolean)
      : [];
  if (!items.length) return <p>Details zu diesem Update werden nach der Pruefung angezeigt.</p>;
  return (
    <ul className="update-release-notes">
      {items.map((item, index) => <li key={`${item}-${index}`}>{renderMarkdownLine(item)}</li>)}
    </ul>
  );
}

function DeveloperDetails({ status }: { status: UpdateStatus }) {
  const version = status.version;
  return (
    <details className="update-developer-details">
      <summary>Technische Details</summary>
      <div className="update-version-grid">
        <VersionItem label="Version" value={version?.version || version?.app_version || '-'} />
        <VersionItem label="Build" value={version?.build || '-'} />
        <VersionItem label="Edition" value={version?.edition || '-'} />
        <VersionItem label="Commit" value={version?.commit || '-'} />
        <VersionItem label="Modus" value={status.execution_mode || '-'} />
      </div>
    </details>
  );
}

function renderMarkdownLine(value: string) {
  const parts = value.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, index) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    return <span key={index}>{part}</span>;
  });
}

function defaultSteps(): UpdateStep[] {
  return [
    { key: 'prepare', label: 'Vorbereitung', status: 'pending' },
    { key: 'backup', label: 'Sicherung', status: 'pending' },
    { key: 'install', label: 'Installation', status: 'pending' },
    { key: 'restart', label: 'Neustart', status: 'pending' },
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
  if (state === 'failed' || state === 'error') return 'Das Update konnte nicht vollstaendig installiert werden. Bitte versuchen Sie es erneut oder kontaktieren Sie den Support.';
  if (state === 'check_failed') return status?.message || 'Die Update-Pruefung konnte nicht abgeschlossen werden. Bitte versuchen Sie es spaeter erneut.';
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
  return 'Ausstehend';
}

function formatDate(value?: string | null) {
  if (!value) return 'Noch nicht geprueft';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }).format(date);
}
