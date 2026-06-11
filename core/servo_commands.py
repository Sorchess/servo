# core/servo_commands.py
# Высокоуровневые команды привода Delta ASDA-B3-E (CiA 402, PP режим).
# Работают через core.ethercat_driver.EtherCATController, у которого запущен
# фоновый PDO-поток. Controlword и Target Position идут через PDO-буфер,
# всё остальное — через SDO.

import time
import struct

from core import ethercat_driver
from utils import config


# --- Controlword маски ---
CW_SHUTDOWN           = 0x0006
CW_SWITCH_ON          = 0x0007
CW_ENABLE_OPERATION   = 0x000F
CW_FAULT_RESET        = 0x0080
CW_DISABLE_VOLTAGE    = 0x0000
# PP режим: bit4 = new setpoint, bit5 = change set immediately, bit6 = relative
CW_NEW_SETPOINT_ABS   = 0x001F   # 0x000F | bit4 (rising edge)


# --- Statusword ---
SW_STATE_MASK         = 0x006F
SW_READY_TO_SWITCH    = 0x0021
SW_SWITCHED_ON        = 0x0023
SW_OPERATION_ENABLED  = 0x0027
SW_FAULT_STATE        = 0x0008
SW_FAULT_BIT          = 1 << 3
SW_SETPOINT_ACK       = 1 << 12
SW_TARGET_REACHED     = 1 << 10


def _ctrl(controller):
    # Разрешаем передавать либо EtherCATController, либо «сырой» master —
    # но все команды, зависящие от PDO, требуют EtherCATController.
    if isinstance(controller, ethercat_driver.EtherCATController):
        return controller
    raise TypeError(
        "servo_commands ожидают EtherCATController "
        "(используйте setup_ethercat_controller)"
    )


def _wait_state(ctrl, expected, timeout=2.0, period=0.01):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if (ctrl.statusword() & SW_STATE_MASK) == expected:
            return ctrl.statusword()
        time.sleep(period)
    return ctrl.statusword()


def POWER_ON(controller):
    """Перевод привода в Operation Enabled по CiA 402."""
    ctrl = _ctrl(controller)
    sw = ctrl.statusword()

    # Сброс ошибок, если есть
    if (sw & SW_FAULT_BIT) and (sw & SW_STATE_MASK) == SW_FAULT_STATE:
        ctrl.set_controlword(CW_FAULT_RESET)
        time.sleep(0.1)
        ctrl.set_controlword(0x0000)
        time.sleep(0.1)

    ctrl.set_controlword(CW_SHUTDOWN)
    sw = _wait_state(ctrl, SW_READY_TO_SWITCH, 1.5)

    ctrl.set_controlword(CW_SWITCH_ON)
    sw = _wait_state(ctrl, SW_SWITCHED_ON, 1.5)

    ctrl.set_controlword(CW_ENABLE_OPERATION)
    sw = _wait_state(ctrl, SW_OPERATION_ENABLED, 1.5)

    if (sw & SW_STATE_MASK) != SW_OPERATION_ENABLED:
        raise RuntimeError(f"POWER_ON failed, SW=0x{sw:04X}")
    return True


def POWER_OFF(controller):
    """Перевод в Ready-to-switch-on (мотор обесточен, но связь жива)."""
    ctrl = _ctrl(controller)
    ctrl.set_controlword(CW_SHUTDOWN)
    _wait_state(ctrl, SW_READY_TO_SWITCH, 1.5)


def DISABLE_MOVE_AXIS(controller):
    """Сбросить бит 4 Controlword (new setpoint) — готов к следующей команде."""
    ctrl = _ctrl(controller)
    ctrl.set_controlword(CW_ENABLE_OPERATION)


def ENABLE_MOVE_AXIS(controller):
    """Выставить бит 4 Controlword — защёлкнуть записанный Target Position."""
    ctrl = _ctrl(controller)
    ctrl.set_controlword(CW_NEW_SETPOINT_ABS)


