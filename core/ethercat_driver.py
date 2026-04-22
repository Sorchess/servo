###
### Программный модуль предназначен для связи с сервоприводом
### Delta ASDA-B3-E через EtherCAT (CoE) в режиме Profile Position (PP).
### Требование: на приводе P1-01 = 0x0B (CANopen mode).
###

import pysoem
import struct
import threading
import time

from utils import config


# ---- PDO layout (после config_map для ASDA-B3-E) -------------------------
# RxPDO (master -> slave), 6 байт: Controlword(2) | TargetPosition(4)
# TxPDO (slave -> master), 6 байт: Statusword(2)  | PositionActual(4)
RX_LEN = 6
TX_LEN = 6


class _PdoPump(threading.Thread):
    """Фоновый поток, поддерживающий циклический PDO-обмен."""

    def __init__(self, controller, period_s=0.002):
        super().__init__(daemon=True)
        self.ctrl = controller
        self.period = period_s
        self._stop = threading.Event()
        self.error = None

    def run(self):
        try:
            while not self._stop.is_set():
                with self.ctrl._lock:
                    self.ctrl._slave.output = bytes(self.ctrl._out)
                self.ctrl._master.send_processdata()
                self.ctrl._master.receive_processdata(2000)
                time.sleep(self.period)
        except Exception as e:
            self.error = e

    def stop(self):
        self._stop.set()


class EtherCATController:
    """Высокоуровневая обёртка над pysoem.Master с фоновым PDO-обменом.

    Хранит «желаемый» Controlword и Target Position в выходном буфере,
    читает Statusword и Position Actual из входного буфера.
    """

    MODE_PP = 1

    def __init__(self, master, slave_index=0):
        self._master = master
        self._slave = master.slaves[slave_index]
        self._out = bytearray(RX_LEN)         # CW=0, TargetPos=0
        self._lock = threading.Lock()
        self._pump = None
        self._cw = 0
        self._target = 0

    # -------- запись в выходной буфер --------
    def set_controlword(self, value):
        with self._lock:
            self._cw = value & 0xFFFF
            struct.pack_into('<H', self._out, 0, self._cw)

    def set_target_position(self, value):
        with self._lock:
            self._target = int(value)
            struct.pack_into('<i', self._out, 2, self._target)

    # -------- чтение входного буфера --------
    def statusword(self):
        return struct.unpack('<H', bytes(self._slave.input[0:2]))[0]

    def position_actual(self):
        return struct.unpack('<i', bytes(self._slave.input[2:6]))[0]

    # -------- старт / стоп фонового потока --------
    def start_pump(self, period_s=0.002):
        if self._pump is None:
            self._pump = _PdoPump(self, period_s)
            self._pump.start()

    def stop_pump(self):
        if self._pump is not None:
            self._pump.stop()
            self._pump.join(timeout=1.0)
            self._pump = None

    # -------- доступ для совместимости со старым кодом --------
    @property
    def master(self):
        return self._master

    @property
    def slave(self):
        return self._slave


def setup_ethercat_controller(ifname='eth0'):
    """Инициализация EtherCAT, переход в OP и запуск фонового PDO-потока.

    Возвращает экземпляр EtherCATController. Старый код, ожидавший master,
    может работать через .master — но запись CW/TargetPos должна идти
    через методы контроллера.
    """
    master = pysoem.Master()
    master.open(ifname)

    count = master.config_init()
    if count <= 0:
        raise RuntimeError("No slaves found")

    print(f"Slaves found: {count}")
    for i, slave in enumerate(master.slaves):
        print(f"[{i}] name={slave.name}, man={hex(slave.man)}, id={hex(slave.id)}")

    master.config_map()
    if master.state_check(pysoem.SAFEOP_STATE, 50_000) != pysoem.SAFEOP_STATE:
        master.read_state()
        for s in master.slaves:
            print(f"  AL: {hex(s.al_status)}")
        raise RuntimeError("Failed to reach SAFE-OP")

    slave = master.slaves[config.SLAVE_INDEX]
    if len(slave.output) != RX_LEN or len(slave.input) != TX_LEN:
        raise RuntimeError(
            f"Unexpected PDO layout: out={len(slave.output)} in={len(slave.input)}"
        )

    # Mode of Operation = PP (объект 0x6060 не входит в PDO -> SDO)
    slave.sdo_write(config.MODES_OF_OPERATION_ADDR, 0,
                    struct.pack('b', EtherCATController.MODE_PP))

    # Профильные параметры (тоже не в PDO)
    try:
        slave.sdo_write(0x6081, 0, struct.pack('<I', config.PROFILE_VELOCITY))
        slave.sdo_write(0x6083, 0, struct.pack('<I', config.PROFILE_ACCEL_MS))
        slave.sdo_write(0x6084, 0, struct.pack('<I', config.PROFILE_DECEL_MS))
    except Exception as e:
        print(f"  warn: profile params: {e}")

    controller = EtherCATController(master, config.SLAVE_INDEX)

    # Прайминг PDO-кадров до перехода в OP — нужно, чтобы slave увидел
    # валидный output в момент входа в OP (иначе словим AL.180 watchdog).
    for _ in range(20):
        slave.output = bytes(controller._out)
        master.send_processdata()
        master.receive_processdata(2000)
        time.sleep(0.002)

    master.state = pysoem.OP_STATE
    master.write_state()
    deadline = time.time() + 3.0
    while time.time() < deadline:
        slave.output = bytes(controller._out)
        master.send_processdata()
        master.receive_processdata(2000)
        if master.state_check(pysoem.OP_STATE, 50_000) == pysoem.OP_STATE:
            break
        master.write_state()
        time.sleep(0.002)

    master.read_state()
    if slave.state != pysoem.OP_STATE:
        raise RuntimeError(
            f"Failed to reach OP: slave.state={slave.state} AL={hex(slave.al_status)}"
        )

    controller.start_pump(period_s=0.002)
    print("EtherCAT OP reached, PDO pump started.")
    return controller


# ---- read/write SDO для всего, что не в PDO -----------------------------

def read_dint_variable(controller_or_master, slave_index, index, subindex):
    master = _as_master(controller_or_master)
    device = master.slaves[slave_index]
    raw_data = device.sdo_read(index, subindex)
    return int.from_bytes(raw_data, byteorder='little', signed=True)


def write_variable(controller_or_master, slave_index, index, subindex, data):
    master = _as_master(controller_or_master)
    device = master.slaves[slave_index]
    device.sdo_write(index, subindex,
                     int(data).to_bytes(4, byteorder='little', signed=True))


def close_ethercat_controller(controller_or_master):
    if isinstance(controller_or_master, EtherCATController):
        controller_or_master.stop_pump()
        try:
            controller_or_master.master.state = pysoem.INIT_STATE
            controller_or_master.master.write_state()
        except Exception:
            pass
        controller_or_master.master.close()
    else:
        controller_or_master.close()


def _as_master(obj):
    if isinstance(obj, EtherCATController):
        return obj.master
    return obj
