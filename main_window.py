"""Main application window with menus, toolbar, status bar, and canvas."""

import os

from PyQt5.QtWidgets import (
    QMainWindow, QScrollArea, QAction, QActionGroup,
    QFileDialog, QMessageBox, QToolBar, QLabel, QMenu, QDialog,
)
from PyQt5.QtCore import Qt, QUrl, QPoint, QEvent
from PyQt5.QtGui import QIcon, QKeyEvent, QCursor
from PyQt5.QtGui import QDesktopServices

from model import StitchPattern, ELEM_STITCH, ELEM_AUTO, elem_has_coords
from canvas import StitchCanvas
from tools import PanTool, AddPointTool, MovePointTool, DeletePointTool, SelectPointTool
import file_io
from config import Config
from version import APP_VERSION
from preferences_dialog import PreferencesDialog
from machine_comm import MachineComm, MachineCommError
from pmemory_dialog import PMemoryDialog
from animation_window import AnimationWindow
from browser_dialog import PatternBrowserDialog
from color_palette_bar import ColorPaletteBar

from auto_stitch_dialog import AutoStitchLengthDialog


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PC Stitch Designer")
        self.resize(1200, 700)

        # Load configuration
        self._config = Config()
        self._recent_files = self._config.get_recent_files()

        # Machine communication
        self._machine_comm = MachineComm()

        self._file_path = None
        self._clipboard = None  # List of (x, y) tuples copied from selection
        self._pattern = StitchPattern()
        self._canvas = StitchCanvas(self._pattern)
        self._canvas.changed.connect(self._on_pattern_changed)
        self._canvas.cursor_moved.connect(self._on_cursor_moved)
        self._canvas.selection_changed.connect(self._update_selection_action_state)
        self._canvas.drag_finished.connect(self._on_drag_finished)
        
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

        # Color palette toolbar (left edge, hidden until a palette is loaded)
        self._palette_bar = ColorPaletteBar(self)
        self.addToolBar(Qt.LeftToolBarArea, self._palette_bar)

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
        # Apply saved display settings to canvas
        self._apply_display_settings()
        self._last_auto_stitch_length_mm = None
        self._last_auto_stitch_max_dx_active = True

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

        self._act_browser = QAction("&Browser…", self)
        self._act_browser.triggered.connect(self._file_browser)

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

        self._act_copy = QAction("&Copy", self)
        self._act_copy.setShortcut("Ctrl+C")
        self._act_copy.setEnabled(False)
        self._act_copy.triggered.connect(self._edit_copy)

        self._act_cut = QAction("Cu&t", self)
        self._act_cut.setShortcut("Ctrl+X")
        self._act_cut.setEnabled(False)
        self._act_cut.triggered.connect(self._edit_cut)

        self._act_paste = QAction("&Paste", self)
        self._act_paste.setShortcut("Ctrl+V")
        self._act_paste.setEnabled(False)
        self._act_paste.triggered.connect(self._edit_paste)

        self._act_select_all = QAction("Select &All", self)
        self._act_select_all.setShortcut("Ctrl+A")
        self._act_select_all.triggered.connect(self._edit_select_all)

        self._act_clear_selection = QAction("Clear Selection", self)
        self._act_clear_selection.setShortcut("Ctrl+D")
        self._act_clear_selection.setEnabled(False)
        self._act_clear_selection.triggered.connect(self._edit_clear_selection)

        self._act_delete_selected = QAction("&Delete", self)
        self._act_delete_selected.setShortcut("Delete")
        self._act_delete_selected.setEnabled(False)
        self._act_delete_selected.triggered.connect(self._edit_delete_selected)

        self._act_invert_selected = QAction("&Invert Selected", self)
        self._act_invert_selected.setEnabled(False)
        self._act_invert_selected.triggered.connect(self._edit_invert_selected)

        self._act_mirror_vertical = QAction("Mirror &Vertically", self)
        self._act_mirror_vertical.setEnabled(False)
        self._act_mirror_vertical.triggered.connect(self._edit_mirror_vertical)

        self._act_mirror_horizontal = QAction("Mirror &Horizontally", self)
        self._act_mirror_horizontal.setEnabled(False)
        self._act_mirror_horizontal.triggered.connect(self._edit_mirror_horizontal)

        self._act_sel_extend = QAction("Extend by 1 stitch", self)
        self._act_sel_extend.setEnabled(False)
        self._act_sel_extend.triggered.connect(self._edit_sel_extend)

        self._act_sel_reduce = QAction("Reduce by 1 stitch", self)
        self._act_sel_reduce.setEnabled(False)
        self._act_sel_reduce.triggered.connect(self._edit_sel_reduce)

        self._act_sel_move_forward = QAction("Move forwards", self)
        self._act_sel_move_forward.setEnabled(False)
        self._act_sel_move_forward.triggered.connect(self._edit_sel_move_forward)

        self._act_sel_move_backward = QAction("Move backwards", self)
        self._act_sel_move_backward.setEnabled(False)
        self._act_sel_move_backward.triggered.connect(self._edit_sel_move_backward)

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

        self._act_show_grid = QAction(QIcon(os.path.join(_icons, "grid.svg")), "Show &Grid", self)
        self._act_show_grid.setCheckable(True)
        self._act_show_grid.setChecked(True)
        self._act_show_grid.triggered.connect(self._toggle_show_grid)

        # Menu-only version: checkbox, no icon
        self._act_show_grid_menu = QAction("Show &Grid", self)
        self._act_show_grid_menu.setCheckable(True)
        self._act_show_grid_menu.setChecked(True)
        self._act_show_grid_menu.triggered.connect(self._toggle_show_grid)
        self._act_show_grid_menu.triggered.connect(self._act_show_grid.setChecked)
        self._act_show_grid.triggered.connect(self._act_show_grid_menu.setChecked)

        self._act_show_stitch_points = QAction(
            QIcon(os.path.join(_icons, "stitch_point.svg")), "Show &Stitch Points", self)
        self._act_show_stitch_points.setCheckable(True)
        self._act_show_stitch_points.setChecked(True)
        self._act_show_stitch_points.triggered.connect(self._toggle_show_stitch_points)

        # Menu-only version: checkbox, no icon
        self._act_show_stitch_points_menu = QAction("Show &Stitch Points", self)
        self._act_show_stitch_points_menu.setCheckable(True)
        self._act_show_stitch_points_menu.setChecked(True)
        self._act_show_stitch_points_menu.triggered.connect(self._toggle_show_stitch_points)
        self._act_show_stitch_points_menu.triggered.connect(self._act_show_stitch_points.setChecked)
        self._act_show_stitch_points.triggered.connect(self._act_show_stitch_points_menu.setChecked)

        self._act_show_auto_stitch_points = QAction(
            QIcon(os.path.join(_icons, "stitch_cross.svg")),
            "Show Automatic Stitch Points",
            self,
        )
        self._act_show_auto_stitch_points.setCheckable(True)
        self._act_show_auto_stitch_points.setChecked(True)
        self._act_show_auto_stitch_points.triggered.connect(self._toggle_show_auto_stitch_points)

        self._act_show_auto_stitch_points_menu = QAction("Show Automatic Stitch Points", self)
        self._act_show_auto_stitch_points_menu.setCheckable(True)
        self._act_show_auto_stitch_points_menu.setChecked(True)
        self._act_show_auto_stitch_points_menu.triggered.connect(self._toggle_show_auto_stitch_points)
        self._act_show_auto_stitch_points_menu.triggered.connect(
            self._act_show_auto_stitch_points.setChecked
        )
        self._act_show_auto_stitch_points.triggered.connect(
            self._act_show_auto_stitch_points_menu.setChecked
        )

        self._act_animate = QAction(
            QIcon(os.path.join(_icons, "player.svg")),
            "&Animate Stitching", self
        )
        self._act_animate.triggered.connect(self._view_animate_stitching)

        # View orientation toggle (toolbar button)
        self._act_toggle_orientation = QAction(
            QIcon(os.path.join(_icons, "rotate_view.svg")),
            "Toggle Orientation", self
        )
        self._act_toggle_orientation.setCheckable(True)
        self._act_toggle_orientation.setToolTip(
            "Toggle view orientation: Default / Sewing Direction"
        )
        self._act_toggle_orientation.triggered.connect(self._on_toggle_orientation)

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

        # Hoop type (Small / Large) - visible when P-Design is selected
        self._act_small_hoop = QAction("Small Hoop", self)
        self._act_small_hoop.setCheckable(True)
        self._act_small_hoop.triggered.connect(self._on_stitch_small_hoop)

        self._act_large_hoop = QAction("Large Hoop", self)
        self._act_large_hoop.setCheckable(True)
        self._act_large_hoop.triggered.connect(self._on_stitch_large_hoop)

        self._stitch_group.addAction(self._act_small_hoop)
        self._stitch_group.addAction(self._act_large_hoop)

        # Design – Automatic Stitches
        self._act_set_auto_stitch_length = QAction("Set Maximum &Length…", self)
        self._act_set_auto_stitch_length.triggered.connect(self._design_set_auto_stitch_length)

        self._act_remove_auto_stitches = QAction("Remove &All", self)
        self._act_remove_auto_stitches.triggered.connect(self._design_remove_auto_stitches)

        self._act_convert_auto_stitches = QAction("&Convert to Normal Stitches", self)
        self._act_convert_auto_stitches.triggered.connect(self._design_convert_auto_stitches)

        self._act_auto_stitch_align_grid = QAction("&Align to Grid", self)
        self._act_auto_stitch_align_grid.setCheckable(True)
        self._act_auto_stitch_align_grid.setChecked(False)
        self._act_auto_stitch_align_grid.triggered.connect(self._design_auto_stitch_align_grid_toggled)

        # Machine
        self._act_machine_load_pmem = QAction("Load P-Memory", self)
        self._act_machine_load_pmem.triggered.connect(self._machine_load_pmemory)

        self._act_machine_send_pmem = QAction("Send P-Memory", self)
        self._act_machine_send_pmem.triggered.connect(self._machine_send_pmemory)

        self._act_machine_insert_pmem = QAction("Insert P-Memory", self)
        self._act_machine_insert_pmem.triggered.connect(self._machine_insert_pmemory)

        self._act_machine_delete_pmem = QAction("Delete P-Memory", self)
        self._act_machine_delete_pmem.triggered.connect(self._machine_delete_pmemory)

        self._act_machine_config = QAction("Configuration…", self)
        self._act_machine_config.triggered.connect(self._machine_configuration)

        # Settings
        self._act_preferences = QAction(QIcon(os.path.join(_icons, "settings.svg")), "Preferences…", self)
        self._act_preferences.triggered.connect(self._settings_preferences)

        # Help
        self._act_about = QAction("&About", self)
        self._act_about.triggered.connect(self._help_about)

        self._act_get_releases = QAction("&Get new version", self)
        self._act_get_releases.triggered.connect(self._help_get_releases)

        self._act_donate = QAction("&Donate!", self)
        self._act_donate.triggered.connect(self._help_donate)

    # ── Menus ──

    def _build_menus(self):
        mb = self.menuBar()

        file_menu = mb.addMenu("&File")
        file_menu.addAction(self._act_new)
        file_menu.addAction(self._act_open)
        file_menu.addAction(self._act_browser)
        
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
        edit_menu.addAction(self._act_copy)
        edit_menu.addAction(self._act_cut)
        edit_menu.addAction(self._act_paste)
        edit_menu.addAction(self._act_delete_selected)
        edit_menu.addSeparator()
        edit_menu.addAction(self._act_select_all)
        sel_submenu = edit_menu.addMenu("&Selection")
        sel_submenu.addAction(self._act_sel_extend)
        sel_submenu.addAction(self._act_sel_reduce)
        sel_submenu.addSeparator()
        sel_submenu.addAction(self._act_sel_move_forward)
        sel_submenu.addAction(self._act_sel_move_backward)
        edit_menu.addAction(self._act_clear_selection)
        edit_menu.addSeparator()
        edit_menu.addAction(self._act_invert_selected)
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
        view_menu.addSeparator()
        view_menu.addAction(self._act_show_grid_menu)
        view_menu.addAction(self._act_show_stitch_points_menu)
        view_menu.addAction(self._act_show_auto_stitch_points_menu)
        view_menu.addSeparator()
        view_menu.addAction(self._act_animate)

        design_menu = mb.addMenu("&Design")
        design_menu.addAction(self._act_pdesign)
        design_menu.addAction(self._act_mdesign)
        design_menu.addSeparator()
        design_menu.addAction(self._act_9mm)
        design_menu.addAction(self._act_maxi)
        design_menu.addAction(self._act_small_hoop)
        design_menu.addAction(self._act_large_hoop)
        design_menu.addSeparator()
        auto_stitches_menu = design_menu.addMenu("&Automatic Stitches")
        auto_stitches_menu.addAction(self._act_set_auto_stitch_length)
        auto_stitches_menu.addAction(self._act_remove_auto_stitches)
        auto_stitches_menu.addAction(self._act_convert_auto_stitches)
        auto_stitches_menu.addSeparator()
        auto_stitches_menu.addAction(self._act_auto_stitch_align_grid)

        machine_menu = mb.addMenu("&Machine")
        machine_menu.addAction(self._act_machine_load_pmem)
        machine_menu.addAction(self._act_machine_send_pmem)
        machine_menu.addAction(self._act_machine_insert_pmem)
        machine_menu.addAction(self._act_machine_delete_pmem)
        machine_menu.addSeparator()
        machine_menu.addAction(self._act_machine_config)

        settings_menu = mb.addMenu("&Settings")
        settings_menu.addAction(self._act_preferences)

        help_menu = mb.addMenu("&Help")
        help_menu.addAction(self._act_get_releases)
        help_menu.addAction(self._act_donate)
        help_menu.addSeparator()
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
        tb.addSeparator()
        tb.addAction(self._act_toggle_orientation)
        tb.addAction(self._act_show_grid)
        tb.addAction(self._act_show_stitch_points)
        tb.addAction(self._act_show_auto_stitch_points)
        tb.addSeparator()
        tb.addAction(self._act_animate)
        tb.addSeparator()
        tb.addAction(self._act_preferences)

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
        self._act_small_hoop.setVisible(True)
        self._act_large_hoop.setVisible(True)

    def _on_mdesign_selected(self):
        self._act_9mm.setVisible(False)
        self._act_maxi.setVisible(False)
        self._act_small_hoop.setVisible(False)
        self._act_large_hoop.setVisible(False)

    # ── View orientation ──

    def _view_animate_stitching(self):
        dlg = AnimationWindow(self._pattern, parent=self)
        dlg.exec_()

    def _on_orientation_default(self):
        self._view_orientation = "default"
        self._canvas.set_view_orientation("default")
        self._act_toggle_orientation.setChecked(False)
        self._fit_pattern()

    def _on_orientation_sewing(self):
        self._view_orientation = "sewing_direction"
        self._canvas.set_view_orientation("sewing_direction")
        self._act_toggle_orientation.setChecked(True)
        self._fit_pattern()

    def _on_toggle_orientation(self, checked):
        if checked:
            self._act_orientation_sewing.setChecked(True)
            self._on_orientation_sewing()
        else:
            self._act_orientation_default.setChecked(True)
            self._on_orientation_default()

    # ── Stitch type selection ──

    def _pattern_fits_in_canvas(self, canvas_size):
        """Check if current pattern fits in the given canvas size."""
        w, h = StitchPattern.CANVAS_SIZES[canvas_size]
        for e in self._pattern.elements:
            if elem_has_coords(e):
                x, y = e[1], e[2]
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
        self._apply_display_settings()
        self._canvas._update_size()
        self._canvas.update()
        self._on_pattern_changed()
        self._update_hoop_restricted_actions()
        if not self._pattern.elements:
            self._zoom_fit_height()

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
        self._apply_display_settings()
        self._canvas._update_size()
        self._canvas.update()
        self._on_pattern_changed()
        self._update_hoop_restricted_actions()

        # Center the pattern in the view
        if self._pattern.elements:
            self._fit_pattern()
        else:
            self._zoom_fit_height()

    def _on_stitch_small_hoop(self):
        if not self._pattern_fits_in_canvas("small hoop"):
            QMessageBox.warning(
                self, "Canvas Size Change",
                "Current pattern will be too large for the working area."
            )
            self._act_large_hoop.setChecked(True)
            return
        was_hoop = self._is_hoop_type()
        self._pattern.stitch_type = "small hoop"
        if self._pattern.has_auto_stitches:
            self._pattern.clear_auto_stitches()
        self._last_auto_stitch_length_mm = None
        self._last_auto_stitch_max_dx_active = False
        self._apply_display_settings()
        self._canvas._update_size()
        self._canvas.update()
        self._on_pattern_changed()
        self._update_hoop_restricted_actions()
        if not was_hoop:
            self._show_hoop_info()
        if self._pattern.elements:
            self._fit_pattern()
        else:
            self._zoom_fit_height()

    def _on_stitch_large_hoop(self):
        if not self._pattern_fits_in_canvas("large hoop"):
            QMessageBox.warning(
                self, "Canvas Size Change",
                "Current pattern will be too large for the working area."
            )
            self._act_small_hoop.setChecked(True)
            return
        was_hoop = self._is_hoop_type()
        self._pattern.stitch_type = "large hoop"
        if self._pattern.has_auto_stitches:
            self._pattern.clear_auto_stitches()
        self._last_auto_stitch_length_mm = None
        self._last_auto_stitch_max_dx_active = False
        self._apply_display_settings()
        self._canvas._update_size()
        self._canvas.update()
        self._on_pattern_changed()
        self._update_hoop_restricted_actions()
        if not was_hoop:
            self._show_hoop_info()
        if self._pattern.elements:
            self._fit_pattern()
        else:
            self._zoom_fit_height()

    # ── Status bar updates ──

    def _show_hoop_info(self):
        QMessageBox.information(
            self, "Embroidery Design",
            "Embroidery designs can be loaded and viewed, "
            "but editing and transfer to the sewing machine are not yet supported."
        )

    def _is_hoop_type(self):
        """Return True when the current stitch type is a hoop (read-only) pattern."""
        return self._pattern.stitch_type in ("small hoop", "large hoop")

    def _update_hoop_restricted_actions(self):
        """Enable or disable actions that are not supported for hoop patterns."""
        enabled = not self._is_hoop_type()
        # File
        self._act_save.setEnabled(enabled)
        self._act_save_as.setEnabled(enabled)
        # Tools
        self._act_add.setEnabled(enabled)
        self._act_delete.setEnabled(enabled)
        # Machine
        self._act_machine_send_pmem.setEnabled(enabled)
        self._act_machine_insert_pmem.setEnabled(enabled)
        # Edit (selection-dependent ones are re-evaluated by _update_selection_action_state)
        self._act_copy.setEnabled(enabled and self._act_copy.isEnabled())
        self._act_cut.setEnabled(enabled and self._act_cut.isEnabled())
        self._act_paste.setEnabled(enabled and self._clipboard is not None)
        self._act_delete_selected.setEnabled(enabled and self._act_delete_selected.isEnabled())
        self._act_invert_selected.setEnabled(enabled and self._act_invert_selected.isEnabled())
        self._act_mirror_vertical.setEnabled(enabled and self._act_mirror_vertical.isEnabled())
        self._act_mirror_horizontal.setEnabled(enabled and self._act_mirror_horizontal.isEnabled())
        # View orientation
        self._act_orientation_sewing.setEnabled(enabled)
        self._act_toggle_orientation.setEnabled(enabled)
        if not enabled:
            # Force back to default orientation for hoop types
            self._act_orientation_default.setChecked(True)
            self._on_orientation_default()
        # Automatic stitches — disabled entirely for hoop types
        self._act_set_auto_stitch_length.setEnabled(enabled)
        self._act_remove_auto_stitches.setEnabled(enabled)
        self._act_convert_auto_stitches.setEnabled(enabled)
        self._act_auto_stitch_align_grid.setEnabled(enabled)
        # If hoop type is active and the current tool is add/delete, switch to pan
        if not enabled and self._canvas._tool in (self._add_tool, self._delete_tool):
            self._act_pan.setChecked(True)
            self._on_tool_pan()

    def _on_cursor_moved(self, cx, cy):
        cx_clamped = max(0, min(self._pattern.CANVAS_WIDTH, cx))
        cy_clamped = max(0, min(self._pattern.CANVAS_HEIGHT, cy))
        self._coord_label.setText(f"x: {cx_clamped:.0f}  y: {cy_clamped:.0f}")

    def _on_pattern_changed(self):
        stitch_count = sum(1 for e in self._pattern.elements if elem_has_coords(e))
        self._count_label.setText(f"Stitches: {stitch_count}")
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
        hoop = self._is_hoop_type()

        n = len(self._pattern.elements)
        self._act_copy.setEnabled(has_multiple_selection and not hoop)
        self._act_cut.setEnabled(has_multiple_selection and not hoop)
        self._act_paste.setEnabled(self._clipboard is not None and not hoop)
        self._act_clear_selection.setEnabled(has_selection)
        self._act_delete_selected.setEnabled(has_selection and not hoop)
        self._act_invert_selected.setEnabled(has_multiple_selection and not hoop)
        self._act_mirror_vertical.setEnabled(has_multiple_selection and not hoop)
        self._act_mirror_horizontal.setEnabled(has_multiple_selection and not hoop)
        self._act_sel_extend.setEnabled(has_selection and end < n - 1)
        self._act_sel_reduce.setEnabled(has_multiple_selection)
        self._act_sel_move_backward.setEnabled(has_selection and start > 0)
        self._act_sel_move_forward.setEnabled(has_selection and end < n - 1)

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
        self._update_palette_bar()
        self._last_auto_stitch_length_mm = None
        self._last_auto_stitch_max_dx_active = True

    def _file_open(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Stitch Pattern", "",
            "All Supported Files (*.pcd *.pcq *.pcs);;Stitch Files (*.pcd *.pcq);;Embroidery Files (*.pcs);;All Files (*)",
        )
        if not path:
            return
        self._open_file(path)

    def _file_browser(self):
        if not self._confirm_discard():
            return

        directory = os.path.dirname(self._file_path) if self._file_path else ""
        path, _ = PatternBrowserDialog.getOpenFileName(self, directory=directory)
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
        self._last_auto_stitch_length_mm = None
        self._last_auto_stitch_max_dx_active = True
        
        # Update stitch type selection based on loaded pattern
        if self._pattern.stitch_type == "9mm":
            self._act_9mm.setChecked(True)
            self._on_pdesign_selected()
        elif self._pattern.stitch_type == "MAXI":
            self._act_maxi.setChecked(True)
            self._on_pdesign_selected()
        elif self._pattern.stitch_type == "small hoop":
            self._act_small_hoop.setChecked(True)
            self._on_pdesign_selected()
        elif self._pattern.stitch_type == "large hoop":
            self._act_large_hoop.setChecked(True)
            self._on_pdesign_selected()
        
        # Update last auto stitch length from the loaded pattern if it contains auto stitches
        if self._pattern.has_auto_stitches:
            gap = self._pattern.get_max_stitch_gap_mm()
            if gap:
                self._last_auto_stitch_length_mm = gap

        # Hoop patterns never use auto-stitches
        if self._is_hoop_type():
            self._last_auto_stitch_max_dx_active = False

        # Update canvas
        self._apply_display_settings()
        self._canvas._update_size()
        self._canvas.update()
        self._on_pattern_changed()
        self._update_hoop_restricted_actions()
        self._update_palette_bar()
        if self._is_hoop_type():
            self._show_hoop_info()

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
        self._canvas.set_selection(None, None)
        self._canvas.update()
        self._on_pattern_changed()
        self._recalculate_auto_if_active()

    def _edit_redo(self):
        self._pattern.redo()
        self._canvas.set_selection(None, None)
        self._canvas.update()
        self._on_pattern_changed()
        self._recalculate_auto_if_active()

    def _edit_copy(self):
        """Copy selected stitch points to the internal clipboard."""
        start, end = self._canvas.get_selection()
        if start is None or end is None or end <= start:
            return
        self._clipboard = [(e[1], e[2]) for e in self._pattern.elements[start:end + 1] if elem_has_coords(e)]
        self._act_paste.setEnabled(True)

    def _edit_cut(self):
        """Cut selected stitch points: copy to clipboard then delete in one undo step."""
        start, end = self._canvas.get_selection()
        if start is None or end is None or end <= start:
            return
        cut_elems = self._pattern.cut_range(start, end)
        self._clipboard = [(e[1], e[2]) for e in cut_elems if elem_has_coords(e)]
        self._canvas.set_selection(None, None)
        self._canvas._update_size()
        self._canvas.update()
        self._on_pattern_changed()
        self._act_paste.setEnabled(True)
        self._recalculate_auto_if_active()

    def _edit_paste(self):
        """Paste clipboard points using the same logic as Insert P-Memory."""
        if not self._clipboard:
            return
        self._apply_insert_pattern(self._clipboard, None)

    def _edit_select_all(self):
        """Select all stitch points in the pattern."""
        if len(self._pattern.elements) > 0:
            self._canvas.set_selection(0, len(self._pattern.elements) - 1)
        else:
            self._canvas.set_selection(None, None)

    def _edit_clear_selection(self):
        """Clear all selected stitch points."""
        self._canvas.set_selection(None, None)

    def _edit_sel_extend(self):
        start, end = self._canvas.get_selection()
        if start is None or end is None:
            return
        if end < len(self._pattern.elements) - 1:
            self._canvas.set_selection(start, end + 1)
        self._canvas.update()
        self._update_selection_action_state()

    def _edit_sel_reduce(self):
        start, end = self._canvas.get_selection()
        if start is None or end is None:
            return
        if end > start:
            self._canvas.set_selection(start, end - 1)
        self._canvas.update()
        self._update_selection_action_state()

    def _edit_sel_move_backward(self):
        start, end = self._canvas.get_selection()
        if start is None or end is None:
            return
        if start > 0:
            self._canvas.set_selection(start - 1, end - 1)
        self._canvas.update()
        self._update_selection_action_state()

    def _edit_sel_move_forward(self):
        start, end = self._canvas.get_selection()
        if start is None or end is None:
            return
        if end < len(self._pattern.elements) - 1:
            self._canvas.set_selection(start + 1, end + 1)
        self._canvas.update()
        self._update_selection_action_state()

    def _edit_delete_selected(self):
        """Delete all selected stitch points."""
        start, end = self._canvas.get_selection()
        if start is None or end is None:
            return  # No selection

        self._pattern.delete_range(start, end)

        # Clear selection and update
        self._canvas.set_selection(None, None)
        self._canvas.update()
        self._on_pattern_changed()
        self._recalculate_auto_if_active()

    def _edit_invert_selected(self):
        """Invert the order of selected stitch points."""
        start, end = self._canvas.get_selection()
        if start is None or end is None:
            return  # No selection
        
        self._pattern.invert_selected(start, end)
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
        bounds = self._pattern.get_stitch_bounds()
        if bounds is None:
            return
        min_x, min_y, max_x, max_y = bounds
        
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
            # canvas_to_screen (90° CW): sx = MARGIN + cy * scale
            #                            sy = MARGIN + cx * scale
            canvas_center_x = bounds_center_y * new_scale + self._canvas.MARGIN
            canvas_center_y = bounds_center_x * new_scale + self._canvas.MARGIN
        else:
            # Default: pattern y is measured from bottom, canvas y is measured from top
            canvas_center_x = bounds_center_x * new_scale + self._canvas.MARGIN
            canvas_center_y = (self._pattern.CANVAS_HEIGHT - bounds_center_y) * new_scale + self._canvas.MARGIN
        
        # Scroll to center the pattern in the viewport
        h_scroll = self._scroll.horizontalScrollBar()
        v_scroll = self._scroll.verticalScrollBar()
        
        h_scroll.setValue(int(canvas_center_x - vw / 2))
        v_scroll.setValue(int(canvas_center_y - vh / 2))

    def _toggle_show_grid(self, checked):
        self._canvas._show_grid = checked
        self._canvas.update()

    def _toggle_show_stitch_points(self, checked):
        self._act_show_stitch_points.setChecked(checked)
        self._act_show_stitch_points_menu.setChecked(checked)
        self._canvas._show_stitch_points = checked
        self._sync_auto_stitch_point_visibility(checked)
        self._canvas.update()

    def _toggle_show_auto_stitch_points(self, checked):
        self._act_show_auto_stitch_points.setChecked(checked)
        self._act_show_auto_stitch_points_menu.setChecked(checked)
        self._sync_auto_stitch_point_visibility(self._act_show_stitch_points.isChecked())
        self._canvas.update()

    def _sync_auto_stitch_point_visibility(self, show_stitch_points=None):
        if show_stitch_points is None:
            show_stitch_points = self._act_show_stitch_points.isChecked()
        self._act_show_auto_stitch_points.setEnabled(show_stitch_points)
        self._act_show_auto_stitch_points_menu.setEnabled(show_stitch_points)
        self._canvas._show_auto_stitch_points = (
            show_stitch_points and self._act_show_auto_stitch_points.isChecked()
        )

    # ── Machine ──

    def _open_machine_connection(self):
        """Open the serial port and perform the initial query_machine handshake.

        Returns the machine identification dict on success, or None if the
        user should abort (error already shown to the user).
        """
        prefs = self._config.get_machine_preferences()
        port = prefs.get("port", "")
        if not port:
            QMessageBox.warning(
                self, "Communication Error",
                "No serial port configured.\n"
                "Please set the port in Settings → Preferences → Machine."
            )
            return None

        baudrate = (
            MachineComm.FAST_BAUDRATE
            if prefs.get("high_speed", False)
            else MachineComm.DEFAULT_BAUDRATE
        )

        try:
            self._machine_comm.open(port, baudrate=baudrate)
        except Exception as exc:
            QMessageBox.critical(
                self, "Communication Error",
                f"Could not open port {port!r}:\n{exc}"
            )
            return None

        try:
            info = self._machine_comm.query_machine()
        except (MachineCommError, Exception) as exc:
            self._machine_comm.close()
            QMessageBox.critical(
                self, "Communication Error",
                f"No communication with the machine:\n{exc}"
            )
            return None

        detected = info.get('model', '')
        configured = prefs.get('model', '')
        if detected and configured and detected != configured:
            self._machine_comm.close()
            QMessageBox.critical(
                self, "Communication Error",
                f"Connected machine ({detected}) does not match "
                f"the configured model ({configured}).\n"
                f"Please check Settings → Preferences → Machine."
            )
            return None

        return info

    def _query_and_show_pmemory(self, action):
        """Open connection, query P-Memory, and show the P-Memory dialog.

        Args:
            action (str): One of PMemoryDialog.ACTION_* constants.
        """
        # For Load, confirm discarding unsaved changes before touching the machine.
        # Insert keeps the current design open, so no confirmation is needed.
        if action == PMemoryDialog.ACTION_LOAD:
            if not self._confirm_discard():
                return

        if not self._open_machine_connection():
            return

        # Query P-Memory directory
        try:
            raw = self._machine_comm.query_pmemory()
        except (MachineCommError, Exception) as exc:
            self._machine_comm.end_transmission()
            QMessageBox.critical(
                self, "Communication Error",
                f"Failed to read P-Memory:\n{exc}"
            )
            return

        # Decode the raw response
        machine_model = self._config.get_machine_preferences().get("model", "")
        try:
            pmem_info = MachineComm.decode_pmemory(raw, machine_model)
        except Exception as exc:
            self._machine_comm.end_transmission()
            QMessageBox.critical(
                self, "Communication Error",
                f"Failed to decode P-Memory data:\n{exc}"
            )
            return

        dlg = PMemoryDialog(
            pmem_info,
            action,
            self._machine_comm,
            pattern=self._pattern if action == PMemoryDialog.ACTION_SEND else None,
            parent=self,
        )
        result = dlg.exec_()

        if action == PMemoryDialog.ACTION_LOAD and result == QDialog.Accepted:
            if dlg.loaded_points is not None:
                self._apply_machine_pattern(dlg.loaded_points, dlg.loaded_slot_type)

        if action == PMemoryDialog.ACTION_INSERT and result == QDialog.Accepted:
            if dlg.loaded_points is not None:
                self._apply_insert_pattern(dlg.loaded_points, dlg.loaded_slot_type)

    def _apply_machine_pattern(self, points, slot_type):
        """Replace the current pattern with points loaded from the machine."""
        new_pattern = StitchPattern()
        new_pattern.set_machine_data(points, slot_type or self._pattern.stitch_type)

        self._pattern = new_pattern
        self._canvas.pattern = new_pattern
        self._canvas.set_selected_point(None)
        self._file_path = None

        if new_pattern.stitch_type == "9mm":
            self._act_9mm.setChecked(True)
        elif new_pattern.stitch_type == "MAXI":
            self._act_maxi.setChecked(True)
        elif new_pattern.stitch_type == "small hoop":
            self._act_small_hoop.setChecked(True)
        elif new_pattern.stitch_type == "large hoop":
            self._act_large_hoop.setChecked(True)

        self._apply_display_settings()
        self._canvas._update_size()
        self._canvas.update()
        self._on_pattern_changed()
        self._update_hoop_restricted_actions()
        self._update_palette_bar()
        self._act_pan.setChecked(True)
        self._on_tool_pan()
        self._fit_pattern()

    def _apply_insert_pattern(self, points, slot_type, append_on_no_selection=False):
        """Replace the currently selected range with new points.

        If no points are selected and *append_on_no_selection* is False (the
        default), the last pattern point is used as the single-point selection
        (P-Memory insert behaviour).  When *append_on_no_selection* is True the
        points are instead appended to the end of the pattern (Copy/Paste
        behaviour).

        The incoming points are translated so that their first point coincides
        with the first point of the selection before the replacement takes
        place.
        """
        start, end = self._canvas.get_selection()

        if not append_on_no_selection:
            # Fall back to the last point when nothing is selected
            if start is None and self._pattern.elements:
                start = len(self._pattern.elements) - 1
                end = start

        # Warn when the loaded slot type differs from the open pattern type,
        # except when inserting a 9mm pattern into a MAXI canvas (compatible).
        if (slot_type and slot_type != self._pattern.stitch_type
                and not (slot_type == "9mm" and self._pattern.stitch_type == "MAXI")):
            ret = QMessageBox.warning(
                self, "Insert P-Memory",
                f"The loaded slot type ({slot_type}) differs from the current "
                f"pattern type ({self._pattern.stitch_type}).\n\n"
                "Point coordinates will be clamped to fit the current canvas.\n"
                "Continue?",
                QMessageBox.Ok | QMessageBox.Cancel,
                QMessageBox.Cancel,
            )
            if ret != QMessageBox.Ok:
                return

        # Translate loaded points so their first point aligns with
        # the element at [start], then clamp to the current canvas bounds.
        cw = self._pattern.CANVAS_WIDTH
        ch = self._pattern.CANVAS_HEIGHT
        if points and start is not None and start < len(self._pattern.elements):
            anchor_coords = self._pattern.get_coords(start)
            anchor_x, anchor_y = anchor_coords if anchor_coords is not None else (0, 0)
            dx = anchor_x - points[0][0]
            dy = anchor_y - points[0][1]
            translated = [(x + dx, y + dy) for x, y in points]
        else:
            translated = list(points)

        # If the translated pattern extends outside the canvas, shift it as a
        # whole so it fits without squashing.  Only clamp individual points when
        # the pattern is genuinely larger than the canvas in that axis.
        shift_applied = False
        if translated:
            xs = [x for x, y in translated]
            ys = [y for x, y in translated]
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)

            shift_x = 0
            if x_max - x_min <= cw:
                if x_min < 0:
                    shift_x = -x_min
                elif x_max > cw:
                    shift_x = cw - x_max

            shift_y = 0
            if y_max - y_min <= ch:
                if y_min < 0:
                    shift_y = -y_min
                elif y_max > ch:
                    shift_y = ch - y_max

            if shift_x or shift_y:
                translated = [(x + shift_x, y + shift_y) for x, y in translated]
                shift_applied = True

        clamped = [(max(0, min(cw, x)), max(0, min(ch, y))) for x, y in translated]

        if start is None:
            if append_on_no_selection:
                # No selection — append to end of the pattern.
                n = len(self._pattern.elements)
                self._pattern.replace_range(n, n - 1, [(ELEM_STITCH, x, y) for x, y in clamped])
                if clamped:
                    self._canvas.set_selection(n, n + len(clamped) - 1)
                else:
                    self._canvas.set_selected_point(None)
            else:
                # Pattern is empty – just load the points as-is (machine path).
                self._pattern.set_machine_data(clamped, self._pattern.stitch_type)
                self._canvas.set_selected_point(None)
        else:
            # When a canvas-fitting shift was applied the inserted pattern no
            # longer starts at the anchor point, so keep elements[start]
            # in place and replace [start+1, end] with the new points.
            # Without a shift, replace from start so the anchor is overwritten.
            replace_start = start + 1 if shift_applied else start
            self._pattern.replace_range(replace_start, end, [(ELEM_STITCH, x, y) for x, y in clamped])
            # Update selection to cover the newly inserted points
            if clamped:
                self._canvas.set_selection(replace_start, replace_start + len(clamped) - 1)
            else:
                self._canvas.set_selected_point(None)

        self._canvas._update_size()
        self._canvas.update()
        self._on_pattern_changed()
        self._recalculate_auto_if_active()

    def _machine_load_pmemory(self):
        self._query_and_show_pmemory(PMemoryDialog.ACTION_LOAD)

    def _machine_send_pmemory(self):
        if not any(elem_has_coords(e) for e in self._pattern.elements):
            QMessageBox.warning(
                self, "Send P-Memory",
                "The stitch pattern is empty. Add stitch points before sending to the machine."
            )
            return
        self._query_and_show_pmemory(PMemoryDialog.ACTION_SEND)

    def _machine_insert_pmemory(self):
        self._query_and_show_pmemory(PMemoryDialog.ACTION_INSERT)

    def _machine_delete_pmemory(self):
        self._query_and_show_pmemory(PMemoryDialog.ACTION_DELETE)

    def _machine_configuration(self):
        self._settings_preferences()

    # ── Settings ──

    def _update_palette_bar(self):
        """Show/hide and populate the color palette bar based on the current pattern."""
        if self._pattern.has_palette:
            self._palette_bar.set_colors(self._pattern.colors)
            self._palette_bar.setVisible(True)
        else:
            self._palette_bar.setVisible(False)

    def _settings_preferences(self):
        dlg = PreferencesDialog(self._config, parent=self)
        if dlg.exec_() == PreferencesDialog.Accepted:
            self._apply_display_settings()

    def _apply_display_settings(self):
        d = self._config.get_display_preferences()
        if self._is_hoop_type():
            line_width = d["embroidery_line_width"]
            point_size = d["embroidery_point_size"]
            grid_color = d["embroidery_grid_color"]
            show_stitch_points = d["embroidery_show_stitch_points"]
            show_grid = d["embroidery_show_grid"]
        else:
            line_width = d["line_width"]
            point_size = d["point_size"]
            grid_color = d["grid_color"]
            show_stitch_points = d["show_stitch_points"]
            show_grid = d["show_grid"]
        self._canvas.apply_display_settings(
            line_color=d["line_color"],
            line_width=line_width,
            point_color=d["point_color"],
            point_size=point_size,
            grid_color=grid_color,
            show_stitch_points=show_stitch_points,
            show_grid=show_grid,
        )
        self._act_show_grid.setChecked(bool(show_grid))
        self._act_show_grid_menu.setChecked(bool(show_grid))
        self._act_show_stitch_points.setChecked(bool(show_stitch_points))
        self._act_show_stitch_points_menu.setChecked(bool(show_stitch_points))
        self._sync_auto_stitch_point_visibility()

    # ── Design ──

    def _design_remove_auto_stitches(self):
        if not self._pattern.has_auto_stitches:
            return
        self._pattern.clear_auto_stitches()
        self._last_auto_stitch_length_mm = None
        self._last_auto_stitch_max_dx_active = False
        self._canvas.update()
        self._on_pattern_changed()

    def _design_convert_auto_stitches(self):
        self._pattern.convert_auto_to_normal()
        self._canvas.update()
        self._on_pattern_changed()

    def _on_drag_finished(self):
        """Called after a stitch drag is committed. Recalculates auto stitches."""
        self._recalculate_auto_if_active()

    def _recalculate_auto_if_active(self):
        """Recalculate auto stitches if a length or dx constraint has been configured."""
        if self._last_auto_stitch_length_mm is not None or self._last_auto_stitch_max_dx_active:
            max_length = self._last_auto_stitch_length_mm if self._last_auto_stitch_length_mm is not None else float('inf')
            self._pattern.recalculate_auto_stitches(
                max_length,
                align_to_grid=self._act_auto_stitch_align_grid.isChecked(),
                max_dx_mm=6.0 if self._last_auto_stitch_max_dx_active else None,
            )
            self._canvas.update()
            self._on_pattern_changed()

    def _design_set_auto_stitch_length(self):
        prefill = self._pattern.get_max_stitch_gap_mm() or self._last_auto_stitch_length_mm or 5.0
        dlg = AutoStitchLengthDialog(prefill, self._last_auto_stitch_max_dx_active, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            self._last_auto_stitch_length_mm = dlg.max_length_mm
            self._last_auto_stitch_max_dx_active = dlg.max_dx_active
            self._pattern.recalculate_auto_stitches(
                dlg.max_length_mm,
                align_to_grid=self._act_auto_stitch_align_grid.isChecked(),
                max_dx_mm=6.0 if dlg.max_dx_active else None,
            )
            self._canvas.update()
            self._on_pattern_changed()

    def _design_auto_stitch_align_grid_toggled(self):
        if self._last_auto_stitch_length_mm is not None:
            self._pattern.recalculate_auto_stitches(
                self._last_auto_stitch_length_mm,
                align_to_grid=self._act_auto_stitch_align_grid.isChecked(),
                max_dx_mm=6.0 if self._last_auto_stitch_max_dx_active else None,
            )
            self._canvas.update()
            self._on_pattern_changed()

    # ── Help ──

    def _help_about(self):
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("About PC Stitch Designer")
        # msg_box.setIcon(QMessageBox.Information)
        msg_box.setTextFormat(Qt.RichText)
        
        # Create a label with clickable links
        label = QLabel(
            f"<h3>PC Stitch Designer v{APP_VERSION}</h3>"
            "<p>A stitch pattern editor for PFAFF machines.</p>"
            "<p>Allows pattern transfer to and from PFAFF Creative 7570, 7550 and 1475 CD.</p>"
            "<p><b>Project:</b> "
            '<a href="https://github.com/arthendev/pcstitchdesigner">'
            "github.com/arthendev/pcstitchdesigner</a></p>"
            "<p><b>New Releases:</b> "
            '<a href="https://github.com/arthendev/pcstitchdesigner/releases">'
            "github.com/arthendev/pcstitchdesigner/releases</a></p>"
            # "<p><b>Documentation:</b> "
            # '<a href="https://github.com/arthendev/pcstitchdesigner/wiki">'
            # "github.com/arthendev/pcstitchdesigner/wiki</a></p>"
            "<p>© 2026 A. Frej (arthendev)</p>"
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

    def _help_donate(self):
        """Open PayPal donation page in default web browser."""
        QDesktopServices.openUrl(
            QUrl("https://www.paypal.com/donate/?hosted_button_id=ALB975LFDA7AE")
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
                  self._canvas.get_selected_point() == len(self._pattern.elements) - 1):
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