def MOVE_AXIS_TO(controller, value, wait_ack=True, ack_timeout=1.0):
    """Команда абсолютного движения в позицию `value` (инкременты).

    Логика (Profile Position + handshake):
      1. Пишем Target Position (0x607A) в выходной PDO.
      2. Сбрасываем бит 4 Controlword (0x000F).
      3. Выставляем бит 4 (0x002F — new setpoint + change set immediately).
      4. Ждём Setpoint Acknowledge (Statusword бит 12).
      5. Сбрасываем бит 4 обратно, чтобы можно было отправить следующую цель.
    """
    ctrl = _ctrl(controller)
    print(f"[MOVE_AXIS_TO] target position: {value}")

    # 1. Target Position
    ctrl.set_target_position(value)

    # 2. bit4 = 0
    ctrl.set_controlword(CW_ENABLE_OPERATION)
    time.sleep(0.01)

    # 3. bit4 = 1 (rising edge)
    ctrl.set_controlword(CW_NEW_SETPOINT_ABS)

    # 4. ждём ACK
    if wait_ack:
        t0 = time.time()
        while time.time() - t0 < ack_timeout:
            if ctrl.statusword() & SW_SETPOINT_ACK:
                break
            time.sleep(0.005)

    # 5. снимаем бит 4 — готовы к следующему setpoint'у
    ctrl.set_controlword(CW_ENABLE_OPERATION)


def IS_TARGET_REACHED(controller):
    ctrl = _ctrl(controller)
    return bool(ctrl.statusword() & SW_TARGET_REACHED)


def READ_POS_RAW(controller):
    """Текущая позиция в инкрементах. Читаем из PDO, если доступен."""
    if isinstance(controller, ethercat_driver.EtherCATController):
        return controller.position_actual()
    # fallback через SDO
    return ethercat_driver.read_dint_variable(
        controller, config.SLAVE_INDEX, config.COMMAND_POS_ADDR, config.SUBINDEX
    )


def READ_POS_SCALE(controller):
    return int(READ_POS_RAW(controller) / config.PRECESION_SCALER)


# ===========================================================================
# Profile Velocity (PV) — для лабораторных сценариев из tools/run_experiment.py
# ===========================================================================
#
# В PV-режиме PDO-маппинг тот же, что и для PP (CW(2) + TargetPosition(4)).
# TargetVelocity (0x60FF) пишется напрямую через SDO с частотой 10–50 Гц
# из оркестратора. Это решение принято осознанно: не делаем рискованный
# динамический PDO-remapping на ASDA-B3.
#
# Ограничения безопасности (жёсткие):
#   * |target_rpm| ≤ MAX_TARGET_RPM (1500)
#   * |acc/dec|   ≤ MAX_PROFILE_ACCEL (5000 RPM/s)
#   * SET_VELOCITY работает только при mode == 'pv' и Operation Enabled.
#

MAX_TARGET_RPM    = 1500
MAX_PROFILE_ACCEL = 5000   # RPM/s, для 0x6083 / 0x6084
STOP_VELOCITY_EPS = 5      # rpm: считаем "достигли нуля", если |v| < этого


def _require_pv(ctrl):
    if getattr(ctrl, "mode", "pp") != "pv":
        raise TypeError(
            "SET_VELOCITY / STOP_VELOCITY работают только в PV-режиме. "
            "Используйте setup_ethercat_controller(ifname, mode='pv')."
        )


def _read_velocity_actual_rpm(ctrl):
    """Прочитать текущую скорость (0x606C, int32 inc/s или rpm в зависимости
    от настроек привода). Возвращаем как есть в "единицах привода" — на
    ASDA-B3-E это inc/s; для контроля останова достаточно сравнения с порогом
    в этих же единицах. STOP_VELOCITY_EPS подобран небольшим, чтобы работало
    в обоих случаях."""
    raw = ctrl.slave.sdo_read(0x606C, 0)
    return int.from_bytes(raw, byteorder='little', signed=True)


