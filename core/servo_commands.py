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
