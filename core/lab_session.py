# core/lab_session.py
# Метаданные лабораторного эксперимента и журнал experiments.csv.
#
# LabSessionMetadata — то, что оператор задаёт перед записью телеметрии.
# Эти же поля затем "прокидываются" в каждую строку CSV-телеметрии
# (см. core/lab_export.py) и в одну строку experiments.csv (журнал опытов).
#
# Модуль НЕ управляет приводом и НЕ запускает движение. Он только формирует
# метаданные и пишет журнал.

from __future__ import annotations

import csv
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from core.csv_schema import (
    EXPERIMENTS_COLUMNS,
    ALLOWED_REGIME_LABELS,
    ALLOWED_RISK_LABELS,
)


DEFAULT_SOFTWARE_VERSION = "servo-app/lab-0.1"


@dataclass
class LabSessionMetadata:
    """Метаданные сессии записи телеметрии.

    Эти поля попадают:
      * в каждую строку телеметрии (через core/lab_export.py);
      * в одну строку experiments.csv (через core/lab_session.append_experiment).
    """

    experiment_id: str
    session_id: str
    device_id: str = "delta-asda-b3-e"
    experiment_type: str = ""

    # Метки разметки. Должны быть в ALLOWED_*.
    regime_label: str = "idle"
    risk_label: str = "normal"

    load_type: str = "no_load"
    direction: str = "none"

    target_position: str = ""
    target_velocity: str = ""
    target_torque: str = ""
    acceleration_cmd: str = ""
    deceleration_cmd: str = ""

    operator_comment: str = ""
    is_artificial_anomaly: int = 0
    software_version: str = DEFAULT_SOFTWARE_VERSION

    # ----------------------------------------------------------------- helpers
    def validate(self) -> list[str]:
        """Вернуть список ошибок (пустой = всё ок). Не бросает исключения."""
        errors: list[str] = []
        if not self.experiment_id:
            errors.append("experiment_id is empty")
        if not self.session_id:
            errors.append("session_id is empty")
        if self.regime_label not in ALLOWED_REGIME_LABELS:
            errors.append(
                f"regime_label={self.regime_label!r} not in allowed set "
                f"({sorted(ALLOWED_REGIME_LABELS)})"
            )
        if self.risk_label not in ALLOWED_RISK_LABELS:
            errors.append(
                f"risk_label={self.risk_label!r} not in allowed set "
                f"({sorted(ALLOWED_RISK_LABELS)})"
            )
        if int(self.is_artificial_anomaly) not in (0, 1):
            errors.append("is_artificial_anomaly must be 0 or 1")
        return errors

    def require_valid(self) -> None:
        errs = self.validate()
        if errs:
            raise ValueError(
                "LabSessionMetadata validation failed:\n  - " + "\n  - ".join(errs)
            )

    def as_row_for_telemetry(self) -> dict[str, str]:
        """Только те поля, которые попадают в КАЖДУЮ строку CSV-телеметрии."""
        return {
            "experiment_id": self.experiment_id,
            "session_id": self.session_id,
            "device_id": self.device_id,
            "target_position": self.target_position,
            "target_velocity": self.target_velocity,
            "target_torque": self.target_torque,
            "acceleration_cmd": self.acceleration_cmd,
            "deceleration_cmd": self.deceleration_cmd,
            "regime_label": self.regime_label,
            "risk_label": self.risk_label,
            "is_artificial_anomaly": str(int(self.is_artificial_anomaly)),
            "load_type": self.load_type,
            "direction": self.direction,
            "operator_comment": self.operator_comment,
            "software_version": self.software_version,
        }


# ---------------------------------------------------------------------------
# experiments.csv — журнал
# ---------------------------------------------------------------------------

def _format_row_for_experiments(row: dict[str, Any]) -> list[str]:
    return [str(row.get(c, "") if row.get(c, "") is not None else "")
            for c in EXPERIMENTS_COLUMNS]


def append_experiment(
    experiments_csv: str | Path,
    meta: LabSessionMetadata,
    *,
    file_name: str = "",
    date_start: str = "",
    date_end: str = "",
    operator: str = "",
    drive_model: str = "",
    motor_model: str = "",
    recording_purpose: str = "training",
    dataset_split: str = "unassigned",
    duration_s: float | str = "",
    sample_rate_hz: float | str = "",
    stop_condition: str = "",
    session_status: str = "ok",
) -> Path:
    """Добавить строку в experiments.csv (создаст файл с заголовком, если нужно)."""
    meta.require_valid()
    path = Path(experiments_csv)
    path.parent.mkdir(parents=True, exist_ok=True)
    need_header = not path.exists() or path.stat().st_size == 0

    row: dict[str, Any] = {
        "experiment_id": meta.experiment_id,
        "session_id": meta.session_id,
        "file_name": file_name,
        "date_start": date_start or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date_end": date_end,
        "operator": operator,
        "drive_model": drive_model,
        "motor_model": motor_model,
        "experiment_type": meta.experiment_type,
        "regime_label": meta.regime_label,
        "risk_label": meta.risk_label,
        "recording_purpose": recording_purpose,
        "dataset_split": dataset_split,
        "load_type": meta.load_type,
        "direction": meta.direction,
        "target_position": meta.target_position,
        "target_velocity": meta.target_velocity,
        "target_torque": meta.target_torque,
        "acceleration_cmd": meta.acceleration_cmd,
        "deceleration_cmd": meta.deceleration_cmd,
        "duration_s": duration_s,
        "sample_rate_hz": sample_rate_hz,
        "stop_condition": stop_condition,
        "session_status": session_status,
        "is_artificial_anomaly": int(meta.is_artificial_anomaly),
        "operator_comment": meta.operator_comment,
    }
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if need_header:
            w.writerow(EXPERIMENTS_COLUMNS)
        w.writerow(_format_row_for_experiments(row))
    return path


def read_experiments(experiments_csv: str | Path) -> list[dict[str, str]]:
    path = Path(experiments_csv)
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))
