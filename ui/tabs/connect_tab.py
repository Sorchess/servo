# ui/tabs/connect_tab.py
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QPushButton, QLabel
from PyQt6.QtCore import QTimer
from core import ethercat_driver
from utils.config import ETH_INTERFACE
import pysoem
# from core import mode_controller


class ConnectTab(QWidget):
    def __init__(self,mode_controller):
        super().__init__()
        self.mode_controller = mode_controller
        self.master = None
        self.iface_input = QLineEdit(ETH_INTERFACE)
        self.connect_btn = QPushButton("Подключиться")
        self.status_label = QLabel("Статус: ❌ Не подключено")
        self.message_label = QLabel("")

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Интерфейс:"))
        layout.addWidget(self.iface_input)
        layout.addWidget(self.connect_btn)
        layout.addWidget(self.status_label)
        layout.addWidget(self.message_label)
        self.setLayout(layout)

        # Сигналы
        self.connect_btn.clicked.connect(self.try_connect)

        # Таймер для проверки соединения
        self.check_timer = QTimer(self)
        self.check_timer.timeout.connect(self.check_connection)
        self.check_timer.start(2000)  # каждые 2 секунды

    def show_temp_message(self, text: str, duration: int = 3000):
        """Показать временное сообщение (исчезает через duration мс)."""
        self.message_label.setText(text)
        QTimer.singleShot(duration, lambda: self.message_label.setText(""))

    def try_connect(self):
        iface = self.iface_input.text()
        try:
            self.master = ethercat_driver.setup_ethercat_controller(iface)
            if self.master:
                self.status_label.setText("Статус: ✅ Подключено")
                self.show_temp_message("Подключение успешно!", 3000)
                self.mode_controller.set_master(self.master)
            else:
                self.status_label.setText("Статус: ❌ Не подключено")
                self.show_temp_message("Устройства не найдены", 3000)
        except Exception as e:
            self.master = None
            self.status_label.setText("Статус: ❌ Не подключено")
            self.show_temp_message(f"Ошибка: {e}", 4000)

    def check_connection(self):
        """Проверяем доступность привода."""
        if not self.master:
            return  # если не подключались — нечего проверять

        try:
            self.master.read_state()
            if any(slave.state != pysoem.OP_STATE for slave in self.master.slaves):
                self.status_label.setText("Статус: ❌ Соединение потеряно")
            else:
                self.status_label.setText("Статус: ✅ Подключено")
        except Exception:
            self.status_label.setText("Статус: ❌ Соединение потеряно")
            self.master = None
