# tests/test_lab_layer.py
# Тесты лабораторного слоя — все без железа.
#
# Покрытие:
#   * порядок колонок CSV;
#   * экспорт из искусственной SQLite-таблицы `samples`;
#   * вычисление t = ts - first_ts;
#   * валидация корректного CSV;
#   * ошибки при пропущенных обязательных колонках;
#   * создание stand_passport.csv.

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

import pytest

from core.csv_schema import (
    TELEMETRY_COLUMNS,
    EXPERIMENTS_COLUMNS,
    STAND_PASSPORT_COLUMNS,
)
from core.lab_export import export_samples_to_csv, decode_statusword_flags
from core.lab_session import LabSessionMetadata, append_experiment
from core.stand_passport import StandPassport, build_passport_with_autofill
from tools.validate_lab_csv import validate_csv


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------

def _make_fake_samples_db(path: Path, n_rows: int = 5,
                         start_ts: float = 1_700_000_000.0,
                         dt: float = 0.1) -> None:
    """Создать SQLite с минимальной таблицей `samples`, совместимой с
    core.telemetry."""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE samples (
                ts              REAL,
                statusword      INTEGER,
                mode_display    INTEGER,
                position        INTEGER,
                velocity        INTEGER,
                torque          INTEGER,
                current         INTEGER,
                error_code      INTEGER,
                rated_current   INTEGER,
                rated_torque    INTEGER,
                max_motor_speed INTEGER,
                dc_bus_voltage  REAL,
                drive_temp      REAL,
                current_A       REAL,
                torque_Nm       REAL
            )
            """
        )
        for i in range(n_rows):
            conn.execute(
                "INSERT INTO samples VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    start_ts + i * dt,
                    0x0237,           # statusword (OE|SO|RTSO|VE|TR)
                    1,                # PP
                    1000 + i * 10,    # position
                    50,               # velocity (rpm)
                    100,              # torque (per-mille)
                    200,              # current
                    0,                # error_code
                    2890,             # rated_current mA
                    1270,             # rated_torque mNm
                    5000,             # max_motor_speed rpm
                    310.0,            # dc_bus
                    42.0,             # temp
                    0.578,            # current_A
                    0.127,            # torque_Nm
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _good_meta() -> LabSessionMetadata:
    return LabSessionMetadata(
        experiment_id="A1_HOLDING",
        session_id="A1_R01_20260612_103000",
        device_id="delta-asda-b3-e",
        experiment_type="holding",
        regime_label="holding",
        risk_label="normal",
        load_type="no_load",
        direction="none",
        target_velocity="0",
        operator_comment="test",
        is_artificial_anomaly=0,
    )


# ---------------------------------------------------------------------------
# Тесты схемы
# ---------------------------------------------------------------------------

def test_telemetry_columns_exact_order():
    """Порядок колонок CSV строго фиксирован — это контракт со статьёй."""
    expected = (
        "ts,t,experiment_id,session_id,device_id,statusword,statusword_flags,"
        "mode_display,error_code,position,velocity,current,current_A,torque,"
        "torque_Nm,dc_bus_voltage,drive_temp,rated_current,rated_torque,"
        "max_motor_speed,target_position,target_velocity,target_torque,"
        "acceleration_cmd,deceleration_cmd,regime_label,risk_label,"
        "is_artificial_anomaly,load_type,direction,operator_comment,"
        "software_version"
    ).split(",")
    assert list(TELEMETRY_COLUMNS) == expected


def test_experiments_and_passport_columns_have_required_fields():
    for required in ("experiment_id", "session_id", "regime_label",
                     "risk_label", "duration_s"):
        assert required in EXPERIMENTS_COLUMNS
    for required in ("stand_id", "drive_model", "drive_serial",
                     "motor_model", "motor_serial", "rated_current_A"):
        assert required in STAND_PASSPORT_COLUMNS


# ---------------------------------------------------------------------------
# Тесты экспорта
# ---------------------------------------------------------------------------

def test_export_from_synthetic_sqlite(tmp_path: Path):
    db = tmp_path / "samples.sqlite3"
    csv_path = tmp_path / "out.csv"
    _make_fake_samples_db(db, n_rows=7)

    result = export_samples_to_csv(db, csv_path, _good_meta())

    assert result.row_count == 7
    assert csv_path.exists()

    with csv_path.open(encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows = list(reader)

    # Порядок колонок ровно как в схеме
    assert header == list(TELEMETRY_COLUMNS)
    assert len(rows) == 7

    # Метаданные прокинуты в каждую строку
    idx = {c: i for i, c in enumerate(header)}
    for row in rows:
        assert row[idx["experiment_id"]] == "A1_HOLDING"
        assert row[idx["session_id"]] == "A1_R01_20260612_103000"
        assert row[idx["regime_label"]] == "holding"
        assert row[idx["risk_label"]] == "normal"
        assert row[idx["is_artificial_anomaly"]] == "0"
        # Декодированные флаги statusword
        assert "OE" in row[idx["statusword_flags"]]


def test_t_equals_ts_minus_first_ts(tmp_path: Path):
    db = tmp_path / "samples.sqlite3"
    csv_path = tmp_path / "out.csv"
    _make_fake_samples_db(db, n_rows=4, start_ts=1234.5, dt=0.25)

    export_samples_to_csv(db, csv_path, _good_meta())

    with csv_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    ts_values = [float(r["ts"]) for r in rows]
    t_values = [float(r["t"]) for r in rows]
    assert ts_values[0] == pytest.approx(1234.5)
    # Первое t — ровно 0
    assert t_values[0] == pytest.approx(0.0)
    # Каждое t = ts - first_ts
    first = ts_values[0]
    for ts, t in zip(ts_values, t_values):
        assert t == pytest.approx(ts - first)
    # Монотонность
    assert all(b > a for a, b in zip(t_values, t_values[1:]))


def test_export_raises_on_empty_db(tmp_path: Path):
    db = tmp_path / "empty.sqlite3"
    csv_path = tmp_path / "out.csv"
    # Пустая таблица — но таблица существует.
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE samples (ts REAL, statusword INTEGER, "
                 "mode_display INTEGER, position INTEGER, velocity INTEGER, "
                 "torque INTEGER, current INTEGER, error_code INTEGER, "
                 "rated_current INTEGER, rated_torque INTEGER, "
                 "max_motor_speed INTEGER, dc_bus_voltage REAL, "
                 "drive_temp REAL, current_A REAL, torque_Nm REAL)")
    conn.commit()
    conn.close()
    with pytest.raises(ValueError):
        export_samples_to_csv(db, csv_path, _good_meta())


def test_decode_statusword_flags_basic():
    # OE(0x04) | SO(0x02) | RTSO(0x01) | VE(0x10) = 0x17
    flags = decode_statusword_flags(0x17)
    assert "RTSO" in flags and "SO" in flags and "OE" in flags and "VE" in flags
    assert decode_statusword_flags(None) == ""
    assert decode_statusword_flags(0) == ""


# ---------------------------------------------------------------------------
# Тесты валидатора
# ---------------------------------------------------------------------------

def _produce_valid_csv(tmp_path: Path) -> Path:
    db = tmp_path / "samples.sqlite3"
    csv_path = tmp_path / "out.csv"
    _make_fake_samples_db(db, n_rows=3)
    export_samples_to_csv(db, csv_path, _good_meta())
    return csv_path


def test_validator_accepts_correct_csv(tmp_path: Path):
    csv_path = _produce_valid_csv(tmp_path)
    report = validate_csv(csv_path)
    assert report.ok, f"errors: {report.errors}"
    assert report.row_count == 3


def test_validator_rejects_missing_columns(tmp_path: Path):
    csv_path = tmp_path / "bad.csv"
    # Заголовок без обязательных колонок
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "velocity", "torque"])  # нет experiment_id, etc.
        w.writerow([1.0, 0, 0])
    report = validate_csv(csv_path)
    assert not report.ok
    assert any("missing columns" in e for e in report.errors)


def test_validator_rejects_bad_labels(tmp_path: Path):
    csv_path = _produce_valid_csv(tmp_path)
    # Испортим regime_label в первой строке данных
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    idx = header.index("regime_label")
    rows[1][idx] = "totally_wrong_label"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    report = validate_csv(csv_path)
    assert not report.ok
    assert any("regime_label" in e for e in report.errors)


def test_validator_rejects_non_monotonic_t(tmp_path: Path):
    csv_path = _produce_valid_csv(tmp_path)
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    idx_t = header.index("t")
    # Делаем третью строку < второй
    rows[3][idx_t] = "-1.0"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    report = validate_csv(csv_path)
    assert not report.ok
    assert any("monotonically" in e for e in report.errors)


def test_validator_warns_on_error_code_for_normal(tmp_path: Path):
    csv_path = _produce_valid_csv(tmp_path)
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header = rows[0]
    idx_err = header.index("error_code")
    rows[1][idx_err] = "42"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    report = validate_csv(csv_path)
    # Это warning, не error
    assert report.ok
    assert any("error_code" in w for w in report.warnings)


# ---------------------------------------------------------------------------
# Тесты метаданных
# ---------------------------------------------------------------------------

def test_metadata_rejects_unknown_labels():
    meta = LabSessionMetadata(
        experiment_id="x", session_id="y",
        regime_label="not_a_real_label", risk_label="normal",
    )
    errors = meta.validate()
    assert any("regime_label" in e for e in errors)


def test_metadata_rejects_empty_ids():
    meta = LabSessionMetadata(experiment_id="", session_id="")
    errors = meta.validate()
    assert any("experiment_id" in e for e in errors)
    assert any("session_id" in e for e in errors)


def test_append_experiment_writes_header_once(tmp_path: Path):
    csv_path = tmp_path / "experiments.csv"
    meta = _good_meta()
    append_experiment(csv_path, meta, file_name="a.sqlite3", duration_s=1)
    append_experiment(csv_path, meta, file_name="b.sqlite3", duration_s=2)

    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == list(EXPERIMENTS_COLUMNS)
    assert len(rows) == 3  # header + 2 data


# ---------------------------------------------------------------------------
# Тесты паспорта стенда
# ---------------------------------------------------------------------------

def test_stand_passport_write_csv(tmp_path: Path):
    out = tmp_path / "stand_passport.csv"
    passport = StandPassport(
        stand_id="stand-01",
        date="2026-06-11 10:00:00",
        operator="Иванов",
        drive_model="ASD-B3-0421-E",
        drive_serial="DRV-12345",
        motor_model="ECM-B3M-CA0604RS1",
        motor_serial="MOT-67890",
        rated_current_A="2.89",
        rated_torque_Nm="1.27",
        max_motor_speed_rpm="5000",
    )
    passport.write_csv(out)
    with out.open(encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        data = next(reader)
    assert header == list(STAND_PASSPORT_COLUMNS)
    row = dict(zip(header, data))
    assert row["stand_id"] == "stand-01"
    assert row["drive_model"] == "ASD-B3-0421-E"
    assert row["rated_current_A"] == "2.89"


def test_stand_passport_autofill_without_controller(tmp_path: Path):
    """Без подключённого привода поля rated_* остаются пустыми, но
    обязательные ручные поля переносятся корректно."""
    out = tmp_path / "stand_passport.csv"
    passport = build_passport_with_autofill(
        controller=None,
        stand_id="stand-01",
        drive_model="ASD-B3-0421-E",
        drive_serial="DRV-1",
        motor_model="ECM",
        motor_serial="MOT-1",
    )
    assert passport.rated_current_A == ""
    assert passport.rated_torque_Nm == ""
    assert passport.max_motor_speed_rpm == ""
    assert passport.drive_model == "ASD-B3-0421-E"
    passport.write_csv(out)
    with out.open(encoding="utf-8") as f:
        rows = list(csv.reader(f))
    assert rows[0] == list(STAND_PASSPORT_COLUMNS)


def test_stand_passport_autofill_with_fake_controller(tmp_path: Path):
    """Эмулируем pysoem-slave, отдающий SDO-значения. Никакого железа."""
    import struct

    class FakeSlave:
        def sdo_read(self, idx, sub):
            data = {
                (0x6075, 0): struct.pack("<I", 2890),    # 2.89 A
                (0x6076, 0): struct.pack("<I", 1270),    # 1.27 Nm
                (0x6080, 0): struct.pack("<I", 5000),    # 5000 rpm
            }
            return data[(idx, sub)]

    class FakeController:
        slave = FakeSlave()

    passport = build_passport_with_autofill(
        controller=FakeController(),
        stand_id="stand-01",
        drive_model="ASD-B3-0421-E",
        drive_serial="DRV-1",
        motor_model="ECM",
        motor_serial="MOT-1",
    )
    assert passport.rated_current_A == "2.890"
    assert passport.rated_torque_Nm == "1.270"
    assert passport.max_motor_speed_rpm == "5000"
