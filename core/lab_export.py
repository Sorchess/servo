# core/lab_export.py
# Экспорт телеметрии из SQLite (`samples`) в стандартизированный CSV.
#
# Логика:
#   1. Открыть SQLite, выбрать строки из `samples` в порядке возрастания ts.
#   2. Вычислить t = ts - first_ts (относительное время от начала записи).
#   3. Распаковать statusword в человекочитаемые флаги (statusword_flags).
#   4. Прокинуть metadata-поля из LabSessionMetadata в каждую строку.
#   5. Записать CSV строго в порядке TELEMETRY_COLUMNS.
#
# Существующая таблица `samples` НЕ модифицируется — мы только читаем.

from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from core.csv_schema import TELEMETRY_COLUMNS
from core.lab_session import LabSessionMetadata


# Битовые флаги CiA 402 Statusword (стандарт DS402).
_STATUSWORD_FLAGS = (
    (1 << 0, "RTSO"),   # Ready to switch on
    (1 << 1, "SO"),     # Switched on
    (1 << 2, "OE"),     # Operation enabled
    (1 << 3, "FAULT"),
    (1 << 4, "VE"),     # Voltage enabled
    (1 << 5, "QS"),     # Quick stop
    (1 << 6, "SOD"),    # Switch on disabled
    (1 << 7, "WARN"),
    (1 << 10, "TR"),    # Target reached
    (1 << 11, "ILA"),   # Internal limit active
    (1 << 12, "ACK"),   # Setpoint ack (PP)
    (1 << 13, "FE"),    # Following error
)


def decode_statusword_flags(value: int | None) -> str:
    """Превратить 16-битный statusword в строку 'OE|VE|TR' (пайп-разделение)."""
    if value is None:
        return ""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return ""
    return "|".join(name for mask, name in _STATUSWORD_FLAGS if v & mask)


# Колонки, которые мы реально читаем из текущей таблицы `samples`
# (см. core/telemetry.py — там схема `samples` уже описана).
_SAMPLES_PROJECTION = (
    "ts",
    "statusword",
    "mode_display",
    "position",
    "velocity",
    "torque",
    "current",
    "error_code",
    "rated_current",
    "rated_torque",
    "max_motor_speed",
    "dc_bus_voltage",
    "drive_temp",
    "current_A",
    "torque_Nm",
)


def _iter_samples(db_path: str | Path) -> Iterator[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        cols = ", ".join(_SAMPLES_PROJECTION)
        cur = conn.execute(f"SELECT {cols} FROM samples ORDER BY ts ASC")
        yield from cur
    finally:
        conn.close()


def _row_to_telemetry(
    row: sqlite3.Row | dict,
    first_ts: float,
    meta_row: dict[str, str],
) -> dict[str, object]:
    ts = row["ts"]
    statusword = row["statusword"]
    out: dict[str, object] = {c: "" for c in TELEMETRY_COLUMNS}
    out.update({
        "ts": ts,
        "t": (ts - first_ts) if ts is not None else "",
        "statusword": statusword if statusword is not None else "",
        "statusword_flags": decode_statusword_flags(statusword),
        "mode_display": row["mode_display"] if row["mode_display"] is not None else "",
        "error_code": row["error_code"] if row["error_code"] is not None else "",
        "position": row["position"] if row["position"] is not None else "",
        "velocity": row["velocity"] if row["velocity"] is not None else "",
        "current": row["current"] if row["current"] is not None else "",
        "current_A": row["current_A"] if row["current_A"] is not None else "",
        "torque": row["torque"] if row["torque"] is not None else "",
        "torque_Nm": row["torque_Nm"] if row["torque_Nm"] is not None else "",
        "dc_bus_voltage": row["dc_bus_voltage"] if row["dc_bus_voltage"] is not None else "",
        "drive_temp": row["drive_temp"] if row["drive_temp"] is not None else "",
        "rated_current": row["rated_current"] if row["rated_current"] is not None else "",
        "rated_torque": row["rated_torque"] if row["rated_torque"] is not None else "",
        "max_motor_speed": row["max_motor_speed"] if row["max_motor_speed"] is not None else "",
    })
    # Прокинуть metadata. Эти поля перезаписывают пустые значения выше.
    out.update(meta_row)
    return out


@dataclass
class ExportResult:
    csv_path: Path
    row_count: int
    first_ts: float | None
    last_ts: float | None
    duration_s: float


def export_samples_to_csv(
    db_path: str | Path,
    csv_path: str | Path,
    meta: LabSessionMetadata,
) -> ExportResult:
    """Экспорт `samples` -> CSV в строгом порядке TELEMETRY_COLUMNS.

    Бросает ValueError, если в `samples` нет ни одной строки.
    """
    meta.require_valid()
    meta_row = meta.as_row_for_telemetry()
    db_path = Path(db_path)
    csv_path = Path(csv_path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    rows = list(_iter_samples(db_path))
    if not rows:
        raise ValueError(f"No samples in {db_path}")

    first_ts = rows[0]["ts"]
    last_ts = rows[-1]["ts"]

    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(TELEMETRY_COLUMNS)
        for row in rows:
            out = _row_to_telemetry(row, first_ts, meta_row)
            w.writerow([out.get(c, "") for c in TELEMETRY_COLUMNS])

    duration = (last_ts - first_ts) if (first_ts is not None and last_ts is not None) else 0.0
    return ExportResult(
        csv_path=csv_path,
        row_count=len(rows),
        first_ts=first_ts,
        last_ts=last_ts,
        duration_s=duration,
    )
