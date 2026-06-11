# core/csv_schema.py
# Единый источник правды для CSV-схем лабораторного слоя.
#
# Здесь хранятся:
#   * строгий порядок колонок CSV телеметрии;
#   * заголовок experiments.csv (журнал экспериментов);
#   * заголовок stand_passport.csv (паспорт стенда);
#   * разрешённые значения regime_label и risk_label.
#
# Все экспортеры/валидаторы/UI должны импортировать константы отсюда,
# чтобы исключить расхождение схем между модулями.

from __future__ import annotations

# ---------------------------------------------------------------------------
# Телеметрия: строгий порядок колонок (CSV для статьи)
# ---------------------------------------------------------------------------
TELEMETRY_COLUMNS: tuple[str, ...] = (
    "ts",
    "t",
    "experiment_id",
    "session_id",
    "device_id",
    "statusword",
    "statusword_flags",
    "mode_display",
    "error_code",
    "position",
    "velocity",
    "current",
    "current_A",
    "torque",
    "torque_Nm",
    "dc_bus_voltage",
    "drive_temp",
    "rated_current",
    "rated_torque",
    "max_motor_speed",
    "target_position",
    "target_velocity",
    "target_torque",
    "acceleration_cmd",
    "deceleration_cmd",
    "regime_label",
    "risk_label",
    "is_artificial_anomaly",
    "load_type",
    "direction",
    "operator_comment",
    "software_version",
)

# Минимально обязательные колонки, которые валидатор проверяет особенно
# (см. требования: velocity, current_A, torque_Nm, dc_bus_voltage,
# drive_temp, statusword, error_code должны существовать).
TELEMETRY_REQUIRED_DATA_COLUMNS: tuple[str, ...] = (
    "velocity",
    "current_A",
    "torque_Nm",
    "dc_bus_voltage",
    "drive_temp",
    "statusword",
    "error_code",
)

# ---------------------------------------------------------------------------
# Журнал экспериментов
# ---------------------------------------------------------------------------
EXPERIMENTS_COLUMNS: tuple[str, ...] = (
    "experiment_id",
    "session_id",
    "file_name",
    "date_start",
    "date_end",
    "operator",
    "drive_model",
    "motor_model",
    "experiment_type",
    "regime_label",
    "risk_label",
    "recording_purpose",
    "dataset_split",
    "load_type",
    "direction",
    "target_position",
    "target_velocity",
    "target_torque",
    "acceleration_cmd",
    "deceleration_cmd",
    "duration_s",
    "sample_rate_hz",
    "stop_condition",
    "session_status",
    "is_artificial_anomaly",
    "operator_comment",
)

# ---------------------------------------------------------------------------
# Паспорт стенда
# ---------------------------------------------------------------------------
STAND_PASSPORT_COLUMNS: tuple[str, ...] = (
    "stand_id",
    "date",
    "operator",
    "supervisor",
    "drive_model",
    "drive_serial",
    "motor_model",
    "motor_serial",
    "rated_current_A",
    "rated_torque_Nm",
    "max_motor_speed_rpm",
    "connection_type",
    "control_software",
    "control_mode",
    "load_setup",
    "emergency_stop",
    "allowed_speed_rpm",
    "allowed_acceleration",
    "allowed_deceleration",
    "allowed_loads",
    "notes",
)

# ---------------------------------------------------------------------------
# Разрешённые значения меток
# ---------------------------------------------------------------------------
ALLOWED_REGIME_LABELS: frozenset[str] = frozenset({
    "idle",
    "holding",
    "acceleration",
    "constant_speed",
    "deceleration",
    "reversal",
    "cyclic",
    "load_low",
    "load_medium",
    "synthetic_anomaly",
})

ALLOWED_RISK_LABELS: frozenset[str] = frozenset({
    "normal",
    "transition",
    "elevated_load",
    "pre_emergency_proxy",
    "fault",
})

# Допустимое значение для is_artificial_anomaly в CSV (как строка/число).
ALLOWED_IS_ARTIFICIAL_ANOMALY: frozenset[str] = frozenset({"0", "1"})


def header_line(columns: tuple[str, ...]) -> str:
    """Удобный helper: строка-заголовок CSV без перевода строки."""
    return ",".join(columns)
