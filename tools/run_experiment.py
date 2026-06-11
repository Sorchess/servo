# tools/run_experiment.py
# CLI-оркестратор лабораторного эксперимента: сам поднимает Servo ON,
# сам выполняет требуемое движение, параллельно пишет телеметрию,
# в конце опускает Servo OFF и закрывает EtherCAT-адаптер.
#
# В отличие от tools/start_lab_session.py (пассивный логгер), этот скрипт
# берёт на себя управление приводом. Это нужно, чтобы пресеты A1..E2
# работали end-to-end автоматически и без рассинхрона между оператором и CLI.
#
# Безопасность:
#   * |target_rpm| <= 1500, |acc/dec| <= 5000 RPM/s (проверки в servo_commands).
#   * любое исключение → finally: STOP_VELOCITY (или PP-hold) + POWER_OFF
#     + close_ethercat_controller + строка в experiments.csv с session_status.
#   * SIGINT (Ctrl+C) → graceful STOP+POWER_OFF, session_status='interrupted'.
#   * --no-connect → не трогает контроллер, пишет строку с session_status='dry_run'.
#
# Запускать через lab.bat (см. команду `lab auto`) или напрямую:
#   .venv\Scripts\python.exe -m tools.run_experiment \
#     --scenario rotate_const --target-rpm 300 --duration 60 \
#     --experiment-id A3_ROTATE_ONE_DIR --session-id ... --db ...

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from core.csv_schema import ALLOWED_REGIME_LABELS, ALLOWED_RISK_LABELS
from core.lab_session import LabSessionMetadata, append_experiment


# ----------------------------------------------------------------------------
# Сценарии. Каждая функция получает:
#   ctrl       — EtherCATController (или None в dry-run)
#   stop_flag  — threading.Event, ставится по SIGINT и по таймауту
#   args       — argparse.Namespace
#   sc         — модуль core.servo_commands (передаём, чтобы в dry-run не импортировать)
# Возвращают tuple (stop_condition: str). Завершение по таймеру -> 'timer',
# по SIGINT -> 'operator_interrupt', по нештатному -> 'error'.
# ----------------------------------------------------------------------------


def _sleep_until(deadline: float, stop_flag: threading.Event, step: float = 0.1) -> bool:
    """Спать порциями, проверяя stop_flag. True — дошли до дедлайна, False — прерваны."""
    while True:
        now = time.time()
        if now >= deadline:
            return True
        if stop_flag.is_set():
            return False
        time.sleep(min(step, deadline - now))


def scenario_hold(ctrl, stop_flag, args, sc) -> None:
    """A1, статичный hold: просто ждать duration с Servo ON."""
    _sleep_until(time.time() + args.duration, stop_flag)


def scenario_rotate_const(ctrl, stop_flag, args, sc) -> None:
    """A3, C1..C5, E1, E2: SET_VELOCITY(target_rpm), sleep(duration), STOP."""
    if ctrl is not None:
        sc.SET_VELOCITY(ctrl, args.target_rpm)
    _sleep_until(time.time() + args.duration, stop_flag)


def scenario_rotate_reversal(ctrl, stop_flag, args, sc) -> None:
    """A4, B4: цикл +v / -v c полупериодом half_period."""
    half = args.cycle_half_period_s or 5.0
    deadline = time.time() + args.duration
    sign = +1
    while time.time() < deadline and not stop_flag.is_set():
        if ctrl is not None:
            sc.SET_VELOCITY(ctrl, sign * args.target_rpm)
        # Спим до конца полупериода или до общего дедлайна.
        end_half = min(time.time() + half, deadline)
        if not _sleep_until(end_half, stop_flag):
            return
        sign = -sign


