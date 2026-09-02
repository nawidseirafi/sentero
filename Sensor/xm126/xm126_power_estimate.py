from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from acconeer.exptool import a121
from acconeer.exptool.a121.model import power

from xm126_mqtt_bridge import BridgeConfig, RawTestController, apply_presets, load_config


BASE_DIR = Path(__file__).resolve().parent
_MA = 1000.0


def _sensor_summary(sensor_config: a121.SensorConfig) -> dict[str, Any]:
    return {
        "start_point": sensor_config.start_point,
        "num_points": sensor_config.num_points,
        "step_length": sensor_config.step_length,
        "profile": sensor_config.profile.name if sensor_config.profile is not None else None,
        "prf": sensor_config.prf.name if sensor_config.prf is not None else None,
        "hwaas": sensor_config.hwaas,
        "sweeps_per_frame": sensor_config.sweeps_per_frame,
        "frame_rate_hz": sensor_config.frame_rate,
        "inter_sweep_idle_state": sensor_config.inter_sweep_idle_state.name,
        "inter_frame_idle_state": sensor_config.inter_frame_idle_state.name,
    }


def _estimate_current(
    session_config: a121.SessionConfig,
    *,
    algorithm: power.algo.Algorithm,
    lower_idle_state: power.Sensor.LowerIdleState | None,
) -> float:
    return power.converged_average_current(
        session_config,
        lower_idle_state=lower_idle_state,
        absolute_tolerance=0.01e-3,
        algorithm=algorithm,
    )


def _battery_hours(capacity_mah: float, avg_current_ma: float, efficiency: float) -> float:
    if avg_current_ma <= 0.0:
        return 0.0
    return capacity_mah * efficiency / avg_current_ma


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Estimate XM126/A121 current consumption from the active bridge configuration."
    )
    parser.add_argument("--config", default=str(BASE_DIR / "data" / "xm126_bridge_config.json"))
    parser.add_argument(
        "--algorithm",
        choices=["sparse_iq", "presence"],
        default="presence",
        help="Power model algorithm. Presence includes Acconeer-like processing cost.",
    )
    parser.add_argument(
        "--lower-idle-state",
        choices=["none", "hibernate", "off"],
        default="none",
        help="Optional lower power state between frames/groups, as used by Acconeer resource model.",
    )
    parser.add_argument(
        "--battery-mah",
        type=float,
        nargs="*",
        default=[1000.0, 2400.0, 3000.0, 5000.0],
        help="Battery capacities to estimate.",
    )
    parser.add_argument(
        "--efficiency",
        type=float,
        default=0.85,
        help="Usable battery/regulator efficiency for runtime estimates.",
    )
    parser.add_argument("--frame-rate", type=float, help="Override frame_rate for what-if estimates.")
    parser.add_argument("--hwaas", type=int, help="Override hwaas for what-if estimates.")
    parser.add_argument("--sweeps-per-frame", type=int, help="Override sweeps_per_frame for what-if estimates.")
    parser.add_argument("--start-m", type=float, help="Override start_m for what-if estimates.")
    parser.add_argument("--end-m", type=float, help="Override end_m for what-if estimates.")
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    args = parser.parse_args()

    config_path = Path(args.config)
    bridge_config: BridgeConfig = load_config(config_path)
    effective_config = apply_presets(bridge_config)
    if args.frame_rate is not None:
        effective_config.frame_rate = args.frame_rate
    if args.hwaas is not None:
        effective_config.hwaas = args.hwaas
    if args.sweeps_per_frame is not None:
        effective_config.sweeps_per_frame = args.sweeps_per_frame
    if args.start_m is not None:
        effective_config.start_m = args.start_m
    if args.end_m is not None:
        effective_config.end_m = args.end_m
    sensor_config = RawTestController._sensor_config(effective_config)
    session_config = a121.SessionConfig({1: sensor_config}, extended=False)
    session_config.validate()

    algorithm: power.algo.Algorithm
    if args.algorithm == "presence":
        algorithm = power.algo.Presence()
    else:
        algorithm = power.algo.SparseIq()

    lower_idle_state: power.Sensor.LowerIdleState | None
    if args.lower_idle_state == "hibernate":
        lower_idle_state = power.Sensor.IdleState.HIBERNATE
    elif args.lower_idle_state == "off":
        lower_idle_state = power.Sensor.IdleState.OFF
    else:
        lower_idle_state = None

    avg_current_a = _estimate_current(
        session_config,
        algorithm=algorithm,
        lower_idle_state=lower_idle_state,
    )
    avg_current_ma = avg_current_a * _MA
    configured_rate = power.configured_rate(session_config)

    battery_estimates = [
        {
            "capacity_mah": capacity,
            "runtime_h": round(_battery_hours(capacity, avg_current_ma, args.efficiency), 2),
            "runtime_days": round(_battery_hours(capacity, avg_current_ma, args.efficiency) / 24.0, 2),
        }
        for capacity in args.battery_mah
    ]

    result = {
        "source": "acconeer_exptool_a121_power_model",
        "note": "Model estimate, not a physical USB current measurement.",
        "config_path": str(config_path),
        "mode": effective_config.mode,
        "mounting_mode": effective_config.mounting_mode,
        "sensitivity": effective_config.sensitivity,
        "range_m": {
            "start_m": effective_config.start_m,
            "end_m": effective_config.end_m,
            "presence_max_m": effective_config.presence_max_m,
        },
        "algorithm": args.algorithm,
        "lower_idle_state": args.lower_idle_state,
        "configured_rate_hz": configured_rate,
        "average_current_ma": round(avg_current_ma, 3),
        "battery_efficiency": args.efficiency,
        "battery_estimates": battery_estimates,
        "sensor_config": _sensor_summary(sensor_config),
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    print(f"Config: {config_path}")
    print(f"Mode: {effective_config.mode}, mounting: {effective_config.mounting_mode}, sensitivity: {effective_config.sensitivity}")
    print(f"Range: {effective_config.start_m:.2f}-{effective_config.end_m:.2f} m")
    print(f"Algorithm model: {args.algorithm}, lower idle: {args.lower_idle_state}")
    print(f"Configured rate: {configured_rate:.3f} Hz" if configured_rate else "Configured rate: max")
    print(f"Estimated average current: {avg_current_ma:.3f} mA")
    print()
    print("Battery estimates:")
    for item in battery_estimates:
        print(
            f"  {item['capacity_mah']:.0f} mAh @ {args.efficiency:.0%}: "
            f"{item['runtime_h']:.2f} h / {item['runtime_days']:.2f} d"
        )
    print()
    print("Sensor config:")
    for key, value in _sensor_summary(sensor_config).items():
        print(f"  {key}: {value}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
