# tools/start_lab_session.py
# CLI: запустить лабораторную запись телеметрии.
#
# Алгоритм:
#   1. Считать параметры эксперимента, собрать LabSessionMetadata.
#   2. Подключиться к приводу (только чтение через TelemetryLogger, никаких
#      команд движения; POWER_ON/POWER_OFF этим инструментом не делается).
#   3. Запустить TelemetryLogger на --duration секунд.
#   4. После остановки добавить запись в experiments.csv (журнал).
#
# ВАЖНО: инструмент НЕ управляет приводом. Оператор сам решает, в каком
# режиме (servo on/off, удержание, вращение и т.п.) находится стенд
# в момент записи. Это сделано осознанно: автоматические опасные сценарии
# движения из CLI запрещены требованиями ТЗ.
#
# Пример:
#   python -m tools.start_lab_session \
#     --experiment-id A1_HOLDING_SERVO_ON \
#     --session-id A1_R01_20260612_103000 \
#     --duration 120 \
#     --db lab_data/raw/A1_R01_20260612_103000.sqlite3 \
#     --experiment-type holding \
#     --regime-label holding \
#     --risk-label normal \
#     --load-type no_load \
#     --direction none \
#     --target-velocity 0 \
#     --operator-comment "Servo ON, shaft stationary"

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

from core.csv_schema import ALLOWED_REGIME_LABELS, ALLOWED_RISK_LABELS
from core.lab_session import LabSessionMetadata, append_experiment


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="start_lab_session",
        description="Запуск лабораторной записи телеметрии в SQLite.",
    )
    # --- идентификаторы ---
    p.add_argument("--experiment-id", required=True)
    p.add_argument("--session-id", required=True)
    p.add_argument("--device-id", default="delta-asda-b3-e")
    # --- хранилище ---
    p.add_argument("--db", required=True, help="Путь к SQLite-файлу записи.")
    p.add_argument("--experiments-csv", default="lab_data/experiments.csv",
                   help="Путь к журналу experiments.csv (создаётся при отсутствии).")
    # --- режим/таймер ---
    p.add_argument("--duration", type=float, required=True, help="Сек.")
    p.add_argument("--period-ms", type=int, default=100,
                   help="Период опроса в мс (по умолчанию 100 = ~10 Гц).")
    # --- разметка ---
    p.add_argument("--experiment-type", default="",
                   help="holding / acceleration / cyclic / ...")
    p.add_argument("--regime-label", default="idle",
                   choices=sorted(ALLOWED_REGIME_LABELS))
    p.add_argument("--risk-label", default="normal",
                   choices=sorted(ALLOWED_RISK_LABELS))
    p.add_argument("--load-type", default="no_load")
    p.add_argument("--direction", default="none",
                   help="cw / ccw / both / none")
    # --- команды (для журнала) ---
    p.add_argument("--target-position", default="")
    p.add_argument("--target-velocity", default="")
    p.add_argument("--target-torque", default="")
    p.add_argument("--acceleration-cmd", default="")
    p.add_argument("--deceleration-cmd", default="")
    # --- разное ---
    p.add_argument("--operator", default="")
    p.add_argument("--drive-model", default="")
    p.add_argument("--motor-model", default="")
    p.add_argument("--recording-purpose", default="training")
    p.add_argument("--dataset-split", default="unassigned")
    p.add_argument("--operator-comment", default="")
    p.add_argument("--is-artificial-anomaly", type=int, default=0, choices=(0, 1))
    p.add_argument("--iface", default=None,
                   help="EtherCAT интерфейс. По умолчанию из utils.config.")
    p.add_argument("--no-connect", action="store_true",
                   help="Только подготовить метаданные/журнал, не подключаться к приводу.")
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
    meta.require_valid()

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    date_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if args.no_connect:
        print("[start_lab_session] --no-connect: только записываю строку "
              "в experiments.csv без реальной записи телеметрии")
        append_experiment(
            args.experiments_csv,
            meta,
            file_name=str(db_path.name),
            date_start=date_start,
            date_end=date_start,
            operator=args.operator,
            drive_model=args.drive_model,
            motor_model=args.motor_model,
            recording_purpose=args.recording_purpose,
            dataset_split=args.dataset_split,
            duration_s=0,
            sample_rate_hz=1000.0 / max(args.period_ms, 1),
            stop_condition="dry_run",
            session_status="dry_run",
        )
        return 0

    # ---- реальная запись ----
    from core import ethercat_driver
    from core.telemetry import TelemetryLogger
    from utils.config import ETH_INTERFACE

    iface = args.iface or ETH_INTERFACE
    print(f"[start_lab_session] connecting via {iface!r}")
    controller = ethercat_driver.setup_ethercat_controller(iface)

    logger = TelemetryLogger(
        controller,
        db_path=str(db_path),
        period_s=args.period_ms / 1000.0,
    )
    stop_condition = "timer"
    session_status = "ok"
    t_started = time.time()
    try:
        logger.start()
        print(f"[start_lab_session] recording for {args.duration:.1f}s -> {db_path}")
        try:
            time.sleep(args.duration)
        except KeyboardInterrupt:
            stop_condition = "operator_interrupt"
            session_status = "interrupted"
            print("[start_lab_session] interrupted by operator")
    except Exception as e:  # noqa: BLE001
        session_status = f"error:{e!r}"
        raise
    finally:
        try:
            logger.stop()
        except Exception as e:  # noqa: BLE001
            print(f"[start_lab_session] logger.stop error: {e}")
        try:
            ethercat_driver.close_ethercat_controller(controller)
        except Exception as e:  # noqa: BLE001
            print(f"[start_lab_session] close controller error: {e}")

    duration_actual = time.time() - t_started
    date_end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    append_experiment(
        args.experiments_csv,
        meta,
        file_name=str(db_path.name),
        date_start=date_start,
        date_end=date_end,
        operator=args.operator,
        drive_model=args.drive_model,
        motor_model=args.motor_model,
        recording_purpose=args.recording_purpose,
        dataset_split=args.dataset_split,
        duration_s=f"{duration_actual:.3f}",
        sample_rate_hz=1000.0 / max(args.period_ms, 1),
        stop_condition=stop_condition,
        session_status=session_status,
    )
    print(f"[start_lab_session] OK: db={db_path}, journal={args.experiments_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
