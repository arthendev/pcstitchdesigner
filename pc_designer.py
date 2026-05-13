"""PC Stitch Designer"""

import os
import sys
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTranslator, QLocale, QLibraryInfo
from config import Config
from main_window import MainWindow


def _close_splash():
    """Close the PyInstaller splash screen if running as a frozen bundle."""
    try:
        import pyi_splash  # only available inside a PyInstaller-built EXE
        pyi_splash.close()
    except ImportError:
        pass


def _base_path():
    """Return the directory containing bundled resources (or the script directory)."""
    if getattr(sys, "frozen", False):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


def _load_translation(app, language_pref):
    """Install the appropriate QTranslator for *language_pref*.

    ``language_pref`` is one of ``"system"``, ``"en"``, or an ISO 639-1
    language code such as ``"de"``.  English requires no translation file.
    Returns a list of installed QTranslators (keeping them alive), or [].
    """
    if language_pref == "en":
        return []  # English is the base language; no .qm file needed

    if language_pref == "system":
        ui_langs = QLocale.system().uiLanguages()
        lang_code = ui_langs[0].split("-")[0] if ui_langs else "en"
    else:
        lang_code = language_pref

    if lang_code == "en":
        return []

    translators = []

    # 1. Qt base translations (buttons, file dialog labels, etc.)
    qt_translator = QTranslator()
    qt_translations_path = QLibraryInfo.location(QLibraryInfo.TranslationsPath)
    # When frozen, Qt translations are bundled next to the app translations
    if getattr(sys, "frozen", False):
        qt_translations_path = os.path.join(_base_path(), "translations")
    if qt_translator.load(f"qtbase_{lang_code}", qt_translations_path):
        app.installTranslator(qt_translator)
        translators.append(qt_translator)

    # 2. App translations
    qm_path = os.path.join(_base_path(), "translations", f"pcstitchdesigner_{lang_code}.qm")
    app_translator = QTranslator()
    if app_translator.load(qm_path):
        app.installTranslator(app_translator)
        translators.append(app_translator)

    return translators  # caller must keep references to prevent GC


def main():
    app = QApplication(sys.argv)

    # Load config before constructing the main window so we can install the
    # translator first (all tr() calls happen during widget construction).
    config = Config()
    lang_pref = config.get("language", "system")
    _translator = _load_translation(app, lang_pref)  # noqa: F841 – keep alive

    window = MainWindow(config)
    window.show()
    _close_splash()  # dismiss splash once the main window is visible
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
