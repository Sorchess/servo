###

###Программный модуль предназначен для связи с сервоприводом

###

import pysoem


def setup_ethercat_controller(ifname='eth0'):
    # master = pysoem.Master()
    # master.open(ifname)

    # if master.config_init() <= 0:
    #     print("Устройства не найдены")
    #     return None

    # master.config_map()
    # if master.state_check(pysoem.SAFEOP_STATE, 50000) != pysoem.SAFEOP_STATE:
    #     master.read_state()
    #     for slave in master.slaves:
    #         if slave.state != pysoem.SAFEOP_STATE:
    #             print(f"{slave.name} did not reach SAFEOP state")
    #     raise Exception("Not all slaves reached SAFEOP state")

    # master.state = pysoem.OP_STATE
    # master.write_state()

    # slave_names = [slave.name for slave in master.slaves]
    # print(f"Successfully connected to EtherCAT network on interface {ifname}")
    # print("Connected slaves:", ", ".join(slave_names))

    # return master

    master = pysoem.Master()
    master.open(ifname)

    count = master.config_init()
    if count <= 0:
        raise RuntimeError("No slaves found")

    print(f"Slaves found: {count}")
    for i, slave in enumerate(master.slaves):
        print(f"[{i}] name={slave.name}, man={hex(slave.man)}, id={hex(slave.id)}")

    return master

def read_dint_variable(master, slave_index, index, subindex):
    device = master.slaves[slave_index]
    raw_data = device.sdo_read(index, subindex)
    return int.from_bytes(raw_data, byteorder='little', signed=True)

def write_variable(master, slave_index, index, subindex, data):
    device = master.slaves[slave_index]
    device.sdo_write(index, subindex, (data).to_bytes(4, byteorder='little', signed=True))

def close_ethercat_controller(master):
    master.close()