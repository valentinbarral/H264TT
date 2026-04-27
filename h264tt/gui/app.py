#!/usr/bin/env python3
# pyright: reportOptionalMemberAccess=false, reportAttributeAccessIssue=false, reportAssignmentType=false, reportGeneralTypeIssues=false, reportArgumentType=false, reportCallIssue=false
"""
H264TT - H264 Teaching Tool

Este script combina la funcionalidad de línea de comandos y la interfaz gráfica.
- Ejecutar sin argumentos: abre la interfaz gráfica
- Ejecutar con argumentos: funciona como línea de comandos tradicional
"""

import sys
import os
import subprocess
import re
import json
import cv2  # type: ignore[attr-defined]
from pathlib import Path

from matplotlib.figure import Figure
from matplotlib.backends.backend_qtagg import FigureCanvas
from matplotlib.backends.backend_qt import NavigationToolbar2QT
from matplotlib.ticker import MultipleLocator

from h264tt.core.visualizer import MBVisualizer as CoreMBVisualizer

SETTINGS_FILE_NAME = ".h264tt_settings.json"

# Configurar entorno Qt antes de importar PyQt5
if "DISPLAY" in os.environ or os.name == "nt":
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

# Importaciones condicionales para la GUI
try:
    from PyQt5.QtWidgets import (
        QApplication,
        QMainWindow,
        QWidget,
        QVBoxLayout,
        QHBoxLayout,
        QGridLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QGroupBox,
        QCheckBox,
        QSpinBox,
        QDoubleSpinBox,
        QComboBox,
        QFileDialog,
        QMessageBox,
        QTextEdit,
        QScrollArea,
        QFrame,
        QSplitter,
        QProgressBar,
        QProgressDialog,
        QRadioButton,
        QSizePolicy,
        QDialog,
        QSlider,
        QTabWidget,
    )
    from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
    from PyQt5.QtGui import QFont, QPixmap, QIcon, QImage, QResizeEvent

    HAS_GUI = True
except ImportError as e:
    print(f"Advertencia: No se pudo importar PyQt5: {e}")
    HAS_GUI = False

    class _QtFallback:
        AlignCenter = 0
        Horizontal = 1
        WindowMaximizeButtonHint = 0
        WindowMinimizeButtonHint = 0

        class Orientation:
            Vertical = 0

    class _QtPlaceholder:
        def __init__(self, *args, **kwargs):
            pass

    QApplication = QMainWindow = QWidget = QVBoxLayout = QHBoxLayout = QGridLayout = (
        QLabel
    ) = QLineEdit = QPushButton = QGroupBox = QCheckBox = QSpinBox = QDoubleSpinBox = (
        QComboBox
    ) = QFileDialog = QMessageBox = QTextEdit = QScrollArea = QFrame = QSplitter = (
        QProgressBar
    ) = QProgressDialog = QRadioButton = QSizePolicy = QDialog = QSlider = (
        QTabWidget
    ) = QFont = QPixmap = QIcon = QImage = QResizeEvent = _QtPlaceholder
    Qt = _QtFallback()

    class QThread:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            pass

    def pyqtSignal(*args, **kwargs):  # type: ignore[no-redef]
        return None

    class QTimer(_QtPlaceholder):  # type: ignore[no-redef]
        pass


class EncodingWorker(QThread):
    """Thread para ejecutar la codificación completa sin bloquear la interfaz"""

    finished = pyqtSignal(str)  # path del video codificado
    error = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(
        self, input_file, output_file, ffmpeg_params, ffmpeg_path, ffprobe_path
    ):
        super().__init__()
        self.input_file = input_file
        self.output_file = output_file
        self.ffmpeg_params = ffmpeg_params
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path

    def run(self):
        try:
            # Importar librerías necesarias para el thread
            import cv2
            import matplotlib

            matplotlib.use("Agg")  # Backend no-GUI para evitar conflictos

            self.progress.emit("Iniciando codificación...")

            # Crear instancia de MBVisualizer con los parámetros
            vis = CoreMBVisualizer(
                self.input_file,
                "temp_output",
                self.ffmpeg_params,
                self.output_file,
                self.ffmpeg_path,
                self.ffprobe_path,
                generate_plot_image=False,
                keep_debug_log=False,
            )

            # Verificar FFmpeg
            self.progress.emit("Verificando FFmpeg...")
            try:
                vis.check_ffmpeg()
            except SystemExit:
                self.error.emit(
                    f"No se pudo ejecutar FFmpeg en la ruta configurada: {self.ffmpeg_path}"
                )
                return

            # Codificar video
            self.progress.emit("Codificando video...")
            vis.encode_video()

            # Extraer información de debug
            self.progress.emit("Extrayendo información de macrobloques...")
            vis.extract_debug_info()

            # Generar sidecar de análisis
            self.progress.emit("Generando datos de análisis para la herramienta...")
            vis.generate_analysis_sidecar()

            if os.path.exists(vis.output_mp4):
                self.progress.emit("Codificación completada")
                self.finished.emit(vis.output_mp4)
            else:
                self.error.emit(
                    f"No se pudo encontrar el video codificado: {vis.output_mp4}"
                )

        except Exception as e:
            self.error.emit(str(e))


class MBVisualizerGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("H264TT (H264 Teaching Tool)")
        # Ventana más grande para acomodar el reproductor
        self.setMinimumSize(1400, 1000)

        # Configuración inicial
        self.ffmpeg_path = "ffmpeg"  # Path por defecto
        self.ffprobe_path = "ffprobe"  # Path por defecto
        self._load_local_settings()

        # Widget del reproductor
        self.video_player = VideoPlayerWidget(ffmpeg_path=self.ffmpeg_path)
        self.current_analysis_sidecar = None
        self.current_stats_file = None
        self.qp_cursor = None
        self.size_cursor = None
        self.qp_marker = None
        self.size_marker = None
        self.qp_band = None
        self.size_band = None

        # Widget central
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Crear splitter principal vertical (configuración arriba, consola abajo)
        main_splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(main_splitter)

        # Panel superior (configuración)
        config_panel = self.create_config_panel()
        main_splitter.addWidget(config_panel)

        # Panel inferior (consola) - inicialmente oculto
        self.console_panel = self.create_console_panel()
        main_splitter.addWidget(self.console_panel)
        self.console_panel.setVisible(False)  # Inicialmente oculto

        # Establecer proporciones del splitter (más espacio para configuración)
        main_splitter.setSizes([700, 200])

        # Barra de estado
        self.status_bar = self.statusBar()
        self.quick_metrics_label = QLabel("Sin video cargado")
        self.quick_metrics_label.setObjectName("quickMetrics")
        self.status_bar.addPermanentWidget(self.quick_metrics_label)
        self.status_bar.showMessage("Listo")
        self.encoding_progress_dialog = None

        # Crear barra de menú al final, después de que todos los widgets estén inicializados
        self.create_menu_bar()
        self.apply_professional_styles()

        # Sincronización entre video y análisis
        self.video_player.frameChanged.connect(self.on_video_frame_changed)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adjust_analysis_plot_heights()

    def create_menu_bar(self):
        """Crea la barra de menú con File, View y Help"""
        menubar = self.menuBar()

        # Menú File
        file_menu = menubar.addMenu("Archivo")

        # Settings
        settings_action = file_menu.addAction("Configuración")
        settings_action.setShortcut("Ctrl+P")
        settings_action.triggered.connect(self.show_settings_dialog)

        load_video_action = file_menu.addAction("Cargar video")
        load_video_action.setShortcut("Ctrl+O")
        load_video_action.triggered.connect(self.load_video_with_mb_extraction)

        # Separador
        file_menu.addSeparator()

        # Exit
        exit_action = file_menu.addAction("Salir")
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)

        # Menú View
        view_menu = menubar.addMenu("Ver")

        # Toggle Console
        self.toggle_console_action = view_menu.addAction("Mostrar Consola de Log")
        self.toggle_console_action.setShortcut("Ctrl+L")
        self.toggle_console_action.triggered.connect(self.toggle_console)
        self.toggle_console_action.setCheckable(True)
        self.toggle_console_action.setChecked(False)  # Inicialmente oculta

        self.toggle_config_action = view_menu.addAction("Mostrar Configuración")
        self.toggle_config_action.setCheckable(True)
        self.toggle_config_action.setChecked(True)
        self.toggle_config_action.triggered.connect(self.toggle_configuration_panel)

        self.toggle_inspector_action = view_menu.addAction("Mostrar Inspector")
        self.toggle_inspector_action.setCheckable(True)
        self.toggle_inspector_action.setChecked(True)
        self.toggle_inspector_action.triggered.connect(self.toggle_inspector_panel)

        self.toggle_analysis_action = view_menu.addAction("Mostrar gráficas")
        self.toggle_analysis_action.setCheckable(True)
        self.toggle_analysis_action.setChecked(True)
        self.toggle_analysis_action.triggered.connect(self.toggle_analysis_panel)

        # Menú Help
        help_menu = menubar.addMenu("Ayuda")

        # About
        about_action = help_menu.addAction("Acerca de")
        about_action.triggered.connect(self.show_about_dialog)

    def create_config_panel(self):
        """Crea un espacio de trabajo profesional unificado."""
        workspace = QWidget()
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(8, 8, 8, 8)

        self.workspace_splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(self.workspace_splitter)

        self.configuration_sidebar = self.create_configuration_sidebar()
        self.workspace_splitter.addWidget(self.configuration_sidebar)
        self.workspace_splitter.addWidget(self.create_visual_workspace())
        self.inspector_panel = self.create_inspector_panel()
        self.workspace_splitter.addWidget(self.inspector_panel)
        self.workspace_splitter.setStretchFactor(0, 0)
        self.workspace_splitter.setStretchFactor(1, 1)
        self.workspace_splitter.setStretchFactor(2, 0)
        self.workspace_splitter.setSizes([340, 980, 360])
        self.config_panel_width = 340
        self.inspector_panel_width = 360

        self.clear_analysis_tab()

        return workspace

    def create_configuration_sidebar(self):
        """Barra lateral de configuración de codificación."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        section_label = QLabel("Configuración de codificación")
        section_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(section_label)
        layout.addWidget(
            self.create_collapsible_section("Archivos", self.create_file_group(), True)
        )
        layout.addWidget(
            self.create_collapsible_section(
                "Codificación básica", self.create_basic_params_group(), True
            )
        )
        layout.addWidget(
            self.create_collapsible_section("GOP", self.create_gop_group(), False)
        )
        layout.addWidget(
            self.create_collapsible_section(
                "Modo de codificación", self.create_bitrate_quality_group(), True
            )
        )
        layout.addWidget(
            self.create_collapsible_section(
                "Vectores de movimiento", self.create_advanced_group(), False
            )
        )
        layout.addWidget(self.create_buttons_group())
        layout.addStretch()

        scroll.setWidget(container)
        return scroll

    def create_collapsible_section(self, title, content_widget, expanded=True):
        container = QWidget()
        outer_layout = QVBoxLayout(container)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(4)

        header_btn = QPushButton(f"▾ {title}" if expanded else f"▸ {title}")
        header_btn.setCheckable(True)
        header_btn.setChecked(expanded)
        header_btn.setStyleSheet(
            "QPushButton { text-align: left; font-weight: bold; padding: 8px 10px; background: #eef3fb; }"
        )
        outer_layout.addWidget(header_btn)

        content_widget.setVisible(expanded)
        if isinstance(content_widget, QGroupBox):
            content_widget.setTitle("")
            content_widget.setFlat(True)
        outer_layout.addWidget(content_widget)

        def toggle_section(checked):
            header_btn.setText(f"▾ {title}" if checked else f"▸ {title}")
            content_widget.setVisible(checked)

        header_btn.toggled.connect(toggle_section)
        return container

    def create_visual_workspace(self):
        """Zona central con video y gráficas sincronizadas."""
        self.visual_splitter = QSplitter(Qt.Vertical)
        self.visual_splitter.addWidget(self.create_video_workspace())
        self.visual_splitter.addWidget(self.create_analysis_panel())
        self.visual_splitter.setStretchFactor(0, 3)
        self.visual_splitter.setStretchFactor(1, 3)
        self.visual_splitter.setSizes([560, 440])
        self.visual_splitter.splitterMoved.connect(self._adjust_analysis_plot_heights)
        return self.visual_splitter

    def create_video_workspace(self):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        header = QHBoxLayout()
        self.toggle_config_btn = QPushButton("◂")
        self.toggle_config_btn.setCheckable(True)
        self.toggle_config_btn.setChecked(True)
        self.toggle_config_btn.toggled.connect(self.toggle_configuration_panel)
        header.addWidget(self.toggle_config_btn)

        title = QLabel("Video y overlay de macroblocks")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        header.addWidget(title)
        header.addStretch()

        self.toggle_inspector_btn = QPushButton("▸")
        self.toggle_inspector_btn.setCheckable(True)
        self.toggle_inspector_btn.setChecked(True)
        self.toggle_inspector_btn.toggled.connect(self.toggle_inspector_panel)
        header.addWidget(self.toggle_inspector_btn)
        layout.addLayout(header)
        layout.addWidget(self.video_player)
        return container

    def create_analysis_panel(self):
        """Panel inferior con gráficas interactivas."""
        analysis_widget = QWidget()
        self.analysis_panel_widget = analysis_widget
        layout = QVBoxLayout(analysis_widget)
        layout.setContentsMargins(0, 0, 0, 0)

        plots_group = QGroupBox("Análisis temporal")
        plots_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        plots_layout = QVBoxLayout(plots_group)

        view_mode_layout = QHBoxLayout()
        view_mode_layout.addWidget(QLabel("Vista:"))
        self.analysis_view_mode_combo = QComboBox()
        self.analysis_view_mode_combo.addItems(["Ambas", "Solo QP", "Solo tamaño"])
        self.analysis_view_mode_combo.currentTextChanged.connect(
            self._update_analysis_view_mode
        )
        view_mode_layout.addWidget(self.analysis_view_mode_combo)
        view_mode_layout.addStretch()
        plots_layout.addLayout(view_mode_layout)

        plots_scroll = QScrollArea()
        plots_scroll.setWidgetResizable(True)
        plots_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        plots_content = QWidget()
        plots_content_layout = QVBoxLayout(plots_content)

        self.qp_figure = Figure(figsize=(12, 5.2))
        self.qp_canvas = FigureCanvas(self.qp_figure)
        self.qp_canvas.setMinimumHeight(380)
        self.qp_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.qp_toolbar = NavigationToolbar2QT(self.qp_canvas, self)
        self.qp_plot_widget = QWidget()
        qp_plot_layout = QVBoxLayout(self.qp_plot_widget)
        qp_plot_layout.setContentsMargins(0, 0, 0, 0)
        qp_plot_layout.addWidget(self.qp_toolbar)
        qp_plot_layout.addWidget(self.qp_canvas, 1)
        plots_content_layout.addWidget(self.qp_plot_widget, 1)

        self.size_figure = Figure(figsize=(12, 5.2))
        self.size_canvas = FigureCanvas(self.size_figure)
        self.size_canvas.setMinimumHeight(380)
        self.size_canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.size_toolbar = NavigationToolbar2QT(self.size_canvas, self)
        self.size_plot_widget = QWidget()
        size_plot_layout = QVBoxLayout(self.size_plot_widget)
        size_plot_layout.setContentsMargins(0, 0, 0, 0)
        size_plot_layout.addWidget(self.size_toolbar)
        size_plot_layout.addWidget(self.size_canvas, 1)
        plots_content_layout.addWidget(self.size_plot_widget, 1)

        plots_scroll.setWidget(plots_content)
        plots_layout.addWidget(plots_scroll)
        layout.addWidget(plots_group)

        self.qp_canvas.mpl_connect("button_press_event", self.on_plot_clicked)
        self.size_canvas.mpl_connect("button_press_event", self.on_plot_clicked)
        self._update_analysis_view_mode(self.analysis_view_mode_combo.currentText())
        QTimer.singleShot(0, self._adjust_analysis_plot_heights)

        return analysis_widget

    def _adjust_analysis_plot_heights(self, *args):
        panel = getattr(self, "analysis_panel_widget", None)
        if panel is None:
            return

        available_height = panel.height()
        if available_height <= 0:
            return

        visible_plot_widgets = [
            widget
            for widget in (
                getattr(self, "qp_plot_widget", None),
                getattr(self, "size_plot_widget", None),
            )
            if widget is not None and widget.isVisible()
        ]
        visible_count = max(1, len(visible_plot_widgets))
        target_height = max(380, (available_height - 120) // visible_count)

        for canvas in (
            getattr(self, "qp_canvas", None),
            getattr(self, "size_canvas", None),
        ):
            if canvas is not None:
                canvas.setMinimumHeight(target_height)

    def _update_analysis_view_mode(self, mode_text):
        show_qp = mode_text in ["Ambas", "Solo QP"]
        show_size = mode_text in ["Ambas", "Solo tamaño"]

        if hasattr(self, "qp_plot_widget"):
            self.qp_plot_widget.setVisible(show_qp)
        if hasattr(self, "size_plot_widget"):
            self.size_plot_widget.setVisible(show_size)

        self._adjust_analysis_plot_heights()

    def create_inspector_panel(self):
        """Panel derecho con información de frame, leyenda y resumen."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(0)

        inspector_title = QLabel("Inspector")
        inspector_title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(inspector_title)

        layout.addWidget(self.video_player.info_panel)

        summary_group = QWidget()
        summary_layout = QVBoxLayout(summary_group)
        summary_layout.setContentsMargins(0, 0, 0, 0)
        self.analysis_summary = QTextEdit()
        self.analysis_summary.setReadOnly(True)
        self.analysis_summary.setFont(QFont("Courier New", 10))
        self.analysis_summary.setMinimumHeight(360)
        summary_layout.addWidget(self.analysis_summary)
        self.analysis_summary_section = self.create_collapsible_section(
            "Resumen del análisis", summary_group, False
        )
        self.analysis_summary_section.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Maximum
        )
        layout.addWidget(self.analysis_summary_section)
        layout.addStretch()

        scroll.setWidget(container)
        return scroll

    def clear_analysis_tab(self):
        if hasattr(self, "analysis_summary"):
            self.analysis_summary.setPlainText("No hay datos de análisis cargados.")
        if hasattr(self, "qp_figure"):
            self.qp_figure.clear()
        if hasattr(self, "qp_canvas"):
            self.qp_canvas.draw()
        if hasattr(self, "size_figure"):
            self.size_figure.clear()
        if hasattr(self, "size_canvas"):
            self.size_canvas.draw()
        self.current_analysis_sidecar = None
        self.current_stats_file = None
        self.qp_cursor = None
        self.size_cursor = None
        self.qp_marker = None
        self.size_marker = None
        self.qp_band = None
        self.size_band = None
        if hasattr(self, "quick_metrics_label"):
            self.quick_metrics_label.setText("Sin video cargado")

    def _resolve_analysis_artifacts(self, video_path=None, sidecar_path=None):
        base_candidates = []

        if sidecar_path:
            sidecar_base = sidecar_path.replace(".analysis.json", "")
            base_candidates.append(sidecar_base)

        if video_path:
            video_base = os.path.splitext(video_path)[0]
            base_candidates.extend([video_base, video_base.replace("_encoded", "")])

        seen = set()
        ordered_bases = []
        for base in base_candidates:
            if base and base not in seen:
                seen.add(base)
                ordered_bases.append(base)

        analysis_path = None
        stats_path = None
        for base in ordered_bases:
            candidate_analysis = f"{base}.analysis.json"
            if analysis_path is None and os.path.exists(candidate_analysis):
                analysis_path = candidate_analysis

            candidate_stats = f"{base}_stats.txt"
            if stats_path is None and os.path.exists(candidate_stats):
                stats_path = candidate_stats

        return analysis_path, stats_path

    def _build_summary_text(self, analysis_data=None, stats_path=None):
        if stats_path and os.path.exists(stats_path):
            with open(stats_path, "r", encoding="utf-8") as f:
                return self._normalize_summary_text(f.read())

        if not analysis_data:
            return "No hay datos de análisis cargados."

        metadata = analysis_data.get("metadata", {})
        video_info = analysis_data.get("video_info", {})
        summary = analysis_data.get("summary", {})

        frame_count = summary.get("parsed_frames", video_info.get("total_frames", 0))
        total_mb = summary.get("total_macroblocks", 0)
        frame_type_counts = summary.get("frame_type_counts", {})
        frame_type_percentages = summary.get("frame_type_percentages", {})
        mb_totals = summary.get("macroblock_totals", {})
        mb_percentages = summary.get("macroblock_percentages", {})
        input_size = summary.get("input_size")
        output_size = summary.get("output_size")
        compression_ratio = summary.get("compression_ratio")
        compression_percentage = summary.get("compression_percentage")
        avg_bitrate = summary.get("avg_bitrate")
        avg_psnr = summary.get("avg_psnr")

        lines = [
            "RESUMEN GENERAL",
            "-" * 30,
            f"Archivo de entrada: {metadata.get('input_file', 'N/D')}",
            f"Video codificado: {metadata.get('output_video', 'N/D')}",
            f"Resolución: {video_info.get('width', 0)}x{video_info.get('height', 0)}",
            f"FPS: {video_info.get('fps', 'N/D')}",
            f"Total de macroblocks analizados: {self._format_plain_number(total_mb)}",
            f"Total de frames analizados: {frame_count}",
            f"Tamaño archivo original: {self._format_bytes_human(input_size)}",
            f"Tamaño archivo codificado: {self._format_bytes_human(output_size)}",
            f"Ratio de compresión: {self._format_ratio(compression_ratio)}",
            f"Porcentaje de compresión: {self._format_percentage(compression_percentage)}",
            f"Bitrate promedio: {self._format_bitrate(avg_bitrate)}",
            f"PSNR promedio: {self._format_psnr(avg_psnr)}",
            "",
            "TIPOS DE FRAME",
            "-" * 30,
            f"I-Frames: {self._format_plain_number(frame_type_counts.get('I', 0))} ({self._format_percentage(frame_type_percentages.get('I', 0))})",
            f"P-Frames: {self._format_plain_number(frame_type_counts.get('P', 0))} ({self._format_percentage(frame_type_percentages.get('P', 0))})",
            f"B-Frames: {self._format_plain_number(frame_type_counts.get('B', 0))} ({self._format_percentage(frame_type_percentages.get('B', 0))})",
            "",
            "DISTRIBUCIÓN POR TIPO DE MACROBLOCK",
            "-" * 30,
            f"INTRA: {self._format_plain_number(mb_totals.get('INTRA', 0))} ({self._format_percentage(mb_percentages.get('INTRA', 0))})",
            f"SKIP: {self._format_plain_number(mb_totals.get('SKIP', 0))} ({self._format_percentage(mb_percentages.get('SKIP', 0))})",
            f"INTER: {self._format_plain_number(mb_totals.get('INTER', 0))} ({self._format_percentage(mb_percentages.get('INTER', 0))})",
        ]
        return "\n".join(lines)

    def _format_plain_number(self, value):
        if value is None:
            return "-"
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            if value.is_integer():
                return str(int(value))
            return f"{value:.2f}".rstrip("0").rstrip(".")
        return str(value).replace(",", "")

    def _format_bytes_human(self, num_bytes):
        if num_bytes is None:
            return "-"

        size = float(num_bytes)
        units = ["bytes", "KB", "MB", "GB", "TB"]
        unit_index = 0

        while size >= 1024 and unit_index < len(units) - 1:
            size /= 1024
            unit_index += 1

        if unit_index == 0:
            return f"{int(size)} bytes"

        formatted = f"{size:.2f}".rstrip("0").rstrip(".")
        return f"{formatted} {units[unit_index]}"

    def _format_percentage(self, value):
        if value is None:
            return "-"
        return f"{float(value):.2f}%"

    def _format_ratio(self, value):
        if value is None:
            return "-"
        return f"{float(value):.2f}:1"

    def _format_bitrate(self, value):
        if value is None:
            return "-"
        return f"{float(value) / 1000:.0f} kbps"

    def _format_psnr(self, value):
        if value is None:
            return "-"
        return f"{float(value):.2f} dB"

    def _normalize_summary_text(self, summary_text):
        normalized_lines = []

        for line in summary_text.splitlines():
            byte_match = re.match(r"^(.*?:)\s*(\d[\d,]*)\s+bytes$", line)
            if byte_match:
                label = byte_match.group(1)
                raw_value = int(byte_match.group(2).replace(",", ""))
                normalized_lines.append(
                    f"{label} {self._format_bytes_human(raw_value)}"
                )
                continue

            normalized_lines.append(re.sub(r"(?<=\d),(?=\d)", "", line))

        return "\n".join(normalized_lines)

    def _update_quick_metrics(self):
        if not self.video_player.video_path or self.video_player.total_frames <= 0:
            self.quick_metrics_label.setText("Sin video cargado")
            return

        width = (
            int(self.video_player.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            if self.video_player.cap
            else 0
        )
        height = (
            int(self.video_player.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if self.video_player.cap
            else 0
        )
        fps = self.video_player.fps or 0
        frame_data = getattr(self.video_player, "frame_data", {})
        qp_values = frame_data.get("qp_values", [])
        valid_qp = [value for value in qp_values if value is not None]
        avg_qp = f"{sum(valid_qp) / len(valid_qp):.2f}" if valid_qp else "-"
        self.quick_metrics_label.setText(
            f"{width}x{height} · {fps:.2f} FPS · {self.video_player.total_frames} frames · QP medio {avg_qp}"
        )

    def _create_analysis_plot(
        self, figure, canvas, values, title, ylabel, frame_types=None
    ):
        figure.clear()
        ax = figure.add_subplot(111)

        if not values:
            ax.text(
                0.5,
                0.5,
                "Gráfica no disponible",
                transform=ax.transAxes,
                ha="center",
                va="center",
            )
            ax.set_axis_off()
            canvas.draw()
            return

        valid_values = [0 if value is None else value for value in values]
        frames = list(range(1, len(valid_values) + 1))

        if frame_types is None:
            ax.step(frames, valid_values, where="mid", color="darkorange", linewidth=2)
        else:
            colors = []
            for frame_type in frame_types[: len(valid_values)]:
                if frame_type == "I":
                    colors.append("red")
                elif frame_type == "P":
                    colors.append("blue")
                elif frame_type == "B":
                    colors.append("blueviolet")
                else:
                    colors.append("gray")
            ax.bar(frames, valid_values, color=colors, alpha=0.75, width=0.9)

        ax.set_title(title, fontsize=13, fontweight="bold")
        ax.set_xlabel("Frame")
        ax.set_ylabel(ylabel)
        if ylabel == "QP":
            ax.set_ylim(0, 51)
            ax.xaxis.set_major_locator(MultipleLocator(5))
            ax.grid(True, axis="x", alpha=0.35, linestyle="--")
        ax.grid(True, alpha=0.3, linestyle="--")
        figure.tight_layout()
        canvas.draw()
        return ax

    def update_analysis_tab(self, video_path=None, sidecar_path=None):
        analysis_path, stats_path = self._resolve_analysis_artifacts(
            video_path, sidecar_path
        )

        analysis_data = None
        if analysis_path and os.path.exists(analysis_path):
            with open(analysis_path, "r", encoding="utf-8") as f:
                analysis_data = json.load(f)

        self.current_analysis_sidecar = analysis_path
        self.current_stats_file = stats_path
        self.analysis_summary.setPlainText(
            self._build_summary_text(analysis_data=analysis_data, stats_path=stats_path)
        )

        qp_values = self.video_player.frame_data.get("qp_values", [])
        size_values = self.video_player.frame_data.get("size_values", [])
        frame_types = self.video_player.frame_types

        qp_ax = self._create_analysis_plot(
            self.qp_figure,
            self.qp_canvas,
            qp_values,
            "Evolución del parámetro QP",
            "QP",
        )
        size_ax = self._create_analysis_plot(
            self.size_figure,
            self.size_canvas,
            size_values,
            "Tamaño de cada frame",
            "Bytes",
            frame_types=frame_types,
        )
        self.qp_cursor = self._attach_plot_cursor(
            qp_ax, self.video_player.current_frame
        )
        self.size_cursor = self._attach_plot_cursor(
            size_ax, self.video_player.current_frame
        )
        self.qp_band = self._attach_plot_band(qp_ax, self.video_player.current_frame)
        self.size_band = self._attach_plot_band(
            size_ax, self.video_player.current_frame
        )
        self.qp_marker = self._attach_plot_marker(
            qp_ax,
            self.video_player.current_frame,
            qp_values,
        )
        self.size_marker = self._attach_plot_marker(
            size_ax,
            self.video_player.current_frame,
            size_values,
        )
        self.qp_canvas.draw_idle()
        self.size_canvas.draw_idle()
        self._update_quick_metrics()

    def copy_analysis_summary(self):
        QApplication.clipboard().setText(self.analysis_summary.toPlainText())
        self.status_bar.showMessage("Resumen copiado al portapapeles", 3000)

    def _attach_plot_cursor(self, ax, frame_idx):
        if ax is None:
            return None
        return ax.axvline(frame_idx + 1, color="#d32f2f", linestyle="--", linewidth=1.5)

    def _attach_plot_band(self, ax, frame_idx):
        if ax is None:
            return None
        x = frame_idx + 1
        return ax.axvspan(x - 0.5, x + 0.5, color="#d32f2f", alpha=0.08)

    def _attach_plot_marker(self, ax, frame_idx, values):
        if ax is None or not values or frame_idx >= len(values):
            return None
        y_value = values[frame_idx]
        if y_value is None:
            return None
        return ax.plot(frame_idx + 1, y_value, "o", color="#d32f2f", markersize=6)[0]

    def on_video_frame_changed(self, frame_num):
        frame_data = getattr(self.video_player, "frame_data", {})
        qp_values = frame_data.get("qp_values", [])
        size_values = frame_data.get("size_values", [])

        for cursor, marker, band, values, canvas in (
            (self.qp_cursor, self.qp_marker, self.qp_band, qp_values, self.qp_canvas),
            (
                self.size_cursor,
                self.size_marker,
                self.size_band,
                size_values,
                self.size_canvas,
            ),
        ):
            if cursor is not None:
                cursor.set_xdata([frame_num + 1, frame_num + 1])
            if band is not None:
                band.set_x(frame_num + 0.5)
                band.set_width(1.0)
            if (
                marker is not None
                and frame_num < len(values)
                and values[frame_num] is not None
            ):
                marker.set_data([frame_num + 1], [values[frame_num]])
            canvas.draw_idle()

    def on_plot_clicked(self, event):
        if event.inaxes is None or event.xdata is None:
            return

        if self.video_player.total_frames <= 0:
            return

        frame_num = max(
            0, min(int(round(event.xdata)) - 1, self.video_player.total_frames - 1)
        )
        if frame_num >= 0:
            self.video_player.seek_to_position(frame_num)

    def toggle_configuration_panel(self, checked):
        sizes = self.workspace_splitter.sizes()
        if checked:
            self.configuration_sidebar.show()
            sizes = self.workspace_splitter.sizes()
            target_width = max(220, self.config_panel_width)
            center_width = max(200, sizes[1] - (target_width - sizes[0]))
            self.workspace_splitter.setSizes([target_width, center_width, sizes[2]])
            self.toggle_config_btn.setText("◂")
        else:
            if sizes[0] > 0:
                self.config_panel_width = sizes[0]
            self.configuration_sidebar.hide()
            self.workspace_splitter.setSizes([0, sizes[1] + sizes[0], sizes[2]])
            self.toggle_config_btn.setText("▸")
        self.toggle_config_action.setChecked(checked)
        if (
            hasattr(self, "toggle_config_btn")
            and self.toggle_config_btn.isChecked() != checked
        ):
            self.toggle_config_btn.blockSignals(True)
            self.toggle_config_btn.setChecked(checked)
            self.toggle_config_btn.blockSignals(False)

    def toggle_inspector_panel(self, checked):
        sizes = self.workspace_splitter.sizes()
        if checked:
            self.inspector_panel.show()
            sizes = self.workspace_splitter.sizes()
            target_width = max(240, self.inspector_panel_width)
            center_width = max(200, sizes[1] - (target_width - sizes[2]))
            self.workspace_splitter.setSizes([sizes[0], center_width, target_width])
            self.toggle_inspector_btn.setText("▸")
        else:
            if sizes[2] > 0:
                self.inspector_panel_width = sizes[2]
            self.inspector_panel.hide()
            self.workspace_splitter.setSizes([sizes[0], sizes[1] + sizes[2], 0])
            self.toggle_inspector_btn.setText("◂")
        self.toggle_inspector_action.setChecked(checked)
        if (
            hasattr(self, "toggle_inspector_btn")
            and self.toggle_inspector_btn.isChecked() != checked
        ):
            self.toggle_inspector_btn.blockSignals(True)
            self.toggle_inspector_btn.setChecked(checked)
            self.toggle_inspector_btn.blockSignals(False)

    def toggle_analysis_panel(self, checked=None):
        if checked is None:
            checked = self.visual_splitter.sizes()[1] == 0

        if checked:
            self.visual_splitter.setSizes([620, 380])
        else:
            self.visual_splitter.setSizes([1000, 0])

        if self.toggle_analysis_action.isChecked() != checked:
            self.toggle_analysis_action.blockSignals(True)
            self.toggle_analysis_action.setChecked(checked)
            self.toggle_analysis_action.blockSignals(False)

    def apply_professional_styles(self):
        self.setStyleSheet(
            """
            QWidget { font-size: 10pt; }
            QGroupBox {
                border: 1px solid #d6dbe3;
                border-radius: 8px;
                margin-top: 14px;
                padding-top: 10px;
                background: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
                color: #2b2f36;
                font-weight: bold;
            }
            QPushButton {
                padding: 6px 10px;
                border-radius: 6px;
                border: 1px solid #cfd6e4;
                background: #f7f9fc;
            }
            QPushButton:hover {
                background: #eaf2ff;
            }
            QSplitter::handle {
                background: #dde3ec;
            }
            QSplitter::handle:hover {
                background: #b8c8e6;
            }
            QLabel#quickMetrics, QLabel#quickFrameMetrics {
                color: #445;
                background: #eef4ff;
                border: 1px solid #d4e1ff;
                border-radius: 6px;
                padding: 6px 10px;
            }
            QLabel#frameBadge {
                color: white;
                background: rgba(20, 27, 38, 180);
                border: 1px solid rgba(255, 255, 255, 40);
                border-radius: 6px;
                padding: 6px 10px;
                margin: 10px;
                font-weight: bold;
            }
            """
        )
        self.run_btn.setStyleSheet(
            "QPushButton { background-color: #0078D4; color: white; border: none; border-radius: 6px; padding: 12px; font-size: 14px; font-weight: bold; } QPushButton:hover { background-color: #106ebe; }"
        )

    def load_video_with_mb_extraction(self):
        """Carga un video y extrae automáticamente la información de macrobloques."""
        file_dialog = QFileDialog()
        file_dialog.setNameFilter("Videos (*.mp4 *.avi *.mkv);;Todos los archivos (*)")
        file_dialog.setWindowTitle("Seleccionar Video")

        if file_dialog.exec():
            video_path = file_dialog.selectedFiles()[0]

            # Cargar el video seleccionado (ahora extrae datos automáticamente)
            success = self.video_player.load_video(video_path)
            if success:
                sidecar_path = self.video_player._find_mb_data_file(video_path)
                self.update_analysis_tab(
                    video_path=video_path, sidecar_path=sidecar_path
                )
                QMessageBox.information(
                    self,
                    "Video cargado",
                    f"Video cargado exitosamente con datos de macrobloques.\nArchivo: {os.path.basename(video_path)}",
                )
            else:
                self.clear_analysis_tab()
                QMessageBox.warning(
                    self, "Error", f"No se pudo cargar el video:\n{video_path}"
                )

    def _find_mb_data_file(self, video_path):
        """Busca el archivo de datos de macrobloques correspondiente."""
        base_path = os.path.splitext(video_path)[0]
        candidates = [
            base_path + ".analysis.json",
            base_path.replace("_encoded", "") + ".analysis.json",
            base_path + "_mb_types.txt",
            base_path + "_analysis.log",
            base_path.replace("_encoded", "") + "_mb_types.txt",
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return None

    def create_console_panel(self):
        """Crea el panel de consola (inferior)"""
        group = QGroupBox("Consola de Salida")
        layout = QVBoxLayout(group)

        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setFont(QFont("Courier New", 9))
        layout.addWidget(self.console)

        return group

    def create_file_group(self):
        """Grupo para selección de archivos"""
        group = QGroupBox("Archivos")
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(group)

        # Archivo de entrada
        input_layout = QHBoxLayout()
        input_layout.addWidget(QLabel("Video de entrada:"))
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("Selecciona archivo Y4M o MP4...")
        input_layout.addWidget(self.input_edit)
        self.input_btn = QPushButton("...")
        self.input_btn.clicked.connect(self.select_input_file)
        input_layout.addWidget(self.input_btn)
        layout.addLayout(input_layout)

        # Archivo de salida
        output_layout = QHBoxLayout()
        output_layout.addWidget(QLabel("Video de salida:"))
        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("Nombre del archivo de salida (MP4)...")
        output_layout.addWidget(self.output_edit)
        layout.addLayout(output_layout)

        return group

    def create_basic_params_group(self):
        """Parámetros básicos de codificación"""
        group = QGroupBox("Codificación Básica")
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(group)

        # Codec
        codec_layout = QHBoxLayout()
        codec_label = QLabel("Codec:")
        codec_tooltip = (
            "Codificador de vídeo principal.\n"
            "libx264: genera vídeo H.264/AVC, que es el usado en esta práctica.\n"
            "libx265: genera vídeo H.265/HEVC; puede servir para comparar, pero no es el flujo docente principal."
        )
        codec_label.setToolTip(codec_tooltip)
        codec_layout.addWidget(codec_label)
        self.codec_combo = QComboBox()
        self.codec_combo.addItems(["libx264", "libx265"])
        self.codec_combo.setToolTip(codec_tooltip)
        codec_layout.addWidget(self.codec_combo)
        layout.addLayout(codec_layout)

        # Preset
        preset_layout = QHBoxLayout()
        self.preset_check = QCheckBox("Preset:")
        preset_tooltip = (
            "Activa el parámetro -preset de x264/x265.\n"
            "Cambia la complejidad interna del codificador manteniendo, aproximadamente, el mismo objetivo de calidad.\n"
            "ultrafast/superfast/veryfast: codificación muy rápida, normalmente peor compresión.\n"
            "faster/fast/medium: compromiso entre tiempo y eficiencia; medium es la referencia de la práctica.\n"
            "slow/slower/veryslow: más tiempo de codificación, normalmente mejor compresión a igualdad de calidad."
        )
        self.preset_check.setToolTip(preset_tooltip)
        preset_layout.addWidget(self.preset_check)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(
            [
                "ultrafast",
                "superfast",
                "veryfast",
                "faster",
                "fast",
                "medium",
                "slow",
                "slower",
                "veryslow",
            ]
        )
        self.preset_combo.setCurrentText("medium")
        self.preset_combo.setToolTip(preset_tooltip)
        preset_layout.addWidget(self.preset_combo)
        layout.addLayout(preset_layout)
        self.preset_check.toggled.connect(self.preset_combo.setEnabled)
        self.preset_combo.setEnabled(self.preset_check.isChecked())

        # Tune
        tune_layout = QHBoxLayout()
        self.tune_check = QCheckBox("Tune:")
        tune_tooltip = (
            "Activa el parámetro -tune. Ajusta decisiones internas del codificador según el tipo de contenido o la métrica buscada.\n"
            "film: material cinematográfico general.\n"
            "animation: animación o contenido con bordes planos.\n"
            "grain: conserva mejor el grano/ruido, normalmente gastando más bitrate.\n"
            "stillimage: optimiza para imágenes casi estáticas.\n"
            "psnr: ajusta el codificador para que la métrica PSNR resulte más coherente en comparativas.\n"
            "ssim: prioriza decisiones favorables a la métrica SSIM.\n"
            "fastdecode: facilita la decodificación a costa de eficiencia.\n"
            "zerolatency: reduce buffering y latencia, útil en tiempo real."
        )
        self.tune_check.setToolTip(tune_tooltip)
        tune_layout.addWidget(self.tune_check)
        self.tune_combo = QComboBox()
        self.tune_combo.addItems(
            [
                "film",
                "animation",
                "grain",
                "stillimage",
                "psnr",
                "ssim",
                "fastdecode",
                "zerolatency",
            ]
        )
        self.tune_combo.setToolTip(tune_tooltip)
        tune_layout.addWidget(self.tune_combo)
        layout.addLayout(tune_layout)
        self.tune_check.toggled.connect(self.tune_combo.setEnabled)
        self.tune_combo.setEnabled(self.tune_check.isChecked())

        return group

    def create_gop_group(self):
        """Parámetros del GOP"""
        group = QGroupBox("Group of Pictures (GOP)")
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(group)

        # Habilitar GOP personalizado
        self.gop_check = QCheckBox("Configurar longitud del GOP")
        self.gop_check.setToolTip(
            "Activa parámetros de estructura temporal del GOP.\n"
            "Permite controlar la distancia entre I-frames y reducir el comportamiento automático del codificador para fines docentes."
        )
        layout.addWidget(self.gop_check)

        # Keyint y Min-keyint
        gop_layout = QHBoxLayout()
        keyint_label = QLabel("Keyint (máximo):")
        keyint_tooltip = (
            "Distancia máxima entre dos I-frames.\n"
            "Valores pequeños: más I-frames, más bitrate y acceso aleatorio más frecuente.\n"
            "Valores grandes: GOPs más largos, normalmente mejor compresión."
        )
        keyint_label.setToolTip(keyint_tooltip)
        gop_layout.addWidget(keyint_label)
        self.keyint_spin = QSpinBox()
        self.keyint_spin.setRange(1, 1000)
        self.keyint_spin.setValue(60)
        self.keyint_spin.setToolTip(keyint_tooltip)
        gop_layout.addWidget(self.keyint_spin)

        minkeyint_label = QLabel("Min-keyint (mínimo):")
        minkeyint_tooltip = (
            "Distancia mínima entre dos I-frames.\n"
            "Si coincide con keyint, fuerzas una periodicidad fija de I-frames.\n"
            "Valores menores permiten que el codificador inserte I-frames antes si lo considera útil."
        )
        minkeyint_label.setToolTip(minkeyint_tooltip)
        gop_layout.addWidget(minkeyint_label)
        self.minkeyint_spin = QSpinBox()
        self.minkeyint_spin.setRange(1, 1000)
        self.minkeyint_spin.setValue(60)
        self.minkeyint_spin.setToolTip(minkeyint_tooltip)
        gop_layout.addWidget(self.minkeyint_spin)

        layout.addLayout(gop_layout)

        # Scenecut
        scenecut_layout = QHBoxLayout()
        self.scenecut_check = QCheckBox("Scenecut:")
        scenecut_tooltip = (
            "Activa el parámetro scenecut de x264.\n"
            "Controla la inserción automática de I-frames al detectar cambios bruscos de escena.\n"
            "0: desactivado, útil cuando quieres un GOP estrictamente periódico.\n"
            "Valores mayores: más sensibilidad para introducir I-frames extra en cortes."
        )
        self.scenecut_check.setToolTip(scenecut_tooltip)
        scenecut_layout.addWidget(self.scenecut_check)

        self.scenecut_spin = QSpinBox()
        self.scenecut_spin.setRange(0, 100)
        self.scenecut_spin.setValue(40)
        self.scenecut_spin.setToolTip(scenecut_tooltip)
        scenecut_layout.addWidget(self.scenecut_spin)

        scenecut_layout.addStretch()
        layout.addLayout(scenecut_layout)

        # B-frames
        self.bframes_check = QCheckBox("Configurar B-frames")
        bframes_tooltip = (
            "Activa el control explícito de B-frames.\n"
            "Los B-frames pueden mejorar la compresión usando referencias al pasado y/o al futuro,\n"
            "pero hacen la estructura temporal menos simple de analizar."
        )
        self.bframes_check.setToolTip(bframes_tooltip)
        layout.addWidget(self.bframes_check)

        bframes_layout = QHBoxLayout()
        bframes_label = QLabel("B-frames:")
        bframes_count_tooltip = (
            "Número máximo de B-frames consecutivos (bframes).\n"
            "0: desactiva B-frames.\n"
            "1-2: estructura temporal sencilla y útil para docencia.\n"
            "Valores mayores: más libertad para comprimir mejor, a costa de complejidad."
        )
        bframes_label.setToolTip(bframes_count_tooltip)
        bframes_layout.addWidget(bframes_label)
        self.bframes_spin = QSpinBox()
        self.bframes_spin.setRange(0, 16)
        self.bframes_spin.setValue(2)
        self.bframes_spin.setToolTip(bframes_count_tooltip)
        bframes_layout.addWidget(self.bframes_spin)

        badapt_label = QLabel("B-adapt:")
        badapt_tooltip = (
            "Modo de decisión/adaptación de B-frames (b-adapt).\n"
            "0: patrón fijo; útil para comparativas reproducibles.\n"
            "1: adaptación rápida.\n"
            "2: adaptación más cuidadosa y normalmente más eficiente."
        )
        badapt_label.setToolTip(badapt_tooltip)
        bframes_layout.addWidget(badapt_label)
        self.badapt_combo = QComboBox()
        self.badapt_combo.addItems(["0", "1", "2"])
        self.badapt_combo.setCurrentText("2")
        self.badapt_combo.setToolTip(badapt_tooltip)
        bframes_layout.addWidget(self.badapt_combo)
        layout.addLayout(bframes_layout)

        self.gop_check.toggled.connect(self._update_gop_control_states)
        self.scenecut_check.toggled.connect(self._update_gop_control_states)
        self.bframes_check.toggled.connect(self._update_gop_control_states)
        self._update_gop_control_states()

        return group

    def create_bitrate_quality_group(self):
        """Parámetros de bitrate y calidad (CBR/VBR disjuntos)"""
        group = QGroupBox("Modo de Codificación")
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(group)

        # Grupo de radio buttons para modo CBR/VBR
        self.cbr_radio = QCheckBox("CBR (Bitrate Constante)")
        cbr_tooltip = (
            "Activa un modo de bitrate objetivo aproximado con VBV.\n"
            "El codificador ajusta el QP para acercarse al bitrate indicado.\n"
            "Es útil para comparar comportamiento bajo una restricción de tasa."
        )
        self.cbr_radio.setToolTip(cbr_tooltip)
        self.cbr_radio.clicked.connect(self.on_cbr_toggled)
        layout.addWidget(self.cbr_radio)

        # Parámetros CBR (inicialmente deshabilitados)
        self.cbr_widget = QWidget()
        cbr_layout = QVBoxLayout(self.cbr_widget)

        bitrate_layout = QHBoxLayout()
        bitrate_label = QLabel("Bitrate objetivo (kbps):")
        bitrate_tooltip = (
            "Bitrate medio objetivo de vídeo.\n"
            "Valores más altos suelen dar más calidad y más tamaño final.\n"
            "Valores más bajos fuerzan más compresión y normalmente más QP."
        )
        bitrate_label.setToolTip(bitrate_tooltip)
        bitrate_layout.addWidget(bitrate_label)
        self.bitrate_spin = QSpinBox()
        self.bitrate_spin.setRange(100, 10000)
        self.bitrate_spin.setValue(2000)
        self.bitrate_spin.setToolTip(bitrate_tooltip)
        bitrate_layout.addWidget(self.bitrate_spin)
        cbr_layout.addLayout(bitrate_layout)

        buffer_layout = QHBoxLayout()
        buffer_label = QLabel("Buffer size (kbps):")
        buffer_tooltip = (
            "Tamaño del buffer VBV.\n"
            "Valores pequeños: el bitrate instantáneo varía menos.\n"
            "Valores grandes: el codificador tiene más margen para repartir bits entre frames."
        )
        buffer_label.setToolTip(buffer_tooltip)
        buffer_layout.addWidget(buffer_label)
        self.bufsize_spin = QSpinBox()
        self.bufsize_spin.setRange(100, 20000)
        self.bufsize_spin.setValue(4000)
        self.bufsize_spin.setToolTip(buffer_tooltip)
        buffer_layout.addWidget(self.bufsize_spin)
        cbr_layout.addLayout(buffer_layout)

        self.cbr_widget.setEnabled(False)
        layout.addWidget(self.cbr_widget)

        # Separador
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        # Modo VBR
        self.vbr_radio = QCheckBox("VBR (Calidad Variable)")
        vbr_tooltip = (
            "Activa un modo de calidad objetivo.\n"
            "El bitrate final se adapta al contenido del vídeo.\n"
            "Es útil para comparar cómo varía QP o tamaño cuando se fija calidad en lugar de tasa."
        )
        self.vbr_radio.setToolTip(vbr_tooltip)
        self.vbr_radio.clicked.connect(self.on_vbr_toggled)
        layout.addWidget(self.vbr_radio)

        # Parámetros VBR (inicialmente deshabilitados)
        self.vbr_widget = QWidget()
        vbr_layout = QVBoxLayout(self.vbr_widget)

        crf_layout = QHBoxLayout()
        crf_label = QLabel("CRF (Constant Rate Factor):")
        crf_tooltip = (
            "Control principal del modo CRF.\n"
            "Valores bajos: mejor calidad, más bitrate.\n"
            "Valores altos: peor calidad, menos bitrate.\n"
            "Rango típico para pruebas visuales: 18-28."
        )
        crf_label.setToolTip(crf_tooltip)
        crf_layout.addWidget(crf_label)
        self.crf_spin = QSpinBox()
        self.crf_spin.setRange(0, 51)
        self.crf_spin.setValue(23)
        self.crf_spin.setToolTip(crf_tooltip)
        crf_layout.addWidget(self.crf_spin)
        vbr_layout.addLayout(crf_layout)

        qp_layout = QHBoxLayout()
        self.qp_check = QCheckBox("QP fijo:")
        qp_tooltip = (
            "Fija directamente el parámetro de cuantización base.\n"
            "Valores bajos: mejor calidad y más bitrate.\n"
            "Valores altos: peor calidad y más compresión.\n"
            "Si lo activas, la GUI prioriza -qp y no usa -crf."
        )
        self.qp_check.setToolTip(qp_tooltip)
        qp_layout.addWidget(self.qp_check)
        self.qp_spin = QSpinBox()
        self.qp_spin.setRange(0, 51)
        self.qp_spin.setValue(25)
        self.qp_spin.setToolTip(qp_tooltip)
        qp_layout.addWidget(self.qp_spin)
        vbr_layout.addLayout(qp_layout)
        self.qp_check.toggled.connect(self._update_qp_control_state)
        self.qp_spin.setEnabled(self.qp_check.isChecked())

        self.vbr_widget.setEnabled(False)
        layout.addWidget(self.vbr_widget)

        return group

    def create_advanced_group(self):
        """Parámetros avanzados de vectores de movimiento y x264"""
        group = QGroupBox("Vectores de Movimiento")
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(group)

        self.motion_check = QCheckBox("Configurar búsqueda de movimiento")
        self.motion_check.setToolTip(
            "Activa parámetros de búsqueda de movimiento.\n"
            "Estos controles influyen en cómo el codificador busca bloques parecidos en otros frames."
        )
        layout.addWidget(self.motion_check)

        # Método de búsqueda
        me_layout = QHBoxLayout()
        me_label = QLabel("Método (me):")
        me_tooltip = (
            "Algoritmo de búsqueda de movimiento.\n"
            "dia: rombo pequeño, rápido pero menos exhaustivo.\n"
            "hex: hexágono, buen compromiso entre coste y calidad.\n"
            "umh: búsqueda más amplia y precisa.\n"
            "esa: exhaustiva, muy costosa en tiempo."
        )
        me_label.setToolTip(me_tooltip)
        me_layout.addWidget(me_label)
        self.me_combo = QComboBox()
        self.me_combo.addItems(["dia", "hex", "umh", "esa"])
        self.me_combo.setCurrentText("hex")
        self.me_combo.setToolTip(me_tooltip)
        me_layout.addWidget(self.me_combo)

        merange_label = QLabel("Rango (merange):")
        merange_tooltip = (
            "Ventana de búsqueda en píxeles para los vectores de movimiento.\n"
            "Valores pequeños: menos coste y menos capacidad para seguir movimientos grandes.\n"
            "Valores grandes: más posibilidades de encontrar coincidencias, pero más tiempo de codificación."
        )
        merange_label.setToolTip(merange_tooltip)
        me_layout.addWidget(merange_label)
        self.merange_spin = QSpinBox()
        self.merange_spin.setRange(4, 64)
        self.merange_spin.setValue(16)
        self.merange_spin.setToolTip(merange_tooltip)
        me_layout.addWidget(self.merange_spin)

        layout.addLayout(me_layout)

        self.x264_advanced_check = QCheckBox("Configurar parámetros avanzados de x264")
        self.x264_advanced_check.setToolTip(
            "Configurar parámetros avanzados que pueden afectar a los vectores de movimiento"
        )
        layout.addWidget(self.x264_advanced_check)

        ref_layout = QHBoxLayout()
        ref_label = QLabel("Frames de referencia (ref):")
        ref_tooltip = (
            "Número de frames de referencia que el codificador puede consultar en predicción INTER.\n"
            "1: comparación más simple y fácil de interpretar.\n"
            "Valores mayores: más libertad para comprimir mejor, pero más complejidad y coste."
        )
        ref_label.setToolTip(ref_tooltip)
        ref_layout.addWidget(ref_label)
        self.ref_spin = QSpinBox()
        self.ref_spin.setRange(1, 16)
        self.ref_spin.setValue(1)
        self.ref_spin.setToolTip(ref_tooltip)
        ref_layout.addWidget(self.ref_spin)
        layout.addLayout(ref_layout)

        subme_layout = QHBoxLayout()
        subme_label = QLabel("Refinamiento subpíxel (subme):")
        subme_tooltip = (
            "Nivel de refinamiento en estimación de movimiento y decisión de modos.\n"
            "Valores bajos: menos coste computacional, decisiones más aproximadas.\n"
            "Valores altos: búsqueda y evaluación más precisas, normalmente más lentas."
        )
        subme_label.setToolTip(subme_tooltip)
        subme_layout.addWidget(subme_label)
        self.subme_spin = QSpinBox()
        self.subme_spin.setRange(0, 11)
        self.subme_spin.setValue(7)
        self.subme_spin.setToolTip(subme_tooltip)
        subme_layout.addWidget(self.subme_spin)
        layout.addLayout(subme_layout)

        partitions_layout = QHBoxLayout()
        partitions_label = QLabel("Particiones:")
        partitions_tooltip = (
            "Controla qué particiones INTER se permiten al codificador.\n"
            "all: habilita todas las particiones habituales; es la opción docente recomendada.\n"
            "none: desactiva particiones avanzadas.\n"
            "p8x8: permite particiones P de 8x8.\n"
            "b8x8: permite particiones B de 8x8.\n"
            "i8x8: permite particiones intra de 8x8.\n"
            "i4x4: permite particiones intra de 4x4.\n"
            "mixed: combinación intermedia pensada para experimentar con restricciones."
        )
        partitions_label.setToolTip(partitions_tooltip)
        partitions_layout.addWidget(partitions_label)
        self.partitions_combo = QComboBox()
        self.partitions_combo.addItems(
            ["all", "none", "p8x8", "b8x8", "i8x8", "i4x4", "mixed"]
        )
        self.partitions_combo.setCurrentText("all")
        self.partitions_combo.setToolTip(partitions_tooltip)
        partitions_layout.addWidget(self.partitions_combo)
        layout.addLayout(partitions_layout)

        self.motion_check.toggled.connect(self._update_motion_control_states)
        self.x264_advanced_check.toggled.connect(self._update_motion_control_states)
        self._update_motion_control_states()

        return group

    def create_buttons_group(self):
        """Botones de acción"""
        group = QGroupBox("")
        group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QHBoxLayout(group)

        self.run_btn = QPushButton("Codificar")
        self.run_btn.clicked.connect(self.run_encoding)
        self.run_btn.setStyleSheet(
            "QPushButton { font-weight: bold; padding: 15px; font-size: 20px; min-width: 200px; }"
        )
        layout.addWidget(self.run_btn)

        return group

    def select_input_file(self):
        """Selecciona archivo de entrada"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleccionar video de entrada",
            "",
            "Videos (*.y4m *.mp4 *.avi *.mkv *.mov);;Todos los archivos (*)",
        )
        if file_path:
            self.input_edit.setText(file_path)
            # Sugerir nombre de salida basado en el de entrada
            input_path = Path(file_path)
            output_name = f"{input_path.stem}_codificado.mp4"
            self.output_edit.setText(output_name)

    def build_ffmpeg_params(self):
        """Construye los parámetros de ffmpeg basados en la configuración"""
        params = []
        x264_params = []

        # Codec
        params.append(f"-c:v {self.codec_combo.currentText()}")

        # Preset
        if self.preset_check.isChecked():
            params.append(f"-preset {self.preset_combo.currentText()}")

        # Tune
        if self.tune_check.isChecked():
            params.append(f"-tune {self.tune_combo.currentText()}")

        # GOP
        if self.gop_check.isChecked():
            keyint = self.keyint_spin.value()
            minkeyint = self.minkeyint_spin.value()
            x264_params.extend([f"keyint={keyint}", f"min-keyint={minkeyint}"])
            if self.scenecut_check.isChecked():
                x264_params.append(f"scenecut={self.scenecut_spin.value()}")

        if self.bframes_check.isChecked():
            x264_params.append(f"bframes={self.bframes_spin.value()}")
            x264_params.append(f"b-adapt={self.badapt_combo.currentText()}")

        # CBR
        if self.cbr_radio.isChecked():
            bitrate = self.bitrate_spin.value()
            bufsize = self.bufsize_spin.value()
            params.append(f"-b:v {bitrate}k")
            params.append(f"-minrate {bitrate}k")
            params.append(f"-maxrate {bitrate}k")
            params.append(f"-bufsize {bufsize}k")

        # VBR
        elif self.vbr_radio.isChecked():
            # QP fijo (solo si está marcado en modo VBR)
            if self.qp_check.isChecked():
                qp = self.qp_spin.value()
                params.append(f"-qp {qp}")
            else:
                crf = self.crf_spin.value()
                params.append(f"-crf {crf}")

        # Vectores de movimiento
        if self.motion_check.isChecked():
            me = self.me_combo.currentText()
            merange = self.merange_spin.value()
            x264_params.extend([f"me={me}", f"merange={merange}"])

        if self.x264_advanced_check.isChecked():
            x264_params.extend(
                [
                    f"ref={self.ref_spin.value()}",
                    f"subme={self.subme_spin.value()}",
                    f"partitions={self.partitions_combo.currentText()}",
                ]
            )

        if x264_params:
            params.append(f"-x264-params {':'.join(x264_params)}")

        return " ".join(params)

    def run_encoding(self):
        """Ejecuta la codificación directamente con la clase MBVisualizer"""
        input_file = self.input_edit.text().strip()
        output_file = self.output_edit.text().strip()

        if not input_file:
            QMessageBox.warning(
                self, "Error", "Debes seleccionar un archivo de entrada"
            )
            return

        if not output_file:
            QMessageBox.warning(
                self, "Error", "Debes especificar un nombre para el archivo de salida"
            )
            return

        # Verificar que el archivo de entrada existe
        if not os.path.exists(input_file):
            QMessageBox.warning(
                self, "Error", f"El archivo de entrada no existe: {input_file}"
            )
            return

        # Construir parámetros
        ffmpeg_params = self.build_ffmpeg_params()

        self.console.append(f"Parámetros FFmpeg: {ffmpeg_params}\n")
        self.console.append("\n" + "-" * 50 + "\n")

        self._show_encoding_progress_dialog("Iniciando codificación...")

        # Ejecutar en thread separado
        self.worker = EncodingWorker(
            input_file, output_file, ffmpeg_params, self.ffmpeg_path, self.ffprobe_path
        )
        self.worker.progress.connect(self.update_progress)
        self.worker.finished.connect(self.on_encoding_finished)
        self.worker.error.connect(self.on_encoding_error)
        self.worker.start()

        # Deshabilitar botón mientras se ejecuta
        self.run_btn.setEnabled(False)

    def update_progress(self, message):
        """Actualiza el progreso"""
        self.status_bar.showMessage(message)
        self._update_encoding_progress_dialog(message)

    def on_encoding_finished(self, encoded_video):
        """Codificación completada exitosamente"""
        self.run_btn.setEnabled(True)
        self.status_bar.showMessage("Finalizando carga del vídeo codificado...")
        self._update_encoding_progress_dialog(
            "Finalizando carga del vídeo codificado..."
        )

        self.console.append("Codificación completada correctamente.\n")
        self.console.append(f"Video codificado: {encoded_video}\n")
        self.console.append("=" * 50 + "\n")

        mb_data_path = None
        if os.path.exists(encoded_video):
            try:
                self.video_player.load_video(encoded_video)
                mb_data_path = self._find_mb_data_file(encoded_video)
                if mb_data_path and os.path.exists(mb_data_path):
                    self.console.append(f"Datos de análisis cargados: {mb_data_path}\n")

                self.update_analysis_tab(
                    video_path=encoded_video, sidecar_path=mb_data_path
                )

                print(
                    "Datos de frame cargados desde el sidecar de análisis cuando está disponible"
                )

                self.console.append("Video codificado cargado en la herramienta.\n")
            except Exception as e:
                self.console.append(f"Error cargando video en reproductor: {e}\n")

        self.status_bar.showMessage("Codificación completada")
        self._close_encoding_progress_dialog()
        QMessageBox.information(
            self,
            "Completado",
            "La codificación ha finalizado y el vídeo codificado se ha cargado automáticamente en la herramienta.",
        )

    def on_encoding_error(self, error_msg):
        """Error en la codificación"""
        self.run_btn.setEnabled(True)
        self.status_bar.showMessage("Error en la codificación")
        self._close_encoding_progress_dialog()

        self.console.append(f"ERROR: {error_msg}\n")
        self.console.append("=" * 50 + "\n")
        QMessageBox.critical(
            self, "Error", f"Error durante la codificación:\n{error_msg}"
        )

    def clear_console(self):
        """Limpia la consola"""
        self.console.clear()

    def _show_encoding_progress_dialog(self, message):
        """Muestra un popup modal mientras se codifica."""
        if self.encoding_progress_dialog is None:
            dialog = QProgressDialog("Codificando vídeo...", None, 0, 0, self)
            dialog.setWindowTitle("Codificación en progreso")
            dialog.setWindowModality(Qt.ApplicationModal)
            dialog.setMinimumDuration(0)
            dialog.setCancelButton(None)
            dialog.setAutoClose(False)
            dialog.setAutoReset(False)
            dialog.setValue(0)
            self.encoding_progress_dialog = dialog

        self.encoding_progress_dialog.setLabelText(message)
        self.encoding_progress_dialog.show()
        self.encoding_progress_dialog.raise_()
        self.encoding_progress_dialog.activateWindow()

    def _update_encoding_progress_dialog(self, message):
        """Actualiza el texto del popup de progreso."""
        if self.encoding_progress_dialog is not None:
            self.encoding_progress_dialog.setLabelText(message)

    def _close_encoding_progress_dialog(self):
        """Cierra el popup de progreso si está visible."""
        if self.encoding_progress_dialog is not None:
            self.encoding_progress_dialog.hide()

    def toggle_console(self):
        """Muestra/oculta la consola desde el menú"""
        visible = self.console_panel.isVisible()
        self.console_panel.setVisible(not visible)
        self.toggle_console_action.setChecked(not visible)
        self.toggle_console_action.setText(
            "Ocultar Consola de Log" if not visible else "Mostrar Consola de Log"
        )

    def show_settings_dialog(self):
        """Muestra el diálogo de configuración"""
        dialog = SettingsDialog(self, self.ffmpeg_path, self.ffprobe_path)
        if dialog.exec() == QDialog.Accepted:
            self.ffmpeg_path, self.ffprobe_path = dialog.get_paths()
            self.video_player.ffmpeg_path = self.ffmpeg_path
            self._save_local_settings()
            self.status_bar.showMessage("Configuración guardada", 3000)

    def _settings_file_path(self):
        return Path.cwd() / SETTINGS_FILE_NAME

    def _load_local_settings(self):
        settings_path = self._settings_file_path()
        if not settings_path.exists():
            return

        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                settings_data = json.load(f)

            ffmpeg_path = settings_data.get("ffmpeg_path")
            ffprobe_path = settings_data.get("ffprobe_path")

            if isinstance(ffmpeg_path, str) and ffmpeg_path.strip():
                self.ffmpeg_path = ffmpeg_path.strip()
            if isinstance(ffprobe_path, str) and ffprobe_path.strip():
                self.ffprobe_path = ffprobe_path.strip()
        except (OSError, json.JSONDecodeError) as e:
            print(f"Advertencia: no se pudo cargar la configuración local: {e}")

    def _save_local_settings(self):
        settings_path = self._settings_file_path()
        settings_data = {
            "ffmpeg_path": self.ffmpeg_path,
            "ffprobe_path": self.ffprobe_path,
        }

        with open(settings_path, "w", encoding="utf-8") as f:
            json.dump(settings_data, f, indent=2, ensure_ascii=False)

    def show_about_dialog(self):
        """Muestra el diálogo Acerca de"""
        dialog = AboutDialog(self)
        dialog.exec()

    def on_cbr_toggled(self, checked):
        """Maneja el cambio del checkbox CBR"""
        if checked:
            # Desmarcar VBR si está marcado
            if self.vbr_radio.isChecked():
                self.vbr_radio.setChecked(False)
                self.vbr_widget.setEnabled(False)

        self.cbr_widget.setEnabled(checked)

    def on_vbr_toggled(self, checked):
        """Maneja el cambio del checkbox VBR"""
        if checked:
            # Desmarcar CBR si está marcado
            if self.cbr_radio.isChecked():
                self.cbr_radio.setChecked(False)
                self.cbr_widget.setEnabled(False)

        self.vbr_widget.setEnabled(checked)

    def _set_widgets_enabled(self, widgets, enabled):
        for widget in widgets:
            widget.setEnabled(enabled)

    def _update_qp_control_state(self, checked=None):
        self.qp_spin.setEnabled(self.qp_check.isChecked())

    def _update_gop_control_states(self, checked=None):
        gop_enabled = self.gop_check.isChecked()
        self._set_widgets_enabled(
            [self.keyint_spin, self.minkeyint_spin, self.scenecut_check], gop_enabled
        )
        self.scenecut_spin.setEnabled(gop_enabled and self.scenecut_check.isChecked())

        bframes_enabled = self.bframes_check.isChecked()
        self._set_widgets_enabled(
            [self.bframes_spin, self.badapt_combo], bframes_enabled
        )

    def _update_motion_control_states(self, checked=None):
        motion_enabled = self.motion_check.isChecked()
        self._set_widgets_enabled([self.me_combo, self.merange_spin], motion_enabled)

        x264_advanced_enabled = self.x264_advanced_check.isChecked()
        self._set_widgets_enabled(
            [self.ref_spin, self.subme_spin, self.partitions_combo],
            x264_advanced_enabled,
        )


def launch_gui():
    """Lanza la aplicación gráfica."""
    if not HAS_GUI:
        print("Error: PyQt5 no está instalado. Instala con: pip install PyQt5")
        print("O usa la interfaz de línea de comandos:")
        print(
            'python -m h264tt.gui.main input.y4m --output-video output.mp4 --params "-c:v libx264"'
        )
        sys.exit(1)

    try:
        app = QApplication.instance()
        if app is None:
            app = QApplication(sys.argv)
        app.setStyle("Fusion")
        app.setApplicationName("H264TT")
        app.setApplicationVersion("0.1")
        app.setOrganizationName("UDC")

        window = MBVisualizerGUI()
        window.show()
        return app.exec()
    except Exception as e:
        print(f"Error al iniciar la interfaz gráfica: {e}")
        print("Posibles soluciones:")
        print("1. Si estás en un entorno sin display gráfico: export DISPLAY=:0")
        print(
            "2. Si hay conflicto con OpenCV: pip uninstall opencv-python && pip install opencv-python-headless"
        )
        print(
            '3. Para usar solo línea de comandos: python -m h264tt.gui.main input.mp4 --params "..."'
        )
        sys.exit(1)


def main():
    return launch_gui()


class SettingsDialog(QDialog):
    """Diálogo de configuración para paths de FFmpeg"""

    def __init__(self, parent=None, ffmpeg_path="ffmpeg", ffprobe_path="ffprobe"):
        super().__init__(parent)
        self.setWindowTitle("Configuración")
        self.setModal(True)

        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path

        layout = QVBoxLayout(self)

        # Grupo FFmpeg
        ffmpeg_group = QGroupBox("Ubicación de FFmpeg")
        ffmpeg_layout = QVBoxLayout(ffmpeg_group)

        # FFmpeg path
        ffmpeg_path_layout = QHBoxLayout()
        ffmpeg_label = QLabel("FFmpeg:")
        ffmpeg_tooltip = (
            "Ruta al ejecutable de FFmpeg que usará la aplicación para codificar y extraer datos de bajo nivel.\n"
            "Para esta práctica se recomienda apuntar a FFmpeg 6.1.1."
        )
        ffmpeg_label.setToolTip(ffmpeg_tooltip)
        ffmpeg_path_layout.addWidget(ffmpeg_label)
        self.ffmpeg_edit = QLineEdit(self.ffmpeg_path)
        self.ffmpeg_edit.setToolTip(ffmpeg_tooltip)
        ffmpeg_path_layout.addWidget(self.ffmpeg_edit)
        ffmpeg_browse_btn = QPushButton("...")
        ffmpeg_browse_btn.setToolTip(ffmpeg_tooltip)
        ffmpeg_browse_btn.clicked.connect(self.browse_ffmpeg)
        ffmpeg_path_layout.addWidget(ffmpeg_browse_btn)
        ffmpeg_layout.addLayout(ffmpeg_path_layout)

        # FFprobe path
        ffprobe_path_layout = QHBoxLayout()
        ffprobe_label = QLabel("FFprobe:")
        ffprobe_tooltip = (
            "Ruta al ejecutable de FFprobe, usado para leer metadatos como duración, fps o resolución.\n"
            "Debe corresponder a la misma instalación que FFmpeg siempre que sea posible."
        )
        ffprobe_label.setToolTip(ffprobe_tooltip)
        ffprobe_path_layout.addWidget(ffprobe_label)
        self.ffprobe_edit = QLineEdit(self.ffprobe_path)
        self.ffprobe_edit.setToolTip(ffprobe_tooltip)
        ffprobe_path_layout.addWidget(self.ffprobe_edit)
        ffprobe_browse_btn = QPushButton("...")
        ffprobe_browse_btn.setToolTip(ffprobe_tooltip)
        ffprobe_browse_btn.clicked.connect(self.browse_ffprobe)
        ffprobe_path_layout.addWidget(ffprobe_browse_btn)
        ffmpeg_layout.addLayout(ffprobe_path_layout)

        layout.addWidget(ffmpeg_group)

        # Botones
        buttons_layout = QHBoxLayout()
        buttons_layout.addStretch()

        cancel_btn = QPushButton("Cancelar")
        cancel_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(cancel_btn)

        save_btn = QPushButton("Guardar")
        save_btn.clicked.connect(self.accept)
        save_btn.setDefault(True)
        buttons_layout.addWidget(save_btn)

        layout.addLayout(buttons_layout)

        self.setMinimumWidth(500)

    def browse_ffmpeg(self):
        """Seleccionar archivo FFmpeg"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar FFmpeg", "", "Todos los archivos (*)"
        )
        if file_path:
            self.ffmpeg_edit.setText(file_path)

    def browse_ffprobe(self):
        """Seleccionar archivo FFprobe"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Seleccionar FFprobe", "", "Todos los archivos (*)"
        )
        if file_path:
            self.ffprobe_edit.setText(file_path)

    def get_paths(self):
        """Devuelve los paths configurados"""
        return self.ffmpeg_edit.text(), self.ffprobe_edit.text()


class AboutDialog(QDialog):
    """Diálogo Acerca de con información de licencia"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Acerca de H264TT")
        self.setModal(True)

        layout = QVBoxLayout(self)

        # Título
        title_label = QLabel("H264TT")
        title_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title_label)

        # Versión
        version_label = QLabel("Versión 0.1")
        layout.addWidget(version_label)

        # Autor
        author_label = QLabel("Valentin Barral")
        layout.addWidget(author_label)

        # Descripción
        desc_label = QLabel(
            "H264 Teaching Tool para análisis y visualización docente\n"
            "de codificación H.264/AVC con FFmpeg."
        )
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        layout.addSpacing(10)

        # Licencia
        license_group = QGroupBox("Licencia")
        license_layout = QVBoxLayout(license_group)

        license_text = QTextEdit()
        license_text.setPlainText(
            "Copyright (c) 2026 Valentin Barral\n\n"
            "Licencia Creative Commons con atribución (CC BY 4.0).\n\n"
            "Debe darse atribución apropiada al autor e indicarse si se hicieron cambios.\n\n"
            "Más información: https://creativecommons.org/licenses/by/4.0/"
        )
        license_text.setReadOnly(True)
        license_text.setMaximumHeight(150)
        license_layout.addWidget(license_text)

        layout.addWidget(license_group)

        # Botón cerrar
        close_btn = QPushButton("Cerrar")
        close_btn.clicked.connect(self.accept)
        close_btn.setDefault(True)
        layout.addWidget(close_btn)

        self.setMinimumWidth(500)
        self.setMinimumHeight(400)


