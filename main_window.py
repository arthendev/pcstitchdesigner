"""Main application window with menus, toolbar, status bar, and canvas."""

import os

from PyQt5.QtWidgets import (
    QMainWindow, QScrollArea, QAction, QActionGroup,
    QFileDialog, QMessageBox, QToolBar, QLabel, QMenu,
)
from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QIcon, QKeyEvent
from PyQt5.QtGui import QDesktopServices

from model import StitchPattern
from canvas import StitchCanvas
from tools import PanTool, AddPointTool, EditPointTool, DeletePointTool
import file_io
from config import Config
from version import APP_VERSION


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PC Designer")
        self.resize(1200, 700)

        # Load configuration
        self._config = Config()
        self._recent_files = self._config.get_recent_files()

        self._file_path = None
        self._pattern = StitchPattern()
        self._canvas = StitchCanvas(self._pattern)
        self._canvas.changed.connect(self._on_pattern_changed)
        self._canvas.cursor_moved.connect(self._on_cursor_moved)

        # Tools
        self._pan_tool = PanTool()
        self._add_tool = AddPointTool()
        self._edit_tool = EditPointTool()
        self._delete_tool = DeletePointTool()

        # Scroll area as central widget
        self._scroll = QScrollArea()
        self._scroll.setWidget(self._canvas)
        self._scroll.setAlignment(Qt.AlignCenter)
        self._scroll.setWidgetResizable(False)
        self.setCentralWidget(self._scroll)

        # Status bar labels
        self._tool_label = QLabel("Tool: —")
        self._coord_label = QLabel("x: — y: —")
        self._count_label = QLabel("Points: 0")
        self.statusBar().addWidget(self._tool_label)
        self.statusBar().addPermanentWidget(self._coord_label)
        self.statusBar().addPermanentWidget(self._count_label)

        self._build_actions()
        self._build_menus()
        self._build_toolbar()

        # Default tool & zoom
        self._act_add.setChecked(True)
        self._on_tool_add()
        self._zoom_fit_height()
        # Initialize design menu visibility (P-Design is default)
        self._on_pdesign_selected()

    # ── Actions ──

    def _build_actions(self):
        # File
        self._act_new = QAction("&New", self)
        self._act_new.setShortcut("Ctrl+N")
        self._act_new.triggered.connect(self._file_new)

        self._act_open = QAction("&Open…", self)
        self._act_open.setShortcut("Ctrl+O")
        self._act_open.triggered.connect(self._file_open)

        self._act_save = QAction("&Save", self)
        self._act_save.setShortcut("Ctrl+S")
        self._act_save.triggered.connect(self._file_save)

        self._act_save_as = QAction("Save &As…", self)
        self._act_save_as.setShortcut("Ctrl+Shift+S")
        self._act_save_as.triggered.connect(self._file_save_as)

        self._act_clear_recent = QAction("Clear List", self)
        self._act_clear_recent.triggered.connect(self._clear_recent_files)

        self._act_exit = QAction("E&xit", self)
        self._act_exit.setShortcut("Alt+F4")
        self._act_exit.triggered.connect(self.close)

        # Edit
        self._act_undo = QAction("&Undo", self)
        self._act_undo.setShortcut("Ctrl+Z")
        self._act_undo.triggered.connect(self._edit_undo)

        self._act_redo = QAction("&Redo", self)
        self._act_redo.setShortcut("Ctrl+Y")
        self._act_redo.triggered.connect(self._edit_redo)

        # Tools (checkable, exclusive)
        _icons = os.path.join(os.path.dirname(__file__), "icons")

        self._act_pan = QAction(QIcon(os.path.join(_icons, "pan.svg")),
                                 "Pan", self)
        self._act_pan.setCheckable(True)
        self._act_pan.triggered.connect(self._on_tool_pan)

        self._act_add = QAction(QIcon(os.path.join(_icons, "add_point.svg")),
                                "Add Stitch Point", self)
        self._act_add.setCheckable(True)
        self._act_add.triggered.connect(self._on_tool_add)

        self._act_edit = QAction(QIcon(os.path.join(_icons, "edit_point.svg")),
                                "Edit Stitch Point", self)
        self._act_edit.setCheckable(True)
        self._act_edit.triggered.connect(self._on_tool_edit)

        self._act_delete = QAction(QIcon(os.path.join(_icons, "delete_point.svg")),
                                   "Delete Stitch Point", self)
        self._act_delete.setCheckable(True)
        self._act_delete.triggered.connect(self._on_tool_delete)

        self._tool_group = QActionGroup(self)
        self._tool_group.setExclusive(True)
        self._tool_group.addAction(self._act_pan)
        self._tool_group.addAction(self._act_add)
        self._tool_group.addAction(self._act_edit)
        self._tool_group.addAction(self._act_delete)

        self._act_zoom_in = QAction(QIcon(os.path.join(_icons, "zoom_in.svg")),
                                    "Zoom In", self)
        self._act_zoom_in.setShortcut("Ctrl+=")
        self._act_zoom_in.triggered.connect(self._zoom_in)

        self._act_zoom_out = QAction(QIcon(os.path.join(_icons, "zoom_out.svg")),
                                     "Zoom Out", self)
        self._act_zoom_out.setShortcut("Ctrl+-")
        self._act_zoom_out.triggered.connect(self._zoom_out)

        self._act_fit_height = QAction(QIcon(os.path.join(_icons, "fit_height.svg")),
                                       "Fit Height", self)
        self._act_fit_height.triggered.connect(self._zoom_fit_height)

        self._act_fit_screen = QAction(QIcon(os.path.join(_icons, "fit_screen.svg")),
                                       "Fit Screen", self)
        self._act_fit_screen.triggered.connect(self._zoom_fit_screen)

        # Design (P-Design / M-Design)
        self._act_pdesign = QAction("P-Design", self)
        self._act_pdesign.setCheckable(True)
        self._act_pdesign.setChecked(True)
        self._act_pdesign.triggered.connect(self._on_pdesign_selected)

        self._act_mdesign = QAction("M-Design", self)
        self._act_mdesign.setCheckable(True)
        self._act_mdesign.setVisible(False)
        self._act_mdesign.triggered.connect(self._on_mdesign_selected)

        self._design_group = QActionGroup(self)
        self._design_group.setExclusive(True)
        self._design_group.addAction(self._act_pdesign)
        self._design_group.addAction(self._act_mdesign)

        # Stitch type (9mm / MAXI) - only visible when P-Design is selected
        self._act_9mm = QAction("9mm", self)
        self._act_9mm.setCheckable(True)
        self._act_9mm.setChecked(True)
        self._act_9mm.triggered.connect(self._on_stitch_9mm)

        self._act_maxi = QAction("MAXI", self)
        self._act_maxi.setCheckable(True)
        self._act_maxi.triggered.connect(self._on_stitch_maxi)

        self._stitch_group = QActionGroup(self)
        self._stitch_group.setExclusive(True)
        self._stitch_group.addAction(self._act_9mm)
        self._stitch_group.addAction(self._act_maxi)

        # Help
        self._act_about = QAction("&About", self)
        self._act_about.triggered.connect(self._help_about)

        self._act_get_releases = QAction("&Get new releases", self)
        self._act_get_releases.triggered.connect(self._help_get_releases)

    # ── Menus ──

    def _build_menus(self):
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        file_menu.addAction(self._act_new)
        file_menu.addAction(self._act_open)
        
        # Open Recent submenu
        self._recent_menu = QMenu("Open &Recent", self)
        file_menu.addMenu(self._recent_menu)
        self._update_recent_files_menu()
        
        file_menu.addSeparator()
        file_menu.addAction(self._act_save)
        file_menu.addAction(self._act_save_as)
        file_menu.addSeparator()
        file_menu.addAction(self._act_exit)

        edit_menu = mb.addMenu("&Edit")
        edit_menu.addAction(self._act_undo)
        edit_menu.addAction(self._act_redo)

        tools_menu = mb.addMenu("&Tools")
        tools_menu.addAction(self._act_pan)
        tools_menu.addAction(self._act_add)
        tools_menu.addAction(self._act_edit)
        tools_menu.addAction(self._act_delete)
        tools_menu.addSeparator()
        tools_menu.addAction(self._act_zoom_in)
        tools_menu.addAction(self._act_zoom_out)
        tools_menu.addAction(self._act_fit_height)
        tools_menu.addAction(self._act_fit_screen)

        design_menu = mb.addMenu("&Design")
        design_menu.addAction(self._act_pdesign)
        design_menu.addAction(self._act_mdesign)
        design_menu.addSeparator()
        design_menu.addAction(self._act_9mm)
        design_menu.addAction(self._act_maxi)

        help_menu = mb.addMenu("&Help")
        help_menu.addAction(self._act_get_releases)
        help_menu.addAction(self._act_about)

    # ── Toolbar ──

    def _build_toolbar(self):
        tb = QToolBar("Main Toolbar")
        tb.setMovable(False)
        self.addToolBar(Qt.TopToolBarArea, tb)

        tb.addAction(self._act_pan)
        tb.addAction(self._act_add)
        tb.addAction(self._act_edit)
        tb.addAction(self._act_delete)
        tb.addSeparator()
        tb.addAction(self._act_zoom_in)
        tb.addAction(self._act_zoom_out)
        tb.addAction(self._act_fit_height)
        tb.addAction(self._act_fit_screen)

    # ── Tool selection ──

    def _on_tool_pan(self):
        self._canvas.set_tool(self._pan_tool)
        self._tool_label.setText("Tool: Pan")

    def _on_tool_add(self):
        self._canvas.set_tool(self._add_tool)
        self._tool_label.setText("Tool: Add Stitch Point")

    def _on_tool_edit(self):
        self._canvas.set_tool(self._edit_tool)
        self._tool_label.setText("Tool: Edit Stitch Point")

    def _on_tool_delete(self):
        self._canvas.set_tool(self._delete_tool)
        self._tool_label.setText("Tool: Delete Stitch Point")

    # ── Design selection ──

    def _on_pdesign_selected(self):
        self._act_9mm.setVisible(True)
        self._act_maxi.setVisible(True)

    def _on_mdesign_selected(self):
        self._act_9mm.setVisible(False)
        self._act_maxi.setVisible(False)

    # ── Stitch type selection ──

    def _pattern_fits_in_canvas(self, canvas_size):
        """Check if current pattern fits in the given canvas size."""
        w, h = StitchPattern.CANVAS_SIZES[canvas_size]
        for x, y in self._pattern.points:
            if x < 0 or x > w or y < 0 or y > h:
                return False
        return True

    def _on_stitch_9mm(self):
        if not self._pattern_fits_in_canvas("9mm"):
            QMessageBox.warning(
                self, "Canvas Size Change",
                "Current pattern will be too large for the working area."
            )
            # Revert to MAXI
            self._act_maxi.setChecked(True)
            return
        self._pattern.stitch_type = "9mm"
        self._pattern.canvas_size = "9mm"
        self._canvas._update_size()
        self._canvas.update()
        self._on_pattern_changed()

    def _on_stitch_maxi(self):
        if not self._pattern_fits_in_canvas("MAXI"):
            QMessageBox.warning(
                self, "Canvas Size Change",
                "Current pattern will be too large for the working area."
            )
            # Revert to 9mm
            self._act_9mm.setChecked(True)
            return
        self._pattern.stitch_type = "MAXI"
        self._pattern.canvas_size = "MAXI"
        self._canvas._update_size()
        self._canvas.update()
        self._on_pattern_changed()

    # ── Status bar updates ──

    def _on_cursor_moved(self, cx, cy):
        cx_clamped = max(0, min(self._pattern.CANVAS_WIDTH, cx))
        cy_clamped = max(0, min(self._pattern.CANVAS_HEIGHT, cy))
        self._coord_label.setText(f"x: {cx_clamped:.0f}  y: {cy_clamped:.0f}")

    def _on_pattern_changed(self):
        self._count_label.setText(f"Points: {len(self._pattern.points)}")
        self._update_title()

    def _update_title(self):
        name = os.path.basename(self._file_path) if self._file_path else "Untitled"
        mod = " *" if self._pattern.modified else ""
        self.setWindowTitle(f"{name}{mod} - PC Stitch Designer")

    # ── File actions ──

    def _confirm_discard(self):
        """Return True if it's OK to discard current pattern."""
        if not self._pattern.modified:
            return True
        ret = QMessageBox.question(
            self, "Unsaved Changes",
            "The pattern has been modified.\nDo you want to save before continuing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if ret == QMessageBox.Save:
            return self._file_save()
        return ret == QMessageBox.Discard

    def _file_new(self):
        if not self._confirm_discard():
            return
        self._pattern.clear()
        self._file_path = None
        self._canvas.update()
        self._on_pattern_changed()

    def _file_open(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Stitch Pattern", "",
            "Stitch Files (*.pcd);;All Files (*)",
        )
        if not path:
            return
        self._open_file(path)

    def _open_file(self, path):
        """Open a file and add it to recent files list."""
        try:
            pattern = file_io.load_pattern(path)
        except Exception as e:
            QMessageBox.critical(self, "Open Error", str(e))
            return
        self._pattern = pattern
        self._canvas.pattern = pattern
        self._file_path = path
        self._add_recent_file(path)
        self._canvas.update()
        self._on_pattern_changed()
        # Switch to Pan tool after opening
        self._act_pan.setChecked(True)
        self._on_tool_pan()

    def _file_save(self):
        if self._file_path:
            try:
                file_io.save_pattern(self._file_path, self._pattern)
            except Exception as e:
                QMessageBox.critical(self, "Save Error", str(e))
                return False
            self._update_title()
            return True
        return self._file_save_as()

    def _file_save_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Stitch Pattern", "",
            "Stitch Files (*.pcd);;All Files (*)",
        )
        if not path:
            return False
        self._file_path = path
        result = self._file_save()
        if result:
            self._add_recent_file(path)
        return result

    def _add_recent_file(self, path):
        """Add a file to the recent files list (max 20)."""
        self._config.add_recent_file(path)
        self._recent_files = self._config.get_recent_files()
        # Update menu
        self._update_recent_files_menu()

    def _update_recent_files_menu(self):
        """Rebuild the Open Recent submenu."""
        self._recent_menu.clear()
        
        if self._recent_files:
            for i, path in enumerate(self._recent_files[:20], 1):
                # Display filename with index (Alt+1..9, Alt+0 for 10, etc)
                filename = os.path.basename(path)
                action = self._recent_menu.addAction(
                    f"&{i % 10} {filename}"
                )
                action.triggered.connect(
                    lambda checked, p=path: self._open_recent_file(p)
                )
            self._recent_menu.addSeparator()
        
        self._recent_menu.addAction(self._act_clear_recent)

    def _open_recent_file(self, path):
        """Open a file from the recent files list."""
        if not self._confirm_discard():
            return
        self._open_file(path)

    def _clear_recent_files(self):
        """Clear the recent files list."""
        self._config.clear_recent_files()
        self._recent_files = self._config.get_recent_files()
        self._update_recent_files_menu()

    # ── Edit actions ──

    def _edit_undo(self):
        self._pattern.undo()
        self._canvas.update()
        self._on_pattern_changed()

    def _edit_redo(self):
        self._pattern.redo()
        self._canvas.update()
        self._on_pattern_changed()

    # ── Zoom actions ──

    def _zoom_in(self):
        self._canvas.set_scale(self._canvas.get_scale() * 1.25)

    def _zoom_out(self):
        self._canvas.set_scale(self._canvas.get_scale() / 1.25)

    def _zoom_fit_height(self):
        viewport_h = self._scroll.viewport().height()
        if viewport_h <= 0:
            viewport_h = self.height() - 80  # rough estimate before show
        scale = (viewport_h - 2 * self._canvas.MARGIN) / self._pattern.CANVAS_HEIGHT
        self._canvas.set_scale(scale)

    def _zoom_fit_screen(self):
        vp = self._scroll.viewport()
        vw, vh = vp.width(), vp.height()
        if vw <= 0 or vh <= 0:
            vw, vh = self.width() - 20, self.height() - 80
        margin2 = 2 * self._canvas.MARGIN
        sx = (vw - margin2) / self._pattern.CANVAS_WIDTH
        sy = (vh - margin2) / self._pattern.CANVAS_HEIGHT
        self._canvas.set_scale(min(sx, sy))

    # ── Help ──

    def _help_about(self):
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("About PC Stitch Designer")
        # msg_box.setIcon(QMessageBox.Information)
        msg_box.setTextFormat(Qt.RichText)
        
        # Create a label with clickable links
        label = QLabel(
            f"<b>PC Stitch Designer</b> v{APP_VERSION}<br><br>"
            "A stitch pattern editor for PFAFF machines.<br><br>"
            "<b>Project:</b> "
            '<a href="https://github.com/art-hen/pcstitchdesigner">'
            "github.com/art-hen/pcstitchdesigner</a><br>"
            "<b>New Releases:</b> "
            '<a href="https://github.com/art-hen/pcstitchdesigner/releases">'
            "github.com/art-hen/pcstitchdesigner/releases</a>"
        )
        label.setOpenExternalLinks(True)
        label.setTextFormat(Qt.RichText)
        
        msg_box.layout().addWidget(label, 0, 0)
        msg_box.exec_()

    def _help_get_releases(self):
        """Open GitHub releases page in default web browser."""
        QDesktopServices.openUrl(
            QUrl("https://github.com/art-hen/pcstitchdesigner/releases")
        )

    # ── Keyboard events ──

    def keyPressEvent(self, event):
        """Handle keyboard events."""
        if event.key() == Qt.Key_Escape:
            # Switch to Pan tool when Escape is pressed
            self._act_pan.setChecked(True)
            self._on_tool_pan()
            event.accept()
        else:
            super().keyPressEvent(event)

    # ── Close event ──

    def closeEvent(self, event):
        if self._confirm_discard():
            # Save configuration before closing
            self._config.save()
            event.accept()
        else:
            event.ignore()
