from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


BASE_DIR = Path(__file__).resolve().parent


FEATURE_KEYS = [
    "motion_energy",
    "motion_activity_ratio",
    "raw_peak_delta",
    "raw_threshold",
    "raw_noise_floor",
    "velocity_mps",
    "distance_step_m",
    "center_of_energy_m",
    "center_step_m",
    "center_velocity_mps",
    "distance_m",
    "processor_distance_m",
    "processor_intra_score",
    "processor_inter_score",
    "baseline_delta_score",
    "baseline_delta_max",
    "baseline_changed_point_count",
    "baseline_changed_zone_count",
    "baseline_delta_span",
    "profile_motion_score",
    "profile_motion_max",
    "profile_motion_changed_points",
    "profile_motion_peak_shift",
    "phase_activity_score",
    "phase_activity_max",
    "phase_activity_changed_points",
    "activity_score",
    "activity_score_raw",
    "activity_phase_component",
    "fall_score",
    "fall_distance_span",
    "fall_stable_duration",
]

BOOL_KEYS = [
    "raw_presence",
    "raw_presence_confirmed",
    "processor_presence",
    "person_range_presence",
    "person_anchor_candidate",
    "person_anchor_confirmed",
    "anchored_processor_presence",
    "baseline_assisted_candidate",
    "baseline_assisted_confirmed",
    "motion_presence_basis",
    "motion_raw_active",
    "wall_fall_impact",
]

CLASSES = ["empty", "present_still", "motion", "fall"]
CURRENT_WALL_6M_START = "20260827_201317"
FEATURE_NAMES = (
    [f"{key}_{stat}" for key in FEATURE_KEYS for stat in ("mean", "max", "std")]
    + [f"{key}_ratio" for key in BOOL_KEYS]
    + [f"zone_{idx + 1}_ratio" for idx in range(6)]
    + ["raw_delta_threshold_ratio_mean", "raw_delta_threshold_ratio_max", "raw_delta_threshold_ratio_std"]
)


@dataclass
class Window:
    source: str
    label: str
    features: np.ndarray


@dataclass
class Segment:
    start_s: float
    end_s: float
    label: str


def recording_label(path: Path) -> str | None:
    name = path.name.lower()
    if path.stat().st_size < 10_000:
        return None
    if "falltest" in name:
        return "fall"
    if "leerer_raum" in name or "ml_empty" in name or "empty_room" in name:
        return "empty"
    if (
        "sitzen" in name
        or "ml_present_still" in name
        or "ecke_still" in name
        or "microbewegung" in name
        or "pc_arbeiten" in name
        or "ml_present_wall_mount_pc" in name
        or "ml_present_wall_mount_free_chair" in name
    ):
        return "present_still"
    if "ml_motion_wall_mount" in name:
        return "motion"
    if "ecke" in name:
        return "motion"
    return None


