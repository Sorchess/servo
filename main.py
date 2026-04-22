import sys
from PyQt6.QtWidgets import QApplication
from ui.main_window import MainWindow

if __name__ == "__main__":
    app = QApplication(sys.argv)   # создаём "приложение"
    window = MainWindow()          # создаём главное окно
    window.show()                  # показываем окно
    sys.exit(app.exec())           # запускаем главный цикл событий
