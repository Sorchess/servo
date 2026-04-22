# main.py
import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QTabWidget
from ui.tabs.connect_tab import ConnectTab
from ui.tabs.manual_tab import ManualTab
from ui.tabs.telemetry_tab import TelemetryTab
from core.mode_controller import ModeController
from core import servo_commands
from core import ethercat_driver


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Управление сервоприводом")
        self.resize(600, 400)

        # Создаём контроллер ручного управления
        self.mode_controller = ModeController()

        # Создаём вкладки
        self.tabs = QTabWidget()
        self.connect_tab = ConnectTab(self.mode_controller)
        self.manual_tab = ManualTab(controller=self.mode_controller)
        self.telemetry_tab = TelemetryTab(controller=self.mode_controller)

        self.tabs.addTab(self.connect_tab, "Подключение")
        self.tabs.addTab(self.manual_tab, "Ручное управление")
        self.tabs.addTab(self.telemetry_tab, "Телеметрия")

        self.setCentralWidget(self.tabs)


    def closeEvent(self, event):
        self.master = self.mode_controller.get_master()
        # 0. Останавливаем телеметрию (ДО закрытия EtherCAT)
        try:
            if hasattr(self, 'telemetry_tab'):
                self.telemetry_tab.shutdown()
        except Exception as e:
            print(f"[MainWindow] Ошибка при остановке телеметрии: {e}")

        # 1. Останавливаем режимы
        if self.mode_controller:
            self.mode_controller.stop_mode()
            self.mode_controller.stop_mpc_mode()

        # 2. Выключаем питание, если было включено
        try:
            if self.master:
                servo_commands.POWER_OFF(self.master)
                print("[MainWindow] Питание привода отключено")
        except Exception as e:
            print(f"[MainWindow] Ошибка при отключении питания: {e}")

        # 3. Закрываем соединение с EtherCAT
        try:
            if self.master:
                ethercat_driver.close_ethercat_controller(self.master)
                print("[MainWindow] Соединение с EtherCAT закрыто")
        except Exception as e:
            print(f"[MainWindow] Ошибка при закрытии соединения: {e}")


        # Продолжаем стандартное закрытие окна
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
