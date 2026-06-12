"""Check for Updates dialog - fetches GitHub releases and compares versions."""

import json
import re
import urllib.request
import urllib.error

from PyQt5.QtCore import Qt, QThread, QUrl, QEventLoop, pyqtSignal, QCoreApplication
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton,
    QTextBrowser, QVBoxLayout,
)

GITHUB_API_URL = "https://api.github.com/repos/arthendev/pcstitchdesigner/releases"
DOWNLOAD_URL = "https://github.com/arthendev/pcstitchdesigner/releases"
REQUEST_TIMEOUT = 10  # seconds


def _parse_version(tag: str):
    """Parse version tag like '0.7.9' or 'v0.7.9' into a tuple of ints, or None on failure."""
    tag = tag.lstrip("v")
    parts = tag.split(".")
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return None


def _extract_language_content(body: str, lang: str) -> str:
    """Extract the portion of a release body that corresponds to *lang*.

    Markers look like ``<!-- lang="de" -->`` … ``<!-- /lang="de" -->``.
    If the body has no such markers the full body is returned unchanged.
    When *lang* is not found the function falls back to English (``en``).
    If neither is present, the full body is returned.
    """
    if not body:
        return body

    pat = re.compile(
        r'<!--\s*lang="([^"]+)"\s*-->(.*?)<!--\s*/lang="\1"\s*-->',
        re.DOTALL,
    )
    matches = pat.findall(body)

    if not matches:
        return body  # No language markers at all

    sections = {lc: content.strip() for lc, content in matches}

    if lang and lang in sections:
        return sections[lang]
    if "en" in sections:
        return sections["en"]
    return body


def _build_changelog_html(releases: list, current_ver: tuple, lang: str = "") -> str:
    """Return HTML summarising all releases newer than current_ver.

    When *lang* is supplied the release body is filtered via
    ``_extract_language_content`` so that only the relevant
    language section is displayed.
    """
    sections = []
    for release in releases:
        tag = release.get("tag_name", "")
        ver = _parse_version(tag)
        if ver is None or ver <= current_ver:
            continue
        if release.get("draft"):
            continue
        name = release.get("name") or tag
        body = _extract_language_content((release.get("body") or "").strip(), lang)
        section = [f"<h3>{name}</h3>"]
        if body:
            # Basic markdown → HTML conversion for GitHub release notes.
            for line in body.replace("\r\n", "\n").splitlines():
                stripped = line.strip()
                if not stripped:
                    section.append("<br>")
                elif stripped.startswith("### "):
                    section.append(f"<b>{stripped[4:]}</b><br>")
                elif stripped.startswith("## "):
                    section.append(f"<b>{stripped[3:]}</b><br>")
                elif stripped.startswith("# "):
                    section.append(f"<b>{stripped[2:]}</b><br>")
                elif stripped.startswith(("- ", "* ")):
                    section.append(f"&nbsp;&bull;&nbsp;{stripped[2:]}<br>")
                else:
                    section.append(f"{stripped}<br>")
        section.append("<hr>")
        sections.append("\n".join(section))
    if not sections:
        return ""
    return "<html><body>" + "\n".join(sections) + "</body></html>"


# ── Worker thread ────────────────────────────────────────────────────────────

class _UpdateWorker(QThread):
    """Fetches GitHub releases in a background thread."""

    status_changed = pyqtSignal(str)
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def run(self):
        try:
            self.status_changed.emit(self.tr("Retrieving list of releases…"))
            req = urllib.request.Request(
                GITHUB_API_URL,
                headers={
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                    "User-Agent": "pcstitchdesigner-update-check",
                },
            )
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                self.status_changed.emit(self.tr("Parsing response…"))
                releases = json.loads(resp.read())
            self.finished.emit(releases)
        except Exception as exc:
            self.error.emit(str(exc))


# ── Progress dialog ──────────────────────────────────────────────────────────

