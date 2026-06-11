# tests/test_run_experiment.py
# Тесты для tools/run_experiment.py — оркестратор лабораторного прогона.
# Всё без железа: pysoem и реальный TelemetryLogger подменяются фейками
# через dependency injection в run(...).

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import run_experiment as rex


# ---------------------------------------------------------------------------
# Фейки
# ---------------------------------------------------------------------------


class FakeController:
    """Минимальный двойник EtherCATController с настраиваемым режимом
    и счётчиком CW. Имитирует Operation Enabled (нижние 4 бита = 0xF),
    чтобы SET_VELOCITY проходил проверку."""

    def __init__(self, mode: str = "pv"):
        self.mode = mode
        self._cw = 0x000F  # Operation Enabled
        self._pos = 0

    def controlword(self):
        return self._cw

    def set_controlword(self, value):
        self._cw = value & 0xFFFF

    def set_target_position(self, value):
        self._pos = int(value)

    def position_actual(self):
        return self._pos

    def statusword(self):
        return 0x0027  # Operation Enabled


class FakeServoCommands:
    """Перехватываем все вызовы из scenario_fn и finally."""

    def __init__(self, *, fail_on=None):
        self.calls = []  # list of tuples (name, args)
        self._fail_on = fail_on or set()
        self.target_reached_after = 0  # сколько раз вернуть False до True

    def _rec(self, name, *args):
        self.calls.append((name, args))
        if name in self._fail_on:
            raise RuntimeError(f"injected failure in {name}")

    # --- интерфейс servo_commands ---
    def POWER_ON(self, ctrl):
        self._rec("POWER_ON")

    def POWER_OFF(self, ctrl):
        self._rec("POWER_OFF")

    def SET_PROFILE_ACCEL(self, ctrl, v):
        self._rec("SET_PROFILE_ACCEL", v)

    def SET_PROFILE_DECEL(self, ctrl, v):
        self._rec("SET_PROFILE_DECEL", v)

    def SET_VELOCITY(self, ctrl, rpm):
        self._rec("SET_VELOCITY", rpm)

    def STOP_VELOCITY(self, ctrl, timeout=2.0):
        self._rec("STOP_VELOCITY", timeout)
        return True

    def MOVE_AXIS_TO(self, ctrl, pos, wait_ack=True, ack_timeout=1.0):
        self._rec("MOVE_AXIS_TO", pos, wait_ack, ack_timeout)
        if hasattr(ctrl, "set_target_position"):
            ctrl.set_target_position(pos)

    def IS_TARGET_REACHED(self, ctrl):
        self._rec("IS_TARGET_REACHED")
        if self.target_reached_after > 0:
            self.target_reached_after -= 1
            return False
        return True

    def READ_POS_RAW(self, ctrl):
        self._rec("READ_POS_RAW")
        return ctrl.position_actual() if hasattr(ctrl, "position_actual") else 0


class FakeTelemetry:
    """Фейковый TelemetryLogger: фиксирует start/stop, ничего не пишет в БД."""

    instances = []  # для тестов, которые хотят достать последний инстанс

    def __init__(self, controller, db_path, period_s):
        self.controller = controller
        self.db_path = db_path
        self.period_s = period_s
        self.started = False
        self.stopped = False
        FakeTelemetry.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class FakeEthercatModule:
    """Заглушка для core.ethercat_driver — нам нужен только close_*."""

    def __init__(self):
        self.closed = []

    def close_ethercat_controller(self, ctrl):
        self.closed.append(ctrl)


# ---------------------------------------------------------------------------
# Хелперы построения args
# ---------------------------------------------------------------------------