def SET_VELOCITY(controller, rpm):
    """Записать TargetVelocity (0x60FF) через SDO.

    Знак RPM = направление вращения. Проверки:
      * controller.mode == 'pv';
      * |rpm| ≤ MAX_TARGET_RPM;
      * Controlword содержит CW_ENABLE_OPERATION (привод включён).
    """
    ctrl = _ctrl(controller)
    _require_pv(ctrl)

    rpm = int(rpm)
    if abs(rpm) > MAX_TARGET_RPM:
        raise ValueError(
            f"SET_VELOCITY: |rpm|={abs(rpm)} превышает лимит {MAX_TARGET_RPM}"
        )

    cw = ctrl.controlword()
    # CW_ENABLE_OPERATION = 0x000F — младшие 4 бита должны быть выставлены.
    if (cw & 0x000F) != 0x000F:
        raise RuntimeError(
            f"SET_VELOCITY: привод не в Operation Enabled "
            f"(CW=0x{cw:04X}). Сначала POWER_ON."
        )

    ctrl.write_sdo_target_velocity(rpm)


def STOP_VELOCITY(controller, timeout=2.0, poll_period=0.05):
    """TargetVelocity ← 0 и ждать, пока |velocity_actual| < STOP_VELOCITY_EPS.

    Если за timeout не дошло до нуля — лог WARNING, но управление всё равно
    возвращается (чтобы finally в оркестраторе успел сделать POWER_OFF).
    """
    ctrl = _ctrl(controller)
    _require_pv(ctrl)

    try:
        ctrl.write_sdo_target_velocity(0)
    except Exception as e:  # noqa: BLE001
        print(f"[STOP_VELOCITY] warn: не удалось обнулить TargetVelocity: {e}")
        return False

    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            v = _read_velocity_actual_rpm(ctrl)
        except Exception as e:  # noqa: BLE001
            print(f"[STOP_VELOCITY] warn: чтение 0x606C: {e}")
            return False
        if abs(v) < STOP_VELOCITY_EPS:
            return True
        time.sleep(poll_period)

    try:
        v_final = _read_velocity_actual_rpm(ctrl)
    except Exception:  # noqa: BLE001
        v_final = "?"
    print(
        f"[STOP_VELOCITY] WARNING: за {timeout:.1f}s не дошли до 0 "
        f"(v={v_final}); продолжаем (finally сделает POWER_OFF)."
    )
    return False


def _set_profile_param(ctrl, idx, name, rpm_per_s):
    val = int(rpm_per_s)
    if val < 0:
        raise ValueError(f"{name}: значение должно быть >= 0, got {val}")
    if val > MAX_PROFILE_ACCEL:
        raise ValueError(
            f"{name}: {val} превышает лимит {MAX_PROFILE_ACCEL} RPM/s"
        )
    # CiA-стандарт: 0x6083 / 0x6084 — unsigned 32-bit, единицы по умолчанию
    # совпадают с единицами velocity (на ASDA-B3-E это RPM/s, см. ТЗ).
    data = struct.pack('<I', val)
    ctrl.slave.sdo_write(idx, 0, data)


def SET_PROFILE_ACCEL(controller, rpm_per_s):
    """Profile acceleration (0x6083) в RPM/s, проверка ≤ MAX_PROFILE_ACCEL."""
    ctrl = _ctrl(controller)
    _set_profile_param(ctrl, 0x6083, "SET_PROFILE_ACCEL", rpm_per_s)


def SET_PROFILE_DECEL(controller, rpm_per_s):
    """Profile deceleration (0x6084) в RPM/s, проверка ≤ MAX_PROFILE_ACCEL."""
    ctrl = _ctrl(controller)
    _set_profile_param(ctrl, 0x6084, "SET_PROFILE_DECEL", rpm_per_s)
