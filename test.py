import pysoem
import time

from core.servo_commands import ENABLE_MOVE_AXIS, MOVE_AXIS_TO, POWER_ON

IFNAME = r"\Device\NPF_{8876F418-0535-4303-8688-8C51A8437E39}"


def open_master():
    master = pysoem.Master()
    master.open(IFNAME)

    count = master.config_init()
    if count <= 0:
        raise RuntimeError("No slaves found")

    print(f"Slaves found: {count}")
    for i, slave in enumerate(master.slaves):
        print(f"[{i}] name={slave.name}, man={hex(slave.man)}, id={hex(slave.id)}")

    return master


def read_i32(slave, index, subindex):
    raw = slave.sdo_read(index, subindex)
    if len(raw) != 4:
        raise RuntimeError(
            f"Unexpected data length for {hex(index)}:{subindex} -> {len(raw)} bytes"
        )
    return int.from_bytes(raw, byteorder="little", signed=True)


def main():
    master = open_master()
    try:
        slave = master.slaves[0]
        POWER_ON(master)
        MOVE_AXIS_TO(master, 100000)  # Пример команды движения к позиции 100000

        while True:
            value = read_i32(slave, 0x6064, 0x00)
            print(f"Position actual value (6064h): {value}")
            time.sleep(0.5)

    finally:
        master.close()


if __name__ == "__main__":
    main()
