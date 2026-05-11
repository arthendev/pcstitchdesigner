"""PC Stitch Designer"""

import sys
from PyQt5.QtWidgets import QApplication
from main_window import MainWindow


def _close_splash():
    """Close the PyInstaller splash screen if running as a frozen bundle."""
    try:
        import pyi_splash  # only available inside a PyInstaller-built EXE
        pyi_splash.close()
    except ImportError:
        pass


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    _close_splash()  # dismiss splash once the main window is visible
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
