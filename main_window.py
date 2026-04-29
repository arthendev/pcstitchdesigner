"""Main application window with menus, toolbar, status bar, and canvas."""

import os

from PyQt5.QtWidgets import (
    QMainWindow, QScrollArea, QAction, QActionGroup,
    QFileDialog, QMessageBox, QToolBar, QLabel, QMenu,
)
from PyQt5.QtCore import Qt, QUrl, QPoint, QEvent
from PyQt5.QtGui import QIcon, QKeyEvent, QCursor
from PyQt5.QtGui import QDesktopServices

from model import StitchPattern
from canvas import StitchCanvas
from tools import PanTool, AddPointTool, MovePointTool, DeletePointTool, SelectPointTool
import file_io
from config import Config
from version import APP_VERSION


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PC Stitch Designer")
        self.resize(1200, 700)

        # Load configuration
        self._config = Config()
        self._recent_files = self._config.get_recent_files()

        self._file_path = None
        self._pattern = StitchPattern()
        self._canvas = StitchCanvas(self._pattern)
        self._canvas.changed.connect(self._on_pattern_changed)
        self._canvas.cursor_moved.connect(self._on_cursor_moved)
        self._canvas.selection_changed.connect(self._update_selection_action_state)
        
        # View orientation state
        self._view_orientation = "default"  # "default" or "sewing_direction"

        # Tools
        self._pan_tool = PanTool()
        self._select_tool = SelectPointTool()
        self._add_tool = AddPointTool()
        self._move_tool = MovePointTool()
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

        # Temporary Ctrl→SelectPoint state (while AddPointTool is active)
        self._ctrl_select_active = False
        # Temporary Ctrl→MovePoint state (while SelectPointTool is active)
        self._ctrl_move_active = False

        # Install event filter to watch Ctrl key on the canvas
        self._canvas.installEventFilter(self)

        # Default tool & zoom
        self._act_add.setChecked(True)
        self._on_tool_add()
        self._zoom_fit_height()
        # Initialize design menu visibility (P-Design is default)
        self._on_pdesign_selected()
        # Initialize undo/redo button state
        self._update_undo_redo_state()

    # ── Ctrl temporary tool switch ──

    def eventFilter(self, obj, event):
        if obj is self._canvas:
            if (event.type() == QEvent.KeyPress
                    and event.key() == Qt.Key_Control
                    and not event.isAutoRepeat()):
                if self._canvas._tool is self._add_tool:
                    self._ctrl_select_active = True
                    self._act_move.setChecked(True)
                    self._on_tool_move()
                elif self._canvas._tool is self._select_tool:
                    self._ctrl_move_active = True
                    self._act_move.setChecked(True)
                    self._on_tool_move()
            elif (event.type() == QEvent.KeyRelease
                    and event.key() == Qt.Key_Control
                    and not event.isAutoRepeat()):
                if self._ctrl_select_active:
                    self._ctrl_select_active = False
                    if self._canvas._tool is self._move_tool:
                        self._act_add.setChecked(True)
                        self._on_tool_add()
                elif self._ctrl_move_active:
                    self._ctrl_move_active = False
                    if self._canvas._tool is self._move_tool:
                        self._act_select.setChecked(True)
                        self._on_tool_select()
        return super().eventFilter(obj, event)

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

        # Icons path (needed for undo/redo icons)
        _icons = os.path.join(os.path.dirname(__file__), "icons")

        # Edit
        self._act_undo = QAction(QIcon(os.path.join(_icons, "undo.svg")), "&Undo", self)
        self._act_undo.setShortcut("Ctrl+Z")
        self._act_undo.triggered.connect(self._edit_undo)

        self._act_redo = QAction(QIcon(os.path.join(_icons, "redo.svg")), "&Redo", self)
        self._act_redo.setShortcut("Ctrl+Y")
        self._act_redo.triggered.connect(self._edit_redo)

        self._act_select_all = QAction("Select &All", self)
        self._act_select_all.setShortcut("Ctrl+A")
        self._act_select_all.triggered.connect(self._edit_select_all)

        self._act_clear_selection = QAction("Clear Selection", self)
        self._act_clear_selection.setShortcut("Ctrl+D")
        self._act_clear_selection.setEnabled(False)
        self._act_clear_selection.triggered.connect(self._edit_clear_selection)

        self._act_delete_selection = QAction("Delete Selected", self)
        self._act_delete_selection.setShortcut("Delete")
        self._act_delete_selection.setEnabled(False)
        self._act_delete_selection.triggered.connect(self._edit_delete_selection)

        self._act_invert_selection = QAction("&Invert Selection", self)
        self._act_invert_selection.setEnabled(False)
        self._act_invert_selection.triggered.connect(self._edit_invert_selection)

        self._act_mirror_vertical = QAction("Mirror &Vertically", self)
        self._act_mirror_vertical.setEnabled(False)
        self._act_mirror_vertical.triggered.connect(self._edit_mirror_vertical)

        self._act_mirror_horizontal = QAction("Mirror &Horizontally", self)
        self._act_mirror_horizontal.setEnabled(False)
        self._act_mirror_horizontal.triggered.connect(self._edit_mirror_horizontal)

        # Tools (checkable, exclusive)

        self._act_pan = QAction(QIcon(os.path.join(_icons, "pan.svg")),
                                 "Pan", self)
        self._act_pan.setCheckable(True)
        self._act_pan.triggered.connect(self._on_tool_pan)

        self._act_select = QAction(QIcon(os.path.join(_icons, "select_point.svg")),
                                   "Select Stitch Points", self)
        self._act_select.setCheckable(True)
        self._act_select.triggered.connect(self._on_tool_select)

        self._act_add = QAction(QIcon(os.path.join(_icons, "add_point.svg")),
                                "Add Stitch Point", self)
        self._act_add.setCheckable(True)
        self._act_add.triggered.connect(self._on_tool_add)

        self._act_move = QAction(QIcon(os.path.join(_icons, "move_point.svg")),
                                "Move Stitch Point", self)
        self._act_move.setCheckable(True)
        self._act_move.triggered.connect(self._on_tool_move)

        self._act_delete = QAction(QIcon(os.path.join(_icons, "delete_point.svg")),
                                   "Delete Stitch Point", self)
        self._act_delete.setCheckable(True)
        self._act_delete.triggered.connect(self._on_tool_delete)

        self._tool_group = QActionGroup(self)
        self._tool_group.setExclusive(True)
        self._tool_group.addAction(self._act_pan)
        self._tool_group.addAction(self._act_select)
        self._tool_group.addAction(self._act_add)
        self._tool_group.addAction(self._act_move)
        self._tool_group.addAction(self._act_delete)

        self._act_zoom_in = QAction(QIcon(os.path.join(_icons, "zoom_in.svg")),
                                    "Zoom In", self)
        self._act_zoom_in.setShortcut("Ctrl++")
        self._act_zoom_in.triggered.connect(self._zoom_in)

        self._act_zoom_out = QAction(QIcon(os.path.join(_icons, "zoom_out.svg")),
                                     "Zoom Out", self)
        self._act_zoom_out.setShortcut("Ctrl+-")
        self._act_zoom_out.triggered.connect(self._zoom_out)

        self._act_fit_height = QAction(QIcon(os.path.join(_icons, "fit_height.svg")),
                                       "Fit Height", self)
        self._act_fit_height.setShortcut("Ctrl+0")
        self._act_fit_height.triggered.connect(self._zoom_fit_height)

        self._act_fit_screen = QAction(QIcon(os.path.join(_icons, "fit_screen.svg")),
                                       "Fit Screen", self)
        self._act_fit_screen.triggered.connect(self._zoom_fit_screen)

        self._act_fit_pattern = QAction(QIcon(os.path.join(_icons, "fit_pattern.svg")),
                                        "Fit Pattern", self)
        self._act_fit_pattern.triggered.connect(self._fit_pattern)

        # View orientation
        self._act_orientation_default = QAction("Default", self)
        self._act_orientation_default.setCheckable(True)
        self._act_orientation_default.setChecked(True)
        self._act_orientation_default.triggered.connect(self._on_orientation_default)

        self._act_orientation_sewing = QAction("Sewing Direction", self)
        self._act_orientation_sewing.setCheckable(True)
        self._act_orientation_sewing.triggered.connect(self._on_orientation_sewing)

        self._orientation_group = QActionGroup(self)
        self._orientation_group.setExclusive(True)
        self._orientation_group.addAction(self._act_orientation_default)
        self._orientation_group.addAction(self._act_orientation_sewing)

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

        self._act_get_releases = QAction("&Get new version", self)
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
        edit_menu.addSeparator()
        edit_menu.addAction(self._act_select_all)
        edit_menu.addAction(self._act_clear_selection)
        edit_menu.addSeparator()
        edit_menu.addAction(self._act_delete_selection)
        edit_menu.addAction(self._act_invert_selection)
        edit_menu.addAction(self._act_mirror_vertical)
        edit_menu.addAction(self._act_mirror_horizontal)

        tools_menu = mb.addMenu("&Tools")
        tools_menu.addAction(self._act_pan)
        tools_menu.addAction(self._act_select)
        tools_menu.addAction(self._act_add)
        tools_menu.addAction(self._act_move)
        tools_menu.addAction(self._act_delete)

        view_menu = mb.addMenu("&View")
        orientation_menu = view_menu.addMenu("View &Orientation")
        orientation_menu.addAction(self._act_orientation_default)
        orientation_menu.addAction(self._act_orientation_sewing)
        view_menu.addSeparator()
        view_menu.addAction(self._act_zoom_in)
        view_menu.addAction(self._act_zoom_out)
        view_menu.addSeparator()
        view_menu.addAction(self._act_fit_height)
        view_menu.addAction(self._act_fit_screen)
        view_menu.addAction(self._act_fit_pattern)

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

        tb.addAction(self._act_undo)
        tb.addAction(self._act_redo)
        tb.addSeparator()
        tb.addAction(self._act_pan)
        tb.addAction(self._act_select)
        tb.addAction(self._act_add)
        tb.addAction(self._act_move)
        tb.addAction(self._act_delete)
        tb.addSeparator()
        tb.addAction(self._act_zoom_in)
        tb.addAction(self._act_zoom_out)
        tb.addAction(self._act_fit_height)
        tb.addAction(self._act_fit_screen)
        tb.addAction(self._act_fit_pattern)

    # ── Tool selection ──

    def _on_tool_pan(self):
        self._canvas.set_tool(self._pan_tool)
        self._tool_label.setText("Tool: Pan")

    def _on_tool_select(self):
        self._canvas.set_tool(self._select_tool)
        self._tool_label.setText("Tool: Select Stitch Points")

    def _on_tool_add(self):
        self._canvas.set_tool(self._add_tool)
        self._tool_label.setText("Tool: Add Stitch Point")

    def _on_tool_move(self):
        self._canvas.set_tool(self._move_tool)
        self._tool_label.setText("Tool: Move Stitch Point")

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

    # ── View orientation ──

    def _on_orientation_default(self):
        self._view_orientation = "default"
        self._canvas.set_view_orientation("default")
        self._fit_pattern()

    def _on_orientation_sewing(self):
        self._view_orientation = "sewing_direction"
        self._canvas.set_view_orientation("sewing_direction")
        self._fit_pattern()

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
        self._canvas._update_size()
        self._canvas.update()
        self._on_pattern_changed()

        # Center the pattern in the view
        self._fit_pattern()

    # ── Status bar updates ──

    def _on_cursor_moved(self, cx, cy):
        cx_clamped = max(0, min(self._pattern.CANVAS_WIDTH, cx))
        cy_clamped = max(0, min(self._pattern.CANVAS_HEIGHT, cy))
        self._coord_label.setText(f"x: {cx_clamped:.0f}  y: {cy_clamped:.0f}")

    def _on_pattern_changed(self):
        self._count_label.setText(f"Points: {len(self._pattern.points)}")
        self._update_title()
        self._update_undo_redo_state()
        self._update_selection_action_state()

    def _update_undo_redo_state(self):
        """Enable/disable undo and redo actions based on stack availability."""
        self._act_undo.setEnabled(len(self._pattern._undo_stack) > 0)
        self._act_redo.setEnabled(len(self._pattern._redo_stack) > 0)

    def _update_selection_action_state(self):
        """Enable/disable selection-dependent actions based on whether points are selected."""
        start, end = self._canvas.get_selection()
        has_selection = start is not None and end is not None
        has_multiple_selection = has_selection and end > start
        
        self._act_clear_selection.setEnabled(has_selection)
        self._act_delete_selection.setEnabled(has_selection)
        self._act_invert_selection.setEnabled(has_multiple_selection)
        self._act_mirror_vertical.setEnabled(has_multiple_selection)
        self._act_mirror_horizontal.setEnabled(has_multiple_selection)

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
        self._canvas.set_selected_point(None)
        self._file_path = None
        self._canvas.update()
        self._on_pattern_changed()

    def _file_open(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Stitch Pattern", "",
            "Stitch Files (*.pcd;*pcq);;All Files (*)",
        )
        if not path:
            return
        self._open_file(path)

    def _open_file(self, path):
        """Open a file and add it to recent files list."""
        try:
            pattern = file_io.load_pattern(path)
        except Exception as e:
            QMessageBox.critical(self, "Error opening file", str(e))
            return
        self._pattern = pattern
        self._canvas.pattern = pattern
        self._canvas.set_selected_point(None)
        self._file_path = path
        self._add_recent_file(path)
        
        # Update stitch type selection based on loaded pattern
        if self._pattern.stitch_type == "9mm":
            self._act_9mm.setChecked(True)
        if self._pattern.stitch_type == "MAXI":
            self._act_maxi.setChecked(True)
        else:
            pass
        
        # Update canvas
        self._canvas._update_size()
        self._canvas.update()
        self._on_pattern_changed()

        # Switch to Pan tool after opening
        self._act_pan.setChecked(True)
        self._on_tool_pan()

        # Center the pattern in the view
        self._fit_pattern()

    def _file_save(self):
        if self._file_path:
            try:
                file_io.save_pattern(self._file_path, self._pattern)
            except Exception as e:
                QMessageBox.critical(self, "Error saving file", str(e))
                return False
            self._update_title()
            return True
        return self._file_save_as()

    def _file_save_as(self):
        # Determine file filter and extension based on stitch type
        if self._pattern.stitch_type == "9mm":
            file_filter = "9mm Stitch Files (*.pcd);;All Files (*)"
            default_ext = ".pcd"
        elif self._pattern.stitch_type == "MAXI":
            file_filter = "MAXI Stitch Files (*.pcq);;All Files (*)"
            default_ext = ".pcq"
        else:
            file_filter = "Stitch Files (*);;All Files (*)"
            default_ext = ""
        
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Stitch Pattern", "",
            file_filter,
        )
        if not path:
            return False
        
        # Add extension if not already present
        if default_ext and not path.endswith(default_ext):
            path += default_ext
        
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

    def _edit_select_all(self):
        """Select all stitch points in the pattern."""
        if len(self._pattern.points) > 0:
            self._canvas.set_selection(0, len(self._pattern.points) - 1)
        else:
            self._canvas.set_selection(None, None)

    def _edit_clear_selection(self):
        """Clear all selected stitch points."""
        self._canvas.set_selection(None, None)

    def _edit_delete_selection(self):
        """Delete all selected stitch points."""
        start, end = self._canvas.get_selection()
        if start is None or end is None:
            return  # No selection
        
        # Delete from highest to lowest index to avoid index shifting
        for idx in range(end, start - 1, -1):
            self._pattern.delete_point(idx)
        
        # Clear selection and update
        self._canvas.set_selection(None, None)
        self._canvas.update()
        self._on_pattern_changed()

    def _edit_invert_selection(self):
        """Invert the order of selected stitch points."""
        start, end = self._canvas.get_selection()
        if start is None or end is None:
            return  # No selection
        
        self._pattern.invert_selection(start, end)
        self._canvas.update()
        self._on_pattern_changed()

    def _edit_mirror_vertical(self):
        """Mirror selected points vertically around the center of selection."""
        start, end = self._canvas.get_selection()
        if start is None or end is None:
            return  # No selection
        
        self._pattern.mirror_vertical(start, end)
        self._canvas.update()
        self._on_pattern_changed()

    def _edit_mirror_horizontal(self):
        """Mirror selected points horizontally around the center of selection."""
        start, end = self._canvas.get_selection()
        if start is None or end is None:
            return  # No selection
        
        self._pattern.mirror_horizontal(start, end)
        self._canvas.update()
        self._on_pattern_changed()
    # ── Zoom actions ──

    def _zoom_at_cursor(self, factor):
        """Zoom by factor, anchored to the current cursor position."""
        canvas_pos = self._canvas.mapFromGlobal(QCursor.pos())
        if not self._canvas.rect().contains(canvas_pos):
            # Cursor outside canvas: anchor to visible centre
            vp = self._scroll.viewport()
            h = self._scroll.horizontalScrollBar().value()
            v = self._scroll.verticalScrollBar().value()
            canvas_pos = QPoint(h + vp.width() // 2, v + vp.height() // 2)
        self._canvas.zoom_at(self._canvas.get_scale() * factor, canvas_pos)

    def _zoom_in(self):
        self._zoom_at_cursor(1.25)

    def _zoom_out(self):
        self._zoom_at_cursor(1.0 / 1.25)

    def _zoom_fit_height(self):
        if self._view_orientation == "sewing_direction":
            # In sewing direction, "Fit Height" actually fits the width of the canvas
            viewport_w = self._scroll.viewport().width()
            if viewport_w <= 0:
                viewport_w = self.width() - 20  # rough estimate before show
            scale = (viewport_w - 2 * self._canvas.MARGIN) / self._pattern.CANVAS_HEIGHT
        else:
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
        # In sewing_direction, displayed width is CANVAS_HEIGHT and displayed height is CANVAS_WIDTH
        if self._view_orientation == "sewing_direction":
            sx = (vw - margin2) / self._pattern.CANVAS_HEIGHT
            sy = (vh - margin2) / self._pattern.CANVAS_WIDTH
        else:
            sx = (vw - margin2) / self._pattern.CANVAS_WIDTH
            sy = (vh - margin2) / self._pattern.CANVAS_HEIGHT
        self._canvas.set_scale(min(sx, sy))

    def _fit_pattern(self):
        """Fit the view to show only the designed stitch pattern."""
        if not self._pattern.points:
            return
        
        # Calculate bounding box of all points (using bottom-left reference)
        xs = [x for x, y in self._pattern.points]
        ys = [y for x, y in self._pattern.points]
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        
        # Add margins around the pattern
        margin = 20
        bounds_width = max_x - min_x + 2 * margin
        bounds_height = max_y - min_y + 2 * margin
        
        # Calculate scale to fit bounds in viewport
        vp = self._scroll.viewport()
        vw, vh = vp.width(), vp.height()
        if vw <= 0 or vh <= 0:
            vw, vh = self.width() - 20, self.height() - 80
        
        # In sewing_direction, swap width/height for scale calculation
        if self._view_orientation == "sewing_direction":
            scale_x = vw / bounds_height if bounds_height > 0 else 1
            scale_y = vh / bounds_width if bounds_width > 0 else 1
        else:
            scale_x = vw / bounds_width if bounds_width > 0 else 1
            scale_y = vh / bounds_height if bounds_height > 0 else 1
        new_scale = min(scale_x, scale_y)
        
        self._canvas.set_scale(new_scale)
        
        # Calculate center of pattern bounds (in pattern coordinates, bottom-left reference)
        bounds_center_x = min_x + (max_x - min_x) / 2
        bounds_center_y = min_y + (max_y - min_y) / 2
        
        # Convert pattern coordinates to canvas pixel coordinates, accounting for orientation
        if self._view_orientation == "sewing_direction":
            # canvas_to_screen: sx = MARGIN + (CANVAS_HEIGHT - cy) * scale
            #                   sy = MARGIN + (CANVAS_WIDTH - cx) * scale
            canvas_center_x = (self._pattern.CANVAS_HEIGHT - bounds_center_y) * new_scale + self._canvas.MARGIN
            canvas_center_y = (self._pattern.CANVAS_WIDTH - bounds_center_x) * new_scale + self._canvas.MARGIN
        else:
            # Default: pattern y is measured from bottom, canvas y is measured from top
            canvas_center_x = bounds_center_x * new_scale + self._canvas.MARGIN
            canvas_center_y = (self._pattern.CANVAS_HEIGHT - bounds_center_y) * new_scale + self._canvas.MARGIN
        
        # Scroll to center the pattern in the viewport
        h_scroll = self._scroll.horizontalScrollBar()
        v_scroll = self._scroll.verticalScrollBar()
        
        h_scroll.setValue(int(canvas_center_x - vw / 2))
        v_scroll.setValue(int(canvas_center_y - vh / 2))

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
            '<a href="https://github.com/arthendev/pcstitchdesigner">'
            "github.com/arthendev/pcstitchdesigner</a><br>"
            "<b>New Releases:</b> "
            '<a href="https://github.com/arthendev/pcstitchdesigner/releases">'
            "github.com/arthendev/pcstitchdesigner/releases</a>"
        )
        label.setOpenExternalLinks(True)
        label.setTextFormat(Qt.RichText)
        
        msg_box.layout().addWidget(label, 0, 0)
        msg_box.exec_()

    def _help_get_releases(self):
        """Open GitHub releases page in default web browser."""
        QDesktopServices.openUrl(
            QUrl("https://github.com/arthendev/pcstitchdesigner/releases")
        )

    # ── Keyboard events ──

    def keyPressEvent(self, event):
        """Handle keyboard events."""
        if event.key() == Qt.Key_Escape:
            # If no points selected, switch to Pan tool
            if (self._canvas.get_selection() == (None, None)):
                self._act_pan.setChecked(True)
                self._on_tool_pan()
            # If AddPointTool active and last point selected, switch to Pan tool
            elif (isinstance(self._canvas._tool, AddPointTool) and
                  self._canvas.get_selected_point() == len(self._pattern.points) - 1):
                self._act_pan.setChecked(True)
                self._on_tool_pan()
            else:
                # Default: clear selection
                self._canvas.set_selection(None, None)
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
