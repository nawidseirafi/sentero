import { useEffect, useMemo, useRef, useState } from 'react';
import { api, type SenteroMatterCapabilities, type SenteroMatterDevice, type SenteroMatterStatus } from '@shared/api/client';

const ROOM_OPTIONS = [
  { key: 'living_room', label: 'Wohnzimmer' },
  { key: 'kitchen', label: 'Kueche' },
  { key: 'bathroom', label: 'Bad' },
  { key: 'bedroom', label: 'Schlafzimmer' },
  { key: 'hallway', label: 'Flur' },
  { key: 'entrance', label: 'Eingang' },
];

const ROLE_OPTIONS = [
  { key: 'presence', label: 'Das ist ein Raumsensor' },
  { key: 'main_door', label: 'Das ist die Wohnungstuer' },
  { key: 'contact', label: 'Das ist ein Tuer- oder Fenstersensor' },
];

type PairingState = 'idle' | 'code_found' | 'connecting' | 'setting_up' | 'detected' | 'done' | 'failed';

export function MatterPairingStep({ onSaved }: { onSaved: () => void }) {
  const [setupCode, setSetupCode] = useState('');
  const [commissioningId, setCommissioningId] = useState('');
  const [pairingState, setPairingState] = useState<PairingState>('idle');
  const [status, setStatus] = useState<SenteroMatterStatus | null>(null);
  const [device, setDevice] = useState<SenteroMatterDevice | null>(null);
  const [room, setRoom] = useState('living_room');
  const [role, setRole] = useState('presence');
  const [error, setError] = useState('');
  const [capabilities, setCapabilities] = useState<SenteroMatterCapabilities | null>(null);
  const [capabilityLoading, setCapabilityLoading] = useState(true);
  const [cameraActive, setCameraActive] = useState(false);
  const [cameraError, setCameraError] = useState('');
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const pollRef = useRef<number | null>(null);
  const devMode = useMemo(() => new URLSearchParams(window.location.search).get('dev') === '1', []);

  useEffect(() => {
    void loadCapabilities();
    return () => {
      stopCamera();
      stopPolling();
    };
  }, []);

  async function loadCapabilities() {
    setCapabilityLoading(true);
    try {
      const result = await api.senteroMatterCapabilities(devMode);
      setCapabilities(result);
    } catch {
      setCapabilities({
        home_assistant: false,
        matter_integration: false,
        matter_server: false,
        commissioning_available: false,
        ipv6_available: false,
        thread_available: false,
        message: 'Die Sensor-Einrichtung ist noch nicht bereit.',
      });
    } finally {
      setCapabilityLoading(false);
    }
  }

  async function startPairing(payload: string) {
    const clean = payload.trim();
    if (!clean) return;
    if (!capabilities?.commissioning_available) {
      setError('Die Sensor-Einrichtung ist noch nicht bereit.');
      return;
    }
    stopPolling();
    setError('');
    setDevice(null);
    setStatus(null);
    setPairingState('code_found');
    try {
      const result = await api.startSenteroMatter(clean.startsWith('MT:') ? { qr_payload: clean } : { setup_code: clean });
      setCommissioningId(result.commissioning_id);
      setPairingState('connecting');
      schedulePoll(result.commissioning_id);
    } catch {
      setPairingState('failed');
      setError('Der Sensor konnte nicht verbunden werden.');
    }
  }

  function schedulePoll(id: string) {
    stopPolling();
    pollRef.current = window.setTimeout(() => void pollStatus(id), 2000);
  }

  function stopPolling() {
    if (pollRef.current) window.clearTimeout(pollRef.current);
    pollRef.current = null;
  }

  async function pollStatus(id: string) {
    try {
      const nextStatus = await api.senteroMatterStatus(id, devMode);
      setStatus(nextStatus);
      if (nextStatus.status === 'waiting' || nextStatus.status === 'commissioning') {
        setPairingState(nextStatus.status === 'waiting' ? 'connecting' : 'setting_up');
        schedulePoll(id);
        return;
      }
      if (nextStatus.status === 'completed') {
        const nextDevice = await api.senteroMatterDevice(id, devMode);
        setDevice(nextDevice);
        setPairingState(nextDevice.device_detected ? 'detected' : 'failed');
        if (!nextDevice.device_detected) setError('Der Sensor konnte nicht verbunden werden.');
        return;
      }
      setPairingState('failed');
      setError('Der Sensor konnte nicht verbunden werden.');
    } catch {
      setPairingState('failed');
      setError('Der Sensor konnte nicht verbunden werden.');
    }
  }

  async function saveSensor() {
    if (!commissioningId) return;
    try {
      await api.assignSenteroMatterDevice(commissioningId, { room, role });
      setPairingState('done');
      onSaved();
    } catch {
      setError('Der Sensor konnte nicht gespeichert werden.');
    }
  }

  async function startCamera() {
    setCameraError('');
    const BarcodeDetectorCtor = (window as unknown as { BarcodeDetector?: new (options?: { formats?: string[] }) => { detect: (source: HTMLVideoElement) => Promise<Array<{ rawValue?: string }>> } }).BarcodeDetector;
    if (!BarcodeDetectorCtor) {
      setCameraError('Scannen ist auf diesem Geraet nicht verfuegbar. Bitte Setup-Code eingeben.');
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } });
      streamRef.current = stream;
      setCameraActive(true);
      if (videoRef.current) videoRef.current.srcObject = stream;
      const detector = new BarcodeDetectorCtor({ formats: ['qr_code'] });
      const scan = async () => {
        if (!videoRef.current || !streamRef.current) return;
        const codes = await detector.detect(videoRef.current);
        const value = codes[0]?.rawValue;
        if (value) {
          stopCamera();
          setSetupCode(value);
          void startPairing(value);
          return;
        }
        window.setTimeout(scan, 500);
      };
      window.setTimeout(scan, 700);
    } catch {
      setCameraError('Kamera konnte nicht gestartet werden. Bitte Setup-Code eingeben.');
    }
  }

  function stopCamera() {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setCameraActive(false);
  }

  return (
    <div className="sc-matter-step">
      {!capabilityLoading && !capabilities?.commissioning_available && (
        <div className="sc-matter-panel">
          <p className="sc-sensor-status is-bad">Die Sensor-Einrichtung ist noch nicht bereit.</p>
          <div className="sc-matter-actions">
            <button type="button" onClick={() => void loadCapabilities()}>Erneut pruefen</button>
            <button type="button">Hilfe anzeigen</button>
          </div>
        </div>
      )}
      <div className="sc-matter-panel">
        <p>Scannen Sie den QR-Code auf Ihrem Sensor.</p>
        <div className="sc-matter-scanner">
          {cameraActive ? <video ref={videoRef} autoPlay muted playsInline /> : <button type="button" onClick={() => void startCamera()}>QR-Code scannen</button>}
        </div>
        {cameraError && <p className="sc-sensor-status is-bad">{cameraError}</p>}
        <label className="sc-pairing-code">
          <span>Setup-Code manuell eingeben</span>
          <input value={setupCode} onChange={(event) => setSetupCode(event.target.value)} placeholder="Setup-Code" />
        </label>
        <button type="button" className="sc-primary-action" onClick={() => void startPairing(setupCode)} disabled={!setupCode.trim() || capabilityLoading || !capabilities?.commissioning_available || pairingState === 'connecting' || pairingState === 'setting_up'}>
          Sensor verbinden
        </button>
      </div>

      <PairingProgress state={pairingState} />
      {error && <div className="sc-matter-actions"><p className="sc-sensor-status is-bad">{error}</p><button type="button" onClick={() => void startPairing(setupCode)}>Erneut versuchen</button><button type="button">Hilfe anzeigen</button></div>}

      {pairingState === 'detected' && (
        <div className="sc-matter-panel">
          <p className="sc-sensor-status is-good">Sensor erkannt.</p>
          <label className="sc-pairing-code">
            <span>Raum auswaehlen</span>
            <select value={room} onChange={(event) => setRoom(event.target.value)}>
              {ROOM_OPTIONS.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
            </select>
          </label>
          <label className="sc-pairing-code">
            <span>Sensor verwenden als</span>
            <select value={role} onChange={(event) => setRole(event.target.value)}>
              {ROLE_OPTIONS.map((item) => <option key={item.key} value={item.key}>{item.label}</option>)}
            </select>
          </label>
          <button type="button" className="sc-primary-action" onClick={() => void saveSensor()}>Fertig</button>
        </div>
      )}

      {pairingState === 'done' && <p className="sc-sensor-status is-good">Fertig. Der Sensor ist verbunden.</p>}
      {devMode && <DevDetails status={status} device={device} setupCode={setupCode} capabilities={capabilities} />}
    </div>
  );
}

function PairingProgress({ state }: { state: PairingState }) {
  const steps = [
    ['code_found', 'QR-Code erkannt'],
    ['connecting', 'Verbindung wird hergestellt'],
    ['setting_up', 'Geraet wird eingerichtet'],
    ['detected', 'Sensor erkannt'],
    ['done', 'Fertig'],
  ];
  const activeIndex = Math.max(0, steps.findIndex(([key]) => key === state));
  return (
    <div className="sc-matter-progress">
      {steps.map(([key, label], index) => <div key={key} className={index <= activeIndex ? 'is-active' : ''}><span>{index + 1}</span>{label}</div>)}
    </div>
  );
}

function DevDetails({ status, device, setupCode, capabilities }: { status: SenteroMatterStatus | null; device: SenteroMatterDevice | null; setupCode: string; capabilities: SenteroMatterCapabilities | null }) {
  return (
    <pre className="sc-matter-dev">{JSON.stringify({ setupCode, capabilities, status, device }, null, 2)}</pre>
  );
}
