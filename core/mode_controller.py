import threading
from core import manual_controller
from core import servo_commands
from utils import config
import time
from multiprocessing import Process, Event, Value

class ModeController:
    def __init__(self):
        self.current_thread = None
        self.current_mode = None
        self.stop_flag = threading.Event()  # флаг остановки потока
        self.threads_lock = threading.Lock()
        self.threads_list = []
        self.master = None
        self.camera_thread = None  # ссылка на BallCameraThread
        self.pid_controller = None
        self._pos_update_dt = 0.01
        self._pos_lock = threading.Lock()  # на случай доступа из разных потоков

        self.mpc_process = None
        self.mpc_stop_flag = None  # отдельный флаг для MPC процесса
        self.mpc_data_threads = []  # список потоков данных MPC
        self.mpc_setpoint = 300



        # Shared memory для обмена данными с MPC процессом
        self.ball_position = Value('i', 0)  # позиция шара
        self.servo_position = Value('i', 0)  # позиция привода
        self.control_signal = Value('i', 0)  # сигнал управления
        self.new_data_available = Value('b', False)  # флаг новых данных
        self.control_ready_flag = Value('b', False)  # флаг готовности управления
        self.setpoint =  Value('i', 300)  # уставка




        # Запускаем мониторинг в отдельном потоке
        monitor_thread = threading.Thread(target=self._monitor, daemon=True, name = 'monitor')
        monitor_thread.start()

    def set_master(self, master):
        self.master = master
    def get_master(self):
        return self.master



    def set_mode(self, mode_name):
        if mode_name == "manual":
            # если поток уже работает — ничего не делаем
            for thread in threading.enumerate():
                if thread.name =="manual_control" and thread.is_alive():
                    print("Поток уже запущен!")
                    return

            # останавливаем старый режим (на всякий случай)
            self.stop_mode()

            # сброс флага остановки
            self.stop_flag.clear()

            # создаём поток
            thread = threading.Thread(
                target=manual_controller.run,
                args=(self.stop_flag,self.master),
                daemon=True,
                name = 'manual_control'
            )
            thread.start()

            # сохраняем ссылку на поток
            with self.threads_lock:
                self.threads_list.append(thread)

            self.current_thread = thread
            self.current_mode = "manual"

        elif mode_name == "pid":
            # если поток уже работает — ничего не делаем
            for thread in threading.enumerate():
                if thread.name == "pid_control" and thread.is_alive():
                    print("PID-поток уже запущен!")
                    return

            # останавливаем старый режим
            self.stop_mode()
            self.stop_flag.clear()


            with self.threads_lock:
                self.threads_list.append(thread)

            self.current_thread = thread
            self.current_mode = "pid"






        elif mode_name == "mpc":
            self.stop_mode()
            self.mpc_stop_flag = Event()


            # Запускаем потоки данных для MPC
            self._start_mpc_data_threads()

            self.current_mode = "mpc"

    def _start_mpc_data_threads(self):
        """Запускает потоки обмена данными для MPC"""

        # Поток обновления данных для MPC
        def data_updater():
            dt = 0.025
            while not self.mpc_stop_flag.is_set():
                ball_pos = self.get_ball_position()
                servo_pos = servo_commands.READ_POS_SCALE(self.get_master())

                if ball_pos is not None and servo_pos is not None:
                    with self.ball_position.get_lock():
                        self.ball_position.value = ball_pos[0]
                        self.servo_position.value = servo_pos
                        self.setpoint.value = self.mpc_setpoint
                        self.new_data_available.value = True

                time.sleep(dt)

        data_thread = threading.Thread(
            target=data_updater,
            daemon=True,
            name="mpc_data_updater"
        )
        data_thread.start()
        self.mpc_data_threads.append(data_thread)

        # Поток приёма команд управления
        def control_listener():
            while not self.mpc_stop_flag.is_set():
                if self.control_ready_flag.value and self.master is not None:
                    with self.control_signal.get_lock():
                        cmd = self.control_signal.value
                        self.control_ready_flag.value = False
                    cmd = cmd * config.PRECESION_SCALER
                    servo_commands.MOVE_AXIS_TO(self.master, int(cmd))

                time.sleep(0.01)

        control_thread = threading.Thread(
            target=control_listener,
            daemon=True,
            name="mpc_control_listener"
        )
        control_thread.start()
        self.mpc_data_threads.append(control_thread)

    def stop_mode(self):
        if self.current_thread and self.current_thread.is_alive():
            # сигнал потоку завершиться
            self.stop_flag.set()

        self.current_thread = None
        self.current_mode = None

    def stop_mpc_mode(self):
        """Останавливает MPC режим и все связанные потоки"""
        if self.mpc_stop_flag:
            self.mpc_stop_flag.set()  # сигнал остановки для MPC процесса и потоков

        # Останавливаем MPC процесс
        if self.mpc_process and self.mpc_process.is_alive():
            self.mpc_process.terminate()  # принудительное завершение
            self.mpc_process.join(timeout=2.0)
            if self.mpc_process.is_alive():
                self.mpc_process.kill()  # крайняя мера
            self.mpc_process = None

        # Останавливаем потоки данных MPC
        for thread in self.mpc_data_threads:
            if thread.is_alive():
                thread.join(timeout=1.0)
        self.mpc_data_threads.clear()

        print("[ModeController] MPC режим остановлен")

    def _monitor(self):
        """Мониторинг всех активных потоков и процессов"""
        while True:
            # Мониторим обычные потоки
            with self.threads_lock:
                alive_threads = [t for t in self.threads_list if t.is_alive()]
                self.threads_list = alive_threads

            # Мониторим MPC процесс
            mpc_status = "жив" if self.mpc_process and self.mpc_process.is_alive() else "не активен"

            print(f"[ModeController] Активных потоков: {len(alive_threads)}, MPC процесс: {mpc_status}")
            print('Все активные потоки:', [t.name for t in threading.enumerate()])

            time.sleep(1)




    def set_pid_setpoint(self, value: float):
        """Обновляет уставку ПИД-регулятора, если он активен"""
        if self.pid_controller:
            self.pid_controller.setpoint = value
            print(f"[ModeController] Уставка ПИД обновлена: {value}")

    def set_mpc_setpoint(self, setpoint):
        self.mpc_setpoint = int(setpoint)