def scenario_ramp_up_down(ctrl, stop_flag, args, sc) -> None:
    """B1..B3: PP — MOVE_AXIS_TO(+P) / wait TR / MOVE_AXIS_TO(0) / wait TR.

    Acc/dec уже выставлены оркестратором через SDO до старта (для PV);
    в PP-режиме они применяются приводом из тех же 0x6083/0x6084.
    """
    target_pos = args.target_position_inc or 50000
    deadline = time.time() + args.duration
    going_out = True
    while time.time() < deadline and not stop_flag.is_set():
        if ctrl is not None:
            sc.MOVE_AXIS_TO(ctrl, target_pos if going_out else 0,
                            wait_ack=True, ack_timeout=1.0)
        # Ждём Target Reached или таймаут полупериода.
        wait_until = min(time.time() + (args.cycle_half_period_s or 5.0), deadline)
        while time.time() < wait_until and not stop_flag.is_set():
            if ctrl is not None and sc.IS_TARGET_REACHED(ctrl):
                break
            time.sleep(0.05)
        going_out = not going_out


def scenario_cyclic(ctrl, stop_flag, args, sc) -> None:
    """B5, D1..D3: то же что reversal, но с большим half_period."""
    # Логически идентично reversal — дефолт half побольше.
    if args.cycle_half_period_s is None:
        args.cycle_half_period_s = 10.0
    scenario_rotate_reversal(ctrl, stop_flag, args, sc)


def scenario_move_then_hold(ctrl, stop_flag, args, sc) -> None:
    """A2: PP MOVE_AXIS_TO(+N), wait TR, sleep(remaining duration)."""
    target_pos = args.target_position_inc or 10000
    t0 = time.time()
    if ctrl is not None:
        sc.MOVE_AXIS_TO(ctrl, target_pos, wait_ack=True, ack_timeout=2.0)
        # Ждём Target Reached, но не дольше 5 с — потом всё равно начинаем hold.
        tr_deadline = min(time.time() + 5.0, t0 + args.duration)
        while time.time() < tr_deadline and not stop_flag.is_set():
            if sc.IS_TARGET_REACHED(ctrl):
                break
            time.sleep(0.05)
    # Оставшееся время — hold.
    _sleep_until(t0 + args.duration, stop_flag)


SCENARIOS = {
    "hold":              ("pp", scenario_hold),
    "rotate_const":      ("pv", scenario_rotate_const),
    "rotate_reversal":   ("pv", scenario_rotate_reversal),
    "ramp_up_down":      ("pp", scenario_ramp_up_down),
    "cyclic":            ("pv", scenario_cyclic),
    "move_then_hold":    ("pp", scenario_move_then_hold),
}


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_experiment",
        description="Автоматический лабораторный прогон (Servo ON + движение + "
                    "телеметрия + Servo OFF).",
    )
    # --- идентификаторы / журнал ---
    p.add_argument("--experiment-id", required=True)
    p.add_argument("--session-id", required=True)
    p.add_argument("--device-id", default="delta-asda-b3-e")
    p.add_argument("--db", required=True)
    p.add_argument("--experiments-csv", default="lab_data/experiments.csv")
    # --- разметка ---
    p.add_argument("--experiment-type", default="")
    p.add_argument("--regime-label", default="idle",
                   choices=sorted(ALLOWED_REGIME_LABELS))
    p.add_argument("--risk-label", default="normal",
                   choices=sorted(ALLOWED_RISK_LABELS))
    p.add_argument("--load-type", default="no_load")
    p.add_argument("--direction", default="none")
    p.add_argument("--operator", default="")
    p.add_argument("--drive-model", default="")
    p.add_argument("--motor-model", default="")
    p.add_argument("--recording-purpose", default="training")
    p.add_argument("--dataset-split", default="unassigned")
    p.add_argument("--operator-comment", default="")
    p.add_argument("--is-artificial-anomaly", type=int, default=0, choices=(0, 1))
    # --- параметры движения ---
    p.add_argument("--scenario", required=True, choices=sorted(SCENARIOS.keys()))
    p.add_argument("--target-rpm", type=int, default=0,
                   help="Celevaya skorost, RPM. |v| <= 1500.")
    p.add_argument("--target-position-inc", type=int, default=0,
                   help="Celevaya poziciya v inkrementah dlya PP-scenariev.")
    p.add_argument("--acceleration", type=int, default=1000,
                   help="Profile acceleration, RPM/s (<= 5000).")
    p.add_argument("--deceleration", type=int, default=1000,
                   help="Profile deceleration, RPM/s (<= 5000).")
    p.add_argument("--cycle-half-period-s", type=float, default=None,
                   help="Полупериод реверса/циклики, секунды.")
    # --- режим / таймер ---
    p.add_argument("--duration", type=float, required=True)
    p.add_argument("--period-ms", type=int, default=100)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--pv-mode", dest="forced_mode", action="store_const",
                      const="pv", default=None)
    mode.add_argument("--pp-mode", dest="forced_mode", action="store_const",
                      const="pp", default=None)
    p.add_argument("--iface", default=None,
                   help="EtherCAT интерфейс. По умолчанию из utils.config.")
    p.add_argument("--no-connect", action="store_true",
                   help="Dry-run без обращения к адаптеру: только строка в журнал.")
    return p