class VideoPlayerWidget(QWidget):
    """
    Widget personalizado para reproducción de video con overlay de macrobloques.
    Usa OpenCV para procesamiento de video y QLabel para display.
    """

    frameChanged = pyqtSignal(int)

    def __init__(self, parent=None, ffmpeg_path="ffmpeg"):
        super().__init__(parent)
        self.ffmpeg_path = ffmpeg_path

        # Estado del reproductor
        self.video_path = None
        self.cap = None
        self.current_frame = 0
        self.total_frames = 0
        self.fps = 25
        self.is_playing = False
        self.overlay_enabled = False
        self.motion_vectors_enabled = False
        self.overlay_opacity = 0.35  # Opacidad por defecto (35%)
        self.current_frame_data = None  # Último frame leído

        # Datos de macrobloques (se cargarán cuando se seleccione un video procesado)
        self.mb_data = None
        self.frame_mb_data = []

        # Datos de tamaño de frame
        self.frame_sizes = []
        self.frame_types = []
        self.frame_data = {
            "qp_values": [],
            "size_values": [],
            "frame_numbers": [],
            "bitrate_values": [],
            "motion_vectors": [],
        }
        self.frame_stats = {
            "frame_types": [],
            "by_type": {"I": 0, "P": 0, "B": 0},
            "total_frames": 0,
        }

        # Timer para reproducción
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._next_frame)

        # Configurar UI
        self._setup_ui()

    def _setup_ui(self):
        """Configura la interfaz del reproductor."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        video_frame = QFrame()
        video_frame.setObjectName("videoFrame")
        video_grid = QGridLayout(video_frame)
        video_grid.setContentsMargins(0, 0, 0, 0)

        # Área de video (usando QLabel para mostrar frames)
        self.video_label = QLabel("No video loaded")
        self.video_label.setAlignment(Qt.AlignCenter)
        self.video_label.setStyleSheet(
            "border: 2px solid #ccc; background-color: #f0f0f0;"
        )
        self.video_label.setMinimumSize(640, 360)
        self.video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        video_grid.addWidget(self.video_label, 0, 0)

        layout.addWidget(video_frame)

        # Controles de reproducción
        controls_layout = QHBoxLayout()

        # Botones de reproducción
        self.play_btn = QPushButton("▶")
        self.play_btn.setToolTip("Play/Pause")
        self.play_btn.clicked.connect(self.toggle_play_pause)
        controls_layout.addWidget(self.play_btn)

        self.prev_frame_btn = QPushButton("⏮")
        self.prev_frame_btn.setToolTip("Previous Frame")
        self.prev_frame_btn.clicked.connect(self.prev_frame)
        controls_layout.addWidget(self.prev_frame_btn)

        self.next_frame_btn = QPushButton("⏭")
        self.next_frame_btn.setToolTip("Next Frame")
        self.next_frame_btn.clicked.connect(self.next_frame)
        controls_layout.addWidget(self.next_frame_btn)

        # Slider de posición
        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 100)
        self.position_slider.sliderMoved.connect(self.seek_to_position)
        self.position_slider.sliderPressed.connect(self.pause_on_slider_press)
        self.position_slider.sliderReleased.connect(self.resume_on_slider_release)
        controls_layout.addWidget(self.position_slider)

        # Etiqueta de tiempo
        self.time_label = QLabel("0:00 / 0:00")
        controls_layout.addWidget(self.time_label)

        # Checkbox para overlay
        self.overlay_check = QCheckBox("Mostrar Overlay MB")
        self.overlay_check.setChecked(False)
        self.overlay_check.stateChanged.connect(self.toggle_overlay)
        controls_layout.addWidget(self.overlay_check)

        self.motion_vectors_check = QCheckBox("Mostrar vectores MV")
        self.motion_vectors_check.setChecked(False)
        self.motion_vectors_check.setToolTip(
            "Muestra los vectores de movimiento extraídos del bitstream H.264."
        )
        self.motion_vectors_check.stateChanged.connect(self.toggle_motion_vectors)
        controls_layout.addWidget(self.motion_vectors_check)

        # Control de opacidad
        controls_layout.addWidget(QLabel("Opacidad:"))
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setMinimum(0)
        self.opacity_slider.setMaximum(100)
        self.opacity_slider.setValue(35)  # 35% por defecto
        self.opacity_slider.setMaximumWidth(150)
        self.opacity_slider.setToolTip("Ajusta la transparencia del overlay")
        self.opacity_slider.valueChanged.connect(self.update_overlay_opacity)
        controls_layout.addWidget(self.opacity_slider)

        # Etiqueta de porcentaje
        self.opacity_label = QLabel("35%")
        self.opacity_label.setMinimumWidth(40)
        controls_layout.addWidget(self.opacity_label)

        controls_layout.addStretch()
        layout.addLayout(controls_layout)

        # Panel de información separado para el inspector derecho
        self.info_panel = self._create_info_panel()

        # Estado inicial
        self._update_controls_state()

    def _show_status_message(self, message, timeout=0):
        status_bar = getattr(self, "status_bar", None)
        if status_bar is not None:
            status_bar.showMessage(message, timeout)

    def _format_bytes_human(self, num_bytes):
        if num_bytes is None:
            return "-"

        size = float(num_bytes)
        units = ["bytes", "KB", "MB", "GB", "TB"]
        unit_index = 0

        while size >= 1024 and unit_index < len(units) - 1:
            size /= 1024
            unit_index += 1

        if unit_index == 0:
            return f"{int(size)} bytes"

        formatted = f"{size:.2f}".rstrip("0").rstrip(".")
        return f"{formatted} {units[unit_index]}"

    def _has_motion_vector_data(self):
        return any(
            frame_info.get("motion_vectors")
            for frame_info in getattr(self, "frame_mb_data", [])
        )

    def _find_mb_data_file(self, video_path):
        """Busca el sidecar o log asociado a un video."""
        base_path = os.path.splitext(video_path)[0]
        candidates = [
            base_path + ".analysis.json",
            base_path.replace("_encoded", "") + ".analysis.json",
            base_path + "_mb_types.txt",
            base_path + "_analysis.log",
            base_path.replace("_encoded", "") + "_mb_types.txt",
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        return None

    def _create_info_panel(self):
        """Crea el panel lateral con información del frame."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        stats_widget = QWidget()
        stats_layout = QVBoxLayout(stats_widget)
        stats_layout.setContentsMargins(0, 0, 0, 0)

        frame_layout = QHBoxLayout()
        frame_layout.addWidget(QLabel("Frame:"))
        self.frame_number_label = QLabel("-")
        self.frame_number_label.setStyleSheet("font-weight: bold;")
        frame_layout.addWidget(self.frame_number_label)
        frame_layout.addStretch()
        stats_layout.addLayout(frame_layout)

        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Tipo:"))
        self.frame_type_label = QLabel("-")
        self.frame_type_label.setStyleSheet(
            "font-weight: bold; font-size: 26px; min-width: 34px;"
        )
        type_layout.addWidget(self.frame_type_label)
        type_layout.addStretch()
        stats_layout.addLayout(type_layout)

        qp_layout = QHBoxLayout()
        qp_layout.addWidget(QLabel("QP medio:"))
        self.frame_qp_label = QLabel("-")
        self.frame_qp_label.setStyleSheet("font-weight: bold;")
        qp_layout.addWidget(self.frame_qp_label)
        qp_layout.addStretch()
        stats_layout.addLayout(qp_layout)

        size_layout = QHBoxLayout()
        size_layout.addWidget(QLabel("Tamaño:"))
        self.frame_size_label = QLabel("-")
        self.frame_size_label.setStyleSheet("font-weight: bold;")
        size_layout.addWidget(self.frame_size_label)
        size_layout.addStretch()
        stats_layout.addLayout(size_layout)

        stats_layout.addWidget(QLabel(""))

        # Macroblock statistics
        stats_layout.addWidget(QLabel("Macrobloques:"))
        self.mb_stats_layout = QVBoxLayout()

        # INTRA blocks
        intra_layout = QHBoxLayout()
        intra_layout.addWidget(QLabel("INTRA (Rojo):"))
        self.intra_label = QLabel("-")
        self.intra_label.setStyleSheet("color: red; font-weight: bold;")
        intra_layout.addWidget(self.intra_label)
        intra_layout.addStretch()
        self.mb_stats_layout.addLayout(intra_layout)

        # SKIP blocks
        skip_layout = QHBoxLayout()
        skip_layout.addWidget(QLabel("SKIP (Verde):"))
        self.skip_label = QLabel("-")
        self.skip_label.setStyleSheet("color: green; font-weight: bold;")
        skip_layout.addWidget(self.skip_label)
        skip_layout.addStretch()
        self.mb_stats_layout.addLayout(skip_layout)

        # INTER blocks
        inter_layout = QHBoxLayout()
        inter_layout.addWidget(QLabel("INTER (Azul):"))
        self.inter_label = QLabel("-")
        self.inter_label.setStyleSheet("color: blue; font-weight: bold;")
        inter_layout.addWidget(self.inter_label)
        inter_layout.addStretch()
        self.mb_stats_layout.addLayout(inter_layout)

        stats_layout.addLayout(self.mb_stats_layout)

        info_widget = self._create_collapsible_section(
            "Información del Frame", stats_widget, True, header_font_size=17
        )
        layout.addWidget(info_widget)

        # Añadir leyenda de tipos de MB como sección colapsable
        legend_widget = self._create_collapsible_section(
            "Leyenda de macrobloques",
            self._create_legend_widget(),
            False,
            header_font_size=17,
        )
        layout.addWidget(legend_widget)

        # Siempre visible
        panel.setVisible(True)

        return panel

    def _create_collapsible_section(
        self, title, content_widget, expanded=True, header_font_size=17
    ):
        container = QWidget()
        outer_layout = QVBoxLayout(container)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        header_btn = QPushButton(f"▾ {title}" if expanded else f"▸ {title}")
        header_btn.setCheckable(True)
        header_btn.setChecked(expanded)
        header_btn.setStyleSheet(
            f"QPushButton {{ text-align: left; font-weight: bold; font-size: {header_font_size}px; padding: 8px 10px; background: #eef3fb; margin: 0; }}"
        )
        outer_layout.addWidget(header_btn)

        content_widget.setVisible(expanded)
        if isinstance(content_widget, QGroupBox):
            content_widget.setTitle("")
            content_widget.setFlat(True)
        outer_layout.addWidget(content_widget)

        def toggle_section(checked):
            header_btn.setText(f"▾ {title}" if checked else f"▸ {title}")
            content_widget.setVisible(checked)

        header_btn.toggled.connect(toggle_section)
        return container

    def _create_legend_widget(self):
        """Crea el widget de leyenda con todos los tipos de MB y segmentación."""
        legend_group = QGroupBox("Leyenda de Macrobloques")
        legend_layout = QVBoxLayout(legend_group)

        # Crear área scrollable para la leyenda
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        # Sin límite de altura para que ocupe todo el espacio disponible

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)

        mode_layout = QHBoxLayout()
        mode_layout.addWidget(QLabel("Modo de leyenda:"))
        self.legend_mode_combo = QComboBox()
        self.legend_mode_combo.addItems(["Compacto", "Detallado"])
        self.legend_mode_combo.setCurrentText("Detallado")
        mode_layout.addWidget(self.legend_mode_combo)
        mode_layout.addStretch()
        scroll_layout.addLayout(mode_layout)

        intro_label = QLabel(
            "Símbolos basados en la salida de depuración de FFmpeg/libx264. "
            "Cada fila resume color, símbolo, nombre y una explicación breve del modo de codificación."
        )
        intro_label.setWordWrap(True)
        intro_label.setStyleSheet("color: #556; font-size: 9pt; margin-bottom: 6px;")
        scroll_layout.addWidget(intro_label)

        # Colores y símbolos de MB (BGR format como en OpenCV)
        # Los colores están en formato BGR, necesitamos convertir a RGB para Qt
        # IMPORTANTE: Estos valores deben coincidir exactamente con self.colors en MBVisualizer
        mb_types_info = {
            "INTRA": [
                (
                    "P",
                    (0, 0, 139),
                    "PCM — bloque codificado como píxeles crudos, sin predicción ni transformada.",
                ),
                (
                    "A",
                    (0, 69, 255),
                    "AC Prediction — intra con predicción de coeficientes AC; raro en H.264, más típico de MPEG-4.",
                ),
                (
                    "i",
                    (0, 100, 255),
                    "Intra 4×4 — predicción espacial en bloques pequeños, útil para detalle fino.",
                ),
                (
                    "I",
                    (0, 0, 200),
                    "Intra 16×16 — predicción espacial del macroblock completo, típica en zonas más planas.",
                ),
            ],
            "SKIP": [
                (
                    "S",
                    (0, 180, 0),
                    "Skip — no transmite residuo; reutiliza la predicción de movimiento del bloque.",
                ),
                (
                    "d",
                    (100, 220, 0),
                    "Direct Skip — modo B muy eficiente con predicción temporal directa y residuo nulo.",
                ),
                (
                    "g",
                    (0, 100, 0),
                    "GMC Skip — variante con compensación de movimiento global; rara fuera de MPEG-4.",
                ),
            ],
            "INTER": [
                (
                    "D",
                    (200, 0, 0),
                    "Direct — predicción temporal directa en B-frames con movimiento explícito.",
                ),
                (
                    "G",
                    (200, 0, 100),
                    "GMC — compensación de movimiento global; heredado de MPEG-4, no habitual en H.264.",
                ),
                (
                    ">",
                    (200, 100, 0),
                    "Forward (L0) — usa referencia pasada; es la predicción inter más común en P-frames.",
                ),
                (
                    "<",
                    (200, 200, 0),
                    "Backward (L1) — usa referencia futura; aparece en B-frames.",
                ),
                (
                    "X",
                    (200, 0, 200),
                    "Bi-pred — combina referencia pasada y futura para mejorar la predicción.",
                ),
            ],
        }

        self.legend_detail_labels = []

        # Añadir cada categoría
        for category, types in mb_types_info.items():
            cat_label = QLabel(f"<b>{category}</b>")
            scroll_layout.addWidget(cat_label)

            for symbol, color_bgr, description in types:
                type_layout = QHBoxLayout()

                b, g, r = color_bgr
                color_label = QLabel()
                color_label.setFixedSize(20, 20)
                color_label.setStyleSheet(
                    f"background-color: rgb({r},{g},{b}); border: 1px solid black;"
                )
                color_label.setToolTip(description)
                type_layout.addWidget(color_label)

                symbol_label = QLabel(f"<b>{symbol}</b>")
                symbol_label.setFixedWidth(25)
                symbol_label.setToolTip(description)
                type_layout.addWidget(symbol_label)

                name_label = QLabel(description.split("—")[0].strip())
                name_label.setMinimumWidth(120)
                name_label.setToolTip(description)
                type_layout.addWidget(name_label)

                desc_label = QLabel(description)
                desc_label.setWordWrap(True)
                desc_label.setStyleSheet("font-size: 9pt;")
                desc_label.setToolTip(description)
                type_layout.addWidget(desc_label)
                self.legend_detail_labels.append(desc_label)

                type_layout.addStretch()
                scroll_layout.addLayout(type_layout)

            scroll_layout.addWidget(QLabel(""))

        # Sección de segmentación
        seg_label = QLabel("<b>Segmentación</b>")
        scroll_layout.addWidget(seg_label)

        segmentation_info = [
            (
                "+",
                "8×8 — divide el macroblock en cuatro subbloques para más flexibilidad.",
            ),
            ("-", "16×8 — partición horizontal en dos mitades."),
            ("|", "8×16 — partición vertical en dos mitades."),
            (" ", "16×16 — bloque completo sin subdivisión interna."),
        ]

        for symbol, description in segmentation_info:
            seg_layout = QHBoxLayout()
            symbol_label = QLabel(f"<b>{repr(symbol)}</b>")
            symbol_label.setToolTip(description)
            seg_layout.addWidget(symbol_label)
            desc_label = QLabel(description)
            desc_label.setWordWrap(True)
            desc_label.setToolTip(description)
            seg_layout.addWidget(desc_label)
            self.legend_detail_labels.append(desc_label)
            seg_layout.addStretch()
            scroll_layout.addLayout(seg_layout)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        legend_layout.addWidget(scroll)

        self.legend_mode_combo.currentTextChanged.connect(self._update_legend_mode)
        self._update_legend_mode(self.legend_mode_combo.currentText())

        return legend_group

    def _update_legend_mode(self, mode_text):
        detailed = mode_text == "Detallado"
        for label in getattr(self, "legend_detail_labels", []):
            label.setVisible(detailed)

    def _update_frame_info(self):
        """Actualiza la información del frame actual."""
        if not self.cap or not self.mb_data:
            self.frame_number_label.setText("-")
            self.frame_type_label.setText("-")
            self.frame_type_label.setStyleSheet(
                "font-weight: bold; font-size: 26px; min-width: 34px; color: black;"
            )
            self.frame_qp_label.setText("-")
            self.frame_size_label.setText("-")
            self.intra_label.setText("-")
            self.skip_label.setText("-")
            self.inter_label.setText("-")
            return

        # Si el video está reproduciéndose, mostrar "-" en todos los campos
        if self.is_playing:
            self.frame_number_label.setText("-")
            self.frame_type_label.setText("-")
            self.frame_type_label.setStyleSheet(
                "font-weight: bold; font-size: 26px; min-width: 34px; color: black;"
            )
            self.frame_qp_label.setText("-")
            self.frame_size_label.setText("-")
            self.intra_label.setText("-")
            self.skip_label.setText("-")
            self.inter_label.setText("-")
            return

        frame_num = self.current_frame

        self.frame_number_label.setText(str(frame_num + 1))

        frame_type = (
            self.frame_types[frame_num] if frame_num < len(self.frame_types) else "-"
        )
        frame_type_colors = {"I": "red", "P": "blue", "B": "blueviolet"}
        frame_type_color = frame_type_colors.get(frame_type, "black")
        self.frame_type_label.setText(frame_type)
        self.frame_type_label.setStyleSheet(
            f"font-weight: bold; font-size: 26px; min-width: 34px; color: {frame_type_color};"
        )

        frame_data = getattr(self, "frame_data", {})
        qp_values = frame_data.get("qp_values", [])
        qp_value = qp_values[frame_num] if frame_num < len(qp_values) else None
        self.frame_qp_label.setText(f"{qp_value:.1f}" if qp_value is not None else "-")

        frame_size = (
            self.frame_sizes[frame_num] if frame_num < len(self.frame_sizes) else None
        )
        self.frame_size_label.setText(
            self._format_bytes_human(frame_size) if frame_size is not None else "-"
        )

        # Macroblock statistics - usar datos pre-calculados del log
        if frame_num < len(self.frame_mb_data):
            mb_data = self.frame_mb_data[frame_num]
            mb_stats = mb_data.get("mb_stats", {})

            if mb_stats:
                intra_count = mb_stats.get("intra_count", 0)
                skip_count = mb_stats.get("skip_count", 0)
                inter_count = mb_stats.get("inter_count", 0)
                intra_pct = mb_stats.get("intra_pct", 0)
                skip_pct = mb_stats.get("skip_pct", 0)
                inter_pct = mb_stats.get("inter_pct", 0)

                self.intra_label.setText(f"{intra_count} ({intra_pct:.1f}%)")
                self.skip_label.setText(f"{skip_count} ({skip_pct:.1f}%)")
                self.inter_label.setText(f"{inter_count} ({inter_pct:.1f}%)")
            else:
                # Fallback: calcular desde tokens si no hay estadísticas guardadas
                tokens = mb_data.get("tokens", [])
                total_mb = len(tokens)

                if total_mb > 0:
                    intra_count = sum(
                        1
                        for token in tokens
                        if self._classify_mb_token(token) == "intra"
                    )
                    skip_count = sum(
                        1
                        for token in tokens
                        if self._classify_mb_token(token) == "skip"
                    )
                    inter_count = sum(
                        1
                        for token in tokens
                        if self._classify_mb_token(token) == "inter"
                    )

                    intra_pct = (intra_count / total_mb) * 100
                    skip_pct = (skip_count / total_mb) * 100
                    inter_pct = (inter_count / total_mb) * 100

                    self.intra_label.setText(f"{intra_count} ({intra_pct:.1f}%)")
                    self.skip_label.setText(f"{skip_count} ({skip_pct:.1f}%)")
                    self.inter_label.setText(f"{inter_count} ({inter_pct:.1f}%)")
                else:
                    self.intra_label.setText("-")
                    self.skip_label.setText("-")
                    self.inter_label.setText("-")
        else:
            self.intra_label.setText("-")
            self.skip_label.setText("-")
            self.inter_label.setText("-")

    def _show_frame_info(self, show=True):
        """Muestra el panel de información del frame (siempre visible)."""
        self.info_panel.setVisible(True)  # Siempre visible
        if show:
            self._update_frame_info()

    def _update_frame_badge(self):
        return

    def load_video(self, video_path):
        """Carga un video para reproducción y extrae información de macrobloques usando ffmpeg debug."""
        try:
            if self.cap:
                self.cap.release()

            self.video_path = video_path
            self.cap = cv2.VideoCapture(video_path)

            if not self.cap.isOpened():
                raise ValueError(f"No se pudo abrir el video: {video_path}")

            # Obtener propiedades del video
            self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.fps = self.cap.get(cv2.CAP_PROP_FPS)
            width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

            print(
                f"Video cargado: {width}x{height}, {self.total_frames} frames, {self.fps} fps"
            )

            # Configurar slider
            self.position_slider.setRange(0, self.total_frames - 1)
            self.position_slider.setValue(0)

            # Ir al primer frame
            self.current_frame = 0
            self._display_current_frame()

            sidecar_path = self._find_mb_data_file(video_path)
            if sidecar_path and sidecar_path.endswith(".analysis.json"):
                self.load_mb_data(sidecar_path)
            else:
                # Extraer datos de macrobloques usando ffmpeg debug
                self._extract_mb_data_from_video(video_path)

            # Actualizar controles
            self._update_controls_state()

            if not sidecar_path or not sidecar_path.endswith(".analysis.json"):
                # Cargar información de frames desde archivo .info si existe
                self._load_frame_info_from_file(video_path)

            # Mostrar información del primer frame
            self._update_frame_info()

            return True

        except Exception as e:
            print(f"Error cargando video: {e}")
            QMessageBox.warning(self, "Error", f"No se pudo cargar el video:\n{str(e)}")
            return False

    def _extract_mb_data_from_video(self, video_path):
        """Extrae datos de macrobloques del video usando ffmpeg -debug mb_type."""
        import tempfile
        import json

        try:
            print(f"Extrayendo datos de macrobloques de: {video_path}")
            self._show_status_message("Extrayendo datos de macrobloques...")

            # Crear archivo temporal para la salida de debug
            with tempfile.NamedTemporaryFile(
                mode="w+", suffix=".log", delete=False
            ) as temp_log:
                temp_log_path = temp_log.name

            # Comando ffmpeg para debug mb_type
            cmd = [
                self.ffmpeg_path,
                "-threads",
                "1",  # Procesar con un solo hilo para asegurar salida secuencial
                "-debug",
                "mb_type",
                "-i",
                video_path,
                "-f",
                "null",
                "-",
            ]

            print(f"Ejecutando: {' '.join(cmd)}")
            self._show_status_message(
                "Ejecutando análisis de macrobloques con FFmpeg..."
            )

            # Ejecutar comando y capturar stderr (donde va el debug output)
            with open(temp_log_path, "w", encoding="utf-8") as f_log:
                result = subprocess.run(cmd, stderr=f_log, stdout=subprocess.PIPE)

            if result.returncode != 0:
                print(
                    f"Advertencia: FFmpeg terminó con código {result.returncode}, intentando procesar datos parciales"
                )
                self._show_status_message("Procesando datos parciales...")

            # Parsear la salida del log
            self._show_status_message("Procesando datos de macrobloques...")
            self._parse_mb_debug_output(temp_log_path)

            # Limpiar archivo temporal
            try:
                os.unlink(temp_log_path)
            except OSError:
                pass

            self._show_status_message("Datos de macrobloques cargados exitosamente")

        except Exception as e:
            print(f"Error extrayendo datos de macrobloques: {e}")
            self.mb_data = False
            self.frame_mb_data = []
            self._show_status_message("Error al extraer datos de macrobloques")

    def _load_frame_info_from_file(self, video_path):
        """Carga información de QP y tamaño de frames desde archivo .info si existe."""
        try:
            # Buscar archivo .info correspondiente al video
            video_dir = os.path.dirname(video_path)
            video_name = os.path.splitext(os.path.basename(video_path))[0]
            info_file = os.path.join(video_dir, f"{video_name}.info")

            if not os.path.exists(info_file):
                print(f"No se encontró archivo .info: {info_file}")
                return

            print(f"Cargando información de frames desde: {info_file}")

            frame_info_by_number = {}

            with open(info_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#") or not line:
                        continue  # Saltar comentarios y líneas vacías

                    parts = line.split(",")
                    if len(parts) >= 4:
                        try:
                            frame_num = int(parts[0])
                            frame_type = parts[1]
                            qp_value = float(parts[2])
                            size_value = int(parts[3])

                            frame_info_by_number[frame_num] = {
                                "type": frame_type,
                                "qp": qp_value,
                                "size": size_value,
                            }
                        except (ValueError, IndexError) as e:
                            print(f"Error parseando línea en {info_file}: {line} - {e}")
                            continue

            qp_values = []
            size_values = []
            frame_types = []
            if frame_info_by_number:
                max_frame_num = max(frame_info_by_number)
                for frame_num in range(max_frame_num + 1):
                    frame_info = frame_info_by_number.get(frame_num)
                    if frame_info is None:
                        qp_values.append(None)
                        size_values.append(None)
                        frame_types.append("U")
                    else:
                        qp_values.append(frame_info["qp"])
                        size_values.append(frame_info["size"])
                        frame_types.append(frame_info["type"])

            if qp_values and size_values:
                # Inicializar frame_data si no existe
                if not hasattr(self, "frame_data"):
                    self.frame_data = {
                        "qp_values": [],
                        "size_values": [],
                        "frame_numbers": [],
                        "bitrate_values": [],
                        "motion_vectors": [],
                    }

                # Inicializar frame_stats si no existe
                if not hasattr(self, "frame_stats"):
                    self.frame_stats = {
                        "frame_types": [],
                        "by_type": {"I": 0, "P": 0, "B": 0},
                        "total_frames": 0,
                    }

                # Guardar la información cargada
                self.frame_data["qp_values"] = qp_values
                self.frame_data["size_values"] = size_values
                self.frame_data["motion_vectors"] = [[] for _ in qp_values]
                self.frame_stats["frame_types"] = frame_types

                # También llenar las variables que usa la interfaz para mostrar la información
                self.frame_sizes = size_values.copy()  # Lista de tamaños para la UI
                self.frame_types = frame_types.copy()  # Lista de tipos para la UI

                print(
                    f"✓ Cargados {len(qp_values)} valores QP y {len(size_values)} valores SIZE desde {info_file}"
                )
                self._show_status_message(
                    f"Información de frames cargada desde .info ({len(qp_values)} frames)"
                )
            else:
                print(f"No se pudieron cargar datos válidos desde {info_file}")

        except Exception as e:
            print(f"Error cargando archivo .info: {e}")

    def _parse_mb_debug_output(self, log_path):
        """Parsea la salida de debug de ffmpeg para extraer información de macrobloques."""
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                debug_output = f.read()

            print(f"Procesando {len(debug_output)} caracteres de debug output")

            # Parsear frames y sus macrobloques
            self.frame_mb_data = []
            current_frame_data = None
            frame_counter = 0

            lines = debug_output.split("\n")
            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Detectar nuevo frame
                if "New frame" in line and "type:" in line:
                    # Guardar frame anterior si existe
                    if current_frame_data:
                        self.frame_mb_data.append(current_frame_data)

                    # Extraer tipo de frame
                    frame_type = "U"  # Unknown por defecto
                    if "I" in line:
                        frame_type = "I"
                    elif "P" in line:
                        frame_type = "P"
                    elif "B" in line:
                        frame_type = "B"

                    current_frame_data = {
                        "type": frame_type,
                        "tokens": [],
                        "motion_vectors": [],
                        "frame_idx": frame_counter,
                    }
                    frame_counter += 1

                # Extraer tokens de macrobloques desde debug output
                elif current_frame_data is not None:
                    # Extraer la parte de símbolos de macrobloques (después del prefijo [h264 @ ...])
                    if "] " in line:
                        mb_part = line.split("] ", 1)[1]  # Tomar todo después de '] '
                        tokens = self._extract_mb_tokens_from_debug_line(mb_part)
                        if tokens:
                            current_frame_data["tokens"].extend(tokens)

            # Guardar el último frame
            if current_frame_data and current_frame_data["tokens"]:
                self.frame_mb_data.append(current_frame_data)

            # Calcular estadísticas para cada frame
            for frame_data in self.frame_mb_data:
                tokens = frame_data["tokens"]
                if tokens:
                    total_mb = len(tokens)
                    intra_count = sum(
                        1
                        for token in tokens
                        if self._classify_mb_token(token) == "intra"
                    )
                    skip_count = sum(
                        1
                        for token in tokens
                        if self._classify_mb_token(token) == "skip"
                    )
                    inter_count = sum(
                        1
                        for token in tokens
                        if self._classify_mb_token(token) == "inter"
                    )

                    frame_data["mb_stats"] = {
                        "total_mb": total_mb,
                        "intra_count": intra_count,
                        "skip_count": skip_count,
                        "inter_count": inter_count,
                        "intra_pct": (intra_count / total_mb * 100)
                        if total_mb > 0
                        else 0,
                        "skip_pct": (skip_count / total_mb * 100)
                        if total_mb > 0
                        else 0,
                        "inter_pct": (inter_count / total_mb * 100)
                        if total_mb > 0
                        else 0,
                    }
                else:
                    frame_data["mb_stats"] = {
                        "total_mb": 0,
                        "intra_count": 0,
                        "skip_count": 0,
                        "inter_count": 0,
                        "intra_pct": 0,
                        "skip_pct": 0,
                        "inter_pct": 0,
                    }

            # Inicializar arrays de tamaño y tipos para compatibilidad
            self.frame_sizes = [None] * len(self.frame_mb_data)
            self.frame_types = [frame_data["type"] for frame_data in self.frame_mb_data]
            self.frame_data["motion_vectors"] = [[] for _ in self.frame_mb_data]

            self.mb_data = True
            print(
                f"Datos de macrobloques extraídos: {len(self.frame_mb_data)} frames procesados"
            )

            # Actualizar la información del frame actual si es necesario
            if not self.is_playing and self.cap:
                self._update_frame_info()
                self._refresh_display()

        except Exception as e:
            print(f"Error parseando debug output: {e}")
            self.mb_data = False
            self.frame_mb_data = []

    def _extract_mb_tokens_from_debug_line(self, line):
        """Extrae tokens de macrobloques de una línea de debug output de ffmpeg."""
        # Solo procesar líneas que parezcan contener símbolos de macrobloques
        # Las líneas válidas contienen principalmente símbolos individuales separados por espacios
        # y no contienen caracteres como ':', '=', '[', ']', etc. que aparecen en otras líneas de debug

        # Filtrar líneas que contienen caracteres que no deberían estar en líneas de MB
        if any(
            char in line for char in [":", "=", "[", "]", "(", ")", "{", "}", "@", "#"]
        ):
            return None

        # Filtrar líneas que contienen palabras comunes de debug que no son símbolos MB
        skip_words = [
            "New",
            "frame",
            "type",
            "w:",
            "h:",
            "pixfmt",
            "query_formats",
            "Decoder",
            "EOF",
            "Terminating",
            "Output",
            "stream",
            "Total",
            "Input",
            "Statistics",
            "bytes",
            "packets",
            "frames",
            "encoded",
            "muxed",
            "read",
            "decoded",
            "seeks",
        ]

        if any(word in line for word in skip_words):
            return None

        # Dividir la línea por espacios para obtener símbolos individuales
        symbols = line.split()

        # Verificar que la línea contenga principalmente símbolos de un solo caracter
        # Las líneas válidas de MB tienen muchos símbolos de 1-2 caracteres
        if len(symbols) < 5:  # Líneas muy cortas probablemente no son MB
            return None

        # Contar cuántos símbolos tienen longitud razonable para MB (1-3 caracteres)
        valid_length_symbols = sum(1 for s in symbols if 1 <= len(s.strip()) <= 3)
        if (
            valid_length_symbols / len(symbols) < 0.8
        ):  # Menos del 80% son símbolos válidos
            return None

        # Caracteres válidos de FFmpeg para MB types
        valid_mb_chars = ["P", "A", "i", "I", "S", "d", "g", "D", "G", ">", "<", "X"]
        seg_chars = ["+", "-", "|", " "]

        tokens = []
        for symbol in symbols:
            # Limpiar el símbolo
            symbol = symbol.strip()

            if not symbol:
                continue

            # Clasificar según el símbolo (solo símbolos de 1-2 caracteres)
            if len(symbol) <= 2:
                # Verificar si es un carácter FFmpeg válido
                if len(symbol) > 0 and symbol[0] in valid_mb_chars:
                    # Mantener el token original (puede incluir segmentación)
                    tokens.append(symbol)
                # Legacy: convertir números antiguos
                elif symbol in ["0", "1", "2"]:
                    tokens.append(int(symbol))

        return tokens if tokens else None

    def toggle_play_pause(self):
        """Alterna entre play y pause."""
        if not self.cap:
            return

        if self.is_playing:
            self.pause()
        else:
            self.play()

    def play(self):
        """Inicia la reproducción."""
        if not self.cap:
            return

        self.is_playing = True
        self.play_btn.setText("⏸")
        interval = int(1000 / self.fps)  # milisegundos por frame
        self.timer.start(interval)
        # Actualizar información del frame (mostrará "-" durante reproducción)
        self._update_frame_info()

    def pause(self):
        """Pausa la reproducción."""
        self.is_playing = False
        self.play_btn.setText("▶")
        self.timer.stop()
        # Actualizar información del frame (mostrará datos reales cuando esté pausado)
        self._update_frame_info()

    def prev_frame(self):
        """Va al frame anterior."""
        if self.current_frame > 0:
            self.current_frame -= 1
            self._seek_to_frame(self.current_frame)
            # Mostrar información del frame al navegar frame a frame
            if not self.is_playing:
                self._show_frame_info(True)
            # Actualizar estado de los controles
            self._update_controls_state()

    def next_frame(self):
        """Va al frame siguiente."""
        if self.current_frame < self.total_frames - 1:
            self.current_frame += 1
            self._seek_to_frame(self.current_frame)
            # Mostrar información del frame al navegar frame a frame
            if not self.is_playing:
                self._show_frame_info(True)
            # Actualizar estado de los controles
            self._update_controls_state()

    def seek_to_position(self, frame_num):
        """Busca a una posición específica."""
        self._seek_to_frame(frame_num)
        # Mostrar información del frame al buscar manualmente
        if not self.is_playing:
            self._show_frame_info(True)
        # Actualizar estado de los controles
        self._update_controls_state()

    def pause_on_slider_press(self):
        """Pausa cuando se presiona el slider."""
        self.was_playing = self.is_playing
        if self.is_playing:
            self.pause()

    def resume_on_slider_release(self):
        """Reanuda cuando se suelta el slider."""
        if self.was_playing:
            self.play()

    def toggle_overlay(self, state):
        """Activa/desactiva el overlay de macrobloques."""
        self.overlay_enabled = state == Qt.Checked
        self._refresh_display()

    def toggle_motion_vectors(self, state):
        """Activa/desactiva la visualización de motion vectors."""
        self.motion_vectors_enabled = state == Qt.Checked
        self._refresh_display()

    def update_overlay_opacity(self, value):
        """Actualiza la opacidad del overlay."""
        self.overlay_opacity = value / 100.0  # Convertir de 0-100 a 0.0-1.0
        self.opacity_label.setText(f"{value}%")
        # Refrescar display si el overlay está activo
        if self.overlay_enabled:
            self._refresh_display()

    def _next_frame(self):
        """Avanza al siguiente frame durante reproducción."""
        if self.current_frame < self.total_frames - 1:
            self._seek_to_frame(self.current_frame + 1)
        else:
            # Fin del video
            self.pause()

    def _seek_to_frame(self, frame_num):
        """Busca a un frame específico."""
        if not self.cap:
            return

        self.current_frame = max(0, min(frame_num, self.total_frames - 1))
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, self.current_frame)
        self._display_current_frame()

    def _display_current_frame(self):
        """Lee y muestra el frame actual."""
        if not self.cap:
            return

        ret, frame = self.cap.read()
        if not ret:
            return

        # Almacenar el frame actual para refrescos
        self.current_frame_data = frame.copy()

        # Mostrar el frame
        self._show_frame(frame)

    def _refresh_video_display(self):
        """Refresca la visualización del video, leyendo el frame actual si es necesario."""
        if not self.cap:
            return

        if self.current_frame_data is not None:
            # Usar el frame almacenado si está disponible
            self._show_frame(self.current_frame_data)
        else:
            # Leer y mostrar el frame actual si no hay datos almacenados
            self._display_current_frame()

        # Actualizar información del frame si está visible
        if self.info_panel.isVisible():
            self._update_frame_info()

    def _refresh_display(self):
        """Refresca la visualización del frame actual sin cambiar de frame."""
        if not self.cap or self.current_frame_data is None:
            return

        # Usar el frame almacenado
        self._show_frame(self.current_frame_data)

        # Actualizar información del frame si está visible
        if self.info_panel.isVisible():
            self._update_frame_info()

    def _show_frame(self, frame):
        """Muestra un frame dado aplicando overlay si es necesario."""
        # IMPORTANTE: Aplicar overlay ANTES de convertir a RGB
        # El overlay usa colores BGR (formato OpenCV)

        # Aplicar overlay si está habilitado (en BGR)
        if (
            (self.overlay_enabled or self.motion_vectors_enabled)
            and self.mb_data
            and self.current_frame < len(self.frame_mb_data)
        ):
            frame = self._apply_mb_overlay(
                frame, self.frame_mb_data[self.current_frame]
            )

        # Ahora convertir BGR a RGB para Qt
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Convertir a QPixmap
        height, width, channel = frame_rgb.shape
        bytes_per_line = 3 * width
        q_img = QPixmap.fromImage(
            QImage(frame_rgb.data, width, height, bytes_per_line, QImage.Format_RGB888)
        )

        # Escalar manteniendo aspect ratio
        label_size = self.video_label.size()
        scaled_pixmap = q_img.scaled(
            label_size, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.video_label.setPixmap(scaled_pixmap)

        # Actualizar slider y tiempo
        self.position_slider.blockSignals(True)
        self.position_slider.setValue(self.current_frame)
        self.position_slider.blockSignals(False)

        self._update_time_display()
        self._update_frame_badge()
        self.frameChanged.emit(self.current_frame)

    def _apply_mb_overlay(self, frame, mb_frame_data):
        """Aplica overlay de macrobloques al frame con el nuevo sistema de tipos."""
        if not mb_frame_data or "tokens" not in mb_frame_data:
            return frame

        # Dimensiones del frame
        height, width = frame.shape[:2]

        # Dimensiones de macrobloque
        mb_width, mb_height = 16, 16

        # Calcular número de macrobloques por fila y columna
        cols = (width + mb_width - 1) // mb_width
        rows = (height + mb_height - 1) // mb_height

        padded_width = cols * mb_width
        padded_height = rows * mb_height
        pad_right = padded_width - width
        pad_bottom = padded_height - height

        canvas = cv2.copyMakeBorder(
            frame,
            0,
            pad_bottom,
            0,
            pad_right,
            cv2.BORDER_REPLICATE,
        )

        # Copia del frame para overlay
        overlay_frame = canvas.copy()

        # Colores para tipos de MB (BGR format para OpenCV)
        # Sincronizado con MBVisualizer
        colors = {
            # INTRA Types (Red tones)
            "P": (0, 0, 139),  # PCM - Dark Red
            "A": (0, 69, 255),  # ACPRED - Orange Red
            "i": (0, 100, 255),  # INTRA4x4 - Light Red
            "I": (0, 0, 200),  # INTRA16x16 - Red
            # SKIP Types (Green tones)
            "S": (0, 180, 0),  # SKIP - Green
            "d": (0, 220, 100),  # DIRECT+SKIP - Light Green
            "g": (0, 100, 0),  # GMC+SKIP - Dark Green
            # INTER Types (Blue tones)
            "D": (200, 0, 0),  # DIRECT - Dark Blue
            "G": (200, 0, 100),  # GMC - Purple
            ">": (200, 100, 0),  # FORWARD (L0) - Blue
            "<": (200, 200, 0),  # BACKWARD (L1) - Cyan
            "X": (200, 0, 200),  # BI-PRED - Magenta
            # Legacy support
            0: (0, 180, 0),  # SKIP - Verde
            1: (200, 100, 0),  # INTER - Azul
            2: (0, 0, 200),  # INTRA - Rojo
        }

        # Símbolos para cada tipo
        symbols = {
            "P": "P",
            "A": "A",
            "i": "i",
            "I": "I",
            "S": "S",
            "d": "d",
            "g": "g",
            "D": "D",
            "G": "G",
            ">": ">",
            "<": "<",
            "X": "X",
        }

        tokens = mb_frame_data["tokens"]
        motion_vectors = mb_frame_data.get("motion_vectors", [])
        expected_tokens = rows * cols

        # Si tenemos suficientes tokens, dibujar overlay
        if self.overlay_enabled and len(tokens) >= expected_tokens:
            for mb_idx, mb_token in enumerate(tokens[:expected_tokens]):
                # Calcular posición del macrobloque
                mb_row = mb_idx // cols
                mb_col = mb_idx % cols

                # Coordenadas en píxeles
                x1 = mb_col * mb_width
                y1 = mb_row * mb_height
                x2 = x1 + mb_width - 1
                y2 = y1 + mb_height - 1

                # Obtener tipo y color
                mb_type = self._get_mb_type(mb_token)
                color = colors.get(mb_type, colors.get(mb_token, (128, 128, 128)))

                # Dibujar rectángulo coloreado
                cv2.rectangle(overlay_frame, (x1, y1), (x2, y2), color, -1)

        # Aplicar transparencia
        alpha = self.overlay_opacity  # Usar opacidad configurable
        result = (
            cv2.addWeighted(overlay_frame, alpha, canvas, 1 - alpha, 0)
            if self.overlay_enabled
            else canvas.copy()
        )

        # Dibujar bordes de segmentación y símbolos
        text_overlay = result.copy()
        if self.overlay_enabled and len(tokens) >= expected_tokens:
            for mb_idx, mb_token in enumerate(tokens[:expected_tokens]):
                # Calcular posición del macrobloque
                mb_row = mb_idx // cols
                mb_col = mb_idx % cols

                # Coordenadas en píxeles
                x1 = mb_col * mb_width
                y1 = mb_row * mb_height

                # Obtener tipo y segmentación
                mb_type = self._get_mb_type(mb_token)
                seg_type = self._get_mb_segmentation(mb_token)

                # Dibujar bordes de segmentación
                self._draw_segmentation_borders_player(
                    result, x1, y1, mb_width, mb_height, seg_type
                )

                # Dibujar símbolo
                symbol = symbols.get(mb_type, str(mb_type))
                font_scale = 0.5
                thickness = 1
                (text_w, text_h), _ = cv2.getTextSize(
                    symbol, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness
                )

                text_x = x1 + (mb_width - text_w) // 2
                text_y = y1 + (mb_height + text_h) // 2

                # Sombra negra
                cv2.putText(
                    text_overlay,
                    symbol,
                    (text_x + 1, text_y + 1),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    font_scale,
                    (0, 0, 0),
                    thickness,
                )
                # Texto blanco
                cv2.putText(
                    text_overlay,
                    symbol,
                    (text_x, text_y),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    font_scale,
                    (255, 255, 255),
                    thickness,
                )

        if self.overlay_enabled:
            result = cv2.addWeighted(
                text_overlay,
                alpha,
                result,
                1 - alpha,
                0,
            )

        if self.motion_vectors_enabled and motion_vectors:
            self._draw_motion_vectors_overlay(
                result,
                motion_vectors,
                frame.shape[1],
                frame.shape[0],
            )

        return result

    def _draw_motion_vectors_overlay(
        self, frame, motion_vectors, frame_width, frame_height
    ):
        """Dibuja vectores de movimiento sobre el frame actual."""
        block_sources = {}
        for mv in motion_vectors:
            block_key = (
                mv.get("dst_x"),
                mv.get("dst_y"),
                mv.get("w"),
                mv.get("h"),
            )
            source = mv.get("source", 0)
            if source < 0:
                block_sources.setdefault(block_key, set()).add("past")
            elif source > 0:
                block_sources.setdefault(block_key, set()).add("future")

        for mv in motion_vectors:
            w = mv.get("w", 0)
            h = mv.get("h", 0)
            source = mv.get("source", 0)
            src_x = mv.get("src_x")
            src_y = mv.get("src_y")
            dst_x = mv.get("dst_x")
            dst_y = mv.get("dst_y")

            if None in (src_x, src_y, dst_x, dst_y) or w <= 0 or h <= 0:
                continue

            start_point = (
                int(max(0, min(frame_width - 1, dst_x + w / 2))),
                int(max(0, min(frame_height - 1, dst_y + h / 2))),
            )
            end_point = (
                int(max(0, min(frame_width - 1, src_x + w / 2))),
                int(max(0, min(frame_height - 1, src_y + h / 2))),
            )

            if start_point == end_point:
                continue

            block_key = (dst_x, dst_y, w, h)
            directions = block_sources.get(block_key, set())
            if "past" in directions and "future" in directions:
                color = (200, 0, 200)  # Bi-pred: magenta/violeta
            elif source < 0:
                color = (200, 100, 0)  # Forward / referencia pasada
            elif source > 0:
                color = (200, 200, 0)  # Backward / referencia futura
            else:
                color = (0, 255, 255)

            cv2.arrowedLine(
                frame,
                start_point,
                end_point,
                color,
                1,
                line_type=cv2.LINE_AA,
                tipLength=0.25,
            )

    def _get_mb_type(self, token):
        """Obtiene el tipo de MB del token."""
        if isinstance(token, int):
            if token == 0:
                return "S"
            elif token == 1:
                return ">"
            elif token == 2:
                return "I"

        token_str = str(token)
        if len(token_str) > 0:
            return token_str[0]

        return ">"

    def _classify_mb_token(self, token):
        """Clasifica un token en intra/skip/inter para estadísticas."""
        mb_type = self._get_mb_type(token)

        if mb_type in ("P", "A", "i", "I"):
            return "intra"
        if mb_type in ("S", "d", "g"):
            return "skip"
        return "inter"

    def _get_mb_segmentation(self, token):
        """Obtiene el patrón de segmentación del token."""
        token_str = str(token)

        if len(token_str) > 1:
            seg_char = token_str[1]
            if seg_char in ["+", "-", "|", " "]:
                return seg_char

        # Para INTRA, asumir sin segmentación
        if len(token_str) > 0:
            mb_char = token_str[0]
            if mb_char in ["P", "A", "i", "I"]:
                return " "

        return " "

    def _draw_segmentation_borders_player(self, frame, x, y, mb_w, mb_h, seg_type):
        """Dibuja bordes de segmentación en el reproductor."""
        border_color = (0, 0, 0)  # Negro
        border_thickness = 2
        x2 = x + mb_w - 1
        y2 = y + mb_h - 1

        # Borde exterior
        cv2.rectangle(frame, (x, y), (x2, y2), border_color, border_thickness)

        if seg_type == "+":
            # 8x8: dividir en 4 cuadrantes
            mid_x = x + mb_w // 2
            mid_y = y + mb_h // 2
            cv2.line(frame, (x, mid_y), (x2, mid_y), border_color, border_thickness)
            cv2.line(frame, (mid_x, y), (mid_x, y2), border_color, border_thickness)

        elif seg_type == "-":
            # 16x8: dividir horizontalmente
            mid_y = y + mb_h // 2
            cv2.line(frame, (x, mid_y), (x2, mid_y), border_color, border_thickness)

        elif seg_type == "|":
            # 8x16: dividir verticalmente
            mid_x = x + mb_w // 2
            cv2.line(frame, (mid_x, y), (mid_x, y2), border_color, border_thickness)

    def _update_controls_state(self):
        """Actualiza el estado de los controles."""
        has_video = self.cap is not None

        self.play_btn.setEnabled(has_video)
        self.prev_frame_btn.setEnabled(has_video and self.current_frame > 0)
        self.next_frame_btn.setEnabled(
            has_video and self.current_frame < self.total_frames - 1
        )
        self.position_slider.setEnabled(has_video)
        self.overlay_check.setEnabled(has_video)
        has_motion_vectors = has_video and self._has_motion_vector_data()
        self.motion_vectors_check.setEnabled(has_motion_vectors)
        if not has_motion_vectors:
            self.motion_vectors_check.setChecked(False)

    def resizeEvent(self, event):
        """Maneja el evento de redimensionamiento para reescalar el video."""
        super().resizeEvent(event)
        # Refrescar la visualización del video cuando cambia el tamaño
        if self.cap and self.current_frame_data is not None:
            self._refresh_display()

    def _update_time_display(self):
        """Actualiza la etiqueta de tiempo."""
        if not self.cap:
            self.time_label.setText("0:00 / 0:00")
            return

        current_time = self.current_frame / self.fps
        total_time = self.total_frames / self.fps

        current_str = f"{int(current_time // 60)}:{int(current_time % 60):02d}"
        total_str = f"{int(total_time // 60)}:{int(total_time % 60):02d}"

        self.time_label.setText(f"{current_str} / {total_str}")

    def load_mb_data(self, log_data_path):
        """Carga datos de macrobloques y toda la información desde sidecar o log."""
        import json

        try:
            if not os.path.exists(log_data_path):
                print(f"Archivo de log no encontrado: {log_data_path}")
                return

            print(f"Cargando datos desde archivo log: {log_data_path}")

            with open(log_data_path, "r", encoding="utf-8") as f:
                raw_data = f.read()

            stripped_data = raw_data.lstrip()
            if not stripped_data:
                print(f"Archivo de datos vacío: {log_data_path}")
                return

            if stripped_data[0] != "{":
                self._parse_mb_debug_output(log_data_path)
                return

            log_data = json.loads(raw_data)

            # Extraer datos de frames
            frames_data = log_data.get("frames", [])
            self.frame_mb_data = []

            # Convertir formato del log al formato esperado por el visualizador
            for frame_info in frames_data:
                # Usar los datos exactos de macrobloques guardados en el log
                mb_data = frame_info.get("blocks", frame_info.get("tokens", []))

                # Calcular estadísticas desde los datos de bloques
                total_mb = len(mb_data)
                intra_count = (
                    sum(
                        1
                        for token in mb_data
                        if self._classify_mb_token(token) == "intra"
                    )
                    if mb_data
                    else 0
                )
                skip_count = (
                    sum(
                        1
                        for token in mb_data
                        if self._classify_mb_token(token) == "skip"
                    )
                    if mb_data
                    else 0
                )
                inter_count = (
                    sum(
                        1
                        for token in mb_data
                        if self._classify_mb_token(token) == "inter"
                    )
                    if mb_data
                    else 0
                )

                mb_stats = {
                    "total_mb": total_mb,
                    "intra_count": intra_count,
                    "skip_count": skip_count,
                    "inter_count": inter_count,
                    "intra_pct": (intra_count / total_mb * 100) if total_mb > 0 else 0,
                    "skip_pct": (skip_count / total_mb * 100) if total_mb > 0 else 0,
                    "inter_pct": (inter_count / total_mb * 100) if total_mb > 0 else 0,
                }

                frame_data = {
                    "type": frame_info.get("type", "U"),
                    "tokens": mb_data,
                    "motion_vectors": frame_info.get("motion_vectors", []),
                    "mb_stats": mb_stats,
                }
                self.frame_mb_data.append(frame_data)

            # Cargar datos de tamaño y tipo de frame
            self.frame_sizes = []
            self.frame_types = []
            qp_values = []
            bitrate_values = []

            for frame_info in frames_data:
                self.frame_sizes.append(frame_info.get("size"))
                self.frame_types.append(frame_info.get("type", "U"))
                qp_values.append(frame_info.get("qp", frame_info.get("qp_value")))
                bitrate_values.append(frame_info.get("bitrate"))

            self.frame_data = {
                "qp_values": qp_values,
                "size_values": self.frame_sizes.copy(),
                "frame_numbers": list(range(len(frames_data))),
                "bitrate_values": bitrate_values,
                "motion_vectors": [
                    frame_info.get("motion_vectors", []) for frame_info in frames_data
                ],
            }

            # Información adicional para debugging
            metadata = log_data.get("metadata", {})
            video_info = log_data.get("video_info", {})

            print(f"Datos cargados exitosamente:")
            print(f"  - Frames: {len(self.frame_mb_data)}")
            print(
                f"  - Video: {video_info.get('width', 0)}x{video_info.get('height', 0)} @ {video_info.get('fps', 25)}fps"
            )
            print(f"  - Input: {metadata.get('input_file', 'unknown')}")

            self.mb_data = True

            # Actualizar la información del frame actual si el panel debería estar visible
            if not self.is_playing and self.cap:
                self._show_frame_info(True)
                self._refresh_display()  # Para aplicar overlay si está habilitado

        except Exception as e:
            print(f"Error cargando datos del log: {e}")
            self.mb_data = False
            self.frame_mb_data = []
            self.frame_sizes = []
            self.frame_types = []
            self.mb_data = False
            self.frame_mb_data = []
            self.frame_sizes = []
            self.frame_types = []

    def _extract_mb_tokens_from_line(self, clean_line):
        """Extrae tokens de macrobloques de una línea."""
        tokens = clean_line.split()
        skip_words = [
            "No",
            "automatically",
            "explicit",
            "mapping",
            "maps",
            "streams",
            "New",
            "frame",
            "type",
            "Decoding",
            "Format",
            "Reinit",
        ]

        if (
            tokens
            and len(tokens[0]) <= 2
            and not any(word in clean_line for word in skip_words)
            and not clean_line.startswith("[")
            and not any(char.isdigit() and len(tokens[0]) > 1 for char in tokens[0])
        ):
            # Filtrar tokens válidos (solo números 0, 1, 2 que representan tipos de MB)
            valid_tokens = []
            for token in tokens:
                if token in ["0", "1", "2"]:  # 0=SKIP, 1=INTER, 2=INTRA
                    valid_tokens.append(int(token))
            return valid_tokens
        return None


def test_log_loading():
    """Función de prueba para verificar la carga de datos desde .log"""
    import json
    import os

    print("=== TEST: Simulando carga de datos de log ===")

    # Crear datos de ejemplo como los que se guardarían en un .log real
    sample_log_data = {
        "metadata": {
            "input_file": "test_input.mp4",
            "output_video": "test_encoded.mp4",
            "ffmpeg_params": ["-c:v", "libx264"],
            "timestamp": "2024-12-12T10:30:00",
            "version": "2.0",
        },
        "video_info": {
            "width": 1920,
            "height": 1080,
            "fps": 25.0,
            "total_frames": 3,
            "processed_frames": 3,
        },
        "compression_stats": {
            "qp_values": [23, 24, 22],
            "size_values": [45632, 42189, 48921],
            "bitrate_values": [1456000, 1345000, 1562000],
            "frame_types": ["I", "P", "B"],
        },
        "frames": [
            {
                "type": "I",
                "size": 45632,
                "qp": 23,
                "blocks": [
                    2,
                    2,
                    0,
                    1,
                    2,
                ],  # Datos exactos de macrobloques (0=Skip, 1=Inter, 2=Intra)
            },
            {
                "type": "P",
                "size": 42189,
                "qp": 24,
                "blocks": [0, 1, 0, 0, 1],  # Datos exactos
            },
            {
                "type": "B",
                "size": 48921,
                "qp": 22,
                "blocks": [1, 0, 1, 1, 0],  # Datos exactos
            },
        ],
    }

    # Guardar el archivo de prueba
    test_log_file = "test_sample.log"
    with open(test_log_file, "w", encoding="utf-8") as f:
        json.dump(sample_log_data, f, indent=2, ensure_ascii=False)
    print(f"Archivo de prueba creado: {test_log_file}")

    # Ahora probar la carga como lo haría VideoPlayerWidget
    try:
        with open(test_log_file, "r", encoding="utf-8") as f:
            log_data = json.load(f)

        frames_data = log_data.get("frames", [])
        print(f"Frames en log: {len(frames_data)}")

        if frames_data:
            # Simular la carga como en VideoPlayerWidget.load_mb_data()
            frame_mb_data = []
            frame_sizes = []
            frame_types = []

            for frame_info in frames_data:
                # Usar los datos exactos de macrobloques del log
                mb_data = frame_info.get("blocks", [])

                # Calcular estadísticas
                total_mb = len(mb_data)
                intra_count = mb_data.count(2) if mb_data else 0
                skip_count = mb_data.count(0) if mb_data else 0
                inter_count = mb_data.count(1) if mb_data else 0

                mb_stats = {
                    "total_mb": total_mb,
                    "intra_count": intra_count,
                    "skip_count": skip_count,
                    "inter_count": inter_count,
                    "intra_pct": (intra_count / total_mb * 100) if total_mb > 0 else 0,
                    "skip_pct": (skip_count / total_mb * 100) if total_mb > 0 else 0,
                    "inter_pct": (inter_count / total_mb * 100) if total_mb > 0 else 0,
                }

                frame_data = {
                    "type": frame_info.get("type", "U"),
                    "tokens": mb_data,  # Datos exactos
                    "mb_stats": mb_stats,
                }
                frame_mb_data.append(frame_data)
                frame_sizes.append(frame_info.get("size", 0))
                frame_types.append(frame_info.get("type", "U"))

            print("✅ Datos cargados correctamente:")
            print(f"  - Frames: {len(frame_mb_data)}")
            print(
                f"  - Primer frame - Tipo: {frame_mb_data[0]['type']}, Tokens: {len(frame_mb_data[0]['tokens'])}, MB total: {frame_mb_data[0].get('mb_stats', {}).get('total_mb', 0)}"
            )
            print(f"  - Tamaños: {frame_sizes}")
            print(f"  - Tipos: {frame_types}")

            # Verificar que el overlay funcionaría
            print("\n=== TEST: Simulando aplicación de overlay ===")
            mock_frame = [
                [[100, 100, 100] for _ in range(320)] for _ in range(180)
            ]  # Frame mock 320x180
            mock_frame = [
                [[int(x) for x in pixel] for pixel in row] for row in mock_frame
            ]  # Convertir a int

            for i, frame_data in enumerate(
                frame_mb_data[:2]
            ):  # Probar con primeros 2 frames
                print(f"Frame {i}: Aplicando overlay...")
                if frame_data["tokens"]:
                    print(f"  - Tiene {len(frame_data['tokens'])} tokens de MB")
                    print(f"  - Tipo: {frame_data['type']}")

    except Exception as e:
        print(f"❌ Error en prueba: {e}")
        import traceback

        traceback.print_exc()

    # Limpiar archivo de prueba
    if os.path.exists(test_log_file):
        os.remove(test_log_file)
        print(f"Archivo de prueba eliminado: {test_log_file}")


def test_load_mb_data():
    """Test the load_mb_data functionality"""
    import json
    import os

    print("=== TEST: Probando load_mb_data ===")

    # Create a test VideoPlayerWidget
    from PyQt5.QtWidgets import QApplication
    import sys

    if not QApplication.instance():
        app = QApplication(sys.argv)

    # Create widget
    from PyQt5.QtWidgets import QWidget

    player = VideoPlayerWidget()

    # Create test log data
    test_data = {
        "frames_data": [
            {
                "frame_number": 0,
                "frame_type": "I",
                "mb_stats": {
                    "total_mb": 100,
                    "intra_count": 20,
                    "skip_count": 30,
                    "inter_count": 50,
                    "intra_pct": 20.0,
                    "skip_pct": 30.0,
                    "inter_pct": 50.0,
                },
                "qp_value": 23,
                "size_bytes": 45632,
                "bitrate": 1456000,
            }
        ]
    }

    # Save test log
    test_log = "test_load.log"
    with open(test_log, "w") as f:
        json.dump(test_data, f, indent=2)

    print(f"Archivo de test creado: {test_log}")

    # Test load_mb_data
    print("Llamando load_mb_data...")
    # Redirigir stdout para capturar prints
    import io
    import sys

    old_stdout = sys.stdout
    sys.stdout = buffer = io.StringIO()

    player.load_mb_data(test_log)

    # Restaurar stdout
    sys.stdout = old_stdout
    output = buffer.getvalue()
    print("Output de load_mb_data:")
    print(output)

    print(f"Resultado - mb_data: {player.mb_data}")
    print(f"Resultado - frame_mb_data length: {len(player.frame_mb_data)}")
    if player.frame_mb_data:
        print(f"Primer frame - type: {player.frame_mb_data[0]['type']}")
        print(f"Primer frame - tokens: {len(player.frame_mb_data[0]['tokens'])}")
        print(f"Primer frame - mb_stats: {player.frame_mb_data[0]['mb_stats']}")

    # Cleanup
    if os.path.exists(test_log):
        os.remove(test_log)
        print(f"Archivo de test eliminado: {test_log}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--test-log":
        test_log_loading()
    elif len(sys.argv) > 1 and sys.argv[1] == "--test-load":
        test_load_mb_data()
    else:
        main()
