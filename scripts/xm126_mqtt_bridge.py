from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import attrs
import numpy as np
import paho.mqtt.client as mqtt
from acconeer.exptool import a121
from acconeer.exptool.a121.algo._utils import APPROX_BASE_STEP_LENGTH_M, get_distances_m
from acconeer.exptool.a121.algo.breathing import RefApp, RefAppConfig
from acconeer.exptool.a121.algo.distance import Detector as DistanceDetector
from acconeer.exptool.a121.algo.distance import DetectorConfig as DistanceDetectorConfig
from acconeer.exptool.a121.algo.distance import PeakSortingMethod
from acconeer.exptool.a121.algo.presence import Detector as PresenceDetector
from acconeer.exptool.a121.algo.presence import DetectorConfig as PresenceDetectorConfig
from acconeer.exptool.a121.algo.presence._processors import Processor as PresenceProcessor
from acconeer.exptool.a121.algo.presence._processors import ProcessorConfig as PresenceProcessorConfig
from acconeer.exptool.a121.algo.smart_presence import Processor as SmartPresenceProcessor
from acconeer.exptool.a121.algo.smart_presence import ProcessorConfig as SmartPresenceProcessorConfig

try:
    from xm126_ml_train import predict as ml_predict
    from xm126_ml_train import summarize_window as ml_summarize_window
except Exception:
    ml_predict = None
    ml_summarize_window = None


DEFAULT_DEVICE_ID = "xm126-00124a"
DEFAULT_STATE_TOPIC = "sentero/{device_id}/state"
DEFAULT_DEBUG_TOPIC = "sentero/{device_id}/debug"
DEFAULT_AVAILABILITY_TOPIC = "sentero/{device_id}/availability"
DEFAULT_SETTINGS_TOPIC = "sentero/{device_id}/settings"
DEFAULT_SET_TOPIC = "sentero/{device_id}/set/+"
DEFAULT_COMMAND_PREFIX = "sentero/{device_id}/set"
DEFAULT_EMPTY_ROOM_BASELINE_PATH = "data/xm126_empty_room_baseline.json"
DEFAULT_ML_MODEL_PATH = "data/ml/xm126_tiny_model.json"


@dataclass
class BridgeConfig:
    mode: str = "sturzmodus"
    mounting_mode: str = "ceiling"
    sensitivity: str = "normal"
    start_m: float = 0.30
    end_m: float = 4.00
    frame_rate: float = 8.0
    sweeps_per_frame: int = 16
    hwaas: int = 32
    intra_detection_threshold: float = 1.3
    inter_detection_threshold: float = 1.0
    intra_output_time_const: float = 0.3
    inter_output_time_const: float = 2.0
    inter_frame_presence_timeout: int = 3
    zone_near_m: float = 0.8
    zone_far_m: float = 1.8
    presence_max_m: float = 1.95
    fall_floor_min_m: float = 1.7
    fall_active_window_s: float = 6.0
    fall_still_confirm_s: float = 8.0
    fall_clear_reset_s: float = 15.0
    fall_velocity_min_mps: float = 0.45
    fall_drop_min_m: float = 0.45
    fall_floor_margin_m: float = 0.45
    raw_presence_min_energy: float = 150.0
    raw_presence_threshold_factor: float = 4.0
    raw_presence_confirm_s: float = 1.5
    raw_presence_hold_s: float = 60.0
    baseline_presence_delta_score: float = 2.8
    baseline_presence_delta_max: float = 4.3
    baseline_presence_confirm_s: float = 5.0
    baseline_presence_confirm_count: int = 5
    baseline_presence_hold_s: float = 12.0
    still_presence_inter_score: float = 1.05
    still_presence_confirm_s: float = 20.0
    still_presence_confirm_count: int = 80
    still_presence_hold_s: float = 20.0
    stationary_presence_grace_s: float = 45.0
    person_anchor_confirm_s: float = 6.0
    person_anchor_confirm_count: int = 3
    motion_active_hold_s: float = 2.5
    motion_velocity_min_mps: float = 0.20
    breathing_lowest_rate: float = 6.0
    breathing_highest_rate: float = 30.0
    ceiling_height_m: float = 2.4
    ceiling_calibration_status: str = "not_calibrated"


MODE_PRESETS: dict[str, dict[str, Any]] = {
    "schlafmodus": {
        "start_m": 0.30,
        "end_m": 1.50,
        "frame_rate": 20.0,
        "hwaas": 32,
        "sweeps_per_frame": 16,
    },
    "sturzmodus": {
        "start_m": 0.15,
        "end_m": 3.50,
        "frame_rate": 12.0,
        "sweeps_per_frame": 16,
        "hwaas": 32,
        "raw_presence_min_energy": 150.0,
        "raw_presence_threshold_factor": 4.0,
        "raw_presence_confirm_s": 1.5,
        "raw_presence_hold_s": 8.0,
        "baseline_presence_delta_score": 2.8,
        "baseline_presence_delta_max": 4.3,
        "baseline_presence_confirm_s": 5.0,
        "baseline_presence_confirm_count": 5,
        "baseline_presence_hold_s": 12.0,
        "still_presence_inter_score": 1.05,
        "still_presence_confirm_s": 20.0,
        "still_presence_confirm_count": 80,
        "still_presence_hold_s": 20.0,
        "stationary_presence_grace_s": 45.0,
        "person_anchor_confirm_s": 6.0,
        "person_anchor_confirm_count": 3,
        "presence_max_m": 1.95,
    },
}

SENSITIVITY_PRESETS: dict[str, dict[str, float]] = {
    "low": {"intra_detection_threshold": 2.0, "inter_detection_threshold": 1.8},
    "normal": {"intra_detection_threshold": 1.3, "inter_detection_threshold": 1.0},
    "high": {"intra_detection_threshold": 0.9, "inter_detection_threshold": 0.7},
}

RESTART_KEYS = {
    "mode",
    "sensitivity",
    "start_m",
    "end_m",
    "frame_rate",
    "sweeps_per_frame",
    "hwaas",
    "intra_detection_threshold",
    "inter_detection_threshold",
    "intra_output_time_const",
    "inter_output_time_const",
    "inter_frame_presence_timeout",
    "presence_max_m",
    "raw_presence_min_energy",
    "raw_presence_threshold_factor",
    "raw_presence_confirm_s",
    "raw_presence_hold_s",
    "baseline_presence_delta_score",
    "baseline_presence_delta_max",
    "baseline_presence_confirm_s",
    "baseline_presence_confirm_count",
    "baseline_presence_hold_s",
    "still_presence_inter_score",
    "still_presence_confirm_s",
    "still_presence_confirm_count",
    "still_presence_hold_s",
    "stationary_presence_grace_s",
    "person_anchor_confirm_s",
    "person_anchor_confirm_count",
    "breathing_lowest_rate",
    "breathing_highest_rate",
}

DISCOVERY_CONFIGS = [
    ("binary_sensor", "presence"),
    ("binary_sensor", "fall_detected"),
    ("sensor", "motion_state"),
    ("sensor", "ceiling_calibration_status"),
    ("select", "mode_select"),
    ("select", "sensitivity_select"),
    ("button", "calibrate"),
]

OBSOLETE_DISCOVERY_CONFIGS = [
    ("binary_sensor", "occupancy"),
    ("binary_sensor", "breathing_detected"),
    ("binary_sensor", "fall_candidate"),
    ("binary_sensor", "motion_active"),
    ("sensor", "distance_m"),
    ("sensor", "zone"),
    ("number", "start_m"),
    ("number", "end_m"),
    ("number", "frame_rate"),
    ("number", "hwaas"),
    ("number", "inter_detection_threshold"),
    ("number", "inter_frame_presence_timeout"),
    ("number", "inter_output_time_const"),
    ("number", "intra_detection_threshold"),
    ("number", "intra_output_time_const"),
    ("number", "sweeps_per_frame"),
    ("number", "zone_far_m"),
    ("number", "zone_near_m"),
    ("number", "fall_active_window_s"),
    ("number", "fall_clear_reset_s"),
    ("number", "fall_floor_min_m"),
    ("number", "fall_velocity_min_mps"),
    ("number", "fall_drop_min_m"),
    ("number", "fall_floor_margin_m"),
    ("number", "motion_active_hold_s"),
    ("number", "motion_velocity_min_mps"),
    ("number", "fall_still_confirm_s"),
    ("number", "breathing_lowest_rate"),
    ("number", "breathing_highest_rate"),
    ("sensor", "breathing_app_state"),
    ("sensor", "breathing_rate"),
    ("sensor", "chip_temperature"),
    ("sensor", "calibration_status"),
    ("sensor", "estimated_frame_rate"),
    ("sensor", "fall_confidence"),
    ("sensor", "fall_state"),
    ("sensor", "floor_presence_s"),
    ("sensor", "inter_presence_score"),
    ("sensor", "intra_presence_score"),
    ("sensor", "mode"),
    ("sensor", "motion_state"),
    ("sensor", "port"),
    ("sensor", "presence_score"),
    ("sensor", "sensitivity"),
    ("sensor", "stillness_s"),
    ("sensor", "tick_time"),
    ("sensor", "uptime_s"),
]


def load_config(path: Path) -> BridgeConfig:
    if not path.exists():
        return BridgeConfig()
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("mode") == "raw_test":
        data["mode"] = "sturzmodus"
    if "calibration_status" in data and "ceiling_calibration_status" not in data:
        data["ceiling_calibration_status"] = data.pop("calibration_status")
    if data.get("mode") not in MODE_PRESETS:
        data["mode"] = "sturzmodus"
    return BridgeConfig(**{**asdict(BridgeConfig()), **data})


def save_config(path: Path, config: BridgeConfig) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), indent=2), encoding="utf-8")


def public_settings(config: BridgeConfig) -> dict[str, Any]:
    return {
        "mode": config.mode,
        "mounting_mode": config.mounting_mode,
        "sensitivity": config.sensitivity,
        "ceiling_calibration_status": config.ceiling_calibration_status,
    }


def load_empty_room_baseline(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data.get("amplitude_mean"), list) or not isinstance(data.get("amplitude_std"), list):
        return None
    return data