def load_debug_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not str(item.get("topic", "")).endswith("/debug"):
            continue
        payload = item.get("payload") or item.get("message") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def load_topic_rows(path: Path, suffix: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not str(item.get("topic", "")).endswith(suffix):
            continue
        payload = item.get("payload") or item.get("message") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def load_segments(path: Path) -> dict[str, list[Segment]]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    segments: dict[str, list[Segment]] = {}
    for source, items in data.items():
        if not isinstance(items, list):
            continue
        parsed: list[Segment] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label", ""))
            if label not in CLASSES:
                continue
            parsed.append(
                Segment(
                    start_s=float(item.get("start_s", 0.0)),
                    end_s=float(item.get("end_s", 0.0)),
                    label=label,
                )
            )
        if parsed:
            segments[source] = parsed
    return segments


def load_excluded_recordings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def profile_issue(path: Path, profile: str) -> str | None:
    if profile == "all":
        return None
    if profile == "current_wall_6m":
        if path.name < CURRENT_WALL_6M_START:
            return "outside_current_wall_6m_profile"
        return None
    return f"unknown_profile_{profile}"


def ratio(rows: list[dict[str, Any]], key: str) -> float:
    if not rows:
        return 0.0
    return sum(1 for row in rows if row.get(key)) / len(rows)


def quality_issue(path: Path, label: str) -> str | None:
    debug_rows = load_debug_rows(path)
    state_rows = load_topic_rows(path, "/state")
    if len(debug_rows) < 20:
        return "too_few_debug_rows"

    if label == "empty":
        raw_ratio = ratio(debug_rows, "raw_presence")
        fall_ratio = ratio(state_rows, "fall_detected")
        if fall_ratio > 0.02:
            return f"empty_has_fall_ratio_{fall_ratio:.3f}"
        if raw_ratio > 0.80:
            return f"empty_has_raw_presence_ratio_{raw_ratio:.3f}"

    if label == "present_still":
        presence_ratio = ratio(state_rows, "presence")
        name = path.name.lower()
        if (
            presence_ratio < 0.05
            and "pc_arbeiten" not in name
            and "ml_present_wall_mount_pc" not in name
            and "ml_present_wall_mount_free_chair" not in name
        ):
            return f"present_still_has_presence_ratio_{presence_ratio:.3f}"

    return None


def finite_number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return default


def zone_index(zone: Any) -> int:
    if isinstance(zone, str) and zone.startswith("zone_"):
        try:
            return max(0, min(5, int(zone.split("_", 1)[1]) - 1))
        except ValueError:
            return -1
    return -1


def summarize_window(rows: list[dict[str, Any]]) -> np.ndarray:
    features: list[float] = []
    for key in FEATURE_KEYS:
        values = np.array([finite_number(row.get(key)) for row in rows], dtype=float)
        features.extend(
            [
                float(np.mean(values)),
                float(np.max(values)),
                float(np.std(values)),
            ]
        )

    for key in BOOL_KEYS:
        values = np.array([1.0 if row.get(key) else 0.0 for row in rows], dtype=float)
        features.append(float(np.mean(values)))

    zones = [zone_index(row.get("zone")) for row in rows]
    for idx in range(6):
        features.append(float(sum(1 for zone in zones if zone == idx)) / max(len(zones), 1))

    raw_delta = np.array([finite_number(row.get("raw_peak_delta")) for row in rows], dtype=float)
    raw_threshold = np.array([finite_number(row.get("raw_threshold"), 1.0) for row in rows], dtype=float)
    ratio = raw_delta / np.maximum(raw_threshold, 1.0)
    features.extend([float(np.mean(ratio)), float(np.max(ratio)), float(np.std(ratio))])

    return np.array(features, dtype=float)


def extract_windows(path: Path, label: str, window_samples: int, step_samples: int) -> list[Window]:
    rows = load_debug_rows(path)
    windows: list[Window] = []
    if len(rows) < window_samples:
        return windows
    for start in range(0, len(rows) - window_samples + 1, step_samples):
        chunk = rows[start : start + window_samples]
        chunk_label = label
        if label == "fall":
            has_fall_signal = any(
                row.get("fall_detected") or finite_number(row.get("fall_score")) >= 65 for row in chunk
            )
            chunk_label = "fall" if has_fall_signal else "motion"
        windows.append(Window(source=path.name, label=chunk_label, features=summarize_window(chunk)))
    return windows


def segment_label_for_window(start_s: float, end_s: float, segments: list[Segment]) -> str | None:
    for segment in segments:
        if start_s >= segment.start_s and end_s <= segment.end_s:
            return segment.label
    return None


def extract_segmented_windows(
    path: Path,
    segments: list[Segment],
    window_samples: int,
    step_samples: int,
    sample_period_s: float = 0.5,
) -> list[Window]:
    rows = load_debug_rows(path)
    windows: list[Window] = []
    if len(rows) < window_samples:
        return windows
    for start in range(0, len(rows) - window_samples + 1, step_samples):
        end = start + window_samples
        start_s = start * sample_period_s
        end_s = end * sample_period_s
        label = segment_label_for_window(start_s, end_s, segments)
        if label is None:
            continue
        chunk = rows[start:end]
        windows.append(Window(source=f"{path.name}#segmented", label=label, features=summarize_window(chunk)))
    return windows


def train_centroid_model(windows: list[Window]) -> dict[str, Any]:
    x = np.vstack([window.features for window in windows])
    y = np.array([window.label for window in windows])

    mean = x.mean(axis=0)
    std = x.std(axis=0)
    std[std < 1e-6] = 1.0
    z = (x - mean) / std

    centroids: dict[str, list[float]] = {}
    class_counts: dict[str, int] = {}
    for label in CLASSES:
        mask = y == label
        if np.any(mask):
            centroids[label] = z[mask].mean(axis=0).round(6).tolist()
            class_counts[label] = int(np.sum(mask))

    return {
        "model_type": "standardized_nearest_centroid",
        "classes": [label for label in CLASSES if label in centroids],
        "feature_count": int(x.shape[1]),
        "feature_keys": FEATURE_KEYS,
        "bool_keys": BOOL_KEYS,
        "feature_names": FEATURE_NAMES,
        "mean": mean.round(6).tolist(),
        "std": std.round(6).tolist(),
        "centroids": centroids,
        "class_counts": class_counts,
    }


def gini(labels: np.ndarray) -> float:
    if len(labels) == 0:
        return 0.0
    counts = Counter(str(label) for label in labels)
    return 1.0 - sum((count / len(labels)) ** 2 for count in counts.values())


def majority_label(labels: np.ndarray) -> str:
    counts = Counter(str(label) for label in labels)
    for label, _ in counts.most_common():
        return label
    return "empty"


def split_candidates(values: np.ndarray) -> list[float]:
    finite = np.array([float(value) for value in values if math.isfinite(float(value))], dtype=float)
    if len(finite) < 2:
        return []
    unique = np.unique(finite)
    if len(unique) <= 12:
        return [float((unique[idx] + unique[idx + 1]) / 2.0) for idx in range(len(unique) - 1)]
    return sorted(set(float(np.percentile(finite, pct)) for pct in (10, 20, 30, 40, 50, 60, 70, 80, 90)))


def build_tree(
    x: np.ndarray,
    y: np.ndarray,
    *,
    depth: int = 0,
    max_depth: int = 6,
    min_leaf: int = 16,
) -> dict[str, Any]:
    prediction = majority_label(y)
    counts = dict(Counter(str(label) for label in y))
    if depth >= max_depth or len(y) < min_leaf * 2 or len(set(str(label) for label in y)) == 1:
        return {"type": "leaf", "prediction": prediction, "counts": counts}

    parent_gini = gini(y)
    best: tuple[float, int, float, np.ndarray] | None = None
    for feature_idx in range(x.shape[1]):
        values = x[:, feature_idx]
        for threshold in split_candidates(values):
            left = values <= threshold
            left_count = int(np.sum(left))
            right_count = len(y) - left_count
            if left_count < min_leaf or right_count < min_leaf:
                continue
            score = (left_count / len(y)) * gini(y[left]) + (right_count / len(y)) * gini(y[~left])
            gain = parent_gini - score
            if best is None or gain > best[0]:
                best = (gain, feature_idx, threshold, left)

    if best is None or best[0] < 0.005:
        return {"type": "leaf", "prediction": prediction, "counts": counts}

    gain, feature_idx, threshold, left = best
    return {
        "type": "node",
        "prediction": prediction,
        "counts": counts,
        "feature": int(feature_idx),
        "feature_name": FEATURE_NAMES[feature_idx] if feature_idx < len(FEATURE_NAMES) else f"feature_{feature_idx}",
        "threshold": round(float(threshold), 6),
        "gain": round(float(gain), 6),
        "left": build_tree(x[left], y[left], depth=depth + 1, max_depth=max_depth, min_leaf=min_leaf),
        "right": build_tree(x[~left], y[~left], depth=depth + 1, max_depth=max_depth, min_leaf=min_leaf),
    }


def train_tree_model(windows: list[Window], max_depth: int = 6, min_leaf: int = 16) -> dict[str, Any]:
    x = np.vstack([window.features for window in windows])
    y = np.array([window.label for window in windows])
    return {
        "model_type": "decision_tree",
        "classes": [label for label in CLASSES if label in set(y.tolist())],
        "feature_count": int(x.shape[1]),
        "feature_keys": FEATURE_KEYS,
        "bool_keys": BOOL_KEYS,
        "feature_names": FEATURE_NAMES,
        "max_depth": max_depth,
        "min_leaf": min_leaf,
        "class_counts": dict(Counter(str(label) for label in y)),
        "tree": build_tree(x, y, max_depth=max_depth, min_leaf=min_leaf),
    }


def predict(model: dict[str, Any], features: np.ndarray) -> str:
    if model.get("model_type") == "decision_tree":
        node = model.get("tree", {})
        while isinstance(node, dict) and node.get("type") == "node":
            feature_idx = int(node.get("feature", -1))
            threshold = float(node.get("threshold", 0.0))
            if feature_idx < 0 or feature_idx >= len(features):
                break
            node = node.get("left") if float(features[feature_idx]) <= threshold else node.get("right")
        if isinstance(node, dict):
            return str(node.get("prediction", "empty"))
        return "empty"

    mean = np.array(model["mean"], dtype=float)
    std = np.array(model["std"], dtype=float)
    z = (features - mean) / std
    best_label = ""
    best_distance = float("inf")
    for label, centroid_values in model["centroids"].items():
        centroid = np.array(centroid_values, dtype=float)
        distance = float(np.sum((z - centroid) ** 2))
        if distance < best_distance:
            best_label = label
            best_distance = distance
    return best_label


def train_model(windows: list[Window], model_type: str, max_depth: int, min_leaf: int) -> dict[str, Any]:
    if model_type == "centroid":
        return train_centroid_model(windows)
    if model_type == "tree":
        return train_tree_model(windows, max_depth=max_depth, min_leaf=min_leaf)
    raise ValueError(f"Unsupported model type: {model_type}")


def evaluate_leave_one_recording_out(
    windows: list[Window],
    *,
    model_type: str,
    max_depth: int,
    min_leaf: int,
) -> dict[str, Any]:
    by_source: dict[str, list[Window]] = defaultdict(list)
    for window in windows:
        by_source[window.source].append(window)

    confusion: dict[str, Counter[str]] = {label: Counter() for label in CLASSES}
    source_results: list[dict[str, Any]] = []

    for source, test_windows in sorted(by_source.items()):
        train_windows = [window for window in windows if window.source != source]
        if len({window.label for window in train_windows}) < 2:
            continue
        model = train_model(train_windows, model_type, max_depth, min_leaf)
        correct = 0
        for window in test_windows:
            pred = predict(model, window.features)
            confusion[window.label][pred] += 1
            correct += int(pred == window.label)
        source_results.append(
            {
                "source": source,
                "label_counts": dict(Counter(window.label for window in test_windows)),
                "accuracy": round(correct / max(len(test_windows), 1), 3),
                "windows": len(test_windows),
            }
        )

    total = sum(sum(row.values()) for row in confusion.values())
    correct = sum(confusion[label][label] for label in CLASSES)
    return {
        "accuracy": round(correct / total, 3) if total else 0.0,
        "confusion": {label: dict(confusion[label]) for label in CLASSES},
        "sources": source_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Train a tiny XM126 feature model from MQTT recordings.")
    parser.add_argument("--recordings", default=str(BASE_DIR / "data" / "recordings"))
    parser.add_argument("--output", default=str(BASE_DIR / "data" / "ml" / "xm126_tiny_model.json"))
    parser.add_argument("--report", default=str(BASE_DIR / "data" / "ml" / "xm126_tiny_model_report.json"))
    parser.add_argument("--segments", default=str(BASE_DIR / "data" / "ml" / "xm126_training_segments.json"))
    parser.add_argument("--excluded", default=str(BASE_DIR / "data" / "ml" / "xm126_excluded_recordings.json"))
    parser.add_argument(
        "--profile",
        choices=["current_wall_6m", "all"],
        default="current_wall_6m",
        help="Training data profile. current_wall_6m ignores old ceiling/early tuning recordings.",
    )
    parser.add_argument("--window-samples", type=int, default=10, help="About 5 seconds at 2 Hz.")
    parser.add_argument("--step-samples", type=int, default=2, help="About 1 second at 2 Hz.")
    parser.add_argument("--model-type", choices=["tree", "centroid"], default="tree")
    parser.add_argument("--max-depth", type=int, default=6)
    parser.add_argument("--min-leaf", type=int, default=16)
    args = parser.parse_args()

    recording_dir = Path(args.recordings)
    windows: list[Window] = []
    labeled_files: dict[str, str] = {}
    skipped_files: dict[str, str] = {}
    segments_by_file = load_segments(Path(args.segments))
    excluded_files = load_excluded_recordings(Path(args.excluded))
    for path in sorted(recording_dir.glob("*.jsonl")):
        issue = profile_issue(path, args.profile)
        if issue is not None:
            skipped_files[path.name] = issue
            continue
        if path.name in excluded_files:
            reason = excluded_files[path.name]
            if isinstance(reason, dict):
                skipped_files[path.name] = str(reason.get("reason", "excluded"))
            else:
                skipped_files[path.name] = "excluded"
            continue
        if path.name in segments_by_file:
            file_windows = extract_segmented_windows(
                path,
                segments_by_file[path.name],
                args.window_samples,
                args.step_samples,
            )
            if file_windows:
                windows.extend(file_windows)
                labeled_files[path.name] = "segmented"
            continue
        label = recording_label(path)
        if label is None:
            continue
        issue = quality_issue(path, label)
        if issue is not None:
            skipped_files[path.name] = issue
            continue
        file_windows = extract_windows(path, label, args.window_samples, args.step_samples)
        if file_windows:
            windows.extend(file_windows)
            labeled_files[path.name] = label

    if not windows:
        raise RuntimeError("No labeled training windows found")

    model = train_model(windows, args.model_type, args.max_depth, args.min_leaf)
    evaluation = evaluate_leave_one_recording_out(
        windows,
        model_type=args.model_type,
        max_depth=args.max_depth,
        min_leaf=args.min_leaf,
    )
    report = {
        "window_samples": args.window_samples,
        "step_samples": args.step_samples,
        "profile": args.profile,
        "model_type": args.model_type,
        "max_depth": args.max_depth,
        "min_leaf": args.min_leaf,
        "labeled_files": labeled_files,
        "skipped_files": skipped_files,
        "window_counts": dict(Counter(window.label for window in windows)),
        "evaluation": evaluation,
    }

    output_path = Path(args.output)
    report_path = Path(args.report)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(model, indent=2), encoding="utf-8")
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    print(f"model: {output_path}")
    print(f"report: {report_path}")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
