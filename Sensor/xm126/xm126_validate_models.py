from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from xm126_ml_train import (
    CLASSES,
    extract_windows,
    load_debug_rows,
    load_excluded_recordings,
    load_segments,
    predict,
    profile_issue,
    quality_issue,
    recording_label,
    segment_label_for_window,
    summarize_window,
)


BASE_DIR = Path(__file__).resolve().parent


@dataclass
class StateRow:
    time_s: float
    presence: bool
    motion_state: str
    fall_detected: bool


def parse_time(value: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(value).timestamp()


def load_items(path: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items


def load_state_rows(path: Path) -> list[StateRow]:
    items = load_items(path)
    times = [parse_time(str(item["time"])) for item in items if item.get("time")]
    if not times:
        return []
    t0 = min(times)
    rows: list[StateRow] = []
    for item in items:
        if not str(item.get("topic", "")).endswith("/state") or not item.get("time"):
            continue
        payload = item.get("payload") or item.get("message") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                continue
        if not isinstance(payload, dict):
            continue
        rows.append(
            StateRow(
                time_s=parse_time(str(item["time"])) - t0,
                presence=bool(payload.get("presence")),
                motion_state=str(payload.get("motion_state", "")),
                fall_detected=bool(payload.get("fall_detected")),
            )
        )
    return rows


def majority(values: list[Any], default: Any = None) -> Any:
    if not values:
        return default
    return Counter(values).most_common(1)[0][0]


def bridge_window_prediction(states: list[StateRow], start_s: float, end_s: float) -> dict[str, Any]:
    chunk = [row for row in states if start_s <= row.time_s < end_s]
    if not chunk:
        return {"presence": False, "motion_state": "unknown", "fall": False}
    return {
        "presence": sum(1 for row in chunk if row.presence) >= len(chunk) / 2,
        "motion_state": majority([row.motion_state for row in chunk], "unknown"),
        "fall": any(row.fall_detected for row in chunk),
    }


def expected_presence(label: str) -> bool:
    return label != "empty"


def expected_motion_active(label: str) -> bool:
    return label in {"motion", "fall"}


def expected_fall(label: str) -> bool:
    return label == "fall"


def label_windows_for_file(
    path: Path,
    segments_by_file: dict[str, Any],
    window_samples: int,
    step_samples: int,
    sample_period_s: float,
) -> list[tuple[float, float, str, np.ndarray]]:
    if path.name in segments_by_file:
        rows = load_debug_rows(path)
        result: list[tuple[float, float, str, np.ndarray]] = []
        if len(rows) < window_samples:
            return result
        for start in range(0, len(rows) - window_samples + 1, step_samples):
            end = start + window_samples
            start_s = start * sample_period_s
            end_s = end * sample_period_s
            label = segment_label_for_window(start_s, end_s, segments_by_file[path.name])
            if label is None:
                continue
            result.append((start_s, end_s, label, summarize_window(rows[start:end])))
        return result
    else:
        label = recording_label(path)
        if label is None:
            return []
        issue = quality_issue(path, label)
        if issue is not None:
            return []
        windows = extract_windows(path, label, window_samples, step_samples)

    result = []
    for idx, window in enumerate(windows):
        start_s = idx * step_samples * sample_period_s
        end_s = start_s + window_samples * sample_period_s
        result.append((start_s, end_s, window.label, window.features))
    return result


def pct(value: int, total: int) -> float:
    return round(100 * value / max(total, 1), 1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate XM126 bridge logic against the tiny ML model.")
    parser.add_argument("--recordings", default=str(BASE_DIR / "data" / "recordings"))
    parser.add_argument("--segments", default=str(BASE_DIR / "data" / "ml" / "xm126_training_segments.json"))
    parser.add_argument("--excluded", default=str(BASE_DIR / "data" / "ml" / "xm126_excluded_recordings.json"))
    parser.add_argument("--model", default=str(BASE_DIR / "data" / "ml" / "xm126_tiny_model.json"))
    parser.add_argument(
        "--profile",
        choices=["current_wall_6m", "all"],
        default="current_wall_6m",
        help="Validation data profile. current_wall_6m ignores old ceiling/early tuning recordings.",
    )
    parser.add_argument("--window-samples", type=int, default=10)
    parser.add_argument("--step-samples", type=int, default=2)
    parser.add_argument("--sample-period-s", type=float, default=0.5)
    args = parser.parse_args()

    recordings = Path(args.recordings)
    segments_by_file = load_segments(Path(args.segments))
    excluded = load_excluded_recordings(Path(args.excluded))
    model = json.loads(Path(args.model).read_text(encoding="utf-8"))

    totals = {
        "windows": 0,
        "bridge_presence_ok": 0,
        "bridge_motion_ok": 0,
        "bridge_fall_ok": 0,
        "ml_presence_ok": 0,
        "ml_class_ok": 0,
    }
    bridge_presence_confusion: dict[str, Counter[str]] = defaultdict(Counter)
    ml_presence_confusion: dict[str, Counter[str]] = defaultdict(Counter)
    ml_class_confusion: dict[str, Counter[str]] = defaultdict(Counter)
    per_file: list[dict[str, Any]] = []

    for path in sorted(recordings.glob("*.jsonl")):
        if profile_issue(path, args.profile) is not None:
            continue
        if path.name in excluded:
            continue
        windows = label_windows_for_file(
            path,
            segments_by_file,
            args.window_samples,
            args.step_samples,
            args.sample_period_s,
        )
        if not windows:
            continue
        states = load_state_rows(path)
        file_totals = Counter()
        labels = Counter()

        for start_s, end_s, label, features in windows:
            bridge = bridge_window_prediction(states, start_s, end_s)
            ml_label = predict(model, features)

            exp_presence = expected_presence(label)
            bridge_presence = bool(bridge["presence"])
            ml_presence = expected_presence(ml_label)

            bridge_motion = bridge["motion_state"] == "active"
            exp_motion = expected_motion_active(label)
            bridge_fall = bool(bridge["fall"])
            exp_fall = expected_fall(label)

            totals["windows"] += 1
            file_totals["windows"] += 1
            labels[label] += 1

            if bridge_presence == exp_presence:
                totals["bridge_presence_ok"] += 1
                file_totals["bridge_presence_ok"] += 1
            if bridge_motion == exp_motion:
                totals["bridge_motion_ok"] += 1
                file_totals["bridge_motion_ok"] += 1
            if bridge_fall == exp_fall:
                totals["bridge_fall_ok"] += 1
                file_totals["bridge_fall_ok"] += 1
            if ml_presence == exp_presence:
                totals["ml_presence_ok"] += 1
                file_totals["ml_presence_ok"] += 1
            if ml_label == label:
                totals["ml_class_ok"] += 1
                file_totals["ml_class_ok"] += 1

            exp_presence_name = "present" if exp_presence else "empty"
            bridge_presence_name = "present" if bridge_presence else "empty"
            ml_presence_name = "present" if ml_presence else "empty"
            bridge_presence_confusion[exp_presence_name][bridge_presence_name] += 1
            ml_presence_confusion[exp_presence_name][ml_presence_name] += 1
            ml_class_confusion[label][ml_label] += 1

        per_file.append(
            {
                "file": path.name,
                "labels": dict(labels),
                "windows": file_totals["windows"],
                "bridge_presence_accuracy": pct(file_totals["bridge_presence_ok"], file_totals["windows"]),
                "bridge_motion_accuracy": pct(file_totals["bridge_motion_ok"], file_totals["windows"]),
                "ml_presence_accuracy": pct(file_totals["ml_presence_ok"], file_totals["windows"]),
                "ml_class_accuracy": pct(file_totals["ml_class_ok"], file_totals["windows"]),
            }
        )

    report = {
        "profile": args.profile,
        "windows": totals["windows"],
        "overall": {
            "bridge_presence_accuracy": pct(totals["bridge_presence_ok"], totals["windows"]),
            "bridge_motion_accuracy": pct(totals["bridge_motion_ok"], totals["windows"]),
            "bridge_fall_accuracy": pct(totals["bridge_fall_ok"], totals["windows"]),
            "ml_presence_accuracy": pct(totals["ml_presence_ok"], totals["windows"]),
            "ml_class_accuracy": pct(totals["ml_class_ok"], totals["windows"]),
        },
        "bridge_presence_confusion": {key: dict(value) for key, value in bridge_presence_confusion.items()},
        "ml_presence_confusion": {key: dict(value) for key, value in ml_presence_confusion.items()},
        "ml_class_confusion": {key: dict(value) for key, value in ml_class_confusion.items()},
        "per_file": per_file,
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