def save_empty_room_baseline(
    path: Path,
    *,
    device_id: str,
    config: BridgeConfig,
    distances_m: np.ndarray[Any, Any],
    samples: list[np.ndarray[Any, Any]],
) -> dict[str, Any]:
    stack = np.vstack(samples)
    mean = stack.mean(axis=0)
    std = stack.std(axis=0)
    data = {
        "device_id": device_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "sample_count": len(samples),
        "mode": config.mode,
        "sensitivity": config.sensitivity,
        "ceiling_height_m": config.ceiling_height_m,
        "presence_max_m": config.presence_max_m,
        "distances_m": [round(float(value), 6) for value in distances_m],
        "amplitude_mean": [round(float(value), 6) for value in mean],
        "amplitude_std": [round(max(float(value), 1.0), 6) for value in std],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def apply_presets(config: BridgeConfig) -> BridgeConfig:
    data = asdict(config)
    if config.mode in MODE_PRESETS:
        preset = dict(MODE_PRESETS[config.mode])
        if config.mounting_mode == "wall":
            for key in (
                "start_m",
                "end_m",
                "presence_max_m",
                "fall_floor_min_m",
                "zone_near_m",
                "zone_far_m",
                "frame_rate",
                "hwaas",
                "sweeps_per_frame",
            ):
                preset.pop(key, None)
        data.update(preset)
    if config.sensitivity in SENSITIVITY_PRESETS:
        data.update(SENSITIVITY_PRESETS[config.sensitivity])
    if config.mounting_mode == "wall":
        data.update(
            {
                "baseline_presence_delta_score": 5.5,
                "baseline_presence_delta_max": 9.0,
                "stationary_presence_grace_s": 15.0,
                "raw_presence_hold_s": 6.0,
                "still_presence_hold_s": 10.0,
                "person_anchor_confirm_s": 4.0,
                "person_anchor_confirm_count": 3,
                "motion_active_hold_s": 1.1,
            }
        )
    return BridgeConfig(**data)


def detector_config(config: BridgeConfig) -> PresenceDetectorConfig:
    effective = apply_presets(config)
    return PresenceDetectorConfig(
        start_m=effective.start_m,
        end_m=effective.end_m,
        frame_rate=effective.frame_rate,
        sweeps_per_frame=effective.sweeps_per_frame,
        hwaas=effective.hwaas,
        intra_detection_threshold=effective.intra_detection_threshold,
        inter_detection_threshold=effective.inter_detection_threshold,
        intra_output_time_const=effective.intra_output_time_const,
        inter_output_time_const=effective.inter_output_time_const,
        inter_frame_presence_timeout=effective.inter_frame_presence_timeout,
    )


def distance_detector_config(config: BridgeConfig) -> DistanceDetectorConfig:
    effective = apply_presets(config)
    threshold_sensitivity = {
        "low": 0.35,
        "normal": 0.50,
        "high": 0.75,
    }.get(effective.sensitivity, 0.50)
    return DistanceDetectorConfig(
        start_m=effective.start_m,
        end_m=effective.end_m,
        signal_quality=max(15.0, float(effective.hwaas) / 2.0),
        threshold_sensitivity=threshold_sensitivity,
        peaksorting_method=PeakSortingMethod.CLOSEST,
        update_rate=effective.frame_rate,
    )


def breathing_config(config: BridgeConfig) -> RefAppConfig:
    effective = apply_presets(config)
    ref_config = RefAppConfig()
    ref_config.start_m = effective.start_m
    ref_config.end_m = effective.end_m
    ref_config.frame_rate = effective.frame_rate
    ref_config.hwaas = effective.hwaas
    ref_config.sweeps_per_frame = effective.sweeps_per_frame
    ref_config.breathing_config.lowest_breathing_rate = effective.breathing_lowest_rate
    ref_config.breathing_config.highest_breathing_rate = effective.breathing_highest_rate
    return ref_config


def zone_for(distance_m: float, config: BridgeConfig) -> str:
    if distance_m <= 0:
        return "none"
    if distance_m < config.zone_near_m:
        return "near"
    if distance_m < config.zone_far_m:
        return "middle"
    return "far"


def smart_zone_label(zone_index: int | None) -> str:
    if zone_index is None:
        return "none"
    return f"zone_{zone_index + 1}"


def pick_distance_peak(distances: Any) -> float:
    if distances is None or len(distances) == 0:
        return 0.0
    candidates = [float(distance) for distance in distances if float(distance) > 0]
    if not candidates:
        return 0.0
    return min(candidates)


class RawTestController:
    def __init__(
        self,
        *,
        client: a121.Client,
        config: BridgeConfig,
        device_id: str,
        empty_room_baseline_path: Path,
        ml_model_path: Path,
    ) -> None:
        self.client = client
        self.config = apply_presets(config)
        self.device_id = device_id
        self.empty_room_baseline_path = empty_room_baseline_path
        self.empty_room_baseline = load_empty_room_baseline(empty_room_baseline_path)
        self.sensor_config = self._sensor_config(self.config)
        self.session_config = a121.SessionConfig({1: self.sensor_config}, extended=False)
        self.metadata: a121.Metadata | None = None
        self.presence_processor: PresenceProcessor | None = None
        self.distances_m: np.ndarray[Any, Any] | None = None
        self.baseline: np.ndarray[Any, Any] | None = None
        self.noise_floor = 1.0
        self.frame_count = 0
        self.last_distance_m: float | None = None
        self.last_time_s: float | None = None
        self.motion_active_until = 0.0
        self.motion_activity_samples: list[tuple[float, bool]] = []
        self.active_candidate_samples: list[tuple[float, bool]] = []
        self.baseline_delta_history: list[tuple[float, float]] = []
        self.last_empty_room_z: np.ndarray[Any, Any] | None = None
        self.last_phase_profile: np.ndarray[Any, Any] | None = None
        self.last_center_of_energy_m: float | None = None
        self.motion_fsm_state = "clear"
        self.motion_fsm_changed_at = time.monotonic()
        self.activity_score_smoothed = 0.0
        self.presence_until = 0.0
        self.stationary_presence_until = 0.0
        self.person_anchor_until = 0.0
        self.person_anchor_candidates: list[float] = []
        self.raw_presence_since: float | None = None
        self.baseline_presence_candidates: list[float] = []
        self.baseline_presence_until = 0.0
        self.still_presence_candidates: list[float] = []
        self.still_presence_until = 0.0
        self.quiet_empty_since: float | None = None
        self.last_presence_distance_m = 0.0
        self.last_presence_zone = "none"
        self.fall_impact_until = 0.0
        self.fall_candidate_since: float | None = None
        self.fall_candidate_until = 0.0
        self.fall_detected_until = 0.0
        self.fall_detection_enabled_at = time.monotonic() + 30.0
        self.fall_distance_history: list[tuple[float, float]] = []
        self.empty_room_learning_samples: list[np.ndarray[Any, Any]] = []
        self.empty_room_learning_target_samples = 0
        self.empty_room_learning_starts_at = 0.0
        self.empty_room_learning_duration_s = 60.0
        self.empty_room_baseline_status = "loaded" if self.empty_room_baseline is not None else "not_learned"
        self.ml_model = self._load_ml_model(ml_model_path)
        self.ml_window: list[dict[str, Any]] = []
        self.ml_motion_samples: list[tuple[float, str]] = []

    @staticmethod
    def _load_ml_model(path: Path) -> dict[str, Any] | None:
        if ml_predict is None or ml_summarize_window is None or not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        if data.get("model_type") == "decision_tree" and data.get("tree"):
            return data
        if not data.get("centroids"):
            return None
        return data

    def _apply_ml_hybrid(self, result: dict[str, Any]) -> dict[str, Any]:
        result["rule_presence"] = bool(result["presence"])
        result["hybrid_presence_enabled"] = self.ml_model is not None
        result["hybrid_motion_enabled"] = self.ml_model is not None
        result["ml_label"] = "unavailable"
        result["ml_presence"] = None
        result["ml_motion_ratio"] = 0.0
        result["ml_still_ratio"] = 0.0
        if self.ml_model is None or ml_predict is None or ml_summarize_window is None:
            return result

        self.ml_window.append(dict(result))
        self.ml_window = self.ml_window[-10:]
        if len(self.ml_window) < 10:
            result["ml_label"] = "warming_up"
            return result

        try:
            features = ml_summarize_window(self.ml_window)
            ml_label = str(ml_predict(self.ml_model, features))
        except Exception as exc:
            result["ml_label"] = f"error: {exc}"
            return result

        ml_presence = ml_label != "empty"
        now = time.monotonic()
        self.ml_motion_samples.append((now, ml_label))
        self.ml_motion_samples = [
            sample for sample in self.ml_motion_samples if now - sample[0] <= 3.2
        ]
        ml_motion_ratio = (
            sum(1 for _, label in self.ml_motion_samples if label in {"motion", "fall"})
            / len(self.ml_motion_samples)
            if self.ml_motion_samples
            else 0.0
        )
        ml_still_ratio = (
            sum(1 for _, label in self.ml_motion_samples if label == "present_still")
            / len(self.ml_motion_samples)
            if self.ml_motion_samples
            else 0.0
        )
        result["ml_label"] = ml_label
        result["ml_presence"] = ml_presence
        result["ml_motion_ratio"] = round(float(ml_motion_ratio), 3)
        result["ml_still_ratio"] = round(float(ml_still_ratio), 3)
        if result.get("quiet_empty_confirmed") and ml_label != "fall":
            self.ml_motion_samples = []
            result["presence"] = bool(result["rule_presence"] or result["fall_detected"])
            result["motion_state"] = "still" if result["presence"] else "clear"
            result["presence_reason"] = "quiet_empty"
            result["presence_confidence"] = min(int(result["presence_confidence"]), 20)
            result["motion_active_reason"] = "quiet_empty"
            return result
        result["presence"] = bool(result["rule_presence"] or ml_presence or result["fall_detected"])
        if result["presence"]:
            if result["motion_state"] == "clear":
                result["motion_state"] = "still"
            rule_motion_state = str(result["motion_state"])
            strong_rule_motion = bool(result.get("strong_active_evidence")) or str(
                result.get("motion_active_reason", "none")
            ) in {"velocity", "intra_energy"}
            if ml_motion_ratio >= 0.55 and ml_label in {"motion", "fall"}:
                result["motion_state"] = "active"
                result["motion_active_reason"] = "ml_motion"
            elif (
                ml_still_ratio >= 0.55
                and ml_label == "present_still"
                and not strong_rule_motion
                and not result["fall_detected"]
            ):
                result["motion_state"] = "still"
                result["motion_active_reason"] = "ml_still"
            else:
                result["motion_state"] = rule_motion_state
            if result["fall_detected"]:
                result["presence_reason"] = "fall_detected"
            elif result["rule_presence"]:
                result["presence_reason"] = str(result.get("presence_reason", "baseline_delta"))
            elif ml_label == "fall":
                result["presence_reason"] = "ml_motion_fall_candidate"
            else:
                result["presence_reason"] = f"ml_{ml_label}"
            if ml_presence:
                result["presence_confidence"] = max(int(result["presence_confidence"]), 75)
        elif not result["rule_presence"]:
            result["motion_state"] = "clear"
            self.ml_motion_samples = []
            result["presence_reason"] = "ml_empty"
            result["presence_confidence"] = min(int(result["presence_confidence"]), 35)
        return result

    def start_empty_room_learning(self, duration_s: float = 60.0, delay_s: float = 15.0) -> None:
        self.empty_room_learning_samples = []
        self.empty_room_learning_target_samples = 0
        self.empty_room_learning_duration_s = duration_s
        self.empty_room_learning_starts_at = time.monotonic() + delay_s
        self.empty_room_baseline = None
        self.empty_room_baseline_status = "waiting_to_learn"

    def _empty_room_delta(self, amplitude: np.ndarray[Any, Any]) -> dict[str, Any]:
        if self.empty_room_baseline is None:
            return {
                "empty_room_baseline_status": self.empty_room_baseline_status,
                "baseline_delta_score": 0.0,
                "baseline_delta_max": 0.0,
                "baseline_changed_point_count": 0,
                "baseline_changed_zone_count": 0,
                "baseline_presence_candidate": False,
            }

        mean = np.array(self.empty_room_baseline.get("amplitude_mean", []), dtype=float)
        std = np.array(self.empty_room_baseline.get("amplitude_std", []), dtype=float)
        if mean.shape != amplitude.shape or std.shape != amplitude.shape:
            self.empty_room_baseline_status = "shape_mismatch"
            return {
                "empty_room_baseline_status": self.empty_room_baseline_status,
                "baseline_delta_score": 0.0,
                "baseline_delta_max": 0.0,
                "baseline_changed_point_count": 0,
                "baseline_changed_zone_count": 0,
                "baseline_presence_candidate": False,
            }

        z = np.abs(amplitude - mean) / np.maximum(std, 1.0)
        changed_mask = z >= 4.0
        zone_edges = np.linspace(0, len(z), 7).astype(int)
        changed_zone_count = 0
        for idx in range(6):
            start = zone_edges[idx]
            end = zone_edges[idx + 1]
            if bool(np.any(changed_mask[start:end])):
                changed_zone_count += 1
        delta_score = float(np.mean(np.sort(z)[-min(5, len(z)) :]))
        delta_max = float(np.max(z))
        delta_peak_idx = int(np.argmax(z))
        changed_point_count = int(np.sum(changed_mask))
        return {
            "empty_room_baseline_status": self.empty_room_baseline_status,
            "baseline_delta_score": round(delta_score, 3),
            "baseline_delta_max": round(delta_max, 3),
            "baseline_delta_peak_index": delta_peak_idx,
            "baseline_changed_point_count": changed_point_count,
            "baseline_changed_zone_count": changed_zone_count,
            "baseline_presence_candidate": bool(delta_score >= 4.0 and changed_point_count >= 2),
        }

    def _empty_room_z(self, amplitude: np.ndarray[Any, Any]) -> np.ndarray[Any, Any] | None:
        if self.empty_room_baseline is None:
            return None
        mean = np.array(self.empty_room_baseline.get("amplitude_mean", []), dtype=float)
        std = np.array(self.empty_room_baseline.get("amplitude_std", []), dtype=float)
        if mean.shape != amplitude.shape or std.shape != amplitude.shape:
            return None
        return np.abs(amplitude - mean) / np.maximum(std, 1.0)

    @staticmethod
    def _sensor_config(config: BridgeConfig) -> a121.SensorConfig:
        step_length = 24
        start_point = max(1, int(round(config.start_m / APPROX_BASE_STEP_LENGTH_M)))
        end_point = max(start_point + step_length, int(round(config.end_m / APPROX_BASE_STEP_LENGTH_M)))
        num_points = int((end_point - start_point) / step_length) + 1
        prf = None
        if config.end_m > 18.0:
            prf = a121.PRF.PRF_5_2_MHz
        elif config.end_m > 12.0:
            prf = a121.PRF.PRF_6_5_MHz
        elif config.end_m > 7.0:
            prf = a121.PRF.PRF_8_7_MHz
        elif config.end_m > 5.0:
            prf = a121.PRF.PRF_13_0_MHz
        return a121.SensorConfig(
            start_point=start_point,
            num_points=num_points,
            step_length=step_length,
            hwaas=int(config.hwaas),
            sweeps_per_frame=int(config.sweeps_per_frame),
            frame_rate=float(config.frame_rate),
            prf=prf,
        )

    def start(self) -> None:
        metadata = self.client.setup_session(self.session_config)
        assert isinstance(metadata, a121.Metadata)
        self.metadata = metadata
        self.distances_m = get_distances_m(self.sensor_config, metadata)
        self.presence_processor = PresenceProcessor(
            sensor_config=self.sensor_config,
            metadata=metadata,
            processor_config=PresenceProcessorConfig(
                intra_detection_threshold=self.config.intra_detection_threshold,
                inter_detection_threshold=self.config.inter_detection_threshold,
                intra_output_time_const=self.config.intra_output_time_const,
                inter_output_time_const=self.config.inter_output_time_const,
                inter_frame_presence_timeout=self.config.inter_frame_presence_timeout,
            ),
        )
        self.client.start_session()

    def stop(self) -> None:
        try:
            self.client.stop_session()
        except Exception:
            pass

    def get_next(self) -> dict[str, Any]:
        result = self.client.get_next()
        now = time.monotonic()
        assert self.distances_m is not None
        assert self.presence_processor is not None
        presence_result = self.presence_processor.process(result)
        frame = result.frame
        amplitude = np.abs(frame).mean(axis=0)
        iq_profile = frame.mean(axis=0)
        phase_profile = np.angle(iq_profile)
        phase_activity_score = 0.0
        phase_activity_max = 0.0
        phase_activity_changed_points = 0
        if self.last_phase_profile is not None and self.last_phase_profile.shape == phase_profile.shape:
            phase_delta = np.abs(np.angle(np.exp(1j * (phase_profile - self.last_phase_profile))))
            amplitude_gate = amplitude >= np.percentile(amplitude, 55)
            gated_phase_delta = phase_delta[amplitude_gate] if np.any(amplitude_gate) else phase_delta
            phase_activity_score = float(np.mean(np.sort(gated_phase_delta)[-min(5, len(gated_phase_delta)) :]))
            phase_activity_max = float(np.max(gated_phase_delta))
            phase_activity_changed_points = int(np.sum(gated_phase_delta >= (0.55 if self.config.mounting_mode == "wall" else 0.35)))
        self.last_phase_profile = phase_profile
        if (
            self.empty_room_learning_starts_at > 0.0
            and now >= self.empty_room_learning_starts_at
            and self.empty_room_learning_target_samples == 0
        ):
            self.empty_room_learning_samples = []
            observed_frame_rate = 1.0 / max(now - self.last_time_s, 1e-3) if self.last_time_s else self.config.frame_rate
            self.empty_room_learning_target_samples = max(
                10,
                int(self.empty_room_learning_duration_s * min(self.config.frame_rate, observed_frame_rate)),
            )
            self.empty_room_learning_starts_at = 0.0
            self.empty_room_baseline_status = "learning"
        if self.empty_room_learning_target_samples > 0:
            self.empty_room_learning_samples.append(amplitude.astype(float))
            if len(self.empty_room_learning_samples) >= self.empty_room_learning_target_samples:
                self.empty_room_baseline = save_empty_room_baseline(
                    self.empty_room_baseline_path,
                    device_id=self.device_id,
                    config=self.config,
                    distances_m=self.distances_m,
                    samples=self.empty_room_learning_samples,
                )
                self.empty_room_learning_samples = []
                self.empty_room_learning_target_samples = 0
                self.empty_room_learning_starts_at = 0.0
                self.empty_room_baseline_status = "learned"
        baseline_delta = self._empty_room_delta(amplitude)
        baseline_delta_score = float(baseline_delta.get("baseline_delta_score", 0.0))
        baseline_delta_max = float(baseline_delta.get("baseline_delta_max", 0.0))
        baseline_changed_point_count = int(baseline_delta.get("baseline_changed_point_count", 0))
        baseline_delta_peak_index = int(baseline_delta.get("baseline_delta_peak_index", 0))
        if self.baseline is None:
            self.baseline = amplitude.astype(float)

        delta = np.maximum(amplitude - self.baseline, 0.0)
        self.frame_count += 1
        if self.frame_count <= 50:
            self.noise_floor = float(max(1.0, np.percentile(delta, 95)))
            threshold = self.noise_floor * 6.0
        else:
            threshold = self.noise_floor * self.config.raw_presence_threshold_factor

        active_mask = delta > threshold
        motion_energy = float(delta.sum())
        peak_idx = int(np.argmax(delta))
        peak_delta = float(delta[peak_idx])
        active_indices = np.flatnonzero(active_mask)
        raw_presence = bool(
            active_indices.size > 0
            and motion_energy >= self.config.raw_presence_min_energy
        )
        active_idx = int(active_indices[0]) if raw_presence else peak_idx
        processor_presence = bool(presence_result.presence_detected)
        processor_distance_m = float(presence_result.presence_distance)
        wall_mount = self.config.mounting_mode == "wall"
        if wall_mount:
            floor_presence_min_m = self.config.fall_floor_min_m
            presence_max_m = self.config.presence_max_m
        else:
            floor_presence_min_m = max(
                self.config.fall_floor_min_m,
                self.config.ceiling_height_m * 0.82,
            )
            presence_max_m = min(
                self.config.presence_max_m,
                self.config.ceiling_height_m - 0.25,
                floor_presence_min_m - 0.05,
            )
        person_range_presence = bool(
            processor_presence
            and 0.0 < processor_distance_m <= presence_max_m
        )
        inter_score = float(presence_result.inter_presence_score)
        intra_score = float(presence_result.intra_presence_score)
        quiet_empty_candidate = bool(
            wall_mount
            and not raw_presence
            and inter_score < 2.6
            and intra_score < 1.45
            and motion_energy < 140.0
            and peak_delta < threshold
        )
        has_empty_room_baseline = baseline_delta.get("empty_room_baseline_status") in {"loaded", "learned"}
        baseline_assisted_candidate = bool(
            has_empty_room_baseline
            and not quiet_empty_candidate
            and baseline_changed_point_count >= (2 if self.config.mounting_mode == "wall" else 1)
            and (
                baseline_delta_score >= self.config.baseline_presence_delta_score
                or (
                    baseline_delta_max >= self.config.baseline_presence_delta_max
                    and baseline_delta_score >= self.config.baseline_presence_delta_score * 0.75
                )
            )
        )
        if baseline_assisted_candidate:
            self.baseline_presence_candidates.append(now)
        self.baseline_presence_candidates = [
            sample
            for sample in self.baseline_presence_candidates
            if now - sample <= self.config.baseline_presence_confirm_s
        ]
        baseline_assisted_confirmed = False
        baseline_assisted_would_confirm = bool(
            baseline_assisted_candidate
            and len(self.baseline_presence_candidates) >= self.config.baseline_presence_confirm_count
        )
        if baseline_assisted_would_confirm:
            baseline_assisted_confirmed = True
            self.baseline_presence_until = now + self.config.baseline_presence_hold_s
        still_presence_candidate = bool(
            person_range_presence
            and inter_score >= self.config.still_presence_inter_score
        )
        if still_presence_candidate:
            self.still_presence_candidates.append(now)
        self.still_presence_candidates = [
            sample
            for sample in self.still_presence_candidates
            if now - sample <= self.config.still_presence_confirm_s
        ]
        still_presence_confirmed = bool(
            still_presence_candidate
            and len(self.still_presence_candidates) >= self.config.still_presence_confirm_count
        )
        if still_presence_confirmed:
            self.still_presence_until = now + self.config.still_presence_hold_s
        if raw_presence:
            self.raw_presence_since = self.raw_presence_since or now
        else:
            self.raw_presence_since = None

        strong_presence = (
            raw_presence
            and (
                motion_energy >= max(self.noise_floor * 100.0, self.config.raw_presence_min_energy * 2.5)
                or peak_delta >= threshold * 3.0
            )
        )
        raw_confirmed_presence = bool(
            raw_presence
            and (
                strong_presence
                or (
                    self.raw_presence_since is not None
                    and now - self.raw_presence_since >= self.config.raw_presence_confirm_s
                )
            )
        )
        raw_distance_m = float(self.distances_m[active_idx])
        person_anchor_candidate = bool(
            raw_confirmed_presence
            and 0.35 < raw_distance_m <= presence_max_m
            and (
                strong_presence
                or (
                    motion_energy >= self.config.raw_presence_min_energy * 2.0
                    and peak_delta >= threshold * 2.0
                )
            )
        )
        if person_anchor_candidate:
            self.person_anchor_candidates.append(now)
        self.person_anchor_candidates = [
            sample
            for sample in self.person_anchor_candidates
            if now - sample <= self.config.person_anchor_confirm_s
        ]
        person_anchor_confirmed = bool(
            person_anchor_candidate
            and len(self.person_anchor_candidates) >= self.config.person_anchor_confirm_count
        )
        if raw_confirmed_presence:
            if person_anchor_confirmed:
                self.person_anchor_until = now + self.config.stationary_presence_grace_s

        anchored_processor_presence = bool(
            person_range_presence
            and now <= self.person_anchor_until
        )
        if anchored_processor_presence:
            self.stationary_presence_until = now + self.config.raw_presence_hold_s

        if quiet_empty_candidate:
            self.quiet_empty_since = self.quiet_empty_since or now
        else:
            self.quiet_empty_since = None
        quiet_empty_confirmed = bool(
            self.quiet_empty_since is not None
            and now - self.quiet_empty_since >= 3.5
        )
        if quiet_empty_confirmed:
            self.presence_until = min(self.presence_until, now)
            self.stationary_presence_until = min(self.stationary_presence_until, now)
            self.person_anchor_until = min(self.person_anchor_until, now)
            self.baseline_presence_until = min(self.baseline_presence_until, now)
            self.still_presence_until = min(self.still_presence_until, now)
            self.baseline_presence_candidates = []
            self.still_presence_candidates = []
            self.person_anchor_candidates = []
            baseline_assisted_confirmed = False
            still_presence_confirmed = False
            anchored_processor_presence = False

        confirmed_presence = bool(
            raw_confirmed_presence
            or anchored_processor_presence
            or baseline_assisted_confirmed
            or still_presence_confirmed
        )

        if confirmed_presence:
            self.presence_until = now + self.config.raw_presence_hold_s
        elif self.frame_count > 50:
            self.baseline = 0.998 * self.baseline + 0.002 * amplitude
            quiet_level = float(np.percentile(delta, 80))
            self.noise_floor = 0.995 * self.noise_floor + 0.005 * max(1.0, quiet_level)
        presence = now <= max(
            self.presence_until,
            self.stationary_presence_until,
            self.baseline_presence_until,
            self.still_presence_until,
        )
        baseline_distance_m = float(self.distances_m[
            max(0, min(len(self.distances_m) - 1, baseline_delta_peak_index))
        ])
        distance_m = (
            float(self.distances_m[active_idx])
            if raw_presence
            else baseline_distance_m
            if baseline_assisted_candidate or now <= self.baseline_presence_until
            else processor_distance_m
            if person_range_presence
            else self.last_presence_distance_m
        )

        zone_edges = np.linspace(float(self.distances_m[0]), float(self.distances_m[-1]), 7)
        zone_energies = []
        zone_active = []
        for zone_idx in range(6):
            zone_mask = (self.distances_m >= zone_edges[zone_idx]) & (
                self.distances_m <= zone_edges[zone_idx + 1]
            )
            zone_energies.append(float(delta[zone_mask].sum()))
            zone_active.append(bool(active_mask[zone_mask].any()))
        active_zone_idx = next(
            (idx for idx, is_active in enumerate(zone_active) if is_active),
            -1,
        )
        zone = f"zone_{active_zone_idx + 1}" if active_zone_idx >= 0 else self.last_presence_zone
        empty_room_z = self._empty_room_z(amplitude)
        center_weights = np.maximum(delta - threshold * 0.45, 0.0)
        if empty_room_z is not None:
            center_weights = np.maximum(center_weights, np.maximum(empty_room_z - 3.0, 0.0) * max(threshold, 1.0))
        if float(center_weights.sum()) > 0.0:
            center_of_energy_m = float(np.sum(self.distances_m * center_weights) / np.sum(center_weights))
        else:
            center_of_energy_m = distance_m if presence else 0.0
        center_step_m = 0.0
        center_velocity_mps = 0.0
        if (
            presence
            and self.last_center_of_energy_m is not None
            and self.last_time_s is not None
            and center_of_energy_m > 0.0
        ):
            dt = max(now - self.last_time_s, 0.001)
            center_step_m = abs(center_of_energy_m - self.last_center_of_energy_m)
            center_velocity_mps = (center_of_energy_m - self.last_center_of_energy_m) / dt
        if presence and center_of_energy_m > 0.0:
            self.last_center_of_energy_m = center_of_energy_m
        elif not presence:
            self.last_center_of_energy_m = None
        if raw_presence:
            self.last_presence_distance_m = distance_m
            self.last_presence_zone = zone
        elif not presence:
            distance_m = 0.0
            zone = "none"
            self.last_presence_distance_m = 0.0
            self.last_presence_zone = "none"
            center_of_energy_m = 0.0

        velocity_mps = 0.0
        distance_step_m = 0.0
        if raw_presence and self.last_distance_m is not None and self.last_time_s is not None:
            dt = max(now - self.last_time_s, 0.001)
            distance_step_m = abs(distance_m - self.last_distance_m)
            velocity_mps = (distance_m - self.last_distance_m) / dt
        if raw_presence:
            self.last_distance_m = distance_m
            self.last_time_s = now
        elif not presence:
            self.last_distance_m = None
            self.last_time_s = None

        motion_velocity_min = (
            max(0.45, self.config.motion_velocity_min_mps * 2.0)
            if wall_mount
            else self.config.motion_velocity_min_mps
        )
        strong_motion_energy = (
            max(self.noise_floor * 180.0, self.config.raw_presence_min_energy * 4.0)
            if wall_mount
            else max(self.noise_floor * 80.0, self.config.raw_presence_min_energy * 1.8)
        )
        strong_velocity_signal = (
            (abs(velocity_mps) >= motion_velocity_min or abs(center_velocity_mps) >= motion_velocity_min)
            and max(distance_step_m, center_step_m) >= (0.18 if wall_mount else 0.03)
            and motion_energy >= self.config.raw_presence_min_energy * (1.8 if wall_mount else 1.4)
            and peak_delta >= threshold * (1.6 if wall_mount else 1.4)
        )
        strong_intra_signal = (
            intra_score >= self.config.intra_detection_threshold * (1.55 if wall_mount else 1.0)
            and motion_energy >= (
                self.config.raw_presence_min_energy * 1.7 if wall_mount else strong_motion_energy
            )
            and peak_delta >= threshold * (2.0 if wall_mount else 1.4)
        )
        energy_only_motion = (
            not wall_mount
            and motion_energy >= strong_motion_energy
        )
        motion_presence_basis = bool(
            presence
            or raw_presence
            or baseline_assisted_candidate
            or now <= self.baseline_presence_until
            or person_range_presence
        )
        raw_motion_active = motion_presence_basis and (
            strong_velocity_signal or strong_intra_signal or energy_only_motion
        )
        if strong_velocity_signal:
            motion_active_reason = "velocity"
        elif strong_intra_signal:
            motion_active_reason = "intra_energy"
        elif energy_only_motion:
            motion_active_reason = "energy"
        else:
            motion_active_reason = "none"

        self.baseline_delta_history.append((now, baseline_delta_score))
        baseline_motion_window_s = 4.0 if wall_mount else 2.5
        self.baseline_delta_history = [
            sample for sample in self.baseline_delta_history if now - sample[0] <= baseline_motion_window_s
        ]
        baseline_delta_span = (
            max(sample[1] for sample in self.baseline_delta_history)
            - min(sample[1] for sample in self.baseline_delta_history)
            if len(self.baseline_delta_history) >= 2
            else 0.0
        )
        profile_motion_score = 0.0
        profile_motion_max = 0.0
        profile_motion_changed_points = 0
        profile_motion_peak_shift = 0
        if empty_room_z is not None and self.last_empty_room_z is not None:
            z_delta = np.abs(empty_room_z - self.last_empty_room_z)
            profile_motion_score = float(np.mean(np.sort(z_delta)[-min(5, len(z_delta)) :]))
            profile_motion_max = float(np.max(z_delta))
            profile_motion_changed_points = int(np.sum(z_delta >= (2.2 if wall_mount else 1.6)))
            profile_motion_peak_shift = abs(int(np.argmax(empty_room_z)) - int(np.argmax(self.last_empty_room_z)))
        if empty_room_z is not None:
            self.last_empty_room_z = empty_room_z

        peak_ratio = peak_delta / max(float(threshold), 1.0)
        if wall_mount:
            motion_range_m = max(float(distance_m), float(center_of_energy_m))
            if motion_range_m >= 4.0:
                zone_motion_factor = 0.65
            elif motion_range_m >= 3.0:
                zone_motion_factor = 0.78
            elif motion_range_m >= 2.4:
                zone_motion_factor = 0.90
            else:
                zone_motion_factor = 1.0
            profile_gate_threshold = 10.0 * zone_motion_factor
            center_gate_threshold = 0.75 * zone_motion_factor
            baseline_span_gate_threshold = 16.0 * zone_motion_factor
            distance_step_gate_threshold = 0.35 * zone_motion_factor
            temporal_gate = bool(
                profile_motion_score >= profile_gate_threshold
                or profile_motion_peak_shift >= 4
                or distance_step_m >= distance_step_gate_threshold
                or center_step_m >= center_gate_threshold
                or baseline_delta_span >= baseline_span_gate_threshold
            )
            intra_component = (
                max(0.0, min(10.0, (intra_score - 1.8) / 2.8 * 10.0))
                if temporal_gate
                else 0.0
            )
            energy_component = (
                max(0.0, min(8.0, (motion_energy - 170.0) / 450.0 * 8.0))
                if temporal_gate
                else 0.0
            )
            peak_component = (
                max(0.0, min(8.0, (peak_ratio - 2.2) / 6.0 * 8.0))
                if temporal_gate
                else 0.0
            )
            step_component = max(
                0.0,
                min(
                    24.0,
                    (max(distance_step_m, center_step_m) - 0.30 * zone_motion_factor)
                    / max(0.22, 0.50 * zone_motion_factor)
                    * 24.0,
                ),
            )
            profile_component = max(
                0.0,
                min(
                    42.0,
                    ((profile_motion_score - profile_gate_threshold) / max(8.0, 18.0 * zone_motion_factor) * 30.0)
                    + (min(max(profile_motion_peak_shift - 1, 0), 6) / 6.0 * 12.0),
                ),
            )
            phase_component = max(
                0.0,
                min(
                    6.0,
                    ((phase_activity_score - 2.20) / 1.2 * 4.0)
                    + (min(max(phase_activity_changed_points - 7, 0), 4) / 4.0 * 2.0),
                ),
            )
            activity_baseline_component = (
                max(
                    0.0,
                    min(
                        12.0,
                        (baseline_delta_span - 14.0 * zone_motion_factor)
                        / max(10.0, 24.0 * zone_motion_factor)
                        * 12.0,
                    ),
                )
                if temporal_gate
                else 0.0
            )
            dynamic_signal = max(
                intra_component,
                energy_component,
                peak_component,
                step_component,
                profile_component,
                phase_component,
                activity_baseline_component,
            )
            raw_activity_score = (
                min(intra_component + energy_component + peak_component, 14.0)
                + step_component
                + profile_component
                + phase_component
                + activity_baseline_component
            )
        else:
            intra_component = max(0.0, min(35.0, (intra_score - 1.0) / 1.8 * 35.0))
            energy_component = max(0.0, min(25.0, (motion_energy - 100.0) / 400.0 * 25.0))
            peak_component = max(0.0, min(20.0, (peak_ratio - 1.2) / 4.0 * 20.0))
            step_component = max(0.0, min(15.0, (distance_step_m - 0.02) / 0.18 * 15.0))
            profile_component = max(0.0, min(25.0, (profile_motion_score - 1.4) / 5.0 * 25.0))
            phase_component = max(0.0, min(10.0, (phase_activity_score - 0.35) / 1.2 * 10.0))
            activity_baseline_component = max(0.0, min(15.0, (baseline_delta_span - 1.5) / 12.0 * 15.0))
            raw_activity_score = (
                intra_component
                + energy_component
                + peak_component
                + step_component
                + profile_component
                + phase_component
                + activity_baseline_component
            )
        activity_alpha = 0.42 if raw_activity_score > self.activity_score_smoothed else (0.48 if wall_mount else 0.18)
        self.activity_score_smoothed = (
            (1.0 - activity_alpha) * self.activity_score_smoothed
            + activity_alpha * min(100.0, raw_activity_score)
        )

        activity_on_threshold = 48.0 if wall_mount else 38.0
        activity_off_threshold = 22.0 if wall_mount else 24.0
        wall_evidence_points = 0
        if wall_mount:
            center_candidate_threshold = 0.75 * zone_motion_factor
            center_strong_threshold = 1.20 * zone_motion_factor
            center_velocity_candidate_threshold = 1.10 * zone_motion_factor
            center_velocity_strong_threshold = 1.75 * zone_motion_factor
            distance_candidate_threshold = 0.35 * zone_motion_factor
            distance_strong_threshold = 0.70 * zone_motion_factor
            profile_candidate_threshold = 14.0 * zone_motion_factor
            profile_strong_threshold = 28.0 * zone_motion_factor
            baseline_span_candidate_threshold = 16.0 * zone_motion_factor
            wall_evidence_points += int(raw_motion_active)
            wall_evidence_points += int(
                abs(center_velocity_mps) >= center_velocity_candidate_threshold
                or center_step_m >= center_candidate_threshold
            )
            wall_evidence_points += int(distance_step_m >= distance_candidate_threshold)
            wall_evidence_points += int(
                profile_motion_score >= profile_candidate_threshold
                or profile_motion_peak_shift >= 4
            )
            wall_evidence_points += int(baseline_delta_span >= baseline_span_candidate_threshold)
        strong_active_evidence = bool(
            (raw_motion_active and not wall_mount)
            or (
                wall_mount
                and (
                    abs(center_velocity_mps) >= center_velocity_strong_threshold
                    or center_step_m >= center_strong_threshold
                    or distance_step_m >= distance_strong_threshold
                    or profile_motion_score >= profile_strong_threshold
                    or wall_evidence_points >= 3
                )
            )
        )
        candidate_active_evidence = bool(
            strong_active_evidence
            or (
                not wall_mount
                and self.activity_score_smoothed >= activity_on_threshold
            )
            or (
                wall_mount
                and self.activity_score_smoothed >= activity_on_threshold
                and wall_evidence_points >= 2
            )
            or (
                wall_mount
                and dynamic_signal >= 38.0 * zone_motion_factor
                and wall_evidence_points >= 2
            )
        )
        self.active_candidate_samples.append((now, bool(candidate_active_evidence)))
        active_candidate_window_s = 2.2 if wall_mount else 1.2
        self.active_candidate_samples = [
            sample for sample in self.active_candidate_samples if now - sample[0] <= active_candidate_window_s
        ]
        active_candidate_ratio = (
            sum(1 for _, active in self.active_candidate_samples if active) / len(self.active_candidate_samples)
            if self.active_candidate_samples
            else 0.0
        )
        active_evidence = bool(
            strong_active_evidence
            or (
                candidate_active_evidence
                and active_candidate_ratio >= ((0.30 if zone_motion_factor < 0.8 else 0.40) if wall_mount else 0.0)
                and len(self.active_candidate_samples) >= ((2 if zone_motion_factor < 0.8 else 3) if wall_mount else 1)
                and (not wall_mount or wall_evidence_points >= 2)
            )
        )
        if wall_mount and active_evidence and motion_active_reason == "none":
            if phase_component >= 8.0:
                motion_active_reason = "phase"
            elif center_step_m >= center_candidate_threshold:
                motion_active_reason = "center_shift"
            elif profile_component >= 12.0:
                motion_active_reason = "profile"
            else:
                motion_active_reason = "activity_score"
        elif not active_evidence:
            motion_active_reason = "none"
        if motion_presence_basis and active_evidence:
            active_hold_s = min(self.config.motion_active_hold_s, 1.2) if wall_mount else self.config.motion_active_hold_s
            self.motion_active_until = now + active_hold_s
            if motion_active_reason == "none":
                motion_active_reason = "activity_score"
        elif self.activity_score_smoothed <= activity_off_threshold:
            self.motion_active_until = min(self.motion_active_until, now)
        elif wall_mount and still_presence_confirmed and now <= self.still_presence_until:
            self.motion_active_until = min(self.motion_active_until, now)
        self.motion_activity_samples.append((now, bool(raw_motion_active)))
        motion_window_s = 4.0 if wall_mount else 2.5
        self.motion_activity_samples = [
            sample for sample in self.motion_activity_samples if now - sample[0] <= motion_window_s
        ]
        motion_activity_ratio = (
            sum(1 for _, active in self.motion_activity_samples if active) / len(self.motion_activity_samples)
            if self.motion_activity_samples
            else 0.0
        )
        motion_window_active = bool(
            presence
            and (
                now <= self.motion_active_until
                or motion_activity_ratio >= (0.20 if wall_mount else 0.12)
            )
        )
        next_motion_state = "active" if motion_window_active else "still" if presence else "clear"
        if next_motion_state != self.motion_fsm_state:
            self.motion_fsm_state = next_motion_state
            self.motion_fsm_changed_at = now
        motion_state = self.motion_fsm_state

        floor_energy = zone_energies[-1]
        fall_confirm_min_m = floor_presence_min_m
        wall_impact = (
            wall_mount
            and raw_presence
            and abs(velocity_mps) >= max(1.2, self.config.fall_velocity_min_mps * 2.2)
            and distance_step_m >= 0.25
            and motion_energy >= max(self.noise_floor * 220.0, self.config.raw_presence_min_energy * 5.0)
            and peak_delta >= threshold * 3.0
        )
        recent_floor_impact = (
            raw_presence
            and distance_m >= fall_confirm_min_m
            and velocity_mps >= self.config.fall_velocity_min_mps
            and motion_energy >= max(self.noise_floor * 100.0, self.config.raw_presence_min_energy * 2.0)
            and peak_delta >= threshold * 2.0
        )
        if recent_floor_impact or wall_impact:
            self.fall_impact_until = now + self.config.fall_active_window_s

        had_recent_floor_impact = now <= self.fall_impact_until
        raw_floor_presence = raw_presence and (
            distance_m >= fall_confirm_min_m
            if not wall_mount
            else person_range_presence
        )
        if raw_floor_presence:
            self.fall_distance_history.append((now, distance_m))
            self.fall_distance_history = [
                sample for sample in self.fall_distance_history if now - sample[0] <= 5.0
            ]
        else:
            self.fall_distance_history = []
        fall_distance_span = (
            max(sample[1] for sample in self.fall_distance_history)
            - min(sample[1] for sample in self.fall_distance_history)
            if self.fall_distance_history
            else 0.0
        )
        fall_stable_duration = (
            self.fall_distance_history[-1][0] - self.fall_distance_history[0][0]
            if len(self.fall_distance_history) >= 2
            else 0.0
        )
        stable_floor_presence = (
            raw_floor_presence
            and fall_stable_duration >= (2.0 if wall_mount else 3.0)
            and fall_distance_span <= (0.45 if wall_mount else 0.25)
        )
        fall_score = 0
        if wall_impact:
            fall_score += 35
        if had_recent_floor_impact and raw_floor_presence:
            fall_score += 35
        if raw_floor_presence and had_recent_floor_impact:
            fall_score += 30
        if not wall_mount and raw_floor_presence and floor_energy >= max(zone_energies[:-1] or [0.0]) * 1.5:
            fall_score += 20
        if wall_mount and raw_floor_presence and motion_state == "still" and had_recent_floor_impact:
            fall_score += 20
        if raw_floor_presence and motion_state == "still" and had_recent_floor_impact:
            fall_score += 15
        fall_score = int(min(100, fall_score))

        fall_candidate_trigger = fall_score >= 65 and (wall_impact if wall_mount else True)
        if fall_candidate_trigger:
            self.fall_candidate_since = self.fall_candidate_since or now
            self.fall_candidate_until = now + 0.8
        elif now > self.fall_candidate_until and (not wall_mount or not had_recent_floor_impact):
            self.fall_candidate_since = None

        if (
            self.fall_candidate_since is not None
            and now >= self.fall_detection_enabled_at
            and now - self.fall_candidate_since >= (2.6 if wall_mount else 5.0)
            and stable_floor_presence
            and (motion_state == "still" or (wall_mount and fall_score >= 85))
        ):
            self.fall_detected_until = now + 20.0

        fall_detected = now <= self.fall_detected_until
        inter_component = max(0.0, min(45.0, (inter_score - 0.85) / 0.9 * 45.0))
        presence_baseline_component = max(0.0, min(25.0, (baseline_delta_score - 2.5) / 2.0 * 25.0))
        raw_component = max(0.0, min(20.0, peak_delta / max(threshold * 2.0, 1.0) * 20.0))
        range_component = 10.0 if person_range_presence else 0.0
        presence_confidence = int(
            round(
                    max(
                        100.0 if raw_confirmed_presence or anchored_processor_presence else 0.0,
                    min(100.0, inter_component + presence_baseline_component + raw_component + range_component),
                )
            )
        )
        if presence:
            if raw_presence:
                presence_reason = "raw_confirmed"
            elif now <= self.still_presence_until:
                presence_reason = "still_inter_hold"
            else:
                presence_reason = "confirmed_hold"
        elif presence_confidence >= 70:
            presence_reason = "candidate_strong"
        elif inter_score >= 1.2 and baseline_delta_score >= 3.0:
            presence_reason = "inter_baseline"
        elif inter_score >= 1.2:
            presence_reason = "inter_score"
        elif baseline_delta_score >= 3.0:
            presence_reason = "baseline_delta"
        elif raw_presence:
            presence_reason = "raw_unconfirmed"
        else:
            presence_reason = "clear"

        output = {
            "presence": presence,
            "presence_confidence": presence_confidence,
            "presence_reason": presence_reason,
            "raw_presence": raw_presence,
            "presence_confirmed": confirmed_presence,
            "raw_presence_confirmed": raw_confirmed_presence,
            "person_anchor_candidate": person_anchor_candidate,
            "person_anchor_confirmed": person_anchor_confirmed,
            "person_anchor_candidate_count": len(self.person_anchor_candidates),
            "anchored_processor_presence": anchored_processor_presence,
            "person_anchor_active": now <= self.person_anchor_until,
            "baseline_assisted_candidate": baseline_assisted_candidate,
            "baseline_assisted_confirmed": baseline_assisted_confirmed,
            "baseline_assisted_would_confirm": baseline_assisted_would_confirm,
            "baseline_assisted_active": now <= self.baseline_presence_until,
            "quiet_empty_candidate": quiet_empty_candidate,
            "quiet_empty_confirmed": quiet_empty_confirmed,
            "baseline_assisted_candidate_count": len(self.baseline_presence_candidates),
            "still_presence_candidate": still_presence_candidate,
            "still_presence_confirmed": still_presence_confirmed,
            "still_presence_active": now <= self.still_presence_until,
            "still_presence_candidate_count": len(self.still_presence_candidates),
            "presence_held": presence and not raw_presence,
            "motion_state": motion_state,
            "motion_raw_active": raw_motion_active,
            "motion_presence_basis": motion_presence_basis,
            "motion_active_reason": motion_active_reason,
            "activity_score": round(float(self.activity_score_smoothed), 1),
            "activity_score_raw": round(float(raw_activity_score), 1),
            "activity_threshold_on": round(float(activity_on_threshold), 1),
            "activity_threshold_off": round(float(activity_off_threshold), 1),
            "activity_intra_component": round(float(intra_component), 1),
            "activity_energy_component": round(float(energy_component), 1),
            "activity_peak_component": round(float(peak_component), 1),
            "activity_step_component": round(float(step_component), 1),
            "activity_profile_component": round(float(profile_component), 1),
            "activity_phase_component": round(float(phase_component), 1),
            "activity_baseline_component": round(float(activity_baseline_component), 1),
            "baseline_delta_span": round(float(baseline_delta_span), 3),
            "profile_motion_score": round(float(profile_motion_score), 3),
            "profile_motion_max": round(float(profile_motion_max), 3),
            "profile_motion_changed_points": profile_motion_changed_points,
            "profile_motion_peak_shift": profile_motion_peak_shift,
            "phase_activity_score": round(float(phase_activity_score), 3),
            "phase_activity_max": round(float(phase_activity_max), 3),
            "phase_activity_changed_points": phase_activity_changed_points,
            "center_of_energy_m": round(float(center_of_energy_m), 3),
            "center_step_m": round(float(center_step_m), 3),
            "center_velocity_mps": round(float(center_velocity_mps), 3),
            "motion_fsm_state": motion_state,
            "motion_fsm_age_s": round(float(now - self.motion_fsm_changed_at), 2),
            "motion_activity_ratio": round(float(motion_activity_ratio), 3),
            "active_candidate_ratio": round(float(active_candidate_ratio), 3),
            "active_candidate_window_s": round(float(active_candidate_window_s), 1),
            "strong_active_evidence": strong_active_evidence,
            "candidate_active_evidence": candidate_active_evidence,
            "wall_motion_evidence_points": wall_evidence_points,
            "wall_motion_range_m": round(float(motion_range_m if wall_mount else 0.0), 3),
            "wall_motion_factor": round(float(zone_motion_factor if wall_mount else 1.0), 3),
            "motion_activity_window_s": round(float(motion_window_s), 1),
            "motion_energy_threshold": round(float(strong_motion_energy), 1),
            "motion_velocity_threshold_mps": round(float(motion_velocity_min), 3),
            "motion_hold_remaining_s": round(max(0.0, self.motion_active_until - now), 2),
            "fall_detected": fall_detected,
            "distance_m": round(distance_m, 3),
            "zone": zone,
            "velocity_mps": round(float(velocity_mps), 3),
            "distance_step_m": round(float(distance_step_m), 3),
            "motion_energy": round(motion_energy, 1),
            "raw_peak_delta": round(peak_delta, 1),
            "raw_threshold": round(float(threshold), 1),
            "raw_noise_floor": round(float(self.noise_floor), 1),
            "processor_presence": processor_presence,
            "processor_distance_m": round(processor_distance_m, 3),
            "processor_intra_score": round(intra_score, 3),
            "processor_inter_score": round(inter_score, 3),
            "person_range_presence": person_range_presence,
            "presence_max_m": round(float(presence_max_m), 3),
            "empty_room_learning_delay_s": (
                round(max(0.0, self.empty_room_learning_starts_at - now), 1)
                if self.empty_room_learning_starts_at > 0.0
                else 0.0
            ),
            "empty_room_learning_progress": (
                round(len(self.empty_room_learning_samples) / self.empty_room_learning_target_samples, 3)
                if self.empty_room_learning_target_samples > 0
                else 0.0
            ),
            **baseline_delta,
            "zone_energies": [round(energy, 1) for energy in zone_energies],
            "fall_score": fall_score,
            "wall_fall_impact": wall_impact,
            "wall_fall_mode": wall_mount,
            "fall_distance_span": round(float(fall_distance_span), 3),
            "fall_stable_duration": round(float(fall_stable_duration), 1),
            "raw_frames": self.frame_count,
            "temperature": getattr(result, "temperature", None),
        }
        return self._apply_ml_hybrid(output)


def calibrate_ceiling_height(serial_port: str, config: BridgeConfig, samples: int = 30) -> float:
    client = a121.Client.open(serial_port=serial_port)
    detector = None
    try:
        distance_config = DistanceDetectorConfig(
            start_m=0.2,
            end_m=max(config.end_m, config.ceiling_height_m + 1.0, 4.0),
            update_rate=10.0,
        )
        detector = DistanceDetector(
            client=client,
            sensor_ids=[1],
            detector_config=distance_config,
        )
        detector.calibrate_detector()
        detector.start()
        distances: list[float] = []
        for _ in range(samples):
            result = detector.get_next()[1]
            if result.distances is not None and len(result.distances) > 0:
                distance = float(result.distances[0])
                if 0.5 <= distance <= distance_config.end_m:
                    distances.append(distance)
        if not distances:
            raise RuntimeError("No stable floor distance detected")
        distances.sort()
        return distances[len(distances) // 2]
    finally:
        if detector is not None:
            try:
                detector.stop()
            except Exception:
                pass
        client.close()


def apply_ceiling_calibration(config: BridgeConfig, ceiling_height_m: float) -> BridgeConfig:
    data = asdict(config)
    data["ceiling_height_m"] = round(float(ceiling_height_m), 2)
    data["end_m"] = round(max(ceiling_height_m + 0.3, 2.0), 2)
    data["fall_floor_min_m"] = round(max(0.5, ceiling_height_m - data["fall_floor_margin_m"]), 2)
    data["presence_max_m"] = round(max(0.5, ceiling_height_m - 0.25), 2)
    data["zone_near_m"] = round(max(0.4, ceiling_height_m * 0.35), 2)
    data["zone_far_m"] = round(max(data["zone_near_m"] + 0.2, ceiling_height_m * 0.70), 2)
    data["ceiling_calibration_status"] = f"calibrated_{data['ceiling_height_m']:.2f}m"
    return BridgeConfig(**data)


def apply_wall_room_calibration(config: BridgeConfig) -> BridgeConfig:
    data = asdict(config)
    data["start_m"] = 0.60
    data["end_m"] = 6.00
    data["presence_max_m"] = 6.00
    data["fall_floor_min_m"] = 4.80
    data["zone_near_m"] = 2.00
    data["zone_far_m"] = 4.00
    data["frame_rate"] = 10.0
    data["hwaas"] = max(int(data.get("hwaas", 32)), 96)
    data["sweeps_per_frame"] = max(int(data.get("sweeps_per_frame", 16)), 24)
    data["ceiling_calibration_status"] = "wall_room_range_6.00m"
    return BridgeConfig(**data)


def publish_discovery(client: mqtt.Client, device_id: str, name: str, topics: dict[str, str]) -> None:
    device = {
        "identifiers": [device_id],
        "name": name,
        "manufacturer": "Acconeer",
        "model": "XM126 on XB122",
    }

    def cfg(domain: str, key: str, payload: dict[str, Any]) -> None:
        topic = f"homeassistant/{domain}/{device_id}/{key}/config"
        base = {
            "name": payload.pop("name", key.replace("_", " ").title()),
            "unique_id": f"{device_id}_{key}",
            "object_id": f"{device_id}_{key}",
            "device": device,
            "availability_topic": topics["availability"],
            "payload_available": "online",
            "payload_not_available": "offline",
        }
        base.update(payload)
        client.publish(topic, json.dumps(base), retain=True)

    for domain, key in OBSOLETE_DISCOVERY_CONFIGS:
        client.publish(f"homeassistant/{domain}/{device_id}/{key}/config", "", retain=True)

    cfg(
        "binary_sensor",
        "presence",
        {
            "name": "Präsenz",
            "state_topic": topics["state"],
            "value_template": "{{ value_json.presence }}",
            "payload_on": True,
            "payload_off": False,
            "device_class": "presence",
        },
    )
    cfg(
        "binary_sensor",
        "fall_detected",
        {
            "name": "Sturz",
            "state_topic": topics["state"],
            "value_template": "{{ value_json.fall_detected }}",
            "payload_on": True,
            "payload_off": False,
        },
    )
    cfg(
        "sensor",
        "motion_state",
        {
            "name": "Bewegung",
            "state_topic": topics["state"],
            "value_template": "{{ value_json.motion_state }}",
        },
    )
    cfg(
        "sensor",
        "ceiling_calibration_status",
        {
            "name": "Decken-Kalibrierstatus",
            "state_topic": topics["settings"],
            "value_template": "{{ value_json.ceiling_calibration_status }}",
        },
    )

    cfg(
        "select",
        "mode_select",
        {
            "name": "Mode",
            "state_topic": topics["settings"],
            "value_template": "{{ value_json.mode }}",
            "command_topic": f"{topics['command_prefix']}/mode",
            "options": ["schlafmodus", "sturzmodus"],
        },
    )
    cfg(
        "select",
        "mounting_mode_select",
        {
            "name": "Mounting Mode",
            "state_topic": topics["settings"],
            "value_template": "{{ value_json.mounting_mode }}",
            "command_topic": f"{topics['command_prefix']}/mounting_mode",
            "options": ["wall", "ceiling"],
        },
    )
    cfg(
        "select",
        "sensitivity_select",
        {
            "name": "Sensitivity",
            "state_topic": topics["settings"],
            "value_template": "{{ value_json.sensitivity }}",
            "command_topic": f"{topics['command_prefix']}/sensitivity",
            "options": ["low", "normal", "high", "custom"],
        },
    )
    cfg(
        "button",
        "calibrate",
        {
            "name": "Kalibrierung starten",
            "command_topic": f"{topics['command_prefix']}/calibrate",
            "payload_press": "start",
        },
    )
    cfg(
        "button",
        "learn_empty_room",
        {
            "name": "Leeren Raum lernen",
            "command_topic": f"{topics['command_prefix']}/learn_empty_room",
            "payload_press": "start",
        },
    )


def parse_value(key: str, raw: str) -> Any:
    if key in {"mode", "mounting_mode", "sensitivity"}:
        return raw.strip()
    if key in {
        "sweeps_per_frame",
        "hwaas",
        "inter_frame_presence_timeout",
        "person_anchor_confirm_count",
    }:
        return int(float(raw))
    return float(raw)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge Acconeer XM126 presence data to MQTT.")
    parser.add_argument("--port", default="COM4")
    parser.add_argument("--mqtt-host", default="127.0.0.1")
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--device-id", default=DEFAULT_DEVICE_ID)
    parser.add_argument("--name", default="XM126 Presence Radar")
    parser.add_argument("--config", default="data/xm126_bridge_config.json")
    parser.add_argument("--empty-room-baseline", default=DEFAULT_EMPTY_ROOM_BASELINE_PATH)
    parser.add_argument("--ml-model", default=DEFAULT_ML_MODEL_PATH)
    parser.add_argument("--publish-interval", type=float, default=0.25)
    args = parser.parse_args()

    config_path = Path(args.config)
    empty_room_baseline_path = Path(args.empty_room_baseline)
    ml_model_path = Path(args.ml_model)
    config = load_config(config_path)
    restart_requested = True
    calibration_requested = False
    learn_empty_room_after_calibration = False
    empty_room_learning_requested = False
    stop_requested = False
    controller: Any | None = None
    smart_presence_processor: SmartPresenceProcessor | None = None
    client_a121: a121.Client | None = None
    started_at = time.monotonic()
    last_presence_at = started_at
    last_publish_at = 0.0
    active_motion_at = 0.0
    motion_active_until = 0.0
    fall_impact_at: float | None = None
    floor_presence_since: float | None = None
    last_distance_m: float | None = None
    last_distance_at: float | None = None
    clear_since: float | None = None
    fall_detected = False

    state_topic = DEFAULT_STATE_TOPIC.format(device_id=args.device_id)
    debug_topic = DEFAULT_DEBUG_TOPIC.format(device_id=args.device_id)
    availability_topic = DEFAULT_AVAILABILITY_TOPIC.format(device_id=args.device_id)
    settings_topic = DEFAULT_SETTINGS_TOPIC.format(device_id=args.device_id)
    set_topic = DEFAULT_SET_TOPIC.format(device_id=args.device_id)
    command_prefix = DEFAULT_COMMAND_PREFIX.format(device_id=args.device_id)
    topics = {
        "state": state_topic,
        "debug": debug_topic,
        "availability": availability_topic,
        "settings": settings_topic,
        "set": set_topic,
        "command_prefix": command_prefix,
    }

    mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"{args.device_id}-bridge")

    def on_connect(client: mqtt.Client, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        client.subscribe(set_topic)
        client.publish(availability_topic, "online", retain=True)
        publish_discovery(client, args.device_id, args.name, topics)
        client.publish(settings_topic, json.dumps(public_settings(config)), retain=True)

    def on_message(client: mqtt.Client, userdata: Any, message: Any) -> None:
        nonlocal config, restart_requested, calibration_requested, empty_room_learning_requested
        key = str(message.topic).rsplit("/", 1)[-1]
        raw = message.payload.decode("utf-8", errors="replace")
        if key == "calibrate":
            calibration_requested = True
            restart_requested = True
            return
        if key == "learn_empty_room":
            empty_room_learning_requested = True
            return
        if not hasattr(config, key):
            return
        try:
            value = parse_value(key, raw)
            if key == "mode" and value not in MODE_PRESETS:
                return
            if key == "mounting_mode" and value not in {"ceiling", "wall"}:
                return
            if key == "sensitivity" and value not in {"low", "normal", "high", "custom"}:
                return
            setattr(config, key, value)
            save_config(config_path, config)
            client.publish(settings_topic, json.dumps(public_settings(config)), retain=True)
            if key in RESTART_KEYS:
                restart_requested = True
        except Exception as exc:
            print(f"Could not apply setting {key}={raw!r}: {exc}", file=sys.stderr)

    def shutdown(signum: int, frame: Any) -> None:
        nonlocal stop_requested
        stop_requested = True

    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message
    mqtt_client.will_set(availability_topic, "offline", retain=True)
    mqtt_client.connect(args.mqtt_host, args.mqtt_port, keepalive=30)
    mqtt_client.loop_start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    try:
        while not stop_requested:
            if restart_requested:
                restart_requested = False
                if controller is not None:
                    try:
                        controller.stop()
                    except Exception:
                        pass
                    controller = None
                    smart_presence_processor = None
                if client_a121 is not None:
                    try:
                        client_a121.close()
                    except Exception:
                        pass
                    client_a121 = None

                if calibration_requested:
                    calibration_requested = False
                    learn_empty_room_after_calibration = True
                    config.ceiling_calibration_status = "waiting_to_leave"
                    save_config(config_path, config)
                    mqtt_client.publish(settings_topic, json.dumps(public_settings(config)), retain=True)
                    try:
                        time.sleep(15.0)
                        if config.mounting_mode == "wall":
                            config = apply_wall_room_calibration(config)
                            config.ceiling_calibration_status = "learning_empty_room"
                        else:
                            config.ceiling_calibration_status = "measuring_height"
                            save_config(config_path, config)
                            mqtt_client.publish(settings_topic, json.dumps(public_settings(config)), retain=True)
                            measured_height = calibrate_ceiling_height(args.port, config)
                            config = apply_ceiling_calibration(config, measured_height)
                    except Exception as exc:
                        learn_empty_room_after_calibration = False
                        config.ceiling_calibration_status = f"failed: {exc}"
                    save_config(config_path, config)
                    mqtt_client.publish(settings_topic, json.dumps(public_settings(config)), retain=True)

                client_a121 = a121.Client.open(serial_port=args.port)
                if config.mode == "schlafmodus":
                    controller = RefApp(
                        client=client_a121,
                        sensor_id=1,
                        ref_app_config=breathing_config(config),
                    )
                elif config.mode == "sturzmodus":
                    controller = RawTestController(
                        client=client_a121,
                        config=config,
                        device_id=args.device_id,
                        empty_room_baseline_path=empty_room_baseline_path,
                        ml_model_path=ml_model_path,
                    )
                else:
                    presence_config = detector_config(config)
                    controller = PresenceDetector(
                        client=client_a121,
                        sensor_id=1,
                        detector_config=presence_config,
                    )
                controller.start()
                if isinstance(controller, PresenceDetector):
                    assert controller.detector_metadata is not None
                    smart_presence_processor = SmartPresenceProcessor(
                        SmartPresenceProcessorConfig(num_zones=6),
                        presence_config,
                        controller.session_config,
                        controller.detector_metadata,
                    )
                effective = apply_presets(config)
                mqtt_client.publish(settings_topic, json.dumps(public_settings(effective)), retain=True)
                if learn_empty_room_after_calibration:
                    learn_empty_room_after_calibration = False
                    if isinstance(controller, RawTestController) and not config.ceiling_calibration_status.startswith("failed"):
                        config.ceiling_calibration_status = "learning_empty_room"
                        save_config(config_path, config)
                        mqtt_client.publish(settings_topic, json.dumps(public_settings(config)), retain=True)
                        controller.start_empty_room_learning(delay_s=0.0)

            assert controller is not None
            if empty_room_learning_requested:
                empty_room_learning_requested = False
                if isinstance(controller, RawTestController):
                    controller.start_empty_room_learning()
            result = controller.get_next()
            now = time.monotonic()
            effective_config = apply_presets(config)
            if isinstance(controller, RawTestController):
                if (
                    config.ceiling_calibration_status == "learning_empty_room"
                    and result.get("empty_room_baseline_status") == "learned"
                ):
                    config.ceiling_calibration_status = "ready"
                    save_config(config_path, config)
                    mqtt_client.publish(settings_topic, json.dumps(public_settings(config)), retain=True)
                payload = {
                    "device_id": args.device_id,
                    "presence": bool(result["presence"]),
                    "presence_confidence": int(result["presence_confidence"]),
                    "presence_reason": result["presence_reason"],
                    "motion_state": result["motion_state"],
                    "fall_detected": bool(result["fall_detected"]),
                    "distance_m": result["distance_m"],
                    "zone": result["zone"],
                    "mode": config.mode,
                    "mounting_mode": config.mounting_mode,
                    "sensitivity": config.sensitivity,
                    "ceiling_calibration_status": config.ceiling_calibration_status,
                }
                if now - last_publish_at >= args.publish_interval:
                    mqtt_client.publish(state_topic, json.dumps(payload), retain=True)
                    debug_payload = {
                        "device_id": args.device_id,
                        **result,
                        "mode": config.mode,
                        "mounting_mode": config.mounting_mode,
                        "sensitivity": config.sensitivity,
                        "ceiling_calibration_status": config.ceiling_calibration_status,
                    }
                    mqtt_client.publish(debug_topic, json.dumps(debug_payload), retain=False)
                    last_publish_at = now
                continue

            breathing_rate = None
            breathing_detected = False
            breathing_app_state = None
            chip_temperature = None
            tick_time = None
            zone = "none"
            smart_intra_active = False

            if isinstance(controller, RefApp):
                presence_result = result.presence_result
                breathing_app_state = result.app_state.name.lower()
                if result.breathing_result is not None:
                    breathing_rate = result.breathing_result.breathing_rate
                    breathing_detected = breathing_rate is not None
                presence_detected = bool(presence_result.presence_detected)
                presence_distance_m = float(presence_result.presence_distance)
                intra_presence_score = float(presence_result.intra_presence_score)
                inter_presence_score = float(presence_result.inter_presence_score)
            elif isinstance(controller, PresenceDetector):
                presence_result = result
                if smart_presence_processor is not None:
                    smart_presence_result = smart_presence_processor.process(presence_result)
                    zone = smart_zone_label(smart_presence_result.max_presence_zone)
                    smart_intra_active = smart_presence_result.max_intra_zone is not None
                service_result = result.service_result
                chip_temperature = getattr(service_result, "temperature", None)
                tick_time = getattr(service_result, "tick_time", None)
                presence_detected = bool(presence_result.presence_detected)
                presence_distance_m = float(presence_result.presence_distance)
                intra_presence_score = float(presence_result.intra_presence_score)
                inter_presence_score = float(presence_result.inter_presence_score)
            else:
                distance_result = result[1]
                chip_temperature = distance_result.temperature
                tick_time = None
                presence_distance_m = pick_distance_peak(distance_result.distances)
                presence_detected = presence_distance_m > 0
                intra_presence_score = 0.0
                inter_presence_score = 1.0 if presence_detected else 0.0
                zone = zone_for(float(presence_distance_m), config)

            if presence_detected:
                last_presence_at = now
            presence_score = max(
                intra_presence_score,
                inter_presence_score,
            )

            current_distance_m = presence_distance_m
            distance_velocity_mps = 0.0
            distance_jump_m = 0.0
            if presence_detected and current_distance_m > 0:
                if last_distance_m is not None and last_distance_at is not None:
                    dt = max(now - last_distance_at, 0.001)
                    distance_jump_m = current_distance_m - last_distance_m
                    distance_velocity_mps = distance_jump_m / dt
                last_distance_m = current_distance_m
                last_distance_at = now
            else:
                last_distance_m = None
                last_distance_at = None

            raw_motion_active = (
                presence_detected
                and (
                    intra_presence_score >= effective_config.intra_detection_threshold
                    or smart_intra_active
                    or abs(distance_velocity_mps) >= effective_config.motion_velocity_min_mps
                )
            )
            if raw_motion_active:
                active_motion_at = now
                motion_active_until = now + effective_config.motion_active_hold_s
            motion_state = (
                "active"
                if presence_detected and now <= motion_active_until
                else "still"
                if presence_detected
                else "clear"
            )

            floor_presence = (
                config.mode == "sturzmodus"
                and presence_detected
                and current_distance_m >= effective_config.fall_floor_min_m
            )
            fall_like_drop = (
                floor_presence
                and raw_motion_active
                and distance_jump_m >= effective_config.fall_drop_min_m
                and distance_velocity_mps >= effective_config.fall_velocity_min_mps
            )
            if fall_like_drop:
                fall_impact_at = now
            if floor_presence:
                floor_presence_since = floor_presence_since or now
            else:
                floor_presence_since = None

            if presence_detected:
                clear_since = None
            else:
                clear_since = clear_since or now
                if now - clear_since >= effective_config.fall_clear_reset_s:
                    fall_detected = False

            floor_presence_s = 0.0 if floor_presence_since is None else now - floor_presence_since
            had_recent_fall_impact = (
                fall_impact_at is not None
                and now - fall_impact_at <= effective_config.fall_active_window_s
            )
            fall_candidate = (
                config.mode == "sturzmodus"
                and floor_presence
                and motion_state == "still"
                and had_recent_fall_impact
            )
            if fall_candidate and floor_presence_s >= effective_config.fall_still_confirm_s:
                fall_detected = True
            fall_confidence = 0
            if fall_candidate:
                time_score = min(50, int(50 * floor_presence_s / effective_config.fall_still_confirm_s))
                distance_score = min(
                    30,
                    int(30 * max(0.0, current_distance_m - effective_config.fall_floor_min_m)),
                )
                motion_score = 20 if had_recent_fall_impact else 0
                fall_confidence = min(100, time_score + distance_score + motion_score)
            fall_state = "detected" if fall_detected else "candidate" if fall_candidate else "clear"

            payload = {
                "device_id": args.device_id,
                "presence": bool(presence_detected),
                "motion_state": motion_state,
                "fall_detected": bool(fall_detected),
                "mode": config.mode,
                "sensitivity": config.sensitivity,
                "ceiling_calibration_status": config.ceiling_calibration_status,
            }
            if now - last_publish_at >= args.publish_interval:
                mqtt_client.publish(state_topic, json.dumps(payload), retain=True)
                last_publish_at = now
    finally:
        if controller is not None:
            try:
                controller.stop()
            except Exception:
                pass
        if client_a121 is not None:
            try:
                client_a121.close()
            except Exception:
                pass
        mqtt_client.publish(availability_topic, "offline", retain=True)
        mqtt_client.loop_stop()
        mqtt_client.disconnect()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