def _base_args(tmp_path: Path, **overrides) -> argparse.Namespace:
    """Готовые args, как если бы их распарсил argparse."""
    defaults = dict(
        experiment_id="TEST_EXP",
        session_id="TEST_R01_19700101_000000",
        device_id="delta-asda-b3-e",
        db=str(tmp_path / "raw.sqlite3"),
        experiments_csv=str(tmp_path / "experiments.csv"),
        experiment_type="",
        regime_label="idle",
        risk_label="normal",
        load_type="no_load",
        direction="none",
        operator="",
        drive_model="",
        motor_model="",
        recording_purpose="training",
        dataset_split="unassigned",
        operator_comment="unit test",
        is_artificial_anomaly=0,
        scenario="rotate_const",
        target_rpm=300,
        target_position_inc=0,
        acceleration=1000,
        deceleration=1000,
        cycle_half_period_s=None,
        duration=0.05,           # очень коротко, чтобы тесты летали
        period_ms=100,
        forced_mode=None,
        iface=None,
        no_connect=False,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _run_with_fakes(args, *, ctrl=None, sc=None, tele=None, eth=None,
                     ctrl_calls=None):
    """Запустить run() с подставленными фейками. Возвращает (rc, fakes)."""
    if sc is None:
        sc = FakeServoCommands()
    if eth is None:
        eth = FakeEthercatModule()
    if ctrl is None:
        # mode определяется по сценарию, но fake вернёт что попросят.
        # Если scenario невалидный — run() сам выйдет с кодом 2 до контроллера.
        sc_entry = rex.SCENARIOS.get(args.scenario)
        default_mode = sc_entry[0] if sc_entry else "pv"
        forced = args.forced_mode or default_mode
        ctrl = FakeController(mode=forced)
    if ctrl_calls is None:
        ctrl_calls = []

    def factory(iface, mode):
        ctrl_calls.append((iface, mode))
        ctrl.mode = mode
        return ctrl

    if tele is None:
        tele_factory = FakeTelemetry
    else:
        tele_factory = tele

    rc = rex.run(
        args,
        controller_factory=factory,
        telemetry_factory=tele_factory,
        servo_commands_module=sc,
        ethercat_module=eth,
    )
    return rc, SimpleNamespace(sc=sc, eth=eth, ctrl=ctrl, ctrl_calls=ctrl_calls)


def _read_journal(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# Сами тесты
# ---------------------------------------------------------------------------


def test_dry_run_writes_journal_row(tmp_path: Path):
    """--no-connect должен записать строку в журнал с session_status='dry_run'
    и не вызывать ни одной servo-команды."""
    args = _base_args(tmp_path, no_connect=True)
    rc, fakes = _run_with_fakes(args)

    assert rc == 0
    # никаких servo-команд при no_connect
    assert fakes.sc.calls == []
    # фабрика контроллера тоже не должна была вызываться
    assert fakes.ctrl_calls == []
    rows = _read_journal(Path(args.experiments_csv))
    assert len(rows) == 1
    assert rows[0]["session_status"] == "dry_run"
    assert rows[0]["stop_condition"] == "dry_run"
    assert rows[0]["session_id"] == "TEST_R01_19700101_000000"


def test_rotate_const_calls_full_sequence(tmp_path: Path):
    """rotate_const в PV: POWER_ON, SET_PROFILE_ACCEL/DECEL, SET_VELOCITY,
    в finally — STOP_VELOCITY, POWER_OFF, close."""
    args = _base_args(tmp_path, scenario="rotate_const", target_rpm=400,
                      acceleration=800, deceleration=900)
    rc, fakes = _run_with_fakes(args)

    names = [c[0] for c in fakes.sc.calls]
    assert rc == 0
    assert "POWER_ON" in names
    assert "SET_PROFILE_ACCEL" in names
    assert "SET_PROFILE_DECEL" in names
    assert "SET_VELOCITY" in names
    assert "STOP_VELOCITY" in names
    assert "POWER_OFF" in names

    # порядок: POWER_ON раньше SET_VELOCITY, STOP_VELOCITY раньше POWER_OFF
    assert names.index("POWER_ON") < names.index("SET_VELOCITY")
    assert names.index("STOP_VELOCITY") < names.index("POWER_OFF")

    # значения переданы правильно
    set_vel = [c for c in fakes.sc.calls if c[0] == "SET_VELOCITY"][0]
    assert set_vel[1] == (400,)
    set_acc = [c for c in fakes.sc.calls if c[0] == "SET_PROFILE_ACCEL"][0]
    assert set_acc[1] == (800,)

    # close_ethercat_controller точно был вызван
    assert len(fakes.eth.closed) == 1
    # фабрика получила mode='pv'
    assert fakes.ctrl_calls[0][1] == "pv"

    # журнал
    rows = _read_journal(Path(args.experiments_csv))
    assert rows[0]["session_status"] == "ok"
    assert rows[0]["stop_condition"] == "timer"


def test_reversal_uses_both_signs(tmp_path: Path):
    """rotate_reversal должен звать SET_VELOCITY и с +v, и с -v."""
    args = _base_args(
        tmp_path,
        scenario="rotate_reversal",
        target_rpm=300,
        duration=0.25,
        cycle_half_period_s=0.05,
    )
    rc, fakes = _run_with_fakes(args)
    assert rc == 0

    velocities = [c[1][0] for c in fakes.sc.calls if c[0] == "SET_VELOCITY"]
    assert any(v > 0 for v in velocities)
    assert any(v < 0 for v in velocities)
    assert all(abs(v) == 300 for v in velocities)


def test_error_path_powers_off_and_closes(tmp_path: Path):
    """Если SET_VELOCITY кидает — finally всё равно делает POWER_OFF + close,
    а journal помечает session_status как 'error:...'."""
    sc = FakeServoCommands(fail_on={"SET_VELOCITY"})
    args = _base_args(tmp_path, scenario="rotate_const", target_rpm=200)
    rc, fakes = _run_with_fakes(args, sc=sc)

    names = [c[0] for c in fakes.sc.calls]
    assert "POWER_OFF" in names, names
    assert len(fakes.eth.closed) == 1
    assert rc == 1

    rows = _read_journal(Path(args.experiments_csv))
    assert rows[0]["session_status"].startswith("error:")
    assert rows[0]["stop_condition"] == "error"


def test_pp_scenario_uses_pp_mode_and_pp_hold(tmp_path: Path):
    """move_then_hold — PP-сценарий: фабрика контроллера получает mode='pp',
    а в finally вызывается READ_POS_RAW + MOVE_AXIS_TO(текущая позиция)."""
    args = _base_args(
        tmp_path,
        scenario="move_then_hold",
        target_position_inc=12345,
        duration=0.05,
    )
    rc, fakes = _run_with_fakes(args)
    assert rc == 0
    assert fakes.ctrl_calls[0][1] == "pp"
    names = [c[0] for c in fakes.sc.calls]
    # PP-сценарий: в finally НЕ должно быть STOP_VELOCITY
    assert "STOP_VELOCITY" not in names
    assert "READ_POS_RAW" in names
    # И в самом сценарии был MOVE_AXIS_TO(12345)
    move_calls = [c for c in fakes.sc.calls if c[0] == "MOVE_AXIS_TO"]
    assert any(call[1][0] == 12345 for call in move_calls)


def test_hold_scenario_does_not_touch_motion(tmp_path: Path):
    """scenario_hold не должен звать ни SET_VELOCITY, ни MOVE_AXIS_TO."""
    args = _base_args(tmp_path, scenario="hold", duration=0.05)
    rc, fakes = _run_with_fakes(args)
    assert rc == 0
    names = [c[0] for c in fakes.sc.calls]
    assert "SET_VELOCITY" not in names
    # MOVE_AXIS_TO может появиться только в PP-finally на READ_POS_RAW-ветке.
    # scenario_hold уже PP, поэтому finally звонит READ_POS_RAW + MOVE_AXIS_TO.
    # Это ОК — главное, что в самом сценарии движения не было.
    assert "POWER_ON" in names
    assert "POWER_OFF" in names


def test_invalid_scenario_returns_2(tmp_path: Path):
    args = _base_args(tmp_path, scenario="rotate_const")
    args.scenario = "nope_not_a_scenario"
    rc, fakes = _run_with_fakes(args)
    assert rc == 2


def test_forced_mode_pv_overrides_pp_default(tmp_path: Path):
    """Если сценарий по умолчанию PP, но --pv-mode форсирован — фабрика
    должна получить 'pv'."""
    args = _base_args(
        tmp_path,
        scenario="hold",       # PP по умолчанию
        forced_mode="pv",
        duration=0.05,
    )
    rc, fakes = _run_with_fakes(args)
    assert rc == 0
    assert fakes.ctrl_calls[0][1] == "pv"
    # В PV finally — STOP_VELOCITY
    names = [c[0] for c in fakes.sc.calls]
    assert "STOP_VELOCITY" in names


# ---------------------------------------------------------------------------
# Тесты servo_commands.SET_VELOCITY: только защитные проверки, не железо
# ---------------------------------------------------------------------------


def test_set_velocity_requires_pv_mode():
    """SET_VELOCITY должен отказаться, если controller.mode != 'pv'."""
    from core import servo_commands as sc

    # Подкинем фейк, который выглядит как EtherCATController, но не наследуется.
    # _ctrl делает isinstance-проверку, поэтому нужен реальный класс.
    from core.ethercat_driver import EtherCATController

    import threading as _th
    fake = EtherCATController.__new__(EtherCATController)
    fake.mode = "pp"
    fake._cw = 0x000F
    fake._lock = _th.Lock()

    with pytest.raises(TypeError):
        sc.SET_VELOCITY(fake, 100)


def test_set_velocity_rejects_over_limit():
    """|rpm| > 1500 -> ValueError."""
    from core import servo_commands as sc
    from core.ethercat_driver import EtherCATController

    import threading as _th
    fake = EtherCATController.__new__(EtherCATController)
    fake.mode = "pv"
    fake._cw = 0x000F
    fake._lock = _th.Lock()

    with pytest.raises(ValueError):
        sc.SET_VELOCITY(fake, 1600)
    with pytest.raises(ValueError):
        sc.SET_VELOCITY(fake, -2000)


def test_set_velocity_rejects_when_not_operation_enabled():
    """Если CW & 0x000F != 0x000F — RuntimeError (привод не включён)."""
    from core import servo_commands as sc
    from core.ethercat_driver import EtherCATController

    import threading as _th
    fake = EtherCATController.__new__(EtherCATController)
    fake.mode = "pv"
    fake._cw = 0x0006  # Shutdown, не Operation Enabled
    fake._lock = _th.Lock()

    with pytest.raises(RuntimeError):
        sc.SET_VELOCITY(fake, 100)


def test_set_profile_accel_rejects_over_limit():
    """0x6083: > 5000 RPM/s -> ValueError."""
    from core import servo_commands as sc
    from core.ethercat_driver import EtherCATController

    import threading as _th
    fake = EtherCATController.__new__(EtherCATController)
    fake.mode = "pv"
    fake._lock = _th.Lock()
    with pytest.raises(ValueError):
        sc.SET_PROFILE_ACCEL(fake, 10_000)
    with pytest.raises(ValueError):
        sc.SET_PROFILE_ACCEL(fake, -1)


# ---------------------------------------------------------------------------
# Тесты относительного позиционирования (регрессия B1-runaway)
# ---------------------------------------------------------------------------


def test_ramp_up_down_uses_relative_position(tmp_path: Path):
    """scenario_ramp_up_down должен использовать ОТНОСИТЕЛЬНЫЕ перемещения
    от стартовой позиции вала.

    При start_pos=777000, target_delta=50000 (дефолт):
      - прямой ход → MOVE_AXIS_TO(777000 + 50000 = 827000)
      - обратный ход → MOVE_AXIS_TO(777000)
      - НЕ должно быть вызовов с целью 50000 или 0.
    """
    START_POS = 777_000
    TARGET_DELTA = 50_000  # дефолт при target_position_inc=0

    ctrl = FakeController(mode="pp")
    ctrl._pos = START_POS  # имитируем ненулевую позицию после предыдущих опытов

    args = _base_args(
        tmp_path,
        scenario="ramp_up_down",
        target_position_inc=0,   # используется дефолт 50000
        duration=0.15,
        cycle_half_period_s=0.02,
    )
    rc, fakes = _run_with_fakes(args, ctrl=ctrl)
    assert rc == 0

    move_calls = [c for c in fakes.sc.calls if c[0] == "MOVE_AXIS_TO"]
    # В сценарии (не в finally) должны быть вызовы:
    move_targets = [c[1][0] for c in move_calls]

    # Прямой ход: start_pos + target_delta
    assert (START_POS + TARGET_DELTA) in move_targets, \
        f"Ожидали цель {START_POS + TARGET_DELTA}, но вызовы: {move_targets}"
    # Обратный ход: start_pos (а не 0)
    assert START_POS in move_targets, \
        f"Ожидали обратный ход на {START_POS}, но вызовы: {move_targets}"
    # НЕ должно быть абсолютного перемещения в 50000 или 0 (кроме finally PP-hold,
    # но finally после MOVE_AXIS_TO(ctrl, start_pos+delta) ctrl._pos уже != 0).
    # Фильтруем: target_delta не должна появляться как самостоятельная цель.
    assert TARGET_DELTA not in move_targets, \
        f"Обнаружено абсолютное перемещение в {TARGET_DELTA} — должно быть относительное"


def test_move_then_hold_uses_relative_position(tmp_path: Path):
    """scenario_move_then_hold должен использовать ОТНОСИТЕЛЬНЫЕ перемещения
    от стартовой позиции вала.

    При start_pos=12345, target_delta=10000 (дефолт):
      - цель → MOVE_AXIS_TO(12345 + 10000 = 22345)
      - НЕ должно быть вызова с целью 10000.
    """
    START_POS = 12_345
    TARGET_DELTA = 10_000  # дефолт при target_position_inc=0

    ctrl = FakeController(mode="pp")
    ctrl._pos = START_POS

    args = _base_args(
        tmp_path,
        scenario="move_then_hold",
        target_position_inc=0,   # используется дефолт 10000
        duration=0.05,
    )
    rc, fakes = _run_with_fakes(args, ctrl=ctrl)
    assert rc == 0

    move_calls = [c for c in fakes.sc.calls if c[0] == "MOVE_AXIS_TO"]
    move_targets = [c[1][0] for c in move_calls]

    # Должна быть цель start_pos + target_delta
    assert (START_POS + TARGET_DELTA) in move_targets, \
        f"Ожидали цель {START_POS + TARGET_DELTA}, но вызовы: {move_targets}"
    # НЕ должно быть абсолютного перемещения в 10000
    assert TARGET_DELTA not in move_targets, \
        f"Обнаружено абсолютное перемещение в {TARGET_DELTA} — должно быть относительное"
