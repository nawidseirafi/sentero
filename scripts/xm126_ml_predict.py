from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from xm126_ml_train import extract_windows, predict, recording_label


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the tiny XM126 model on one MQTT recording.")
    parser.add_argument("recording")
    parser.add_argument("--model", default="data/ml/xm126_tiny_model.json")
    parser.add_argument("--window-samples", type=int, default=10)
    parser.add_argument("--step-samples", type=int, default=2)
    args = parser.parse_args()

    recording = Path(args.recording)
    label = recording_label(recording) or "unknown"
    windows = extract_windows(recording, label, args.window_samples, args.step_samples)
    model = json.loads(Path(args.model).read_text(encoding="utf-8"))

    predictions = [predict(model, window.features) for window in windows]
    counts = Counter(predictions)
    total = max(len(predictions), 1)
    result = {
        "recording": recording.name,
        "inferred_label_from_filename": label,
        "windows": len(predictions),
        "predictions": {
            key: {
                "count": value,
                "ratio": round(value / total, 3),
            }
            for key, value in sorted(counts.items())
        },
        "majority": counts.most_common(1)[0][0] if counts else None,
    }
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