class _CheckingDialog(QDialog):
    """Modal progress dialog shown while the update check is in flight."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Check for Updates"))
        self.setModal(True)
        self.setFixedSize(380, 100)
        # Remove close/help buttons so user cannot dismiss it manually.
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self._label = QLabel(self.tr("Checking for new version…"))
        layout.addWidget(self._label)

        progress = QProgressBar()
        progress.setRange(0, 0)  # indeterminate / busy
        layout.addWidget(progress)

    def set_status(self, text: str):
        self._label.setText(text)


# ── Result dialog ────────────────────────────────────────────────────────────

class _ResultDialog(QDialog):
    """Displays the outcome of the update check."""

    def __init__(self, title: str, message: str, changelog_html: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        lbl = QLabel(message)
        lbl.setWordWrap(True)
        layout.addWidget(lbl)

        if changelog_html:
            browser = QTextBrowser()
            browser.setOpenExternalLinks(True)
            browser.setHtml(changelog_html)
            browser.setMinimumHeight(220)
            layout.addWidget(browser)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        open_btn = QPushButton(self.tr("Open Download Site"))
        open_btn.clicked.connect(self._open_download)
        btn_row.addWidget(open_btn)

        close_btn = QPushButton(self.tr("Close"))
        close_btn.setDefault(True)
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _open_download(self):
        QDesktopServices.openUrl(QUrl(DOWNLOAD_URL))


# ── Public entry point ───────────────────────────────────────────────────────

def _tr(text):
    return QCoreApplication.translate("check_updates_dialog", text)


def run_check_for_updates(parent, current_version: str, language: str = ""):
    """Run the full update-check workflow (blocking call from UI thread).

    *language* is an ISO 639-1 code (e.g. ``"de"``, ``"en"``) used to
    select the matching section from the multilingual release body.
    """
    current_ver = _parse_version(current_version)

    checking_dlg = _CheckingDialog(parent)
    worker = _UpdateWorker()

    result = {"releases": None, "error": None}

    def on_finished(releases):
        result["releases"] = releases
        checking_dlg.accept()

    def on_error(msg):
        result["error"] = msg
        checking_dlg.accept()

    worker.status_changed.connect(checking_dlg.set_status)
    worker.finished.connect(on_finished)
    worker.error.connect(on_error)
    worker.start()

    checking_dlg.exec_()

    if result["error"] is not None:
        _ResultDialog(
            _tr("Check for Updates"),
            _tr("Checking for new version failed."),
            parent=parent,
        ).exec_()
        return

    releases = result["releases"] or []

    # Determine the newest available version.
    newest_ver = None
    for release in releases:
        if release.get("draft"):
            continue
        ver = _parse_version(release.get("tag_name", ""))
        if ver is not None and (newest_ver is None or ver > newest_ver):
            newest_ver = ver

    if newest_ver is None or newest_ver <= current_ver:
        _ResultDialog(
            _tr("Check for Updates"),
            _tr("You have the most recent version."),
            parent=parent,
        ).exec_()
    else:
        newest_str = ".".join(str(n) for n in newest_ver)
        changelog_html = _build_changelog_html(releases, current_ver, language)
        _ResultDialog(
            _tr("New Version Available"),
            _tr("A new version is available! (latest: {0})").format(newest_str),
            changelog_html=changelog_html,
            parent=parent,
        ).exec_()


def run_silent_check_for_updates(parent, current_version: str, language: str = ""):
    """Run the update check silently (no progress dialog).

    Only shows a result dialog when a newer version is available.
    Errors and "up to date" results are silently ignored.
    """
    current_ver = _parse_version(current_version)

    worker = _UpdateWorker()
    result = {"releases": None, "error": None}
    loop = QEventLoop()

    def on_finished(releases):
        result["releases"] = releases
        loop.quit()

    def on_error(msg):
        result["error"] = msg
        loop.quit()

    worker.finished.connect(on_finished)
    worker.error.connect(on_error)
    worker.start()
    loop.exec_()

    if result["error"] is not None:
        return  # Silent: ignore network/parse errors

    releases = result["releases"] or []

    newest_ver = None
    for release in releases:
        if release.get("draft"):
            continue
        ver = _parse_version(release.get("tag_name", ""))
        if ver is not None and (newest_ver is None or ver > newest_ver):
            newest_ver = ver

    if newest_ver is not None and newest_ver > current_ver:
        newest_str = ".".join(str(n) for n in newest_ver)
        changelog_html = _build_changelog_html(releases, current_ver, language)
        _ResultDialog(
            _tr("New Version Available"),
            _tr("A new version is available! (latest: {0})").format(newest_str),
            changelog_html=changelog_html,
            parent=parent,
        ).exec_()
