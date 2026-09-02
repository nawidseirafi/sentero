from __future__ import annotations

import argparse
import json
import signal
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt


BASE_DIR = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Record Sentero XM126 MQTT topics as JSONL.")
    parser.add_argument("--mqtt-host", default="127.0.0.1")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--device-id", default="xm126-00124a")
    parser.add_argument("--label", default="test")
    parser.add_argument("--duration", type=float, default=120.0)
    parser.add_argument("--out-dir", default=str(BASE_DIR / "data" / "recordings"))
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    started = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_label = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in args.label)
    out_path = out_dir / f"{started}_{args.device_id}_{safe_label}.jsonl"

    topics = [
        f"sentero/{args.device_id}/state",
        f"sentero/{args.device_id}/settings",
        f"sentero/{args.device_id}/debug",
    ]
    stop = False

    def handle_stop(signum: int, frame: Any) -> None:
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{args.device_id}-recorder-{started}")

    with out_path.open("w", encoding="utf-8") as fh:
        def on_connect(client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
            for topic in topics:
                client.subscribe(topic)

        def on_message(client: mqtt.Client, userdata: Any, message: Any) -> None:
            raw = message.payload.decode("utf-8", errors="replace")
            try:
                payload: Any = json.loads(raw)
            except json.JSONDecodeError:
                payload = raw
            row = {
                "time": datetime.now().isoformat(timespec="milliseconds"),
                "topic": message.topic,
                "payload": payload,
            }
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()

        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(args.mqtt_host, args.mqtt_port, 20)
        client.loop_start()
        end_at = time.monotonic() + args.duration
        print(f"Recording {', '.join(topics)}")
        print(f"Output: {out_path}")
        while not stop and time.monotonic() < end_at:
            time.sleep(0.2)
        client.loop_stop()
        client.disconnect()

    print(f"Done: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
