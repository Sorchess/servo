# core/servo_commands.py
from core import ethercat_driver
from utils import config

def POWER_ON(master):
    """Включение привода"""
    ethercat_driver.write_variable(
        master,
        config.SLAVE_INDEX,
        config.COMMAND_POWER_ADDR,
        config.SUBINDEX,
        1
    )

def POWER_OFF(master):
    """Выключение привода"""
    ethercat_driver.write_variable(
        master,
        config.SLAVE_INDEX,
        config.COMMAND_POWER_ADDR,
        config.SUBINDEX,
        0
    )

def ENABLE_MOVE_AXIS(master):
    """Пример команды движения оси"""
    ethercat_driver.write_variable(
        master,
        config.SLAVE_INDEX,
        config.COMMAND_MOVE_EN_ADDR,
        config.SUBINDEX,
        1
    )


def MOVE_AXIS_TO(master, value):
    """Пример команды движения оси"""
    print(value)
    ethercat_driver.write_variable(
        master,
        config.SLAVE_INDEX,
        config.COMMAND_MOVE_POS_ADDR,
        config.SUBINDEX,
        value
    )
    ENABLE_MOVE_AXIS(master)

def READ_POS_RAW(master):
        raw_pos = ethercat_driver.read_dint_variable(master, config.SLAVE_INDEX,config.COMMAND_POS_ADDR,config.SUBINDEX)
        return raw_pos


def READ_POS_SCALE(master):
    scale_pos = int(ethercat_driver.read_dint_variable(master, config.SLAVE_INDEX, config.COMMAND_POS_ADDR, config.SUBINDEX)/config.PRECESION_SCALER)
    return scale_pos
