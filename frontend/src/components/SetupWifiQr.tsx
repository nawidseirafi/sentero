import { useEffect, useMemo, useRef } from 'react';
import QRCode from 'qrcode';

const SETUP_WIFI_SSID = 'Sentero-mmWave';
const SETUP_WIFI_PASSWORD = 'senteroSetup';

type Props = {
  compact?: boolean;
  details?: boolean;
};

export function SetupWifiQr({ compact = false, details = true }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const qrValue = useMemo(
    () => `WIFI:T:WPA;S:${wifiQrEscape(SETUP_WIFI_SSID)};P:${wifiQrEscape(SETUP_WIFI_PASSWORD)};;`,
    [],
  );

  useEffect(() => {
    if (!canvasRef.current) return;
    void QRCode.toCanvas(canvasRef.current, qrValue, {
      errorCorrectionLevel: 'M',
      margin: 1,
      scale: compact ? 4 : 6,
      color: {
        dark: '#12221a',
        light: '#ffffff',
      },
    });
  }, [compact, qrValue]);

  return (
    <div className={`sc-setup-wifi-qr${compact ? ' compact' : ''}${details ? '' : ' icon-only'}`}>
      <canvas ref={canvasRef} width={compact ? 94 : 132} height={compact ? 94 : 132} aria-label="QR-Code fuer das Sensor Setup-WLAN" />
      {details && (
        <div>
          <strong>Setup-Hotspot</strong>
          <p>QR-Code scannen, um das Handy mit dem Sensor-Hotspot zu verbinden.</p>
          <dl>
            <div><dt>SSID</dt><dd>{SETUP_WIFI_SSID}</dd></div>
            <div><dt>Passwort</dt><dd>{SETUP_WIFI_PASSWORD}</dd></div>
          </dl>
        </div>
      )}
    </div>
  );
}

function wifiQrEscape(value: string) {
  return value.replace(/([\\;,:"])/g, '\\$1');
}