def _meta_from_args(args: argparse.Namespace) -> LabSessionMetadata:
    return LabSessionMetadata(
        experiment_id=args.experiment_id,
        session_id=args.session_id,
        device_id=args.device_id,
        experiment_type=args.experiment_type or args.scenario,
        regime_label=args.regime_label,
        risk_label=args.risk_label,
        load_type=args.load_type,
        direction=args.direction,
        target_position=str(args.target_position_inc) if args.target_position_inc else "",
        target_velocity=str(args.target_rpm) if args.target_rpm else "",
        acceleration_cmd=str(args.acceleration),
        deceleration_cmd=str(args.deceleration),
        operator_comment=args.operator_comment,
        is_artificial_anomaly=int(args.is_artificial_anomaly),
    )


def _write_journal(args, meta, *, duration_actual, stop_condition,
                   session_status, date_start, date_end):
    db_path = Path(args.db)
    append_experiment(
        args.experiments_csv,
        meta,
        file_name=db_path.name,
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


# ----------------------------------------------------------------------------
# Главный конвейер. Вынесен в отдельную функцию для тестируемости.
# ----------------------------------------------------------------------------

def run(args: argparse.Namespace, *,
        controller_factory=None,
        telemetry_factory=None,
        servo_commands_module=None,
        ethercat_module=None) -> int:
    """Запустить эксперимент. Все внешние зависимости можно подменить
    (для тестов без железа).

    controller_factory(iface, mode)        -> ctrl
    telemetry_factory(ctrl, db_path, period_s) -> logger with .start/.stop
    servo_commands_module                  -> модуль с POWER_ON/.../SET_VELOCITY
    ethercat_module                        -> модуль с close_ethercat_controller
    """
    meta = _meta_from_args(args)
    meta.require_valid()

    if args.scenario not in SCENARIOS:
        print(f"[run_experiment] unknown scenario: {args.scenario}")
        return 2
    default_mode, scenario_fn = SCENARIOS[args.scenario]
    mode = args.forced_mode or default_mode

    date_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ----- dry-run: ничего не трогаем, пишем строку и выходим -----
    if args.no_connect:
        print("[run_experiment] --no-connect: dry-run, контроллер не открывается")
        _write_journal(
            args, meta,
            duration_actual=0.0,
            stop_condition="dry_run",
            session_status="dry_run",
            date_start=date_start,
            date_end=date_start,
        )
        return 0

    # ----- реальный запуск -----
    if servo_commands_module is None:
        from core import servo_commands as servo_commands_module  # type: ignore
    if ethercat_module is None:
        from core import ethercat_driver as ethercat_module  # type: ignore
    if controller_factory is None:
        controller_factory = ethercat_module.setup_ethercat_controller
    if telemetry_factory is None:
        from core.telemetry import TelemetryLogger
        telemetry_factory = TelemetryLogger

    from utils.config import ETH_INTERFACE
    iface = args.iface or ETH_INTERFACE

    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    sc = servo_commands_module
    stop_flag = threading.Event()
    stop_condition = "timer"
    session_status = "ok"

    # SIGINT handler: только ставит флаг; реальная остановка — в основном потоке.
    def _sigint(_signo, _frame):
        nonlocal stop_condition, session_status
        if not stop_flag.is_set():
            stop_condition = "operator_interrupt"
            session_status = "interrupted"
            print("[run_experiment] SIGINT: завершаю эксперимент…")
        stop_flag.set()

    prev_handler = signal.signal(signal.SIGINT, _sigint)

    ctrl = None
    logger = None
    t_started = time.time()
    try:
        print(f"[run_experiment] connecting via {iface!r} (mode={mode})")
        ctrl = controller_factory(iface, mode=mode)

        print("[run_experiment] POWER_ON …")
        sc.POWER_ON(ctrl)

        # В PV-режиме явно прописываем acc/dec перед движением.
        if mode == "pv":
            sc.SET_PROFILE_ACCEL(ctrl, args.acceleration)
            sc.SET_PROFILE_DECEL(ctrl, args.deceleration)

        logger = telemetry_factory(ctrl, db_path=str(db_path),
                                   period_s=args.period_ms / 1000.0)
        logger.start()
        print(f"[run_experiment] recording for {args.duration:.1f}s -> {db_path}")

        # Запускаем сценарий. Он сам опрашивает stop_flag.
        scenario_fn(ctrl, stop_flag, args, sc)

    except KeyboardInterrupt:
        # На случай, если SIGINT прилетел до установки handler-а.
        stop_condition = "operator_interrupt"
        session_status = "interrupted"
        print("[run_experiment] KeyboardInterrupt: завершаю эксперимент…")
    except Exception as e:  # noqa: BLE001
        stop_condition = stop_condition if stop_condition != "timer" else "error"
        session_status = f"error:{e!r}"
        print(f"[run_experiment] ОШИБКА: {e!r}")
    finally:
        # ---- мягкая остановка движения ----
        if ctrl is not None:
            try:
                if mode == "pv":
                    sc.STOP_VELOCITY(ctrl, timeout=2.0)
                else:
                    try:
                        pos = sc.READ_POS_RAW(ctrl)
                        sc.MOVE_AXIS_TO(ctrl, pos, wait_ack=False)
                    except Exception as e:  # noqa: BLE001
                        print(f"[run_experiment] PP hold warn: {e}")
            except Exception as e:  # noqa: BLE001
                print(f"[run_experiment] STOP warn: {e}")
            # ---- POWER_OFF ----
            try:
                sc.POWER_OFF(ctrl)
            except Exception as e:  # noqa: BLE001
                print(f"[run_experiment] POWER_OFF warn: {e}")
        # ---- остановить логгер ----
        if logger is not None:
            try:
                logger.stop()
            except Exception as e:  # noqa: BLE001
                print(f"[run_experiment] logger.stop warn: {e}")
        # ---- закрыть EtherCAT ----
        if ctrl is not None:
            try:
                ethercat_module.close_ethercat_controller(ctrl)
            except Exception as e:  # noqa: BLE001
                print(f"[run_experiment] close warn: {e}")

        # вернуть прежний SIGINT-handler
        try:
            signal.signal(signal.SIGINT, prev_handler)
        except Exception:  # noqa: BLE001
            pass

    duration_actual = time.time() - t_started
    date_end = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _write_journal(
        args, meta,
        duration_actual=duration_actual,
        stop_condition=stop_condition,
        session_status=session_status,
        date_start=date_start,
        date_end=date_end,
    )
    print(f"[run_experiment] done: status={session_status} stop={stop_condition} "
          f"duration={duration_actual:.1f}s")
    # Код возврата: 0 если всё ок или штатное прерывание, 1 если ошибка.
    return 0 if session_status in ("ok", "interrupted", "dry_run") else 1


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
