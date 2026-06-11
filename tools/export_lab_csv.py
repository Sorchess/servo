# tools/export_lab_csv.py
# CLI: экспортировать SQLite-телеметрию в стандартизированный CSV.
#
# Пример:
#   python -m tools.export_lab_csv \
#     --db lab_data/raw/A1_R01_20260612_103000.sqlite3 \
#     --out lab_data/raw/telemetry_A1_R01_20260612_103000.csv \
#     --experiment-id A1_HOLDING_SERVO_ON \
#     --session-id A1_R01_20260612_103000

from __future__ import annotations

import argparse
import sys

from core.csv_schema import ALLOWED_REGIME_LABELS, ALLOWED_RISK_LABELS
from core.lab_session import LabSessionMetadata
from core.lab_export import export_samples_to_csv


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="export_lab_csv",
        description="Экспорт телеметрии SQLite -> стандартизированный CSV.",
    )
    p.add_argument("--db", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--experiment-id", required=True)
    p.add_argument("--session-id", required=True)
    p.add_argument("--device-id", default="delta-asda-b3-e")
    p.add_argument("--experiment-type", default="")
    p.add_argument("--regime-label", default="idle",
                   choices=sorted(ALLOWED_REGIME_LABELS))
    p.add_argument("--risk-label", default="normal",
                   choices=sorted(ALLOWED_RISK_LABELS))
    p.add_argument("--load-type", default="no_load")
    p.add_argument("--direction", default="none")
    p.add_argument("--target-position", default="")
    p.add_argument("--target-velocity", default="")
    p.add_argument("--target-torque", default="")
    p.add_argument("--acceleration-cmd", default="")
    p.add_argument("--deceleration-cmd", default="")
    p.add_argument("--operator-comment", default="")
    p.add_argument("--is-artificial-anomaly", type=int, default=0, choices=(0, 1))
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    meta = LabSessionMetadata(
        experiment_id=args.experiment_id,
        session_id=args.session_id,
        device_id=args.device_id,
        experiment_type=args.experiment_type,
        regime_label=args.regime_label,
        risk_label=args.risk_label,
        load_type=args.load_type,
        direction=args.direction,
        target_position=args.target_position,
        target_velocity=args.target_velocity,
        target_torque=args.target_torque,
        acceleration_cmd=args.acceleration_cmd,
        deceleration_cmd=args.deceleration_cmd,
        operator_comment=args.operator_comment,
        is_artificial_anomaly=int(args.is_artificial_anomaly),
    )
    result = export_samples_to_csv(args.db, args.out, meta)
    print(
        f"[export_lab_csv] OK: rows={result.row_count}, "
        f"duration={result.duration_s:.3f}s, csv={result.csv_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
