from __future__ import annotations

import base64
import csv
import json
from dataclasses import dataclass
from datetime import datetime
import logging
from pathlib import Path
import shutil
import subprocess
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
from tkinter import ttk
try:
    import winsound
except ImportError:
    winsound = None

import cv2

from backend.camera import list_available_cameras, probe_camera, start_camera
from config import ALLOW_IN_APP_ROOT_BOOTSTRAP, APP_NAME, PATHS, harden_permissions
from frontend.dialogs import (
    DatePickerDialog,
    clamp_window_size_to_screen,
    compute_centered_position,
    prepare_modal_size,
    present_modal_window,
)
from backend.logging_utils import configure_logging
from backend.recognition import (
    CURRENT_FEATURE_VERSION,
    LivenessTracker,
    SimpleFaceRecognizer,
    compute_face_structure_signature,
    detect_facial_geometry,
    preprocess_face,
    slugify_name,
)
from backend.reporting import export_attendance_to_pdf, export_attendance_to_xlsx
from database.models import PasswordResetRequiredError, Student
from database.repository import AttendanceRepository


BASE_DIR = PATHS.base_dir
RUNTIME_DIR = PATHS.runtime_dir
DATA_DIR = PATHS.data_dir
DATASET_DIR = PATHS.dataset_dir
MODEL_DIR = PATHS.model_dir
DB_PATH = PATHS.db_path
EXPORTS_DIR = PATHS.exports_dir
REPORTS_DIR = EXPORTS_DIR
PHOTOS_DIR = PATHS.photos_dir
STUDENTS_DIR = PATHS.students_dir
BACKUPS_DIR = PATHS.backups_dir
LOGS_DIR = PATHS.logs_dir
PROFILE_PHOTO_NAME = "profile_front.jpg"
CASCADE_PATH = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
CAPTURE_BUCKET_TARGETS = {
    "front": 9,
    "left": 4,
    "right": 4,
    "up": 4,
    "down": 4,
}
READ_ONLY_ROLES = ("root", "operator", "teacher", "viewer")
EDIT_ROLES = ("root", "operator", "teacher")
ROOT_ONLY_LABELS = {
    "Admin",
}
VIEWER_HIDDEN_LABELS = {
    "Students",
    "Attendance",
    "Exports",
}
STUDENT_VIEWER_HIDDEN_LABELS = {
    "Students",
    "Attendance",
    "Reports",
    "Admin",
    "Exports",
}

LOGGER = logging.getLogger(__name__)


@dataclass
class PersonRecord:
    """Small helper object used while capturing/training face samples."""
    label_id: int
    student_id: str
    name: str
class FaceRecognitionAttendanceApp:
    """Main desktop application: dashboard, face capture, recognition, and management tools."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title(APP_NAME.upper())
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        window_width = min(1220, max(980, screen_width - 120))
        window_height = min(760, max(640, screen_height - 140))
        self.root.geometry(f"{window_width}x{window_height}")
        self.root.minsize(760, 520)

        PATHS.ensure_runtime_dirs()
        LOGGER.info("Initializing application with database at %s", DB_PATH)

        self.repository = AttendanceRepository(DB_PATH)
        self.repository.refresh_database()
        self.institute_settings = self.repository.get_institute_settings()

        self.face_detector = cv2.CascadeClassifier(str(CASCADE_PATH))
        if self.face_detector.empty():
            LOGGER.error("Failed to load face cascade from %s", CASCADE_PATH)
            raise RuntimeError("Face detection model could not be loaded.")

        self.status_var = tk.StringVar(
            value="Ready. Register a student, train the recognizer, then start attendance."
        )
        self.student_panel_subtitle_var = tk.StringVar(value="Browse registered students and open profile actions.")
        self.attendance_panel_subtitle_var = tk.StringVar(value="Track records, filter by date, and review attendance updates.")
        self.student_count_var = tk.StringVar(value="0")
        self.today_attendance_var = tk.StringVar(value="0")
        self.model_status_var = tk.StringVar(value="Not Trained")
        self.last_marked_var = tk.StringVar(value="None")
        self.card_label_vars = [
            tk.StringVar(value="Students"),
            tk.StringVar(value="Today Present"),
            tk.StringVar(value="Last Marked"),
            tk.StringVar(value="Model"),
        ]
        initial_class = self.repository.list_classes()[0]
        initial_subject = self.repository.list_subjects(initial_class)[0]
        initial_year = self.institute_settings["academic_year"]
        initial_semester = "Year" if self.institute_settings["attendance_mode"] == "yearly" else ""
        self.current_class_var = tk.StringVar(value=initial_class)
        self.current_subject_var = tk.StringVar(value=initial_subject)
        self.current_section_var = tk.StringVar(value="")
        self.current_academic_year_var = tk.StringVar(value=initial_year)
        self.current_semester_var = tk.StringVar(value=initial_semester)
        self.context_var = tk.StringVar(value="")
        self.date_filter_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        self.date_from_var = tk.StringVar(value="")
        self.date_to_var = tk.StringVar(value="")
        self.attendance_search_var = tk.StringVar()
        self.attendance_status_filter_var = tk.StringVar(value="All")
        self.attendance_source_filter_var = tk.StringVar(value="All")
        self.student_search_var = tk.StringVar()
        self.attendance_summary_var = tk.StringVar(value="Showing all attendance records.")
        self.attendance_session_var = tk.StringVar(value="Session status: Open")
        self.activity_var = tk.StringVar(value="System started")
        self.selected_student_var = tk.StringVar(value="No student selected")
        self.selected_attendance_var = tk.StringVar(value="No attendance record selected")
        self.current_user_var = tk.StringVar(value="Not signed in")
        self.current_role_var = tk.StringVar(value="-")
        self.current_username = ""
        self.current_student_id = ""
        self.current_user_scope = {"class_name": "", "subject_name": ""}
        self.selected_camera_index = 0
        self.duplicate_alert_times: dict[str, float] = {}
        self.sidebar_button_order: list[str] = []
        self.sidebar_buttons: dict[str, tk.Widget] = {}
        self.sidebar_menus: dict[str, tk.Menu] = {}
        self.style = ttk.Style()
        self.style.theme_use("clam")
        self._configure_theme()
        self._build_ui()
        self.refresh_dashboard()
        self.root.withdraw()

    def _configure_theme(self) -> None:
        """Define a cohesive visual language shared across the desktop UI."""
        self.colors = {
            "app_bg": "#eef3f1",
            "surface": "#fbfcfb",
            "surface_alt": "#f4f8f6",
            "surface_emphasis": "#e8f4f5",
            "border": "#d5e2dd",
            "text": "#173038",
            "muted": "#5d7175",
            "sidebar": "#18363d",
            "sidebar_alt": "#244c56",
            "sidebar_text": "#f4fbfa",
            "sidebar_muted": "#c9d9d6",
            "primary": "#1f6f78",
            "primary_hover": "#185b62",
            "accent": "#d0ad5a",
            "accent_text": "#58410d",
            "success": "#2e8b57",
            "success_bg": "#e7f6ec",
            "danger": "#b24b39",
            "danger_bg": "#fcebe7",
            "warning": "#a86b19",
            "warning_bg": "#fff2dc",
            "info_bg": "#e6f3f6",
        }
        self.fonts = {
            "title": ("Segoe UI Semibold", 24),
            "heading": ("Segoe UI Semibold", 14),
            "subheading": ("Segoe UI Semibold", 11),
            "body": ("Segoe UI", 10),
            "small": ("Segoe UI", 9),
            "button": ("Segoe UI Semibold", 9),
            "caption": ("Segoe UI", 8),
        }

        self.root.configure(bg=self.colors["app_bg"])
        self.root.option_add("*Font", self.fonts["body"])
        self.root.option_add("*Label.Font", self.fonts["body"])
        self.root.option_add("*Button.Font", self.fonts["button"])
        self.root.option_add("*Menubutton.Font", self.fonts["button"])
        self.root.option_add("*Listbox.Font", self.fonts["body"])
        self.root.option_add("*Menu.Background", self.colors["surface"])
        self.root.option_add("*Menu.Foreground", self.colors["text"])
        self.root.option_add("*Menu.ActiveBackground", self.colors["info_bg"])
        self.root.option_add("*Menu.ActiveForeground", self.colors["text"])

        self.style.configure(".", background=self.colors["surface"], foreground=self.colors["text"])
        self.style.configure(
            "App.TFrame",
            background=self.colors["surface"],
            relief="flat",
        )
        self.style.configure(
            "App.TLabel",
            background=self.colors["surface"],
            foreground=self.colors["text"],
            font=self.fonts["body"],
        )
        self.style.configure(
            "Muted.TLabel",
            background=self.colors["surface"],
            foreground=self.colors["muted"],
            font=self.fonts["small"],
        )
        for treeview_style in ("Treeview", "Card.Treeview"):
            self.style.configure(
                treeview_style,
                background=self.colors["surface"],
                fieldbackground=self.colors["surface"],
                foreground=self.colors["text"],
                rowheight=30,
                borderwidth=0,
                relief="flat",
                font=self.fonts["body"],
            )
            self.style.map(
                treeview_style,
                background=[("selected", self.colors["primary"])],
                foreground=[("selected", self.colors["sidebar_text"])],
            )
        for heading_style in ("Treeview.Heading", "Card.Treeview.Heading"):
            self.style.configure(
                heading_style,
                background=self.colors["surface_alt"],
                foreground=self.colors["text"],
                borderwidth=0,
                relief="flat",
                font=self.fonts["button"],
                padding=(10, 8),
            )
            self.style.map(
                heading_style,
                background=[("active", self.colors["info_bg"])],
            )
        for scrollbar_style in ("Vertical.TScrollbar", "App.Vertical.TScrollbar"):
            self.style.configure(
                scrollbar_style,
                gripcount=0,
                background=self.colors["surface_alt"],
                darkcolor=self.colors["surface_alt"],
                lightcolor=self.colors["surface_alt"],
                troughcolor=self.colors["app_bg"],
                bordercolor=self.colors["app_bg"],
                arrowcolor=self.colors["muted"],
            )
        for scrollbar_style in ("Horizontal.TScrollbar", "App.Horizontal.TScrollbar"):
            self.style.configure(
                scrollbar_style,
                gripcount=0,
                background=self.colors["surface_alt"],
                darkcolor=self.colors["surface_alt"],
                lightcolor=self.colors["surface_alt"],
                troughcolor=self.colors["app_bg"],
                bordercolor=self.colors["app_bg"],
                arrowcolor=self.colors["muted"],
            )
        for entry_style in ("TEntry", "App.TEntry"):
            self.style.configure(
                entry_style,
                fieldbackground=self.colors["surface"],
                foreground=self.colors["text"],
                bordercolor=self.colors["border"],
                lightcolor=self.colors["border"],
                darkcolor=self.colors["border"],
                insertcolor=self.colors["text"],
                padding=(10, 8),
            )
            self.style.map(
                entry_style,
                bordercolor=[("focus", self.colors["primary"])],
                lightcolor=[("focus", self.colors["primary"])],
                darkcolor=[("focus", self.colors["primary"])],
            )
        for combo_style in ("TCombobox", "App.TCombobox"):
            self.style.configure(
                combo_style,
                fieldbackground=self.colors["surface"],
                background=self.colors["surface"],
                foreground=self.colors["text"],
                bordercolor=self.colors["border"],
                lightcolor=self.colors["border"],
                darkcolor=self.colors["border"],
                insertcolor=self.colors["text"],
                padding=(10, 8),
                arrowsize=16,
            )
            self.style.map(
                combo_style,
                bordercolor=[("focus", self.colors["primary"])],
                lightcolor=[("focus", self.colors["primary"])],
                darkcolor=[("focus", self.colors["primary"])],
            )
        for notebook_style in ("TNotebook", "App.TNotebook"):
            self.style.configure(
                notebook_style,
                background=self.colors["surface"],
                borderwidth=0,
                tabmargins=(0, 0, 0, 0),
            )
        for tab_style in ("TNotebook.Tab", "App.TNotebook.Tab"):
            self.style.configure(
                tab_style,
                background=self.colors["surface_alt"],
                foreground=self.colors["muted"],
                padding=(16, 10),
                font=self.fonts["button"],
            )
            self.style.map(
                tab_style,
                background=[("selected", self.colors["primary"]), ("active", self.colors["info_bg"])],
                foreground=[("selected", self.colors["sidebar_text"]), ("active", self.colors["text"])],
            )

    def _make_button(
        self,
        parent: tk.Misc,
        text: str,
        command,
        *,
        variant: str = "secondary",
        width: int | None = None,
        padx: int = 14,
        pady: int = 8,
        **kwargs,
    ) -> tk.Button:
        """Create a consistently styled button with semantic variants."""
        variant_map = {
            "primary": (self.colors["primary"], self.colors["sidebar_text"], self.colors["primary_hover"]),
            "secondary": (self.colors["surface_alt"], self.colors["text"], self.colors["info_bg"]),
            "success": (self.colors["success_bg"], self.colors["success"], "#d7f0e0"),
            "danger": (self.colors["danger_bg"], self.colors["danger"], "#f8ddd7"),
            "warning": (self.colors["warning_bg"], self.colors["warning"], "#fde9c0"),
            "accent": (self.colors["accent"], self.colors["accent_text"], "#e4c77f"),
            "sidebar": (self.colors["surface"], self.colors["text"], self.colors["info_bg"]),
        }
        bg, fg, active_bg = variant_map.get(variant, variant_map["secondary"])
        button_kwargs = {
            "text": text,
            "command": command,
            "bg": bg,
            "fg": fg,
            "activebackground": active_bg,
            "activeforeground": fg,
            "relief": "flat",
            "bd": 0,
            "cursor": "hand2",
            "padx": padx,
            "pady": pady,
            "font": self.fonts["button"],
            **kwargs,
        }
        if width is not None:
            button_kwargs["width"] = width
        return tk.Button(parent, **button_kwargs)

    def _style_dialog_shell(self, dialog: tk.Toplevel) -> None:
        """Apply a consistent top-level window treatment."""
        dialog.configure(bg=self.colors["surface"])
        try:
            dialog.option_add("*TCombobox*Listbox*Background", self.colors["surface"])
            dialog.option_add("*TCombobox*Listbox*Foreground", self.colors["text"])
            dialog.option_add("*TCombobox*Listbox*selectBackground", self.colors["primary"])
            dialog.option_add("*TCombobox*Listbox*selectForeground", self.colors["sidebar_text"])
        except tk.TclError:
            pass

    def _resolve_combo_value(
        self,
        preferred: str,
        options: list[str],
        *,
        allow_blank: bool = True,
    ) -> str:
        """Return a safe combobox value even when the preferred selection is no longer valid."""
        normalized_options = [str(option) for option in options if option is not None]
        if preferred in normalized_options:
            return preferred
        if allow_blank and "" in normalized_options:
            return ""
        for option in normalized_options:
            if option.strip():
                return option
        return ""

    def _student_asset_key(self, student_id: str, label_id: int) -> str:
        """Build a stable folder name for student-specific files."""
        return f"{student_id}_{label_id}"

    def _get_student_assets_dir(self, student: Student) -> Path:
        """Return the folder that stores non-photo student assets."""
        return STUDENTS_DIR / self._student_asset_key(student.student_id, student.label_id)

    def _get_student_photo_dir(self, student: Student) -> Path:
        """Return the folder dedicated to the student's stored photos."""
        return PHOTOS_DIR / self._student_asset_key(student.student_id, student.label_id)

    def _write_student_details_snapshot(self, student: Student) -> None:
        """Persist a lightweight student snapshot for local file browsing."""
        details_dir = self._get_student_assets_dir(student)
        details_dir.mkdir(parents=True, exist_ok=True)
        harden_permissions(details_dir, is_dir=True)
        payload = {
            "label_id": student.label_id,
            "student_id": student.student_id,
            "name": student.name,
            "class_name": student.class_name,
            "section_name": student.section_name,
            "batch_name": student.batch_name,
            "academic_year": student.academic_year,
            "semester": student.semester,
            "guardian_name": student.guardian_name,
            "status": student.status,
            "created_at": student.created_at,
            "updated_at": student.updated_at,
        }
        snapshot_path = details_dir / "student_details.json"
        snapshot_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        harden_permissions(snapshot_path, is_dir=False)

    def _format_column_heading(self, column_name: str) -> str:
        """Convert storage-style column names into friendlier UI labels."""
        special_labels = {
            "id": "ID",
            "student_id": "Student ID",
            "class_name": "Class",
            "section_name": "Section",
            "subject_name": "Subject",
            "day_name": "Day",
            "start_time": "Start Time",
            "end_time": "End Time",
            "room_name": "Room",
            "teacher_name": "Teacher",
            "academic_year": "Academic Year",
            "attendance_rate": "Attendance Rate",
            "present_like": "Present-Like",
            "total_records": "Total Records",
            "created_at": "Created At",
            "updated_at": "Updated At",
            "actor_username": "Actor",
            "entity_type": "Entity Type",
            "entity_id": "Entity ID",
            "attendance_id": "Attendance ID",
            "requested_by": "Requested By",
            "original_date": "Original Date",
            "requested_date": "Requested Date",
            "original_status": "Original Status",
            "requested_status": "Requested Status",
        }
        if column_name in special_labels:
            return special_labels[column_name]
        return column_name.replace("_", " ").title()

    def report_exception(
        self,
        title: str,
        exc: Exception,
        *,
        user_message: str | None = None,
        parent: tk.Misc | None = None,
    ) -> None:
        """Log an exception and show a friendly dialog without losing traceback context."""
        LOGGER.exception("%s: %s", title, exc)
        messagebox.showerror(title, user_message or str(exc), parent=parent or self.root)

    def _build_ui(self) -> None:
        """Create the main dashboard layout, sidebar, tables, and action buttons."""
        self.root.configure(bg=self.colors["app_bg"])

        container = tk.Frame(self.root, bg=self.colors["app_bg"], padx=24, pady=24)
        container.pack(fill="both", expand=True)
        self.container = container
        container.grid_columnconfigure(0, weight=0, minsize=168)
        container.grid_columnconfigure(1, weight=4)
        container.grid_rowconfigure(0, weight=1)

        sidebar_shell = tk.Frame(container, bg=self.colors["sidebar"], padx=1, pady=1, highlightthickness=0)
        sidebar_shell.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 18))
        sidebar_shell.grid_rowconfigure(0, weight=1)
        sidebar_shell.grid_columnconfigure(0, weight=1)
        sidebar = tk.Frame(sidebar_shell, bg=self.colors["sidebar"], padx=16, pady=18)
        self.sidebar_frame = sidebar
        sidebar.grid(row=0, column=0, sticky="nsew")
        sidebar.grid_columnconfigure(0, weight=1)
        sidebar.grid_rowconfigure(2, weight=1)

        title = tk.Label(
            sidebar,
            text="Face Attendance Pro",
            bg=self.colors["sidebar"],
            fg=self.colors["sidebar_text"],
            font=("Georgia", 16, "bold"),
            wraplength=140,
            justify="left",
        )
        title.grid(row=0, column=0, sticky="ew", pady=(2, 8))

        subtitle = tk.Label(
            sidebar,
            text="",
            bg=self.colors["sidebar"],
            fg=self.colors["sidebar_muted"],
            wraplength=126,
            justify="left",
            font=self.fonts["caption"],
        )
        subtitle.grid(row=1, column=0, sticky="ew", pady=(0, 12))
        self.sidebar_subtitle = subtitle

        self.sidebar_menu_frame = tk.Frame(sidebar, bg=self.colors["sidebar"])
        self.sidebar_menu_frame.grid(row=2, column=0, sticky="new")
        self.sidebar_menu_frame.grid_columnconfigure(0, weight=1)

        self.build_sidebar_groups()

        auth_info = tk.Frame(sidebar, bg=self.colors["sidebar_alt"], padx=12, pady=12)
        auth_info.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        self.auth_info = auth_info
        auth_info.grid_columnconfigure(0, weight=1)
        tk.Label(
            auth_info,
            text="Signed In User",
            bg=self.colors["sidebar_alt"],
            fg=self.colors["sidebar_text"],
            font=self.fonts["caption"],
        ).pack(anchor="w")
        tk.Label(
            auth_info,
            textvariable=self.current_user_var,
            bg=self.colors["sidebar_alt"],
            fg=self.colors["sidebar_text"],
            font=self.fonts["small"],
            wraplength=120,
            justify="left",
        ).pack(anchor="w", pady=(6, 0))
        tk.Label(
            auth_info,
            textvariable=self.current_role_var,
            bg=self.colors["sidebar_alt"],
            fg=self.colors["sidebar_muted"],
            font=self.fonts["caption"],
        ).pack(anchor="w", pady=(2, 0))

        quick_info = tk.Frame(sidebar, bg=self.colors["sidebar_alt"], padx=12, pady=12)
        quick_info.grid(row=4, column=0, sticky="sew", pady=(12, 0))
        self.quick_info = quick_info

        tk.Label(
            quick_info,
            text="Live Activity",
            bg=self.colors["sidebar_alt"],
            fg=self.colors["sidebar_text"],
            font=self.fonts["caption"],
        ).pack(anchor="w")
        tk.Label(
            quick_info,
            textvariable=self.activity_var,
            bg=self.colors["sidebar_alt"],
            fg=self.colors["sidebar_muted"],
            font=self.fonts["small"],
            wraplength=120,
            justify="left",
        ).pack(anchor="w", pady=(8, 0))

        main_shell = tk.Frame(container, bg=self.colors["app_bg"])
        main_shell.grid(row=0, column=1, rowspan=2, sticky="nsew")
        main_shell.grid_columnconfigure(0, weight=1)
        main_shell.grid_rowconfigure(0, weight=1)
        main_panel = tk.Frame(main_shell, bg=self.colors["app_bg"])
        self.main_panel = main_panel
        main_panel.grid(row=0, column=0, sticky="nsew")

        main_panel.grid_columnconfigure(0, weight=1)
        main_panel.grid_rowconfigure(0, weight=0)
        main_panel.grid_rowconfigure(1, weight=1)

        header = tk.Frame(main_panel, bg=self.colors["app_bg"])
        self.header = header
        header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        header.grid_columnconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=0)
        header.grid_rowconfigure(0, weight=0)
        header.grid_rowconfigure(1, weight=0)
        header.grid_rowconfigure(2, weight=0)

        self.dashboard_title_label = tk.Label(
            header,
            text="Dashboard",
            bg=self.colors["app_bg"],
            fg=self.colors["text"],
            font=self.fonts["title"],
        )
        self.dashboard_title_label.grid(row=0, column=0, sticky="w")
        self.context_label = tk.Label(
            header,
            textvariable=self.context_var,
            bg=self.colors["app_bg"],
            fg=self.colors["muted"],
            font=self.fonts["small"],
        )
        self.context_label.grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.header_status_label = tk.Label(
            header,
            textvariable=self.status_var,
            bg=self.colors["app_bg"],
            fg=self.colors["muted"],
            font=self.fonts["small"],
            wraplength=360,
            justify="right",
        )
        self.header_status_label.grid(row=1, column=1, sticky="e", padx=(16, 0), pady=(4, 0))

        stats_frame = tk.Frame(header, bg=self.colors["surface"], padx=12, pady=10, highlightbackground=self.colors["border"], highlightthickness=1)
        self.stats_frame = stats_frame
        stats_frame.grid(row=0, column=1, sticky="e", padx=(16, 0))
        for column in range(4):
            stats_frame.grid_columnconfigure(column, weight=1)

        stats = [
            (self.card_label_vars[0], self.student_count_var, "#ffffff"),
            (self.card_label_vars[1], self.today_attendance_var, self.colors["success_bg"]),
            (self.card_label_vars[2], self.last_marked_var, self.colors["info_bg"]),
            (self.card_label_vars[3], self.model_status_var, self.colors["warning_bg"]),
        ]

        self.stat_cards: list[tk.Frame] = []
        for index, (label_var, variable, bg_color) in enumerate(stats):
            card = tk.Frame(stats_frame, bg=bg_color, padx=14, pady=10, highlightbackground=self.colors["border"], highlightthickness=1)
            card.grid(row=0, column=index, sticky="nsew", padx=(0, 8) if index < 3 else 0, pady=0)
            tk.Label(card, textvariable=label_var, bg=bg_color, fg=self.colors["muted"], font=self.fonts["caption"]).pack(anchor="w")
            tk.Label(card, textvariable=variable, bg=bg_color, fg=self.colors["text"], font=("Segoe UI Semibold", 14)).pack(anchor="w", pady=(6, 0))
            self.stat_cards.append(card)

        lower_frame = tk.Frame(main_panel, bg=self.colors["app_bg"])
        self.lower_frame = lower_frame
        lower_frame.grid(row=1, column=0, sticky="nsew")
        lower_frame.grid_columnconfigure(0, weight=1)
        lower_frame.grid_columnconfigure(1, weight=2)
        lower_frame.grid_rowconfigure(0, weight=1)
        lower_frame.grid_rowconfigure(1, weight=0)

        student_panel = tk.Frame(lower_frame, bg=self.colors["surface"], padx=16, pady=16, highlightbackground=self.colors["border"], highlightthickness=1)
        self.student_panel = student_panel
        student_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        student_panel.grid_rowconfigure(3, weight=1)
        student_panel.grid_columnconfigure(0, weight=1)

        self.student_panel_title = tk.Label(student_panel, text="Students", bg=self.colors["surface"], fg=self.colors["text"], font=self.fonts["heading"])
        self.student_panel_title.grid(row=0, column=0, sticky="w")
        self.student_panel_subtitle = tk.Label(
            student_panel,
            textvariable=self.student_panel_subtitle_var,
            bg=self.colors["surface"],
            fg=self.colors["muted"],
            font=self.fonts["caption"],
            wraplength=300,
            justify="left",
        )
        self.student_panel_subtitle.grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.student_search_row = tk.Frame(student_panel, bg=self.colors["surface"])
        self.student_search_row.grid(row=2, column=0, sticky="ew", pady=(12, 10))
        self.student_search_row.grid_columnconfigure(1, weight=1)
        self.student_search_label = tk.Label(
            self.student_search_row,
            text="Search",
            bg=self.colors["surface"],
            fg=self.colors["muted"],
            font=self.fonts["small"],
        )
        self.student_search_label.grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.student_search_entry = ttk.Entry(self.student_search_row, textvariable=self.student_search_var, style="App.TEntry")
        self.student_search_entry.grid(row=0, column=1, sticky="ew")
        self.student_search_var.trace_add("write", lambda *_: self.refresh_student_table())

        self.student_tree = ttk.Treeview(
            student_panel,
            columns=("student_id", "name", "samples"),
            show="headings",
            height=12,
            style="Card.Treeview",
        )
        for col, heading, width in (
            ("student_id", "Student ID", 90),
            ("name", "Name", 150),
            ("samples", "Samples", 70),
        ):
            self.student_tree.heading(col, text=heading)
            self.student_tree.column(col, width=width, anchor="center" if col != "name" else "w")
        self.student_tree.grid(row=3, column=0, sticky="nsew")
        self.student_tree.bind("<<TreeviewSelect>>", self.on_student_select)
        student_scroll = ttk.Scrollbar(student_panel, orient="vertical", command=self.student_tree.yview, style="App.Vertical.TScrollbar")
        self.student_x_scroll = ttk.Scrollbar(student_panel, orient="horizontal", command=self.student_tree.xview, style="App.Horizontal.TScrollbar")
        self.student_tree.configure(yscrollcommand=student_scroll.set, xscrollcommand=self.student_x_scroll.set)
        student_scroll.grid(row=3, column=1, sticky="ns")
        self.student_x_scroll.grid(row=6, column=0, sticky="ew", pady=(8, 0))

        tk.Label(
            student_panel,
            textvariable=self.selected_student_var,
            bg=self.colors["surface"],
            fg=self.colors["muted"],
            font=self.fonts["small"],
        ).grid(row=4, column=0, sticky="w", pady=(10, 6))

        student_actions = tk.Frame(student_panel, bg=self.colors["surface"])
        student_actions.grid(row=5, column=0, sticky="ew")
        for index in range(5):
            student_actions.grid_columnconfigure(index, weight=1)
        self._make_button(student_actions, "Profile", self.open_student_profile, variant="accent").grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.edit_student_button = self._make_button(student_actions, "Edit", self.edit_selected_student, variant="secondary")
        self.edit_student_button.grid(row=0, column=1, sticky="ew", padx=3)
        self.recapture_student_button = self._make_button(student_actions, "Re-Capture", self.recapture_selected_student, variant="success")
        self.recapture_student_button.grid(row=0, column=2, sticky="ew", padx=3)
        self.delete_student_button = self._make_button(student_actions, "Delete", self.delete_selected_student, variant="danger")
        self.delete_student_button.grid(row=0, column=3, sticky="ew", padx=(6, 0))
        self.reset_student_password_button = self._make_button(student_actions, "Reset Password", self.reset_selected_student_password, variant="warning")
        self.reset_student_password_button.grid(row=0, column=4, sticky="ew", padx=(6, 0))

        attendance_panel = tk.Frame(lower_frame, bg=self.colors["surface"], padx=16, pady=16, highlightbackground=self.colors["border"], highlightthickness=1)
        self.attendance_panel = attendance_panel
        attendance_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        attendance_panel.grid_rowconfigure(4, weight=1)
        attendance_panel.grid_columnconfigure(0, weight=1)

        top_bar = tk.Frame(attendance_panel, bg=self.colors["surface"])
        self.top_bar = top_bar
        top_bar.grid(row=0, column=0, sticky="ew")
        top_bar.grid_columnconfigure(0, weight=1)

        self.attendance_panel_title = tk.Label(top_bar, text="Attendance Table", bg=self.colors["surface"], fg=self.colors["text"], font=self.fonts["heading"])
        self.attendance_panel_title.grid(row=0, column=0, sticky="w")
        self.attendance_panel_subtitle = tk.Label(
            top_bar,
            textvariable=self.attendance_panel_subtitle_var,
            bg=self.colors["surface"],
            fg=self.colors["muted"],
            font=self.fonts["caption"],
            wraplength=420,
            justify="left",
        )
        self.attendance_panel_subtitle.grid(row=1, column=0, sticky="w", pady=(4, 0))

        self.filter_row = tk.Frame(attendance_panel, bg=self.colors["surface_alt"], padx=12, pady=10, highlightbackground=self.colors["border"], highlightthickness=1)
        self.filter_row.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        self.filter_row.grid_columnconfigure(1, weight=1)
        tk.Label(self.filter_row, text="Find", bg=self.colors["surface_alt"], fg=self.colors["muted"], font=self.fonts["caption"]).grid(row=0, column=0, sticky="w", padx=(0, 8), pady=2)
        ttk.Entry(self.filter_row, textvariable=self.attendance_search_var, width=18, style="App.TEntry").grid(row=0, column=1, sticky="ew", pady=2)
        tk.Label(self.filter_row, text="Date", bg=self.colors["surface_alt"], fg=self.colors["muted"], font=self.fonts["caption"]).grid(row=0, column=2, sticky="w", padx=(12, 8), pady=2)
        self.date_filter_entry = ttk.Entry(self.filter_row, textvariable=self.date_filter_var, width=14, style="App.TEntry")
        self.date_filter_entry.grid(row=0, column=3, sticky="ew", pady=2)
        self._make_button(self.filter_row, "Pick", lambda: self.open_date_picker(self.date_filter_var), variant="secondary", padx=12, pady=7).grid(row=0, column=4, sticky="ew", padx=(8, 8), pady=2)
        self.today_filter_button = self._make_button(self.filter_row, "Today", self.set_today_filter, variant="success", padx=12, pady=7)
        self.today_filter_button.grid(row=0, column=5, sticky="ew", pady=2)
        self._make_button(self.filter_row, "Filters", self.open_attendance_filter_dialog, variant="accent", padx=12, pady=7).grid(row=0, column=6, sticky="ew", padx=(8, 0), pady=2)

        tk.Label(
            attendance_panel,
            textvariable=self.attendance_summary_var,
            bg=self.colors["surface"],
            fg=self.colors["muted"],
            font=self.fonts["body"],
        ).grid(row=2, column=0, sticky="w", pady=(10, 4))
        tk.Label(
            attendance_panel,
            textvariable=self.attendance_session_var,
            bg=self.colors["surface"],
            fg=self.colors["danger"],
            font=self.fonts["small"],
        ).grid(row=3, column=0, sticky="w", pady=(0, 10))

        self.attendance_tree = ttk.Treeview(
            attendance_panel,
            columns=("id", "student_id", "name", "date", "time", "status", "source"),
            show="headings",
            height=14,
            style="Card.Treeview",
        )
        for col, heading, width, anchor in (
            ("id", "ID", 50, "center"),
            ("student_id", "Student ID", 100, "center"),
            ("name", "Name", 180, "w"),
            ("date", "Date", 110, "center"),
            ("time", "Time", 100, "center"),
            ("status", "Status", 90, "center"),
            ("source", "Source", 90, "center"),
        ):
            self.attendance_tree.heading(col, text=heading)
            self.attendance_tree.column(col, width=width, anchor=anchor)
        self.attendance_tree.grid(row=4, column=0, sticky="nsew")
        self.attendance_tree.bind("<<TreeviewSelect>>", self.on_attendance_select)
        attendance_scroll = ttk.Scrollbar(attendance_panel, orient="vertical", command=self.attendance_tree.yview, style="App.Vertical.TScrollbar")
        self.attendance_x_scroll = ttk.Scrollbar(attendance_panel, orient="horizontal", command=self.attendance_tree.xview, style="App.Horizontal.TScrollbar")
        self.attendance_tree.configure(yscrollcommand=attendance_scroll.set, xscrollcommand=self.attendance_x_scroll.set)
        attendance_scroll.grid(row=4, column=1, sticky="ns")
        self.attendance_x_scroll.grid(row=5, column=0, sticky="ew", pady=(8, 0))

        tk.Label(
            attendance_panel,
            textvariable=self.selected_attendance_var,
            bg=self.colors["surface"],
            fg=self.colors["muted"],
            font=self.fonts["small"],
        ).grid(row=6, column=0, sticky="w", pady=(10, 6))

        attendance_actions = tk.Frame(attendance_panel, bg=self.colors["surface"])
        attendance_actions.grid(row=7, column=0, sticky="ew")
        self.edit_attendance_button = self._make_button(attendance_actions, "Edit Selected Record", self.edit_selected_attendance, variant="secondary")
        self.edit_attendance_button.pack(side="left")

        activity_panel = tk.Frame(attendance_panel, bg=self.colors["surface_alt"], padx=12, pady=12, highlightbackground=self.colors["border"], highlightthickness=1)
        activity_panel.grid(row=8, column=0, sticky="ew", pady=(12, 0))
        activity_panel.grid_columnconfigure(0, weight=1)
        tk.Label(activity_panel, text="Recent Activity", bg=self.colors["surface_alt"], fg=self.colors["text"], font=self.fonts["subheading"]).grid(row=0, column=0, sticky="w")
        self.activity_list = tk.Listbox(
            activity_panel,
            height=4,
            borderwidth=0,
            highlightthickness=0,
            bg=self.colors["surface_alt"],
            fg=self.colors["text"],
            selectbackground=self.colors["primary"],
            selectforeground=self.colors["sidebar_text"],
            font=self.fonts["body"],
        )
        self.activity_list.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        self.attendance_search_var.trace_add("write", lambda *_: self.refresh_attendance_table())
        self.date_filter_entry.bind("<Return>", lambda _event: self.refresh_attendance_table())
        self.root.bind("<Configure>", self.on_root_resize)
        self.update_sidebar_permissions()
        self.update_responsive_layout()
        self.refresh_institute_settings()

    def run(self) -> None:
        """Start the Tkinter event loop."""
        LOGGER.info("Starting Tkinter main loop.")
        self.root.mainloop()

    def build_sidebar_groups(self) -> None:
        """Create compact grouped menus so the sidebar stays narrow."""
        group_specs = [
            (
                "Context",
                [
                    ("Switch Class / Subject", self.open_context_switcher),
                    ("Use Current Timetable Slot", self.apply_current_timetable_context),
                ],
            ),
            (
                "Students",
                [
                    ("Register Student", self.register_student),
                    ("Bulk Import Students", self.bulk_import_students),
                    ("Train Model", self.train_model),
                ],
            ),
            (
                "Attendance",
                [
                    ("Start Attendance", self.start_attendance),
                    ("Camera Diagnostics", self.open_camera_diagnostics),
                    ("Manual Attendance", self.add_manual_attendance),
                    ("Auto Mark Absentees", self.auto_mark_absentees_for_selected_date),
                    ("Finalize Attendance", self.finalize_attendance_for_selected_date),
                    ("Review Change Requests", self.open_attendance_change_request_viewer),
                ],
            ),
            (
                "Reports",
                [
                    ("Attendance Analytics", self.open_analytics_viewer),
                    ("Report Summary", self.open_report_summary_viewer),
                    ("Low Attendance Alerts", self.open_low_attendance_alerts_viewer),
                ],
            ),
            (
                "Admin",
                [
                    ("Institute Setup", self.open_institute_setup_dialog),
                    ("Manage Classes / Subjects", self.open_class_subject_manager),
                    ("Student Promotion", self.open_student_promotion_manager),
                    ("Manage Timetable", self.open_timetable_manager),
                    ("Manage Users", self.open_user_manager),
                    ("Clean Temp Credentials", self.clean_temporary_credential_files),
                    ("Audit Logs", self.open_audit_log_viewer),
                    ("Database Viewer", self.open_database_viewer),
                    ("Backup Database", self.backup_database_file),
                    ("Restore Backup", self.open_backup_restore_dialog),
                ],
            ),
            (
                "Exports",
                [
                    ("Export Excel", self.export_attendance_excel),
                    ("Export PDF", self.export_attendance_pdf),
                    ("Export Report", self.export_attendance_report),
                    ("Open Attendance File", self.show_attendance_path),
                ],
            ),
        ]

        for row_index, (label, items) in enumerate(group_specs):
            button = tk.Menubutton(
                self.sidebar_menu_frame,
                text=label,
                width=15,
                font=self.fonts["button"],
                padx=12,
                pady=7,
                bg=self.colors["accent"] if label == "Attendance" else self.colors["surface"],
                fg=self.colors["accent_text"] if label == "Attendance" else self.colors["text"],
                relief="flat",
                bd=0,
                activebackground=self.colors["info_bg"],
                activeforeground=self.colors["text"],
                direction="below",
                anchor="w",
                cursor="hand2",
            )
            menu = tk.Menu(button, tearoff=False)
            for item_label, command in items:
                menu.add_command(label=item_label, command=command)
            button.configure(menu=menu)
            button.grid(row=row_index, column=0, sticky="ew", pady=4)
            self.sidebar_button_order.append(label)
            self.sidebar_buttons[label] = button
            self.sidebar_menus[label] = menu

        for label, command in (
            ("Change Password", self.open_change_password_dialog),
            ("Refresh Dashboard", self.refresh_dashboard),
            ("Exit", self.root.destroy),
        ):
            row_index = len(self.sidebar_button_order)
            variant = "danger" if label == "Exit" else "sidebar"
            button = self._make_button(
                self.sidebar_menu_frame,
                label,
                command,
                variant=variant,
                width=15,
                padx=12,
                pady=7,
            )
            button.grid(row=row_index, column=0, sticky="ew", pady=4)
            self.sidebar_button_order.append(label)
            self.sidebar_buttons[label] = button

    def refresh_institute_settings(self) -> None:
        """Reload and display the active institute configuration."""
        self.institute_settings = self.repository.get_institute_settings()
        self.sidebar_subtitle.configure(
            text=(
                f"{self.institute_settings['institute_name']}\n"
                f"{self.institute_settings['institute_type']} | {self.institute_settings['academic_year']}"
            )
        )

    def on_root_resize(self, event=None) -> None:
        """Reflow the dashboard when the main window size changes."""
        if event is not None and event.widget is not self.root:
            return
        self.update_responsive_layout()

    def update_responsive_layout(self) -> None:
        """Adapt panel placement and card wrapping to the available window width."""
        width = max(self.root.winfo_width(), self.main_panel.winfo_width())

        if width < 920:
            self.header.grid_columnconfigure(1, weight=1)
            self.stats_frame.grid_configure(row=2, column=0, columnspan=2, sticky="ew", padx=0, pady=(12, 0))
            self.header_status_label.grid_configure(row=3, column=0, columnspan=2, rowspan=1, sticky="w", padx=0, pady=(10, 0))
            self.header_status_label.configure(wraplength=max(280, width - 140), justify="left")
        else:
            self.header.grid_columnconfigure(1, weight=0)
            self.stats_frame.grid_configure(row=0, column=1, columnspan=1, sticky="e", padx=(16, 0), pady=0)
            self.header_status_label.grid_configure(row=1, column=1, columnspan=1, rowspan=1, sticky="e", padx=(16, 0), pady=(4, 0))
            self.header_status_label.configure(wraplength=max(240, width - 760), justify="right")

        stat_columns = 1 if width < 820 else 2 if width < 1180 else 4
        for column in range(4):
            self.stats_frame.grid_columnconfigure(column, weight=1 if column < stat_columns else 0)
        for index, card in enumerate(self.stat_cards):
            card.grid_configure(
                row=index // stat_columns,
                column=index % stat_columns,
                padx=(0, 8) if (index % stat_columns) < stat_columns - 1 else 0,
                pady=(0, 6) if index >= stat_columns else 0,
            )

        mobile_view = width < 900
        if mobile_view:
            self.student_x_scroll.grid()
            self.attendance_x_scroll.grid()
        else:
            self.student_x_scroll.grid_remove()
            self.attendance_x_scroll.grid_remove()

        if width < 1160:
            self.lower_frame.grid_columnconfigure(0, weight=1)
            self.lower_frame.grid_columnconfigure(1, weight=0)
            self.lower_frame.grid_rowconfigure(0, weight=1)
            self.lower_frame.grid_rowconfigure(1, weight=2)
            self.student_panel.grid_configure(row=0, column=0, padx=0, pady=(0, 12))
            self.attendance_panel.grid_configure(row=1, column=0, padx=0, pady=0)
        else:
            self.lower_frame.grid_columnconfigure(0, weight=1)
            self.lower_frame.grid_columnconfigure(1, weight=2)
            self.lower_frame.grid_rowconfigure(0, weight=1)
            self.lower_frame.grid_rowconfigure(1, weight=0)
            self.student_panel.grid_configure(row=0, column=0, padx=(0, 8), pady=0)
            self.attendance_panel.grid_configure(row=0, column=1, padx=(8, 0), pady=0)

    def clear_attendance_filters(self) -> None:
        """Reset the attendance filters to a simple, user-friendly default state."""
        self.attendance_search_var.set("")
        self.attendance_status_filter_var.set("All")
        self.attendance_source_filter_var.set("All")
        self.set_today_filter()

    def play_duplicate_punch_alert(self, identity_key: str = "duplicate") -> None:
        """Play an audible duplicate-punch warning with a 3-second per-person cooldown."""
        now = time.monotonic()
        last_played = self.duplicate_alert_times.get(identity_key, 0.0)
        if now - last_played < 3.0:
            return
        self.duplicate_alert_times[identity_key] = now

        if winsound is not None:
            try:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                return
            except Exception:
                try:
                    winsound.Beep(1000, 200)
                    return
                except Exception:
                    pass
        try:
            self.root.bell()
        except Exception:
            pass

    def open_attendance_filter_dialog(self) -> None:
        """Open a compact popup for the extra attendance filters."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Attendance Filters")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(True, True)
        self._style_dialog_shell(dialog)
        dialog.minsize(500, 340)
        self.center_window(dialog, 540, 360)

        frame = tk.Frame(dialog, bg="white", padx=16, pady=16)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=0)
        frame.grid_columnconfigure(1, weight=1)

        tk.Label(frame, text="More Attendance Filters", bg="white", fg="#10233d", font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12)
        )
        tk.Label(frame, text="Status", bg="white", font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", pady=6)
        ttk.Combobox(
            frame,
            textvariable=self.attendance_status_filter_var,
            values=["All", "Present", "Late", "Absent", "Excused"],
            state="readonly",
        ).grid(row=1, column=1, sticky="ew", pady=6)
        tk.Label(frame, text="Source", bg="white", font=("Segoe UI", 10)).grid(row=2, column=0, sticky="w", pady=6)
        ttk.Combobox(
            frame,
            textvariable=self.attendance_source_filter_var,
            values=["All", "camera", "manual", "system"],
            state="readonly",
        ).grid(row=2, column=1, sticky="ew", pady=6)
        tk.Label(frame, text="From Date", bg="white", font=("Segoe UI", 10)).grid(row=3, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=self.date_from_var).grid(row=3, column=1, sticky="ew", pady=6)
        tk.Label(frame, text="To Date", bg="white", font=("Segoe UI", 10)).grid(row=4, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=self.date_to_var).grid(row=4, column=1, sticky="ew", pady=6)

        tk.Label(
            frame,
            text="Use YYYY-MM-DD format for date ranges. Leave both dates blank to keep the current day filter.",
            bg="white",
            fg="#5b7186",
            font=("Segoe UI", 9),
            wraplength=470,
            justify="left",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 0))

        button_row = tk.Frame(frame, bg="white")
        button_row.grid(row=6, column=0, columnspan=2, sticky="e", pady=(18, 0))
        self._make_button(
            button_row,
            "Clear",
            lambda: (self.clear_attendance_filters(), dialog.destroy()),
            variant="secondary",
            padx=12,
            pady=6,
        ).pack(side="right")
        self._make_button(
            button_row,
            "Apply",
            lambda: (self.refresh_attendance_table(), dialog.destroy()),
            variant="primary",
            padx=12,
            pady=6,
        ).pack(side="right", padx=(0, 8))

    def current_role(self) -> str:
        """Return the active signed-in role in normalized form."""
        value = self.current_role_var.get()
        if value.startswith("Role: "):
            return value.split(":", 1)[1].strip().lower()
        return value.strip().lower()

    def is_root_user(self) -> bool:
        """Return whether the signed-in user has full root control."""
        return self.current_role() == "root"

    def is_teacher_user(self) -> bool:
        """Return whether the signed-in user is a teacher-scoped account."""
        return self.current_role() == "teacher"

    def is_student_viewer(self) -> bool:
        """Return whether the current viewer session belongs to a student login."""
        return self.current_role() == "viewer" and bool(self.current_student_id)

    def get_allowed_classes(self) -> list[str]:
        """Return the classes the current user may access."""
        if self.is_teacher_user():
            scoped_class = self.current_user_scope.get("class_name", "").strip()
            return [scoped_class] if scoped_class else []
        return self.repository.list_classes()

    def get_allowed_subjects(
        self,
        class_name: str,
        *,
        section_name: str | None = None,
        academic_year: str | None = None,
        semester: str | None = None,
    ) -> list[str]:
        """Return the subjects the current user may access for the selected class."""
        resolved_section = self.current_section_var.get().strip() if section_name is None else section_name.strip()
        resolved_year = self.current_academic_year_var.get().strip() if academic_year is None else academic_year.strip()
        resolved_semester = self.current_semester_var.get().strip() if semester is None else semester.strip()
        subjects = self.repository.list_subjects(
            class_name,
            section_name=resolved_section or None,
            academic_year=resolved_year or None,
            semester=resolved_semester or None,
        )
        if subjects == ["General"] and not (resolved_section or resolved_year or resolved_semester):
            subjects = self.repository.list_subjects(class_name)
        if self.is_teacher_user():
            scoped_subject = self.current_user_scope.get("subject_name", "").strip()
            if scoped_subject:
                return [scoped_subject] if scoped_subject in subjects else [scoped_subject]
        return subjects

    def get_allowed_sections(
        self,
        class_name: str,
        *,
        academic_year: str | None = None,
        semester: str | None = None,
    ) -> list[str]:
        """Return sections available for the selected class."""
        resolved_year = self.current_academic_year_var.get().strip() if academic_year is None else academic_year.strip()
        resolved_semester = self.current_semester_var.get().strip() if semester is None else semester.strip()
        sections = self.repository.list_sections(
            class_name,
            resolved_year or None,
            resolved_semester or None,
        )
        return sections or [""]

    def get_allowed_semesters(self, class_name: str) -> list[str]:
        """Return semesters for the selected class in the active academic year."""
        semesters = self.repository.list_semesters(
            class_name,
            self.current_academic_year_var.get().strip() or None,
        )
        return semesters or [""]

    def enforce_current_user_scope(self) -> None:
        """Keep the current class/subject inside the signed-in teacher's assigned scope."""
        if not self.is_teacher_user():
            return
        scoped_class = self.current_user_scope.get("class_name", "").strip()
        scoped_subject = self.current_user_scope.get("subject_name", "").strip()
        if scoped_class:
            self.current_class_var.set(scoped_class)
        if scoped_subject:
            self.current_subject_var.set(scoped_subject)

    def update_sidebar_permissions(self) -> None:
        """Show only the controls available to the signed-in user."""
        is_root = self.is_root_user()
        is_viewer = self.current_role() == "viewer"
        is_student = self.is_student_viewer()
        for button in self.sidebar_buttons.values():
            button.grid_remove()
        for label in self.sidebar_button_order:
            if not is_root and label in ROOT_ONLY_LABELS:
                continue
            if is_student and label in STUDENT_VIEWER_HIDDEN_LABELS:
                continue
            if is_viewer and label in VIEWER_HIDDEN_LABELS:
                continue
            self.sidebar_buttons[label].grid()
        self.update_dashboard_role_view()

    def update_dashboard_role_view(self) -> None:
        """Adjust visible dashboard copy and actions for root versus staff users."""
        if self.is_root_user():
            self.student_search_row.grid()
            self.dashboard_title_label.configure(text="Dashboard")
            self.student_panel_title.configure(text="Students")
            self.attendance_panel_title.configure(text="Attendance Table")
            self.student_panel_subtitle_var.set("Browse registered students, open profiles, and manage face data.")
            self.attendance_panel_subtitle_var.set("Review attendance records, filter the table, and update selected entries.")
            self.card_label_vars[0].set("Students")
            self.card_label_vars[1].set("Today Present")
            self.card_label_vars[2].set("Last Marked")
            self.card_label_vars[3].set("Model")
            self.edit_student_button.grid()
            self.recapture_student_button.grid()
            self.delete_student_button.grid()
            self.reset_student_password_button.grid()
            self.edit_attendance_button.pack(side="left")
            self.student_tree.heading("student_id", text="Student ID")
            self.student_tree.heading("name", text="Name")
            self.student_tree.heading("samples", text="Samples")
            self.attendance_tree.heading("student_id", text="Student ID")
            self.attendance_tree.heading("name", text="Name")
            if not self.current_username:
                self.status_var.set("Ready. Register a student, train the recognizer, then start attendance.")
        elif self.current_role() == "viewer":
            self.dashboard_title_label.configure(text="Student Portal" if self.is_student_viewer() else "Viewer Dashboard")
            self.student_panel_title.configure(text="My Profile" if self.is_student_viewer() else "Class Roster")
            self.attendance_panel_title.configure(text="My Attendance" if self.is_student_viewer() else "Attendance Records")
            self.student_panel_subtitle_var.set(
                "Review your profile details and saved face record."
                if self.is_student_viewer()
                else "Read-only roster view for the selected class."
            )
            self.attendance_panel_subtitle_var.set(
                "Track your own attendance history and latest status."
                if self.is_student_viewer()
                else "Review filtered attendance records without editing access."
            )
            self.card_label_vars[0].set("My Class" if self.is_student_viewer() else "Roster Size")
            self.card_label_vars[1].set("My Rate" if self.is_student_viewer() else "Today's Records")
            self.card_label_vars[2].set("Latest Status" if self.is_student_viewer() else "Latest Mark")
            self.card_label_vars[3].set("Alert" if self.is_student_viewer() else "Model State")
            self.edit_student_button.grid_remove()
            self.recapture_student_button.grid_remove()
            self.delete_student_button.grid_remove()
            self.reset_student_password_button.grid_remove()
            self.edit_attendance_button.pack_forget()
            if self.is_student_viewer():
                self.student_search_row.grid_remove()
                self.student_tree.heading("student_id", text="My TNU ID")
                self.student_tree.heading("name", text="My Name")
                self.student_tree.heading("samples", text="Face Samples")
                self.attendance_tree.heading("student_id", text="My TNU ID")
                self.attendance_tree.heading("name", text="My Name")
                self.selected_student_var.set("Your profile record is shown below.")
                self.selected_attendance_var.set("Select one of your attendance rows to review it.")
                self.attendance_session_var.set("Session status: Personal attendance view")
            else:
                self.student_search_row.grid()
                self.student_tree.heading("student_id", text="Student ID")
                self.student_tree.heading("name", text="Name")
                self.student_tree.heading("samples", text="Samples")
                self.attendance_tree.heading("student_id", text="Student ID")
                self.attendance_tree.heading("name", text="Name")
            if self.current_username:
                self.status_var.set(
                    "Student viewer access is read-only. Review your profile and attendance history."
                    if self.is_student_viewer()
                    else "Viewer access is read-only. You can review students, attendance, and analytics."
                )
        else:
            role_name = self.current_role() or "staff"
            self.student_search_row.grid()
            self.dashboard_title_label.configure(text="Teacher Dashboard" if self.is_teacher_user() else "Staff Dashboard")
            self.student_panel_title.configure(text="Class Roster")
            self.attendance_panel_title.configure(text="Attendance Workspace")
            self.student_panel_subtitle_var.set("Find students quickly and open the actions you need for class operations.")
            self.attendance_panel_subtitle_var.set("Filter, inspect, and correct attendance for the active teaching context.")
            self.card_label_vars[0].set("Class Size")
            self.card_label_vars[1].set("Today's Marks")
            self.card_label_vars[2].set("Last Update")
            self.card_label_vars[3].set("Model State")
            self.edit_student_button.grid()
            self.recapture_student_button.grid()
            self.delete_student_button.grid_remove()
            self.reset_student_password_button.grid()
            self.edit_attendance_button.pack(side="left")
            self.student_tree.heading("student_id", text="Student ID")
            self.student_tree.heading("name", text="Name")
            self.student_tree.heading("samples", text="Samples")
            self.attendance_tree.heading("student_id", text="Student ID")
            self.attendance_tree.heading("name", text="Name")
            if self.current_username:
                self.status_var.set(
                    f"Signed in as {role_name}. Use the visible tools to manage students and attendance."
                )

    def require_roles(self, allowed_roles: tuple[str, ...], action_label: str) -> bool:
        """Block actions that the current role should not access."""
        if self.current_role() in allowed_roles:
            return True
        messagebox.showwarning(
            "Permission Required",
            f"You need one of these roles to {action_label}: {', '.join(allowed_roles)}.",
            parent=self.root,
        )
        return False

    def audit(self, action: str, entity_type: str, entity_id: str = "", details: str = "") -> None:
        """Write one audit trail event for the current user."""
        self.repository.log_audit_event(
            self.current_username or "system",
            action,
            entity_type,
            entity_id,
            details,
        )
        LOGGER.info("Audit event: %s | %s | %s", action, entity_type, entity_id or "-")

    def apply_role_landing(self) -> None:
        """Set the initial dashboard state after login based on the signed-in role."""
        self.date_filter_var.set(datetime.now().strftime("%Y-%m-%d"))

        if self.is_root_user():
            self.refresh_dashboard()
            self.status_var.set(
                "Root access enabled. You have full control over users, settings, data, and attendance."
            )
            self.log_activity("Root dashboard loaded with full system controls.")
            return

        if self.current_role() == "viewer":
            self.apply_current_timetable_context(show_message=False)
            self.refresh_dashboard()
            self.status_var.set(
                "Student viewer access is read-only. Review your profile and attendance history."
                if self.is_student_viewer()
                else "Viewer dashboard loaded. Review today's roster, attendance records, and analytics."
            )
            self.log_activity("Viewer dashboard loaded in read-only mode.")
            return

        self.enforce_current_user_scope()
        self.apply_current_timetable_context(show_message=False)
        self.refresh_dashboard()
        self.status_var.set(
            "Teacher dashboard loaded. Your access is limited to the assigned class and subject."
            if self.is_teacher_user()
            else "Operator dashboard loaded. Focus on registration, attendance updates, and today's class activity."
        )
        self.log_activity(
            "Teacher dashboard loaded for scoped attendance work."
            if self.is_teacher_user()
            else "Operator dashboard loaded for daily attendance work."
        )

    def login_user(self) -> bool:
        """Require a local login before showing the upgraded dashboard."""
        self.root.withdraw()
        dialog = tk.Toplevel(self.root)
        dialog.title("Sign In")
        dialog.grab_set()
        dialog.resizable(False, False)
        self._style_dialog_shell(dialog)
        self.center_dialog_on_screen(dialog, 920, 680)
        dialog.lift()
        dialog.attributes("-topmost", True)
        dialog.after_idle(lambda: dialog.attributes("-topmost", False))

        username_var = tk.StringVar()
        password_var = tk.StringVar()
        student_id_var = tk.StringVar()
        student_password_var = tk.StringVar()
        show_staff_password_var = tk.BooleanVar(value=False)
        show_student_password_var = tk.BooleanVar(value=False)
        result = {"ok": False}
        root_setup_required = not self.repository.is_root_account_configured()
        bootstrap_mode_enabled = ALLOW_IN_APP_ROOT_BOOTSTRAP or root_setup_required

        frame = tk.Frame(dialog, bg=self.colors["surface"], padx=20, pady=20)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=0)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        brand_panel = tk.Frame(
            frame,
            bg=self.colors["sidebar"],
            padx=22,
            pady=22,
            highlightbackground=self.colors["sidebar_alt"],
            highlightthickness=1,
        )
        brand_panel.grid(row=0, column=0, sticky="nsw", padx=(0, 18))
        brand_panel.grid_columnconfigure(0, weight=1)
        tk.Label(
            brand_panel,
            text="Face Attendance Pro",
            bg=self.colors["sidebar"],
            fg=self.colors["sidebar_text"],
            font=("Segoe UI Semibold", 20),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            brand_panel,
            text="Secure attendance control for staff and quick access for students.",
            bg=self.colors["sidebar"],
            fg=self.colors["sidebar_muted"],
            font=self.fonts["body"],
            wraplength=240,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(8, 18))

        highlights = (
            ("Live camera attendance", "Start recognition sessions and mark entries in real time."),
            ("Student viewer access", "Check attendance from a student account without exposing staff tools."),
            ("Role-based security", "Admin, staff, and student actions stay clearly separated after sign-in."),
        )
        for index, (title, text) in enumerate(highlights, start=2):
            highlight_card = tk.Frame(
                brand_panel,
                bg=self.colors["sidebar_alt"],
                padx=12,
                pady=12,
                highlightbackground=self.colors["sidebar_alt"],
                highlightthickness=1,
            )
            highlight_card.grid(row=index, column=0, sticky="ew", pady=(0, 10))
            tk.Label(
                highlight_card,
                text=title,
                bg=self.colors["sidebar_alt"],
                fg=self.colors["sidebar_text"],
                font=self.fonts["subheading"],
            ).grid(row=0, column=0, sticky="w")
            tk.Label(
                highlight_card,
                text=text,
                bg=self.colors["sidebar_alt"],
                fg=self.colors["sidebar_muted"],
                font=self.fonts["small"],
                wraplength=220,
                justify="left",
            ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        auth_panel = tk.Frame(
            frame,
            bg=self.colors["surface"],
            padx=22,
            pady=22,
            highlightbackground=self.colors["border"],
            highlightthickness=1,
        )
        auth_panel.grid(row=0, column=1, sticky="nsew")
        auth_panel.grid_columnconfigure(0, weight=1)
        auth_panel.grid_rowconfigure(2, weight=1)
        tk.Label(
            auth_panel,
            text="Sign In",
            bg=self.colors["surface"],
            fg=self.colors["text"],
            font=("Segoe UI Semibold", 18),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            auth_panel,
            text="Choose the matching workspace below and continue with the correct account type.",
            bg=self.colors["surface"],
            fg=self.colors["muted"],
            font=self.fonts["body"],
            wraplength=520,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(6, 14))

        notebook = ttk.Notebook(auth_panel, style="App.TNotebook")
        notebook.grid(row=2, column=0, sticky="nsew")

        staff_tab = tk.Frame(notebook, bg=self.colors["surface"], padx=8, pady=10)
        student_tab = tk.Frame(notebook, bg=self.colors["surface"], padx=8, pady=10)
        notebook.add(staff_tab, text="Root / Staff")
        notebook.add(student_tab, text="Student Viewer")

        for tab in (staff_tab, student_tab):
            tab.grid_columnconfigure(0, weight=1)

        staff_card = tk.Frame(
            staff_tab,
            bg=self.colors["surface_alt"],
            padx=18,
            pady=18,
            highlightbackground=self.colors["border"],
            highlightthickness=1,
        )
        staff_card.grid(row=0, column=0, sticky="nsew")
        staff_card.grid_columnconfigure(1, weight=1)

        tk.Label(
            staff_card,
            text="Staff Workspace",
            bg=self.colors["surface_alt"],
            fg=self.colors["text"],
            font=self.fonts["heading"],
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        tk.Label(
            staff_card,
            text="Use this tab for root and staff accounts created by root.",
            bg=self.colors["surface_alt"],
            fg=self.colors["muted"],
            font=self.fonts["small"],
            wraplength=500,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 14))
        tk.Label(staff_card, text="Username", bg=self.colors["surface_alt"], font=self.fonts["body"]).grid(row=2, column=0, sticky="w", pady=6)
        username_entry = ttk.Entry(staff_card, textvariable=username_var, style="App.TEntry")
        username_entry.grid(row=2, column=1, sticky="ew", pady=6)
        tk.Label(staff_card, text="Password", bg=self.colors["surface_alt"], font=self.fonts["body"]).grid(row=3, column=0, sticky="w", pady=6)
        password_row = tk.Frame(staff_card, bg=self.colors["surface_alt"])
        password_row.grid(row=3, column=1, sticky="ew", pady=6)
        password_row.grid_columnconfigure(0, weight=1)
        password_entry = ttk.Entry(password_row, textvariable=password_var, show="*", style="App.TEntry")
        password_entry.grid(row=0, column=0, sticky="ew")
        tk.Checkbutton(
            password_row,
            text="Show",
            variable=show_staff_password_var,
            command=lambda: password_entry.configure(show="" if show_staff_password_var.get() else "*"),
            bg=self.colors["surface_alt"],
            fg=self.colors["muted"],
            activebackground=self.colors["surface_alt"],
            activeforeground=self.colors["text"],
            selectcolor=self.colors["surface"],
            font=self.fonts["small"],
            padx=8,
        ).grid(row=0, column=1, sticky="e", padx=(10, 0))
        if root_setup_required:
            setup_notice = tk.Frame(
                staff_card,
                bg=self.colors["warning_bg"],
                padx=12,
                pady=10,
                highlightbackground="#f2d8a6",
                highlightthickness=1,
            )
            setup_notice.grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 0))
            setup_notice.grid_columnconfigure(0, weight=1)
            tk.Label(
                setup_notice,
                text="Root setup required",
                bg=self.colors["warning_bg"],
                fg=self.colors["warning"],
                font=self.fonts["subheading"],
            ).grid(row=0, column=0, sticky="w")
            tk.Label(
                setup_notice,
                text=(
                    "Initialize the root account before the first admin sign-in."
                    if bootstrap_mode_enabled
                    else "Root account setup is locked for this session."
                ),
                bg=self.colors["warning_bg"],
                fg=self.colors["warning"],
                font=self.fonts["caption"],
                wraplength=470,
                justify="left",
            ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        def open_root_setup_dialog() -> None:
            if not bootstrap_mode_enabled:
                messagebox.showwarning(
                    "Bootstrap Locked",
                    "Root account setup is not available in this session.",
                    parent=dialog,
                )
                return
            setup_dialog = tk.Toplevel(dialog)
            setup_dialog.title("Initialize Root Account")
            setup_dialog.transient(dialog)
            setup_dialog.grab_set()
            self._style_dialog_shell(setup_dialog)
            self.center_window(setup_dialog, 520, 300)

            new_password_var = tk.StringVar()
            confirm_password_var = tk.StringVar()
            form = tk.Frame(setup_dialog, bg="white", padx=18, pady=18)
            form.pack(fill="both", expand=True)
            form.grid_columnconfigure(1, weight=1)

            tk.Label(form, text="Initialize Root Account", bg="white", fg="#10233d", font=("Segoe UI", 14, "bold")).grid(
                row=0, column=0, columnspan=2, sticky="w"
            )
            tk.Label(
                form,
                text="Create a strong admin password in explicit bootstrap mode. No bootstrap password file will be written to disk.",
                bg="white",
                fg="#5b7186",
                font=("Segoe UI", 9),
                wraplength=420,
                justify="left",
            ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 14))
            tk.Label(form, text="New Password", bg="white", font=("Segoe UI", 10)).grid(row=2, column=0, sticky="w", pady=6)
            ttk.Entry(form, textvariable=new_password_var, show="*").grid(row=2, column=1, sticky="ew", pady=6)
            tk.Label(form, text="Confirm Password", bg="white", font=("Segoe UI", 10)).grid(row=3, column=0, sticky="w", pady=6)
            ttk.Entry(form, textvariable=confirm_password_var, show="*").grid(row=3, column=1, sticky="ew", pady=6)

            def save_root_password() -> None:
                if new_password_var.get() != confirm_password_var.get():
                    messagebox.showerror("Password Mismatch", "The two password fields must match.", parent=setup_dialog)
                    return
                try:
                    self.repository.initialize_root_account(new_password_var.get())
                except ValueError as exc:
                    messagebox.showerror("Root Setup Failed", str(exc), parent=setup_dialog)
                    return
                messagebox.showinfo("Root Ready", "The root account has been initialized. You can now sign in as admin.", parent=setup_dialog)
                setup_dialog.destroy()

            button_row = tk.Frame(form, bg="white")
            button_row.grid(row=4, column=0, columnspan=2, sticky="e", pady=(18, 0))
            self._make_button(button_row, "Cancel", setup_dialog.destroy, variant="secondary").pack(side="right")
            self._make_button(button_row, "Initialize", save_root_password, variant="primary").pack(side="right", padx=(0, 8))

        if root_setup_required and bootstrap_mode_enabled:
            dialog.after_idle(open_root_setup_dialog)

        student_card = tk.Frame(
            student_tab,
            bg=self.colors["surface_alt"],
            padx=18,
            pady=18,
            highlightbackground=self.colors["border"],
            highlightthickness=1,
        )
        student_card.grid(row=0, column=0, sticky="nsew")
        student_card.grid_columnconfigure(1, weight=1)

        tk.Label(
            student_card,
            text="Student Viewer",
            bg=self.colors["surface_alt"],
            fg=self.colors["text"],
            font=self.fonts["heading"],
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        tk.Label(
            student_card,
            text="Students can sign in using their TNU ID and password.",
            bg=self.colors["surface_alt"],
            fg=self.colors["muted"],
            font=self.fonts["small"],
            wraplength=500,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 14))
        tk.Label(student_card, text="TNU ID", bg=self.colors["surface_alt"], font=self.fonts["body"]).grid(row=2, column=0, sticky="w", pady=6)
        student_id_entry = ttk.Entry(student_card, textvariable=student_id_var, style="App.TEntry")
        student_id_entry.grid(row=2, column=1, sticky="ew", pady=6)
        tk.Label(student_card, text="Password", bg=self.colors["surface_alt"], font=self.fonts["body"]).grid(row=3, column=0, sticky="w", pady=6)
        student_password_row = tk.Frame(student_card, bg=self.colors["surface_alt"])
        student_password_row.grid(row=3, column=1, sticky="ew", pady=6)
        student_password_row.grid_columnconfigure(0, weight=1)
        student_password_entry = ttk.Entry(student_password_row, textvariable=student_password_var, show="*", style="App.TEntry")
        student_password_entry.grid(row=0, column=0, sticky="ew")
        tk.Checkbutton(
            student_password_row,
            text="Show",
            variable=show_student_password_var,
            command=lambda: student_password_entry.configure(show="" if show_student_password_var.get() else "*"),
            bg=self.colors["surface_alt"],
            fg=self.colors["muted"],
            activebackground=self.colors["surface_alt"],
            activeforeground=self.colors["text"],
            selectcolor=self.colors["surface"],
            font=self.fonts["small"],
            padx=8,
        ).grid(row=0, column=1, sticky="e", padx=(10, 0))
        tk.Label(
            student_card,
            text="Use your assigned student password to access your attendance view.",
            bg=self.colors["surface_alt"],
            fg=self.colors["muted"],
            font=self.fonts["small"],
            wraplength=500,
            justify="left",
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(10, 0))

        def submit_login() -> None:
            normalized_username = username_var.get().strip()
            identity_key = f"staff:{normalized_username.lower()}"
            if normalized_username == self.repository.ADMIN_USERNAME and not self.repository.is_root_account_configured():
                messagebox.showwarning(
                    "Root Setup Required",
                    "Initialize the root account first before signing in as admin.",
                    parent=dialog,
                )
                return
            cooldown_remaining = self.repository.get_login_cooldown_remaining(identity_key)
            if cooldown_remaining > 0:
                message = f"Too many failed attempts. Try again in {self.format_cooldown_seconds(cooldown_remaining)}."
                messagebox.showerror("Login Blocked", message, parent=dialog)
                self.repository.log_audit_event(normalized_username or "unknown", "auth.login_blocked", "user", normalized_username, message)
                return
            try:
                account = self.repository.authenticate_user(
                    normalized_username,
                    password_var.get(),
                )
            except PasswordResetRequiredError:
                messagebox.showerror(
                    "Password Reset Required",
                    "This staff account used an older insecure password format and must be reset by root before it can sign in again.",
                    parent=dialog,
                )
                self.repository.log_audit_event(
                    normalized_username or "unknown",
                    "auth.login_reset_required",
                    "user",
                    normalized_username,
                    "Blocked insecure legacy account pending password reset.",
                )
                return
            if account is None:
                cooldown_remaining = self.repository.register_login_failure(identity_key)
                message = "Invalid username or password."
                if cooldown_remaining > 0:
                    message = (
                        "Too many failed attempts. "
                        f"Try again in {self.format_cooldown_seconds(cooldown_remaining)}."
                    )
                self.repository.log_audit_event(normalized_username or "unknown", "auth.login_failed", "user", normalized_username, message)
                messagebox.showerror("Login Failed", message, parent=dialog)
                return

            self.repository.clear_login_failures(identity_key)
            self.current_student_id = ""
            self.current_username = account.username
            self.current_user_scope = self.repository.get_user_scope(account.username)
            self.current_user_var.set(f"User: {account.username}")
            self.current_role_var.set(f"Role: {account.role}")
            self.enforce_current_user_scope()
            self.update_sidebar_permissions()
            self.apply_role_landing()
            self.log_activity(f"Signed in as {account.role}.")
            self.audit(
                "auth.login",
                "user",
                account.username,
                f"Role={account.role}",
            )
            result["ok"] = True
            dialog.destroy()

        def submit_student_login() -> None:
            normalized_student_id = student_id_var.get().strip()
            identity_key = f"student:{normalized_student_id.lower()}"
            cooldown_remaining = self.repository.get_login_cooldown_remaining(identity_key)
            if cooldown_remaining > 0:
                message = f"Too many failed attempts. Try again in {self.format_cooldown_seconds(cooldown_remaining)}."
                messagebox.showerror("Login Blocked", message, parent=dialog)
                self.repository.log_audit_event(normalized_student_id or "unknown", "auth.student_login_blocked", "student", normalized_student_id, message)
                return
            try:
                student_login = self.repository.authenticate_student(
                    normalized_student_id,
                    student_password_var.get(),
                )
            except PasswordResetRequiredError:
                messagebox.showerror(
                    "Password Reset Required",
                    "This student account used an older insecure password format and must be reset by staff before it can sign in again.",
                    parent=dialog,
                )
                self.repository.log_audit_event(
                    normalized_student_id or "unknown",
                    "auth.student_login_reset_required",
                    "student",
                    normalized_student_id,
                    "Blocked insecure legacy account pending password reset.",
                )
                return
            if student_login is None:
                cooldown_remaining = self.repository.register_login_failure(identity_key)
                message = "Invalid TNU ID or password."
                if cooldown_remaining > 0:
                    message = (
                        "Too many failed attempts. "
                        f"Try again in {self.format_cooldown_seconds(cooldown_remaining)}."
                    )
                self.repository.log_audit_event(normalized_student_id or "unknown", "auth.student_login_failed", "student", normalized_student_id, message)
                messagebox.showerror("Login Failed", message, parent=dialog)
                return

            self.repository.clear_login_failures(identity_key)
            self.current_student_id = student_login.student_id
            self.current_username = student_login.student_id
            self.current_user_scope = {"class_name": student_login.class_name, "subject_name": ""}
            self.current_user_var.set(f"Student: {student_login.name}")
            self.current_role_var.set("Role: viewer")
            self.current_class_var.set(student_login.class_name)
            self.current_section_var.set(student_login.section_name)
            self.current_academic_year_var.set(student_login.academic_year)
            self.current_semester_var.set(student_login.semester)
            self.refresh_context_options()
            self.update_sidebar_permissions()
            self.apply_role_landing()
            self.log_activity("Student viewer signed in.")
            self.audit(
                "auth.student_login",
                "student",
                student_login.student_id,
                "Student viewer session started.",
            )
            result["ok"] = True
            dialog.destroy()

        staff_actions = tk.Frame(staff_card, bg=self.colors["surface_alt"])
        staff_actions.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        staff_actions.grid_columnconfigure(0, weight=1)
        tk.Label(
            staff_actions,
            text="Admin tip: sign in here to train models, manage users, and run attendance.",
            bg=self.colors["surface_alt"],
            fg=self.colors["muted"],
            font=self.fonts["small"],
            justify="left",
            wraplength=500,
        ).grid(row=0, column=0, sticky="w")
        if root_setup_required and bootstrap_mode_enabled:
            self._make_button(staff_actions, "Initialize Root", open_root_setup_dialog, variant="warning", padx=14, pady=6).grid(
                row=0,
                column=1,
                sticky="e",
                padx=(14, 0),
            )

        student_actions = tk.Frame(student_card, bg=self.colors["surface_alt"])
        student_actions.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        student_actions.grid_columnconfigure(0, weight=1)
        tk.Label(
            student_actions,
            text="Student access is limited to viewer-safe screens after sign-in.",
            bg=self.colors["surface_alt"],
            fg=self.colors["muted"],
            font=self.fonts["small"],
            justify="left",
            wraplength=500,
        ).grid(row=0, column=0, sticky="w")

        footer = tk.Frame(auth_panel, bg=self.colors["surface"])
        footer.grid(row=3, column=0, sticky="ew", pady=(16, 0))
        footer.grid_columnconfigure(0, weight=1)
        tk.Label(
            footer,
            text="Press Enter inside the current form to continue.",
            bg=self.colors["surface"],
            fg=self.colors["muted"],
            font=self.fonts["small"],
            justify="left",
        ).grid(row=0, column=0, sticky="w")
        footer_button_row = tk.Frame(footer, bg=self.colors["surface"])
        footer_button_row.grid(row=0, column=1, sticky="e")

        def submit_active_login() -> None:
            current_tab = notebook.select()
            if current_tab == str(student_tab):
                submit_student_login()
            else:
                submit_login()

        active_login_button = self._make_button(footer_button_row, "Staff Login", submit_active_login, variant="primary", padx=16, pady=7)
        active_login_button.pack(side="right", padx=(0, 8))
        self._make_button(footer_button_row, "Exit", dialog.destroy, variant="secondary", padx=16, pady=7).pack(side="right")

        def refresh_footer_action(_event=None) -> None:
            current_tab = notebook.select()
            if current_tab == str(student_tab):
                active_login_button.configure(text="Student Login", bg=self.colors["accent"], fg=self.colors["accent_text"], activebackground="#e4c77f", activeforeground=self.colors["accent_text"])
            else:
                active_login_button.configure(text="Staff Login", bg=self.colors["primary"], fg=self.colors["sidebar_text"], activebackground=self.colors["primary_hover"], activeforeground=self.colors["sidebar_text"])

        notebook.bind("<<NotebookTabChanged>>", refresh_footer_action)
        refresh_footer_action()

        username_entry.focus_set()
        username_entry.bind("<Return>", lambda _event: submit_login())
        password_entry.bind("<Return>", lambda _event: submit_login())
        student_id_entry.bind("<Return>", lambda _event: submit_student_login())
        student_password_entry.bind("<Return>", lambda _event: submit_student_login())
        dialog.wait_window()
        if result["ok"]:
            self.root.deiconify()
        return result["ok"]

    def register_student(self) -> None:
        """Collect student details, then capture face samples for that student."""
        if not self.require_roles(EDIT_ROLES, "register students"):
            return
        student_payload = self.open_register_student_dialog()
        if student_payload is None:
            return
        name = student_payload["name"]
        student_id = student_payload["student_id"]
        student_password = student_payload.get("password") or None

        try:
            record = self._ensure_person_record(
                name=name.strip(),
                student_id=student_id.strip(),
                password=student_password or None,
                section_name=self.current_section_var.get().strip(),
                academic_year=self.current_academic_year_var.get().strip(),
                semester=self.current_semester_var.get().strip(),
            )
            image_count, face_signature = self._capture_student_faces(record)
        except Exception as exc:
            self.report_exception("Registration Failed", exc, parent=self.root)
            self.status_var.set(f"Registration failed: {exc}")
            return
        self.repository.update_student_face_signature(record.student_id, face_signature)
        stored_student = self.repository.get_student_by_student_id(record.student_id)
        if stored_student is not None:
            self._write_student_details_snapshot(stored_student)

        messagebox.showinfo(
            "Registration Complete",
            (
                f"Saved {image_count} face samples for {record.name} ({record.student_id}).\n"
                f"Face structure value: {face_signature}"
            ),
            parent=self.root,
        )
        self.status_var.set(
            f"Registered {record.name} with {image_count} samples. Train the model next."
        )
        self.log_activity(f"Registered student {record.name} ({record.student_id}) with {image_count} samples.")
        self.audit(
            "student.register",
            "student",
            record.student_id,
            f"Registered {record.name} in class {self.current_class_var.get()} with {image_count} samples.",
        )
        self.repository.mark_model_dirty(
            self.current_class_var.get(),
            f"Student data changed after registering {record.name}. Retraining is required.",
        )
        if self.get_active_model_path().exists():
            messagebox.showinfo(
                "Retrain Required",
                "Student data changed. Retrain this class model before starting attendance again.",
                parent=self.root,
            )
        self.refresh_dashboard()

    def open_register_student_dialog(self) -> dict[str, str] | None:
        """Show a simple registration form for the active academic context."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Register Student")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        self._style_dialog_shell(dialog)
        self.center_window(dialog, 620, 410)

        name_var = tk.StringVar()
        student_id_var = tk.StringVar()
        password_var = tk.StringVar()
        confirm_password_var = tk.StringVar()
        result: dict[str, dict[str, str] | None] = {"value": None}

        frame = tk.Frame(dialog, bg="white", padx=18, pady=18)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(1, weight=1)

        tk.Label(frame, text="Register Student", bg="white", fg="#10233d", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )
        tk.Label(
            frame,
            text=(
                f"Class: {self.current_class_var.get()}   |   Section: {self.current_section_var.get() or '-'}   |   "
                f"Year: {self.current_academic_year_var.get() or '-'}   |   "
                f"Semester: {self.current_semester_var.get() or '-'}"
            ),
            bg="white",
            fg="#5b7186",
            font=("Segoe UI", 10),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 12))

        tk.Label(frame, text="Student Name", bg="white", font=("Segoe UI", 10)).grid(row=2, column=0, sticky="w", pady=6)
        name_entry = ttk.Entry(frame, textvariable=name_var)
        name_entry.grid(row=2, column=1, sticky="ew", pady=6)

        tk.Label(frame, text="Student ID", bg="white", font=("Segoe UI", 10)).grid(row=3, column=0, sticky="w", pady=6)
        student_id_entry = ttk.Entry(frame, textvariable=student_id_var)
        student_id_entry.grid(row=3, column=1, sticky="ew", pady=6)
        tk.Label(frame, text="Initial Password", bg="white", font=("Segoe UI", 10)).grid(row=4, column=0, sticky="w", pady=6)
        password_entry = ttk.Entry(frame, textvariable=password_var, show="*")
        password_entry.grid(row=4, column=1, sticky="ew", pady=6)
        tk.Label(frame, text="Confirm Password", bg="white", font=("Segoe UI", 10)).grid(row=5, column=0, sticky="w", pady=6)
        confirm_password_entry = ttk.Entry(frame, textvariable=confirm_password_var, show="*")
        confirm_password_entry.grid(row=5, column=1, sticky="ew", pady=6)
        tk.Label(
            frame,
            text="Set the student's initial password here. For security, the app will not auto-generate or save plaintext passwords.",
            bg="white",
            fg="#5b7186",
            font=("Segoe UI", 9),
            wraplength=420,
            justify="left",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(10, 0))

        def submit_registration() -> None:
            name = name_var.get().strip()
            student_id = student_id_var.get().strip()
            password = password_var.get()
            if not name or not student_id or not password:
                messagebox.showwarning(
                    "Missing Details",
                    "Enter student name, student ID, and an initial password.",
                    parent=dialog,
                )
                return
            if password != confirm_password_var.get():
                messagebox.showerror("Password Mismatch", "The two password fields must match.", parent=dialog)
                return
            try:
                self.repository._validate_new_password(password)
            except ValueError as err:
                messagebox.showerror("Password Requirement", str(err), parent=dialog)
                return
            result["value"] = {
                "name": name,
                "student_id": student_id,
                "password": password,
            }
            dialog.destroy()

        button_row = tk.Frame(frame, bg="white")
        button_row.grid(row=7, column=0, columnspan=2, sticky="e", pady=(20, 0))
        self._make_button(button_row, "Cancel", dialog.destroy, variant="secondary", padx=14, pady=6).pack(side="right")
        self._make_button(button_row, "Continue", submit_registration, variant="primary", padx=14, pady=6).pack(side="right", padx=(0, 8))

        name_entry.focus_set()
        student_id_entry.bind("<Return>", lambda _event: submit_registration())
        password_entry.bind("<Return>", lambda _event: submit_registration())
        confirm_password_entry.bind("<Return>", lambda _event: submit_registration())
        dialog.wait_window()
        return result["value"]

    def bulk_import_students(self) -> None:
        """Import many students at once from a CSV file into the active class."""
        if not self.require_roles(EDIT_ROLES, "import students"):
            return
        csv_path = filedialog.askopenfilename(
            parent=self.root,
            title="Import Students from CSV",
            filetypes=[("CSV Files", "*.csv")],
        )
        if not csv_path:
            return

        try:
            result = self.repository.import_students_from_csv(
                Path(csv_path),
                self.current_class_var.get(),
            )
        except Exception as exc:
            self.report_exception("Import Failed", exc, parent=self.root)
            self.status_var.set(f"Bulk import failed: {exc}")
            return

        summary_lines = [
            f"Imported: {result['imported']}",
            f"Updated: {result['updated']}",
            f"Skipped: {result['skipped']}",
        ]
        errors = result.get("errors", [])
        generated_credentials = result.get("generated_credentials", [])
        if errors:
            summary_lines.append("")
            summary_lines.append("Notes:")
            summary_lines.extend(errors[:8])
            if len(errors) > 8:
                summary_lines.append(f"...and {len(errors) - 8} more")
        if generated_credentials:
            summary_lines.append("")
            summary_lines.append(
                "Secure import mode is enabled. Provide explicit passwords in the CSV for new students."
            )

        self.status_var.set(
            f"Bulk import complete. Imported {result['imported']} student(s), updated {result['updated']}."
        )
        self.log_activity(
            f"Bulk student import completed from {Path(csv_path).name} into class {self.current_class_var.get()}."
        )
        self.audit(
            "student.import",
            "student",
            self.current_class_var.get(),
            f"Imported {result['imported']} and updated {result['updated']} students from {Path(csv_path).name}.",
        )
        if result["imported"] or result["updated"]:
            self.repository.mark_model_dirty(
                self.current_class_var.get(),
                "Student roster changed after CSV import. Retraining is required.",
            )
        self.refresh_dashboard()
        messagebox.showinfo("Bulk Import Complete", "\n".join(summary_lines), parent=self.root)

    def train_model(self) -> None:
        """Train the recognizer using only students from the active class."""
        if not self.require_roles(EDIT_ROLES, "train models"):
            return
        faces, labels = self._load_training_data()
        model_path = self.get_active_model_path()
        if not faces:
            messagebox.showwarning(
                "No Data",
                f"No face samples were found for class {self.current_class_var.get()}. Register at least one student first.",
                parent=self.root,
            )
            self.status_var.set("Training skipped because no samples are available.")
            return

        recognizer = SimpleFaceRecognizer()
        recognizer.train(faces, labels)
        recognizer.save(model_path)
        self.repository.clear_model_dirty(self.current_class_var.get())

        self.status_var.set(f"Training complete. Model saved to {model_path.name}.")
        messagebox.showinfo(
            "Training Complete",
            f"Model trained for class {self.current_class_var.get()} with {len(faces)} face samples across {len(set(labels))} student(s).",
            parent=self.root,
        )
        self.log_activity(
            f"Model trained with {len(faces)} samples for {len(set(labels))} students."
        )
        self.audit(
            "model.train",
            "model",
            self.current_class_var.get(),
            f"Trained model with {len(faces)} samples across {len(set(labels))} students.",
        )
        self.refresh_dashboard()

    def start_attendance(self) -> None:
        """Start the live camera loop and mark attendance for recognized faces."""
        if not self.require_roles(EDIT_ROLES, "start attendance"):
            return
        self.apply_current_timetable_context(show_message=False)
        model_path = self.get_active_model_path()
        if not model_path.exists():
            messagebox.showwarning(
                "Model Missing",
                "Train the model before starting attendance.",
                parent=self.root,
            )
            self.status_var.set("Attendance not started because the trained model is missing.")
            return
        model_state = self.repository.get_model_state(self.current_class_var.get())
        if model_state["is_dirty"]:
            messagebox.showwarning(
                "Retrain Required",
                str(model_state["dirty_reason"]) or "Student face data changed. Retrain the model first.",
                parent=self.root,
            )
            self.status_var.set("Attendance not started because the class model is outdated.")
            return

        label_map = self._load_label_map()
        if not label_map:
            messagebox.showwarning(
                "No Students",
                f"No students are available in class {self.current_class_var.get()}.",
                parent=self.root,
            )
            return
        recognizer = SimpleFaceRecognizer.load(model_path)
        if recognizer.needs_model_upgrade():
            faces, labels = self._load_training_data()
            if not faces:
                messagebox.showwarning(
                    "Model Upgrade Required",
                    "The face model uses an older recognition format, but no training images were found to rebuild it.",
                    parent=self.root,
                )
                self.status_var.set("Attendance not started because the saved face model is outdated.")
                return
            recognizer = SimpleFaceRecognizer()
            recognizer.train(faces, labels)
            recognizer.save(model_path)
            self.repository.clear_model_dirty(self.current_class_var.get())
            self.status_var.set(
                f"Upgraded face model to recognizer v{CURRENT_FEATURE_VERSION}. Attendance live. Press Q to stop the camera window."
            )
        liveness_tracker = LivenessTracker(min_frames=5, min_movement=6.0)

        marked_students: set[str] = set()
        face_tracks: dict[str, dict[str, object]] = {}
        detection_scale = 0.6
        frame_counter = 0

        try:
            cap = start_camera(self.selected_camera_index)
        except Exception as exc:
            self.report_exception("Camera Error", exc, parent=self.root)
            self.status_var.set(f"Camera error: {exc}")
            return

        self.status_var.set("Attendance live. Press Q to stop the camera window.")
        self.audit(
            "attendance.session_start",
            "attendance",
            self.current_class_var.get(),
            f"Live attendance started for {self.current_class_var.get()} - {self.current_subject_var.get()}.",
        )

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
    
                frame_counter += 1
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                detection_gray = gray
                if detection_scale < 1.0:
                    detection_gray = cv2.resize(gray, None, fx=detection_scale, fy=detection_scale)
                faces = self.face_detector.detectMultiScale(
                    detection_gray,
                    scaleFactor=1.1,
                    minNeighbors=4,
                    minSize=(60, 60),
                )
    
                for (raw_x, raw_y, raw_w, raw_h) in faces:
                    if detection_scale < 1.0:
                        x = int(raw_x / detection_scale)
                        y = int(raw_y / detection_scale)
                        w = int(raw_w / detection_scale)
                        h = int(raw_h / detection_scale)
                    else:
                        x, y, w, h = int(raw_x), int(raw_y), int(raw_w), int(raw_h)
                    face_crop = gray[y : y + h, x : x + w]
                    if w < 60 or h < 60:
                        continue
                    track_key = f"{(x + w // 2) // 120}:{(y + h // 2) // 120}"
                    track_state = face_tracks.setdefault(
                        track_key,
                        {
                            "label_id": None,
                            "student_id": "",
                            "stable_frames": 0,
                            "locked_until": 0.0,
                            "last_eval_at": 0.0,
                            "last_result": None,
                        },
                    )
                    now_monotonic = time.monotonic()
    
                    try:
                        prepared_face = preprocess_face(face_crop)
                    except ValueError:
                        continue
    
                    cached_result = track_state.get("last_result")
                    should_refresh_prediction = (
                        cached_result is None
                        or (now_monotonic - float(track_state.get("last_eval_at", 0.0))) >= 0.08
                        or int(track_state.get("stable_frames", 0)) < 3
                        or (frame_counter % 3 == 0)
                    )
                    if should_refresh_prediction:
                        evaluation = recognizer.evaluate_prediction(prepared_face)
                        label_id = int(evaluation["label_id"])
                        confidence = float(evaluation["confidence"])
                        margin = float(evaluation["margin"])
                        track_state["last_eval_at"] = now_monotonic
                        track_state["last_result"] = evaluation
                    else:
                        evaluation = cached_result
                        label_id = int(evaluation["label_id"])
                        confidence = float(evaluation["confidence"])
                        margin = float(evaluation["margin"])
    
                    acceptance_threshold = max(0.85, recognizer.get_acceptance_threshold(label_id))
    
                    if self._is_prediction_acceptable(recognizer, evaluation) and str(label_id) in label_map:
                        record = label_map[str(label_id)]
                        locked_student_id = str(track_state.get("student_id", ""))
                        locked_until = float(track_state.get("locked_until", 0.0))
                        if locked_student_id and locked_until > now_monotonic and locked_student_id != record["student_id"]:
                            display_lines = [
                                "Identity: stabilizing",
                                "Status: face lock active",
                            ]
                            color = (0, 165, 255)
                            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
                            text_y = max(y - 38, 20)
                            for index, line in enumerate(display_lines):
                                cv2.putText(
                                    frame,
                                    line,
                                    (x, text_y + (index * 18)),
                                    cv2.FONT_HERSHEY_SIMPLEX,
                                    0.55,
                                    color,
                                    2,
                                )
                            continue
    
                        if track_state.get("label_id") == label_id:
                            track_state["stable_frames"] = int(track_state.get("stable_frames", 0)) + 1
                        else:
                            track_state["label_id"] = label_id
                            track_state["student_id"] = record["student_id"]
                            track_state["stable_frames"] = 1
    
                        liveness_passed, liveness_state = liveness_tracker.update(
                            record["student_id"],
                            (x, y, w, h),
                        )
    
                        display_lines = [
                            f"Name: {record['name']}",
                            f"Stable: {track_state['stable_frames']}/3",
                        ]
                        color = (0, 180, 0)
    
                        if int(track_state["stable_frames"]) < 3:
                            display_lines.append("Identity: stabilizing")
                            color = (0, 165, 255)
                        elif not liveness_passed:
                            display_lines.append(
                                f"Liveness: verifying {liveness_state.frames_seen}/{liveness_tracker.min_frames}"
                            )
                            color = (0, 165, 255)
                        elif record["student_id"] not in marked_students:
                            display_lines.append("Liveness: passed")
                            was_added = self.mark_attendance_live(
                                record["student_id"],
                                record["name"],
                            )
                            marked_students.add(record["student_id"])
                            track_state["locked_until"] = now_monotonic + 4.0
                            if not was_added:
                                self.status_var.set(f"Duplicate punch detected for {record['name']}.")
                                self.play_duplicate_punch_alert(record["student_id"])
                                display_lines.append("Attendance: duplicate punch")
                        else:
                            track_state["locked_until"] = now_monotonic + 4.0
                            self.play_duplicate_punch_alert(record["student_id"])
                            display_lines.append("Attendance: duplicate punch")
                    else:
                        track_state["label_id"] = None
                        track_state["student_id"] = ""
                        track_state["stable_frames"] = 0
                        display_lines = [
                            "Name: Unknown",
                            "UID: N/A",
                            f"Accuracy: {confidence:.2%}",
                            f"Need: {acceptance_threshold:.2%}",
                            f"Match gap: {margin:.2%}",
                            "Status: unknown face",
                        ]
                        color = (0, 0, 255)
    
                    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
                    text_y = max(y - 56, 20)
                    for index, line in enumerate(display_lines):
                        cv2.putText(
                            frame,
                            line,
                            (x, text_y + (index * 18)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.55,
                            color,
                            2,
                        )
    
                cv2.imshow("Attendance Camera - Press Q to quit", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
                try:
                    self.root.update_idletasks()
                    self.root.update()
                except tk.TclError:
                    break
    
        finally:
            cap.release()
            cv2.destroyAllWindows()
        self.status_var.set("Attendance session ended.")
        self.log_activity("Attendance session stopped.")
        self.audit(
            "attendance.session_end",
            "attendance",
            self.current_class_var.get(),
            f"Live attendance ended for {self.current_class_var.get()} - {self.current_subject_var.get()}.",
        )
        self.refresh_dashboard()

    def show_attendance_path(self) -> None:
        """Show where the database file and exported reports are stored."""
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
        STUDENTS_DIR.mkdir(parents=True, exist_ok=True)
        harden_permissions(EXPORTS_DIR, is_dir=True)
        harden_permissions(PHOTOS_DIR, is_dir=True)
        harden_permissions(STUDENTS_DIR, is_dir=True)
        messagebox.showinfo(
            "Data Location",
            (
                f"Runtime folder:\n{RUNTIME_DIR}\n\n"
                f"Database file:\n{DB_PATH}\n\n"
                f"Exports folder:\n{EXPORTS_DIR}\n\n"
                f"Student photos folder:\n{PHOTOS_DIR}\n\n"
                f"Student details folder:\n{STUDENTS_DIR}\n\n"
                f"Backups folder:\n{BACKUPS_DIR}"
            ),
            parent=self.root,
        )
        self.status_var.set(f"Runtime folder: {RUNTIME_DIR}")
        self.log_activity("Data location viewed.")

    def _ensure_person_record(
        self,
        name: str,
        student_id: str,
        password: str | None = None,
        *,
        section_name: str = "",
        batch_name: str = "",
        academic_year: str = "",
        semester: str = "",
        guardian_name: str = "",
        status: str = "Active",
    ) -> PersonRecord:
        """Create or update one student record in the shared database."""
        existing = self.repository.get_student_by_student_id(student_id)
        if existing is not None:
            updated = self.repository.upsert_student(
                existing.label_id,
                student_id,
                name or existing.name,
                self.current_class_var.get(),
                password=password,
                section_name=section_name or existing.section_name,
                batch_name=batch_name or existing.batch_name,
                academic_year=academic_year or existing.academic_year,
                semester=semester or existing.semester,
                guardian_name=guardian_name or existing.guardian_name,
                status=status or existing.status,
            )
            return PersonRecord(updated.label_id, updated.student_id, updated.name)

        next_label_id = self.repository.get_next_label_id()
        student = self.repository.upsert_student(
            next_label_id,
            student_id,
            name,
            self.current_class_var.get(),
            password=password,
            section_name=section_name,
            batch_name=batch_name,
            academic_year=academic_year,
            semester=semester,
            guardian_name=guardian_name,
            status=status,
        )
        return PersonRecord(student.label_id, student.student_id, student.name)

    def _classify_capture_bucket(
        self,
        frame_shape: tuple[int, ...],
        face_box: tuple[int, int, int, int],
        processed_face: object,
    ) -> tuple[str | None, str]:
        """Classify head orientation from face geometry features inside the crop."""
        geometry = detect_facial_geometry(processed_face)
        face_scale = float(geometry[2])
        eyes_detected = float(geometry[8]) >= 1.0

        if face_scale < 0.12:
            return None, "Move a little closer to the camera"

        frame_height, frame_width = frame_shape[:2]
        x, y, w, h = face_box
        center_x = (x + (w / 2.0)) / max(1.0, frame_width)
        center_y = (y + (h / 2.0)) / max(1.0, frame_height)
        horizontal_offset = center_x - 0.5
        vertical_offset = center_y - 0.5

        if eyes_detected:
            eye_mid_x = float(geometry[0])
            eye_mid_y = float(geometry[1])
            # Check internal head orientation from eye positions within face crop
            if eye_mid_x < 0.44:
                return "left", "Turn head slightly left"
            if eye_mid_x > 0.56:
                return "right", "Turn head slightly right"
            if eye_mid_y < 0.35:
                return "up", "Tilt head slightly up"
            if eye_mid_y > 0.45:
                return "down", "Tilt head slightly down"

        # Frame position fallback for natural movement
        if abs(horizontal_offset) >= 0.08 or abs(vertical_offset) >= 0.08:
            if abs(horizontal_offset) >= abs(vertical_offset):
                if horizontal_offset < 0:
                    return "left", "Turn head slightly left"
                return "right", "Turn head slightly right"
            if vertical_offset < 0:
                return "up", "Tilt head slightly up"
            return "down", "Tilt head slightly down"

        return "front", "Look straight at the camera"

    def _capture_student_faces(self, record: PersonRecord, sample_target: int | None = None) -> tuple[int, str]:
        """Capture a balanced face set across front/left/right/up/down views with non-blocking flow."""
        person_dir = DATASET_DIR / f"{record.student_id}_{record.label_id}"
        photo_dir = PHOTOS_DIR / self._student_asset_key(record.student_id, record.label_id)
        person_dir.mkdir(parents=True, exist_ok=True)
        photo_dir.mkdir(parents=True, exist_ok=True)
        harden_permissions(person_dir, is_dir=True)
        harden_permissions(photo_dir, is_dir=True)
        sample_target = sample_target or sum(CAPTURE_BUCKET_TARGETS.values())

        bucket_order = ["front", "left", "right", "up", "down"]
        bucket_prompts = {
            "front": "Look straight at the camera",
            "left": "Turn your head slightly left",
            "right": "Turn your head slightly right",
            "up": "Tilt your head slightly up",
            "down": "Tilt your head slightly down",
        }

        cap = start_camera(self.selected_camera_index)
        saved = 0
        bucket_counts = {bucket: 0 for bucket in CAPTURE_BUCKET_TARGETS}
        last_saved_at = 0.0
        saved_faces: list[object] = []
        detection_scale = 0.6

        try:
            while saved < sample_target:
                ret, frame = cap.read()
                if not ret:
                    break

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                detection_gray = gray
                if detection_scale < 1.0:
                    detection_gray = cv2.resize(gray, None, fx=detection_scale, fy=detection_scale)
                faces = self.face_detector.detectMultiScale(
                    detection_gray,
                    scaleFactor=1.1,
                    minNeighbors=4,
                    minSize=(60, 60),
                )

                active_bucket = next((b for b in bucket_order if bucket_counts[b] < CAPTURE_BUCKET_TARGETS[b]), None)
                active_prompt = bucket_prompts.get(active_bucket, "Hold steady") if active_bucket else "Complete"

                for (raw_x, raw_y, raw_w, raw_h) in faces:
                    if detection_scale < 1.0:
                        x = int(raw_x / detection_scale)
                        y = int(raw_y / detection_scale)
                        w = int(raw_w / detection_scale)
                        h = int(raw_h / detection_scale)
                    else:
                        x, y, w, h = int(raw_x), int(raw_y), int(raw_w), int(raw_h)
                    face_crop = gray[y : y + h, x : x + w]
                    if w < 60 or h < 60:
                        continue

                    try:
                        resized = preprocess_face(face_crop)
                    except ValueError:
                        continue

                    detected_bucket, _ = self._classify_capture_bucket(frame.shape, (x, y, w, h), resized)
                    now_monotonic = time.monotonic()
                    color = (0, 165, 255)
                    guidance = active_prompt

                    target_bucket = None
                    if detected_bucket is not None and bucket_counts[detected_bucket] < CAPTURE_BUCKET_TARGETS[detected_bucket]:
                        target_bucket = detected_bucket
                    elif active_bucket is not None and bucket_counts[active_bucket] < CAPTURE_BUCKET_TARGETS[active_bucket]:
                        target_bucket = active_bucket

                    if target_bucket is not None and (now_monotonic - last_saved_at >= 0.08):
                        image_path = person_dir / f"{record.label_id}_{saved + 1}.jpg"
                        cv2.imwrite(str(image_path), resized)
                        harden_permissions(image_path, is_dir=False)
                        if target_bucket == "front":
                            profile_path = photo_dir / PROFILE_PHOTO_NAME
                            if not profile_path.exists():
                                cv2.imwrite(str(profile_path), resized)
                                harden_permissions(profile_path, is_dir=False)
                        saved += 1
                        bucket_counts[target_bucket] += 1
                        last_saved_at = now_monotonic
                        saved_faces.append(resized)
                        guidance = f"Saved {target_bucket.title()} ({bucket_counts[target_bucket]}/{CAPTURE_BUCKET_TARGETS[target_bucket]})"
                        color = (0, 220, 0)

                    cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
                    cv2.putText(
                        frame,
                        f"Samples captured: {saved}/{sample_target}",
                        (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        (255, 255, 255),
                        2,
                    )
                    progress_text = "  ".join(
                        f"{bucket.title()} {bucket_counts[bucket]}/{CAPTURE_BUCKET_TARGETS[bucket]}"
                        for bucket in bucket_order
                    )
                    cv2.putText(
                        frame,
                        progress_text,
                        (10, 58),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.5,
                        (255, 255, 255),
                        2,
                    )
                    cv2.putText(
                        frame,
                        guidance,
                        (10, 84),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.65,
                        color,
                        2,
                    )

                    bar_x = 10
                    bar_y = frame.shape[0] - 22
                    bar_w = frame.shape[1] - 20
                    bar_h = 12
                    cv2.rectangle(frame, (bar_x, bar_y), (bar_x + bar_w, bar_y + bar_h), (50, 50, 50), -1)
                    fill_w = int(bar_w * min(1.0, saved / float(max(1, sample_target))))
                    cv2.rectangle(frame, (bar_x, bar_y), (bar_x + fill_w, bar_y + bar_h), (0, 200, 0), -1)

                    if saved >= sample_target:
                        break

                cv2.imshow("Register Face - Press Q to cancel", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break

        finally:
            cap.release()
            cv2.destroyAllWindows()

        if saved == 0:
            raise RuntimeError("No face samples were captured.")
        face_signature = compute_face_structure_signature(saved_faces)
        return saved, face_signature

    def _is_prediction_acceptable(
        self,
        recognizer: SimpleFaceRecognizer,
        evaluation: dict[str, object],
    ) -> bool:
        """Balance unknown-face rejection against real-world recognition variability."""
        label_id = int(evaluation["label_id"])
        confidence = float(evaluation["confidence"])
        margin = float(evaluation["margin"])
        support_score = float(evaluation["support_score"])
        geometry_score = float(evaluation["geometry_score"])
        label_count = int(evaluation["label_count"])
        sample_count = int(evaluation["sample_count"])
        probe_geometry = evaluation["probe_geometry"]
        eyes_detected = float(probe_geometry[8]) >= 1.0

        is_v3 = getattr(recognizer, "feature_version", 2) >= 3
        if is_v3:
            base_threshold = max(0.84, recognizer.get_acceptance_threshold(label_id) - 0.02)
            if not eyes_detected and confidence < max(base_threshold + 0.02, 0.88):
                return False

            if label_count <= 1:
                single_label_threshold = max(0.86, base_threshold)
                return (
                    confidence >= single_label_threshold
                    and support_score >= 0.87
                    and (geometry_score == 0.0 or geometry_score >= 0.10)
                    and sample_count >= 5
                )

            stricter_threshold = base_threshold + 0.01 if sample_count < 8 else base_threshold
            if (
                confidence >= max(0.85, stricter_threshold - 0.02)
                and margin >= 0.025
                and support_score >= 0.90
                and (geometry_score == 0.0 or geometry_score >= 0.15)
            ):
                return True
            return (
                confidence >= stricter_threshold
                and margin >= 0.02
                and support_score >= 0.86
                and (geometry_score == 0.0 or geometry_score >= 0.10)
            )
        else:
            base_threshold = max(0.89, recognizer.get_acceptance_threshold(label_id) - 0.015)
            if not eyes_detected and confidence < max(base_threshold + 0.02, 0.94):
                return False

            if label_count <= 1:
                single_label_threshold = max(0.915, base_threshold)
                return (
                    confidence >= single_label_threshold
                    and support_score >= 0.93
                    and (geometry_score == 0.0 or geometry_score >= 0.10)
                    and sample_count >= 5
                )

            stricter_threshold = base_threshold + 0.01 if sample_count < 8 else base_threshold
            if (
                confidence >= max(0.89, stricter_threshold - 0.02)
                and margin >= 0.025
                and support_score >= 0.985
                and (geometry_score == 0.0 or geometry_score >= 0.18)
            ):
                return True
            return (
                confidence >= stricter_threshold
                and margin >= 0.02
                and support_score >= 0.93
                and (geometry_score == 0.0 or geometry_score >= 0.10)
            )

    def _load_label_map(self) -> dict[str, dict[str, str]]:
        """Build the recognizer label map from the active class roster."""
        return {
            str(student.label_id): {
                "name": student.name,
                "student_id": student.student_id,
            }
            for student in self.repository.list_students(self.current_class_var.get())
        }

    def _get_student_profile_photo_path(self, student: Student) -> Path | None:
        """Return the preferred profile photo path for one student."""
        photo_dir = self._get_student_photo_dir(student)
        profile_path = photo_dir / PROFILE_PHOTO_NAME
        if profile_path.exists():
            return profile_path

        legacy_dir = DATASET_DIR / f"{student.student_id}_{student.label_id}"
        legacy_profile_path = legacy_dir / PROFILE_PHOTO_NAME
        if legacy_profile_path.exists():
            return legacy_profile_path
        if not legacy_dir.exists():
            return None

        jpg_files = sorted(path for path in legacy_dir.glob("*.jpg") if path.is_file())
        return jpg_files[0] if jpg_files else None

    def _load_student_profile_photo(self, student: Student) -> tk.PhotoImage | None:
        """Load the preferred profile photo into a Tk-compatible image."""
        photo_path = self._get_student_profile_photo_path(student)
        if photo_path is None:
            return None
        image = cv2.imread(str(photo_path))
        if image is None:
            return None
        resized = cv2.resize(image, (150, 180))
        success, encoded = cv2.imencode(".png", resized)
        if not success:
            return None
        return tk.PhotoImage(data=base64.b64encode(encoded.tobytes()).decode("ascii"))

    def _build_student_profile_info_lines(self, student: Student, sample_count: int) -> list[str]:
        """Return only the profile fields appropriate for the current viewer."""
        if self.is_student_viewer():
            return [
                f"Name: {student.name}",
                f"TNU ID: {student.student_id}",
                f"Class: {student.class_name}",
                f"Section: {student.section_name or '-'}",
                f"Academic Year: {student.academic_year or '-'}",
                f"Semester: {student.semester or '-'}",
                f"Status: {student.status}",
                f"Face Samples: {sample_count}",
            ]
        face_signature = self.repository.get_student_face_signature(student.student_id)
        return [
            f"Name: {student.name}",
            f"UID: {student.student_id}",
            f"Class: {student.class_name}",
            f"Recognizer Label ID: {student.label_id}",
            f"Face Structure Value: {face_signature or 'Not generated'}",
            f"Face Samples: {sample_count}",
            f"Profile Created: {student.created_at}",
            f"Last Updated: {student.updated_at}",
        ]

    def _load_training_data(self) -> tuple[list, list[int]]:
        """Load saved images for only the active class before training."""
        faces = []
        labels: list[int] = []
        active_label_ids = {
            int(label_id)
            for label_id in self._load_label_map().keys()
        }

        for person_dir in DATASET_DIR.iterdir():
            if not person_dir.is_dir():
                continue

            try:
                label_id = int(person_dir.name.rsplit("_", 1)[-1])
            except ValueError:
                continue
            if label_id not in active_label_ids:
                continue

            for image_path in person_dir.glob("*.jpg"):
                image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
                if image is None:
                    continue
                faces.append(image)
                labels.append(label_id)

        return faces, labels

    def refresh_context_options(self) -> None:
        """Keep the selected class/subject synced with the stored available options."""
        classes = self.get_allowed_classes() or self.repository.list_classes()
        if self.current_class_var.get() not in classes:
            self.current_class_var.set(classes[0])

        available_years = self.repository.list_academic_years()
        if self.current_academic_year_var.get() not in available_years:
            self.current_academic_year_var.set(available_years[0])

        sections = self.get_allowed_sections(self.current_class_var.get())
        if self.current_section_var.get() not in sections:
            self.current_section_var.set(sections[0] if sections else "")

        semesters = self.get_allowed_semesters(self.current_class_var.get())
        if self.current_semester_var.get() not in semesters:
            self.current_semester_var.set(semesters[0] if semesters else "")

        subjects = self.get_allowed_subjects(self.current_class_var.get())
        if self.current_subject_var.get() not in subjects:
            self.current_subject_var.set(subjects[0])
        self.enforce_current_user_scope()

    def get_active_model_path(self) -> Path:
        """Return the model file path for the currently selected class."""
        return MODEL_DIR / f"face_model_{slugify_name(self.current_class_var.get())}.npz"

    def open_context_switcher(self) -> None:
        """Open a simple selector to switch the active class and subject."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Switch Subject" if self.is_student_viewer() else "Switch Academic Context")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        self._style_dialog_shell(dialog)
        self.center_window(dialog, 520, 430)

        classes = [self.current_class_var.get()] if self.is_student_viewer() else self.get_allowed_classes()
        class_var = tk.StringVar(value=self.current_class_var.get())
        section_var = tk.StringVar(value=self.current_section_var.get())
        academic_year_var = tk.StringVar(value=self.current_academic_year_var.get())
        semester_var = tk.StringVar(value=self.current_semester_var.get())
        subject_var = tk.StringVar(value=self.current_subject_var.get())

        frame = tk.Frame(dialog, bg="white", padx=18, pady=18)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(1, weight=1)

        tk.Label(
            frame,
            text="Switch Subject" if self.is_student_viewer() else "Switch Context",
            bg="white",
            fg="#10233d",
            font=("Segoe UI", 14, "bold"),
        ).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )
        tk.Label(frame, text="Class", bg="white", font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", pady=6)
        class_box = ttk.Combobox(frame, textvariable=class_var, values=classes, state="readonly")
        class_box.grid(row=1, column=1, sticky="ew", pady=6)
        if self.is_student_viewer() or self.is_teacher_user():
            class_box.state(["disabled"])

        tk.Label(frame, text="Section", bg="white", font=("Segoe UI", 10)).grid(row=2, column=0, sticky="w", pady=6)
        section_box = ttk.Combobox(frame, textvariable=section_var, values=[], state="readonly")
        section_box.grid(row=2, column=1, sticky="ew", pady=6)
        tk.Label(frame, text="Academic Year", bg="white", font=("Segoe UI", 10)).grid(row=3, column=0, sticky="w", pady=6)
        year_box = ttk.Combobox(frame, textvariable=academic_year_var, values=self.repository.list_academic_years(), state="readonly")
        year_box.grid(row=3, column=1, sticky="ew", pady=6)
        tk.Label(frame, text="Semester", bg="white", font=("Segoe UI", 10)).grid(row=4, column=0, sticky="w", pady=6)
        semester_box = ttk.Combobox(frame, textvariable=semester_var, values=[], state="readonly")
        semester_box.grid(row=4, column=1, sticky="ew", pady=6)
        tk.Label(frame, text="Subject", bg="white", font=("Segoe UI", 10)).grid(row=5, column=0, sticky="w", pady=6)
        subject_box = ttk.Combobox(frame, textvariable=subject_var, values=[], state="readonly")
        subject_box.grid(row=5, column=1, sticky="ew", pady=6)
        if self.is_student_viewer():
            section_box.state(["disabled"])
            year_box.state(["disabled"])
            semester_box.state(["disabled"])

        def update_context_choices(_event=None) -> None:
            selected_class = class_var.get().strip() or "General"
            selected_year = academic_year_var.get().strip() or None
            new_semesters = self.repository.list_semesters(selected_class, selected_year) or [""]
            semester_box["values"] = new_semesters
            if semester_var.get() not in new_semesters:
                semester_var.set(new_semesters[0] if new_semesters else "")

            new_sections = self.get_allowed_sections(
                selected_class,
                academic_year=selected_year or "",
                semester=semester_var.get().strip(),
            )
            section_box["values"] = new_sections
            if section_var.get() not in new_sections:
                section_var.set(new_sections[0] if new_sections else "")

            new_subjects = self.get_allowed_subjects(
                selected_class,
                section_name=section_var.get().strip(),
                academic_year=selected_year or "",
                semester=semester_var.get().strip(),
            )
            subject_box["values"] = new_subjects
            if subject_var.get() not in new_subjects:
                subject_var.set(new_subjects[0] if new_subjects else "General")

        class_box.bind("<<ComboboxSelected>>", update_context_choices)
        section_box.bind("<<ComboboxSelected>>", update_context_choices)
        year_box.bind("<<ComboboxSelected>>", update_context_choices)
        semester_box.bind("<<ComboboxSelected>>", update_context_choices)

        tk.Label(
            frame,
            text=(
                "Choose one of your class subjects to view your attendance. Class, section, year, and semester are fixed to your own record."
                if self.is_student_viewer()
                else "Choose the teaching context first. New student registration will use this class, section, year, and semester automatically."
            ),
            bg="white",
            fg="#5b7186",
            font=("Segoe UI", 9),
            wraplength=450,
            justify="left",
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(12, 0))

        def apply_context() -> None:
            selected_class = class_var.get().strip() or "General"
            selected_section = section_var.get().strip()
            selected_year = academic_year_var.get().strip()
            selected_semester = semester_var.get().strip()
            selected_subject = subject_var.get().strip() or "General"
            self.current_class_var.set(selected_class)
            self.current_section_var.set(selected_section)
            self.current_academic_year_var.set(selected_year)
            self.current_semester_var.set(selected_semester)
            self.current_subject_var.set(selected_subject)
            dialog.destroy()
            self.status_var.set(
                f"Switched to {selected_class} / {selected_section or '-'} / {selected_subject}."
            )
            self.log_activity(
                f"Context switched to class {selected_class} and subject {selected_subject}."
            )
            self.refresh_dashboard()

        button_row = tk.Frame(frame, bg="white")
        button_row.grid(row=7, column=0, columnspan=2, sticky="e", pady=(20, 0))
        if not self.is_student_viewer():
            self._make_button(
                button_row,
                "Manage",
                lambda: [dialog.destroy(), self.open_class_subject_manager()],
                variant="secondary",
                padx=14,
                pady=6,
            ).pack(side="left")
        update_context_choices()
        self._make_button(button_row, "Cancel", dialog.destroy, variant="secondary", padx=14, pady=6).pack(side="right")
        self._make_button(button_row, "Apply", apply_context, variant="primary", padx=14, pady=6).pack(side="right", padx=(0, 8))

    def open_institute_setup_dialog(self) -> None:
        """Manage the institute-level configuration used for academic defaults."""
        if not self.require_roles(("root",), "manage institute setup"):
            return
        settings = self.repository.get_institute_settings()
        dialog = tk.Toplevel(self.root)
        dialog.title("Institute Setup")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(True, True)
        self._style_dialog_shell(dialog)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        min_width, min_height = clamp_window_size_to_screen(560, 380, screen_width, screen_height)
        dialog.minsize(min_width, min_height)
        self.center_window(dialog, 620, 420)

        name_var = tk.StringVar(value=settings["institute_name"])
        type_var = tk.StringVar(value=settings["institute_type"])
        year_var = tk.StringVar(value=settings["academic_year"])

        frame = tk.Frame(dialog, bg="white", padx=18, pady=18)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=0)
        frame.grid_columnconfigure(1, weight=1)

        tk.Label(frame, text="Institute Setup", bg="white", fg="#10233d", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )
        for row_index, (label, variable, choices) in enumerate(
            (
                ("Institute Name", name_var, None),
                ("Institute Type", type_var, list(self.repository.VALID_INSTITUTE_TYPES)),
                ("Academic Year", year_var, None),
            ),
            start=1,
        ):
            tk.Label(frame, text=label, bg="white", font=("Segoe UI", 10)).grid(row=row_index, column=0, sticky="w", pady=6)
            if choices is None:
                ttk.Entry(frame, textvariable=variable).grid(row=row_index, column=1, sticky="ew", pady=6)
            else:
                ttk.Combobox(frame, textvariable=variable, values=choices, state="readonly").grid(row=row_index, column=1, sticky="ew", pady=6)

        help_text = (
            "School mode uses year-wise attendance defaults. "
            "College mode uses semester-wise attendance defaults. "
            "The academic year value is reused across attendance, reports, and context selection."
        )
        tk.Label(
            frame,
            text=help_text,
            bg="white",
            fg="#5b7186",
            justify="left",
            font=("Segoe UI", 9),
            wraplength=520,
        ).grid(
            row=4, column=0, columnspan=2, sticky="w", pady=(12, 0)
        )

        def save_setup() -> None:
            try:
                updated = self.repository.update_institute_settings(
                    name_var.get(),
                    type_var.get(),
                    year_var.get(),
                )
            except ValueError as exc:
                messagebox.showerror("Setup Not Saved", str(exc), parent=dialog)
                return
            self.institute_settings = updated
            self.refresh_institute_settings()
            self.log_activity(f"Institute setup updated for {updated['institute_name']}.")
            self.audit("institute.update", "institute_settings", updated["institute_name"], "Institute configuration updated.")
            dialog.destroy()

        button_row = tk.Frame(frame, bg="white")
        button_row.grid(row=5, column=0, columnspan=2, sticky="e", pady=(24, 0))
        self._make_button(button_row, "Cancel", dialog.destroy, variant="secondary", padx=14, pady=6).pack(side="right")
        self._make_button(button_row, "Save", save_setup, variant="primary", padx=14, pady=6).pack(side="right", padx=(0, 8))

    def open_student_promotion_manager(self) -> None:
        """Allow root to promote, pass out, drop, or otherwise update student academic status."""
        if not self.require_roles(("root",), "manage student promotion and status"):
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Student Promotion Manager")
        dialog.transient(self.root)
        dialog.grab_set()
        self._style_dialog_shell(dialog)
        self.center_window(dialog, 920, 560)

        class_var = tk.StringVar(value=self.current_class_var.get())
        status_var = tk.StringVar(value="Active")
        next_class_var = tk.StringVar(value=self.current_class_var.get())
        next_section_var = tk.StringVar(value=self.current_section_var.get())
        next_year_var = tk.StringVar(value=self.current_academic_year_var.get())
        next_semester_var = tk.StringVar(value=self.current_semester_var.get())

        frame = tk.Frame(dialog, bg="white", padx=16, pady=16)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(2, weight=1)

        tk.Label(frame, text="Student Promotion Manager", bg="white", fg="#10233d", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
        controls = tk.Frame(frame, bg="white")
        controls.grid(row=1, column=0, sticky="ew", pady=(12, 10))
        for index in range(6):
            controls.grid_columnconfigure(index, weight=1)
        control_specs = [
            ("Current Class", class_var, self.repository.list_classes()),
            ("New Status", status_var, list(self.repository.VALID_STUDENT_STATUSES)),
            ("Next Class", next_class_var, self.repository.list_classes()),
            ("Section", next_section_var, [""] + self.repository.list_sections(class_var.get())),
            ("Academic Year", next_year_var, self.repository.list_academic_years()),
            ("Semester", next_semester_var, self.repository.list_semesters(class_var.get(), next_year_var.get())),
        ]
        for column, (label, variable, values) in enumerate(control_specs):
            tk.Label(controls, text=label, bg="white", font=("Segoe UI", 10)).grid(row=0, column=column, sticky="w", padx=(0, 8))
            ttk.Combobox(controls, textvariable=variable, values=values, state="normal" if label in {"Section", "Semester"} else "readonly").grid(row=1, column=column, sticky="ew", padx=(0, 8), pady=(4, 0))

        tree = ttk.Treeview(frame, columns=("student_id", "name", "class_name", "section_name", "academic_year", "semester", "status"), show="headings")
        tree.grid(row=2, column=0, sticky="nsew")
        for column, width in (("student_id", 130), ("name", 170), ("class_name", 120), ("section_name", 90), ("academic_year", 110), ("semester", 100), ("status", 100)):
            tree.heading(column, text=self._format_column_heading(column))
            tree.column(column, width=width, anchor="w")

        def reload_students() -> None:
            tree.delete(*tree.get_children())
            students = self.repository.list_students(class_var.get(), active_only=False)
            for student in students:
                tree.insert("", "end", values=(student.student_id, student.name, student.class_name, student.section_name, student.academic_year, student.semester, student.status))

        def apply_status_change() -> None:
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("Student Required", "Select one or more students first.", parent=dialog)
                return
            for item in selected:
                values = tree.item(item, "values")
                self.repository.update_student_academic_record(
                    str(values[0]),
                    class_name=next_class_var.get().strip() or str(values[2]),
                    section_name=next_section_var.get().strip(),
                    academic_year=next_year_var.get().strip(),
                    semester=next_semester_var.get().strip(),
                    status=status_var.get().strip(),
                )
            self.audit("student.promote", "student", class_var.get().strip(), f"Updated {len(selected)} student academic record(s) to status {status_var.get().strip()}.")
            self.log_activity(f"Updated {len(selected)} student academic record(s).")
            reload_students()
            self.refresh_dashboard()

        button_row = tk.Frame(frame, bg="white")
        button_row.grid(row=3, column=0, sticky="e", pady=(14, 0))
        self._make_button(button_row, "Close", dialog.destroy, variant="secondary", padx=14, pady=6).pack(side="right")
        self._make_button(button_row, "Refresh", reload_students, variant="secondary", padx=14, pady=6).pack(side="right", padx=(0, 8))
        self._make_button(button_row, "Apply Update", apply_status_change, variant="primary", padx=14, pady=6).pack(side="right", padx=(0, 8))
        reload_students()

    def open_class_subject_manager(self, initial_class_name: str | None = None) -> None:
        """Manage selectable classes and subjects before using them in attendance."""
        if not self.require_roles(("root",), "manage classes and subjects"):
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Manage Classes / Sections / Semesters / Subjects")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(True, True)
        self._style_dialog_shell(dialog)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        min_width, min_height = clamp_window_size_to_screen(760, 620, screen_width, screen_height)
        dialog.minsize(min_width, min_height)
        self.center_window(dialog, 840, 680)

        class_var = tk.StringVar()
        new_section_var = tk.StringVar()
        new_subject_var = tk.StringVar()
        new_semester_var = tk.StringVar()
        available_classes = self.repository.list_classes()
        available_years = self.repository.list_academic_years()
        selected_class_var = tk.StringVar(
            value=self._resolve_combo_value(initial_class_name or self.current_class_var.get(), available_classes, allow_blank=False)
        )
        academic_year_var = tk.StringVar(
            value=self._resolve_combo_value(self.current_academic_year_var.get(), available_years, allow_blank=False)
        )
        semester_var = tk.StringVar()
        subject_section_var = tk.StringVar(value=self.current_section_var.get())
        manager_status_var = tk.StringVar(value="Select a class, year, and semester to manage its setup.")

        frame, _canvas = self.create_scrollable_dialog_body(dialog, bg="white", padx=18, pady=18)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_columnconfigure(3, weight=1)
        frame.grid_rowconfigure(5, weight=1)

        tk.Label(frame, text="Manage Academic Setup", bg="white", fg="#10233d", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 10)
        )
        tk.Label(
            frame,
            text="Create classes first, then manage sections, semesters, and semester-specific subjects for the selected class.",
            bg="white",
            fg="#5b7186",
            font=("Segoe UI", 9),
            wraplength=max(520, min_width - 100),
            justify="left",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 14))

        selector_card = tk.Frame(frame, bg="#f7fafc", padx=14, pady=14, highlightbackground="#d9e4ee", highlightthickness=1)
        selector_card.grid(row=2, column=0, columnspan=4, sticky="ew")
        selector_card.grid_columnconfigure(1, weight=1)
        selector_card.grid_columnconfigure(3, weight=1)

        tk.Label(selector_card, text="Working Context", bg="#f7fafc", fg="#10233d", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w"
        )
        tk.Label(selector_card, text="Selected Class", bg="#f7fafc", font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", pady=(12, 6))

        class_box = ttk.Combobox(
            selector_card,
            textvariable=selected_class_var,
            values=available_classes,
            state="readonly",
        )
        class_box.grid(row=1, column=1, sticky="ew", pady=(12, 6))

        tk.Label(selector_card, text="Academic Year", bg="#f7fafc", font=("Segoe UI", 10)).grid(row=1, column=2, sticky="w", pady=(12, 6), padx=(16, 0))
        year_box = ttk.Combobox(selector_card, textvariable=academic_year_var, values=available_years, state="readonly")
        year_box.grid(row=1, column=3, sticky="ew", pady=(12, 6))

        tk.Label(selector_card, text="Selected Semester", bg="#f7fafc", font=("Segoe UI", 10)).grid(row=2, column=0, sticky="w", pady=6)
        semester_box = ttk.Combobox(
            selector_card,
            textvariable=semester_var,
            values=self.repository.list_semesters(selected_class_var.get(), academic_year_var.get()),
            state="readonly",
        )
        semester_box.grid(row=2, column=1, sticky="ew", pady=6)
        tk.Label(
            selector_card,
            text="Choose the class, academic year, and semester you want to edit before adding sections or subjects.",
            bg="#f7fafc",
            fg="#5b7186",
            font=("Segoe UI", 9),
            wraplength=max(420, min_width - 160),
            justify="left",
        ).grid(row=3, column=0, columnspan=4, sticky="w", pady=(10, 0))

        creation_wrap = tk.Frame(frame, bg="white")
        creation_wrap.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(14, 0))
        creation_wrap.grid_columnconfigure(0, weight=1)
        creation_wrap.grid_columnconfigure(1, weight=1)

        class_semester_card = tk.Frame(creation_wrap, bg="#f7fafc", padx=14, pady=14, highlightbackground="#d9e4ee", highlightthickness=1)
        class_semester_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        class_semester_card.grid_columnconfigure(0, weight=1)

        tk.Label(class_semester_card, text="Create Class And Semester", bg="#f7fafc", fg="#10233d", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        tk.Label(
            class_semester_card,
            text="Start here when you need a new class or a new semester in the selected year.",
            bg="#f7fafc",
            fg="#5b7186",
            font=("Segoe UI", 9),
            wraplength=320,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 12))

        tk.Label(class_semester_card, text="New Class", bg="#f7fafc", font=("Segoe UI", 10)).grid(row=2, column=0, sticky="w", pady=(0, 6))
        class_entry = ttk.Entry(class_semester_card, textvariable=class_var)
        class_entry.grid(row=3, column=0, sticky="ew")
        self._make_button(
            class_semester_card,
            text="Add Class",
            command=lambda: self.add_class_option(
                class_var,
                selected_class_var,
                dialog,
                refresh_callback=refresh_manager_lists,
                class_box=class_box,
            ),
            variant="success",
            padx=12,
            pady=6,
        ).grid(row=3, column=1, padx=(8, 0))

        tk.Label(class_semester_card, text="New Semester", bg="#f7fafc", font=("Segoe UI", 10)).grid(row=4, column=0, sticky="w", pady=(12, 6))
        semester_add_frame = tk.Frame(class_semester_card, bg="#f7fafc")
        semester_add_frame.grid(row=5, column=0, columnspan=2, sticky="ew")
        semester_add_frame.grid_columnconfigure(0, weight=1)
        ttk.Entry(semester_add_frame, textvariable=new_semester_var).grid(row=0, column=0, sticky="ew")
        self._make_button(
            semester_add_frame,
            text="Add Semester",
            command=lambda: self.add_semester_option(
                selected_class_var,
                academic_year_var,
                new_semester_var,
                semester_var,
                dialog,
                refresh_callback=refresh_manager_lists,
            ),
            variant="secondary",
            padx=12,
            pady=6,
        ).grid(row=0, column=1, padx=(8, 0))

        section_subject_card = tk.Frame(creation_wrap, bg="#f7fafc", padx=14, pady=14, highlightbackground="#d9e4ee", highlightthickness=1)
        section_subject_card.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        section_subject_card.grid_columnconfigure(0, weight=1)
        section_subject_card.grid_columnconfigure(1, weight=0)

        tk.Label(section_subject_card, text="Create Section And Subject", bg="#f7fafc", fg="#10233d", font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        tk.Label(
            section_subject_card,
            text="Use the current class and semester context to add new sections or semester subjects.",
            bg="#f7fafc",
            fg="#5b7186",
            font=("Segoe UI", 9),
            wraplength=320,
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 12))

        tk.Label(section_subject_card, text="New Section", bg="#f7fafc", font=("Segoe UI", 10)).grid(row=2, column=0, sticky="w", pady=(0, 6))
        ttk.Entry(section_subject_card, textvariable=new_section_var).grid(row=3, column=0, sticky="ew")
        self._make_button(
            section_subject_card,
            text="Add Section",
            command=lambda: self.add_section_option(
                selected_class_var,
                new_section_var,
                academic_year_var,
                semester_var,
                dialog,
                refresh_callback=refresh_manager_lists,
            ),
            variant="secondary",
            padx=12,
            pady=6,
        ).grid(row=3, column=1, padx=(8, 0))

        tk.Label(section_subject_card, text="Subject Section", bg="#f7fafc", font=("Segoe UI", 10)).grid(row=4, column=0, sticky="w", pady=(12, 6))
        subject_section_box = ttk.Combobox(section_subject_card, textvariable=subject_section_var, values=[""], state="readonly")
        subject_section_box.grid(row=5, column=0, columnspan=2, sticky="ew")

        tk.Label(section_subject_card, text="New Subject", bg="#f7fafc", font=("Segoe UI", 10)).grid(row=6, column=0, sticky="w", pady=(12, 6))
        ttk.Entry(section_subject_card, textvariable=new_subject_var).grid(row=7, column=0, sticky="ew")
        self._make_button(
            section_subject_card,
            text="Add Subject",
            command=lambda: self.add_subject_option(
                selected_class_var,
                new_subject_var,
                academic_year_var,
                semester_var,
                dialog,
                refresh_callback=refresh_manager_lists,
                section_name=subject_section_var.get().strip(),
            ),
            variant="secondary",
            padx=12,
            pady=6,
        ).grid(row=7, column=1, padx=(8, 0))

        tk.Label(frame, textvariable=manager_status_var, bg="white", fg="#2f6f44", font=("Segoe UI", 9, "bold")).grid(
            row=4, column=0, columnspan=4, sticky="w", pady=(12, 4)
        )

        section_list_frame = tk.Frame(frame, bg="#f7fafc", padx=14, pady=14, highlightbackground="#d9e4ee", highlightthickness=1)
        section_list_frame.grid(row=5, column=1, sticky="nsew", pady=(12, 6), padx=(0, 12))
        section_list_frame.grid_columnconfigure(0, weight=1)
        section_list_frame.grid_rowconfigure(2, weight=1)
        tk.Label(section_list_frame, text="Available Sections", bg="#f7fafc", fg="#10233d", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(
            section_list_frame,
            text="Sections already available for the selected class context.",
            bg="#f7fafc",
            fg="#5b7186",
            font=("Segoe UI", 9),
            wraplength=240,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 10))

        section_list = tk.Listbox(section_list_frame, height=12)
        section_list.grid(row=2, column=0, sticky="nsew")
        section_scroll = ttk.Scrollbar(section_list_frame, orient="vertical", command=section_list.yview)
        section_list.configure(yscrollcommand=section_scroll.set)
        section_scroll.grid(row=2, column=1, sticky="ns")

        subject_list_frame = tk.Frame(frame, bg="#f7fafc", padx=14, pady=14, highlightbackground="#d9e4ee", highlightthickness=1)
        subject_list_frame.grid(row=5, column=3, sticky="nsew", pady=(12, 6))
        subject_list_frame.grid_columnconfigure(0, weight=1)
        subject_list_frame.grid_rowconfigure(2, weight=1)
        tk.Label(subject_list_frame, text="Semester Subjects", bg="#f7fafc", fg="#10233d", font=("Segoe UI", 11, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(
            subject_list_frame,
            text="Subjects already mapped to the selected semester and optional section.",
            bg="#f7fafc",
            fg="#5b7186",
            font=("Segoe UI", 9),
            wraplength=240,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(4, 10))

        subject_tree = ttk.Treeview(
            subject_list_frame,
            columns=("subject_name", "section_name", "academic_year", "semester"),
            show="headings",
            height=12,
        )
        for column, title, width in (
            ("subject_name", "Subject", 150),
            ("section_name", "Section", 90),
            ("academic_year", "Year", 100),
            ("semester", "Semester", 100),
        ):
            subject_tree.heading(column, text=title)
            subject_tree.column(column, width=width, anchor="w")
        subject_tree.grid(row=2, column=0, sticky="nsew")
        subject_scroll = ttk.Scrollbar(subject_list_frame, orient="vertical", command=subject_tree.yview)
        subject_tree.configure(yscrollcommand=subject_scroll.set)
        subject_scroll.grid(row=2, column=1, sticky="ns")

        def refresh_manager_lists(_event=None) -> None:
            nonlocal available_classes, available_years
            available_classes = self.repository.list_classes()
            available_years = self.repository.list_academic_years()

            class_box["values"] = available_classes
            year_box["values"] = available_years

            class_name = self._resolve_combo_value(selected_class_var.get(), available_classes, allow_blank=False)
            selected_class_var.set(class_name)

            year_name = self._resolve_combo_value(academic_year_var.get(), available_years, allow_blank=False)
            academic_year_var.set(year_name)

            semester_values = self.repository.list_semesters(class_name, year_name)
            semester_box["values"] = semester_values
            semester_name = self._resolve_combo_value(semester_var.get(), semester_values, allow_blank=True)
            semester_var.set(semester_name)

            section_values = [""] + self.repository.list_sections(
                class_name,
                year_name,
                semester_name or None,
            )
            subject_section_box["values"] = section_values
            if subject_section_var.get() not in section_values:
                subject_section_var.set("")

            section_list.delete(0, tk.END)
            for item in self.repository.list_sections(
                class_name,
                year_name,
                semester_name,
            ):
                section_list.insert(tk.END, item)

            for item in subject_tree.get_children():
                subject_tree.delete(item)
            for row in self.repository.list_subject_records(
                class_name,
                academic_year=year_name,
                semester=semester_name,
            ):
                if str(row.get("subject_name", "")) == "General":
                    continue
                subject_tree.insert(
                    "",
                    "end",
                    values=(
                        row.get("subject_name", ""),
                        row.get("section_name", ""),
                        row.get("academic_year", ""),
                        row.get("semester", ""),
                    ),
                )

            manager_status_var.set(
                f"Managing {class_name or '-'} | {academic_year_var.get().strip() or '-'} | {semester_var.get().strip() or '-'}"
            )

        class_box.bind("<<ComboboxSelected>>", refresh_manager_lists)
        year_box.bind("<<ComboboxSelected>>", refresh_manager_lists)
        semester_box.bind("<<ComboboxSelected>>", refresh_manager_lists)
        refresh_manager_lists()

        button_row = tk.Frame(frame, bg="white")
        button_row.grid(row=6, column=0, columnspan=4, sticky="ew", pady=(18, 0))
        self._make_button(
            button_row,
            "Use Selected Context",
            lambda: self.apply_selected_context(dialog, selected_class_var.get(), subject_tree),
            variant="primary",
            padx=14,
            pady=6,
        ).pack(side="right")
        self._make_button(button_row, "Close", dialog.destroy, variant="secondary", padx=14, pady=6).pack(side="right", padx=(0, 8))
        class_entry.bind("<Return>", lambda _event: self.add_class_option(class_var, selected_class_var, dialog, refresh_callback=refresh_manager_lists, class_box=class_box))

    def open_database_viewer(self) -> None:
        """Open a database viewer with cleaner previews and student edit shortcuts."""
        if not self.require_roles(("root",), "open the database viewer"):
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Database Viewer")
        dialog.transient(self.root)
        dialog.grab_set()
        self._style_dialog_shell(dialog)
        self.center_window(dialog, 980, 620)

        frame = tk.Frame(dialog, bg="white", padx=16, pady=16)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(3, weight=1)

        table_var = tk.StringVar(value=self.repository.get_table_names()[0])
        hide_empty_var = tk.BooleanVar(value=True)
        summary_var = tk.StringVar(value="")
        edit_button: tk.Button | None = None

        tk.Label(frame, text="Database Viewer", bg="white", fg="#10233d", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, sticky="w"
        )

        controls = tk.Frame(frame, bg="white")
        controls.grid(row=1, column=0, sticky="ew", pady=(12, 10))
        controls.grid_columnconfigure(1, weight=1)

        tk.Label(controls, text="Table", bg="white", font=("Segoe UI", 10)).grid(row=0, column=0, sticky="w", padx=(0, 8))
        table_box = ttk.Combobox(
            controls,
            textvariable=table_var,
            values=self.repository.get_table_names(),
            state="readonly",
            width=18,
        )
        table_box.grid(row=0, column=1, sticky="w")
        tk.Checkbutton(
            controls,
            text="Hide empty columns",
            variable=hide_empty_var,
            bg="white",
            fg="#10233d",
            activebackground="white",
            command=lambda: load_table(),
        ).grid(row=0, column=2, sticky="w", padx=(16, 0))
        tk.Label(
            controls,
            text=f"Database: {DB_PATH.name}",
            bg="white",
            fg="#5b7186",
            font=("Segoe UI", 9),
        ).grid(row=0, column=3, sticky="e")

        tk.Label(
            frame,
            textvariable=summary_var,
            bg="white",
            fg="#5b7186",
            font=("Segoe UI", 10),
        ).grid(row=2, column=0, sticky="nw", pady=(0, 8))

        table_frame = tk.Frame(frame, bg="white")
        table_frame.grid(row=3, column=0, sticky="nsew")
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        tree = ttk.Treeview(table_frame, show="headings")
        tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll = ttk.Scrollbar(table_frame, orient="horizontal", command=tree.xview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)

        def load_table(_event=None) -> None:
            table_name = table_var.get()
            columns, rows = self.repository.get_table_preview(
                table_name,
                limit=250,
                hide_empty_columns=hide_empty_var.get(),
            )
            tree.delete(*tree.get_children())
            tree["columns"] = columns

            for column in columns:
                tree.heading(column, text=self._format_column_heading(column))
                tree.column(column, width=140, anchor="w", stretch=True)

            for row in rows:
                tree.insert("", "end", values=[row.get(column, "") for column in columns])

            summary_var.set(
                f"Showing {len(rows)} row(s) from table: {table_name}. "
                f"{'Empty columns hidden. ' if hide_empty_var.get() else ''}"
                "Sensitive values are masked."
            )
            if edit_button is not None:
                edit_button.configure(state="normal" if table_name == "students" and not self.is_student_viewer() else "disabled")

        def edit_selected_student_from_table() -> None:
            if table_var.get() != "students":
                messagebox.showwarning("Students Only", "Manual editing from the database viewer currently supports student rows.", parent=dialog)
                return
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("Row Required", "Select a student row first.", parent=dialog)
                return
            values = tree.item(selected[0], "values")
            try:
                student_id_index = list(tree["columns"]).index("student_id")
            except ValueError:
                messagebox.showerror("Student ID Missing", "The selected preview does not include a student_id column.", parent=dialog)
                return
            student_id = str(values[student_id_index])
            student = self.repository.get_student_by_student_id(student_id)
            if student is None:
                messagebox.showerror("Student Missing", "The selected student no longer exists.", parent=dialog)
                return
            dialog.destroy()
            self.open_student_edit_dialog(student)

        table_box.bind("<<ComboboxSelected>>", load_table)

        button_row = tk.Frame(frame, bg="white")
        button_row.grid(row=4, column=0, sticky="e", pady=(12, 0))
        edit_button = self._make_button(
            button_row,
            "Edit Selected Student",
            edit_selected_student_from_table,
            variant="warning",
            padx=14,
            pady=6,
            state="disabled",
        )
        edit_button.pack(side="right", padx=(0, 8))
        load_table()
        self._make_button(button_row, "Refresh", load_table, variant="secondary", padx=14, pady=6).pack(side="right")
        self._make_button(button_row, "Close", dialog.destroy, variant="secondary", padx=14, pady=6).pack(side="right", padx=(0, 8))

    def open_audit_log_viewer(self) -> None:
        """Show recent audit trail activity for admins."""
        if not self.require_roles(("root",), "view audit logs"):
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Audit Logs")
        dialog.transient(self.root)
        dialog.grab_set()
        self._style_dialog_shell(dialog)
        self.center_window(dialog, 980, 620)

        frame = tk.Frame(dialog, bg="white", padx=16, pady=16)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(2, weight=1)

        tk.Label(frame, text="Audit Logs", bg="white", fg="#10233d", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, sticky="w"
        )

        summary_var = tk.StringVar(value="")
        tk.Label(frame, textvariable=summary_var, bg="white", fg="#5b7186", font=("Segoe UI", 10)).grid(
            row=1, column=0, sticky="w", pady=(8, 10)
        )

        table_frame = tk.Frame(frame, bg="white")
        table_frame.grid(row=2, column=0, sticky="nsew")
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=1)

        columns = ("created_at", "actor_username", "action", "entity_type", "entity_id", "details")
        tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=y_scroll.set)
        for column, width in (
            ("created_at", 150),
            ("actor_username", 110),
            ("action", 160),
            ("entity_type", 110),
            ("entity_id", 130),
            ("details", 360),
        ):
            tree.heading(column, text=self._format_column_heading(column))
            tree.column(column, width=width, anchor="w")

        def refresh_logs() -> None:
            tree.delete(*tree.get_children())
            rows = self.repository.list_audit_logs(limit=250)
            for row in rows:
                tree.insert(
                    "",
                    "end",
                    values=tuple(row.get(column, "") for column in columns),
                )
            summary_var.set(f"Showing {len(rows)} audit event(s).")

        button_row = tk.Frame(frame, bg="white")
        button_row.grid(row=3, column=0, sticky="e", pady=(12, 0))
        self._make_button(button_row, "Refresh", refresh_logs, variant="secondary", padx=14, pady=6).pack(side="right")
        self._make_button(button_row, "Close", dialog.destroy, variant="secondary", padx=14, pady=6).pack(side="right", padx=(0, 8))

        refresh_logs()

    def open_timetable_manager(self) -> None:
        """Manage class timetable slots used by the upgraded admin workflow."""
        if not self.require_roles(("root",), "manage the timetable"):
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Timetable Manager")
        dialog.transient(self.root)
        dialog.grab_set()
        self._style_dialog_shell(dialog)
        self.center_window(dialog, 920, 560)

        class_var = tk.StringVar(value=self.current_class_var.get())
        subject_var = tk.StringVar(value=self.current_subject_var.get())
        day_var = tk.StringVar(value="Monday")
        start_var = tk.StringVar(value="09:00")
        end_var = tk.StringVar(value="10:00")
        room_var = tk.StringVar()
        teacher_var = tk.StringVar()

        frame = tk.Frame(dialog, bg="white", padx=16, pady=16)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_rowconfigure(2, weight=1)

        tk.Label(frame, text="Timetable Manager", bg="white", fg="#10233d", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, columnspan=6, sticky="w"
        )

        fields = [
            ("Class", class_var, self.repository.list_classes()),
            ("Subject", subject_var, self.repository.list_subjects(self.current_class_var.get())),
            ("Day", day_var, ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]),
            ("Start", start_var, None),
            ("End", end_var, None),
            ("Room", room_var, None),
            ("Teacher", teacher_var, None),
        ]

        for index, (label, variable, choices) in enumerate(fields):
            tk.Label(frame, text=label, bg="white", font=("Segoe UI", 10)).grid(row=1, column=index, sticky="w", pady=(12, 4), padx=(0, 8))
            if choices is None:
                ttk.Entry(frame, textvariable=variable, width=12).grid(row=2, column=index, sticky="ew", padx=(0, 8))
            else:
                ttk.Combobox(frame, textvariable=variable, values=choices, state="normal" if label == "Subject" else "readonly", width=14).grid(
                    row=2, column=index, sticky="ew", padx=(0, 8)
                )

        list_frame = tk.Frame(frame, bg="white")
        list_frame.grid(row=3, column=0, columnspan=7, sticky="nsew", pady=(16, 0))
        list_frame.grid_columnconfigure(0, weight=1)
        list_frame.grid_rowconfigure(0, weight=1)

        tree = ttk.Treeview(
            list_frame,
            columns=("id", "class_name", "subject_name", "day_name", "start_time", "end_time", "room_name", "teacher_name"),
            show="headings",
        )
        tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        for col, width in (
            ("id", 50),
            ("class_name", 120),
            ("subject_name", 120),
            ("day_name", 100),
            ("start_time", 80),
            ("end_time", 80),
            ("room_name", 100),
            ("teacher_name", 130),
        ):
            tree.heading(col, text=self._format_column_heading(col))
            tree.column(col, width=width, anchor="w")

        def reload_timetable() -> None:
            tree.delete(*tree.get_children())
            for row in self.repository.list_timetable():
                tree.insert("", "end", values=tuple(row.get(col, "") for col in tree["columns"]))

        def add_entry() -> None:
            try:
                self.repository.add_timetable_entry(
                    class_var.get().strip() or "General",
                    subject_var.get().strip() or "General",
                    day_var.get().strip(),
                    start_var.get().strip(),
                    end_var.get().strip(),
                    room_var.get().strip(),
                    teacher_var.get().strip(),
                    section_name=self.current_section_var.get().strip(),
                    academic_year=self.current_academic_year_var.get().strip(),
                    semester=self.current_semester_var.get().strip(),
                )
            except ValueError as exc:
                messagebox.showerror("Invalid Timetable Slot", str(exc), parent=dialog)
                return
            self.log_activity(
                f"Timetable updated for {class_var.get().strip() or 'General'} - {subject_var.get().strip() or 'General'}."
            )
            self.audit(
                "timetable.add",
                "timetable",
                class_var.get().strip() or "General",
                f"Added {subject_var.get().strip() or 'General'} on {day_var.get().strip()} {start_var.get().strip()}-{end_var.get().strip()}.",
            )
            reload_timetable()

        def delete_selected() -> None:
            selected = tree.selection()
            if not selected:
                return
            values = tree.item(selected[0], "values")
            self.repository.delete_timetable_entry(int(values[0]))
            self.audit(
                "timetable.delete",
                "timetable",
                str(values[0]),
                f"Deleted slot {values[2]} on {values[3]} {values[4]}-{values[5]}.",
            )
            reload_timetable()

        button_row = tk.Frame(frame, bg="white")
        button_row.grid(row=4, column=0, columnspan=7, sticky="e", pady=(16, 0))
        self._make_button(button_row, "Delete Selected", delete_selected, variant="danger", padx=12, pady=6).pack(side="right")
        self._make_button(button_row, "Refresh", reload_timetable, variant="secondary", padx=12, pady=6).pack(side="right", padx=(0, 8))
        self._make_button(button_row, "Add Slot", add_entry, variant="primary", padx=12, pady=6).pack(side="right", padx=(0, 8))

        reload_timetable()

    def open_user_manager(self) -> None:
        """Create local users and review existing roles."""
        if not self.require_roles(("root",), "manage users"):
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("User Manager")
        dialog.transient(self.root)
        dialog.grab_set()
        self._style_dialog_shell(dialog)
        dialog.resizable(True, True)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        min_width, min_height = clamp_window_size_to_screen(900, 620, screen_width, screen_height)
        dialog.minsize(min_width, min_height)
        self.center_window(dialog, 980, 680)

        username_var = tk.StringVar()
        password_var = tk.StringVar()
        role_var = tk.StringVar(value=self.repository.USER_ROLES[0])
        available_classes = self.repository.list_classes()
        class_scope_var = tk.StringVar(
            value=self._resolve_combo_value(self.current_class_var.get(), available_classes, allow_blank=False)
        )
        subject_scope_var = tk.StringVar(value="")

        frame, _canvas = self.create_scrollable_dialog_body(dialog, bg="white", padx=16, pady=16)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(3, weight=1)

        header_row = tk.Frame(frame, bg="white")
        header_row.grid(row=0, column=0, sticky="ew")
        header_row.grid_columnconfigure(0, weight=1)

        tk.Label(header_row, text="User Manager", bg="white", fg="#10233d", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        header_actions = tk.Frame(header_row, bg="white")
        header_actions.grid(row=0, column=1, sticky="e")

        tk.Label(
            frame,
            text="Create operator, teacher, or viewer accounts for day-to-day use.",
            bg="white",
            fg="#5b7186",
            font=("Segoe UI", 10),
            wraplength=max(480, min_width - 120),
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(6, 14))

        form_card = tk.Frame(frame, bg="#f7fafc", padx=14, pady=14, highlightbackground="#d9e4ee", highlightthickness=1)
        form_card.grid(row=2, column=0, sticky="ew")
        form_card.grid_columnconfigure(0, weight=1)

        tk.Label(
            form_card,
            text="Create New User",
            bg="#f7fafc",
            fg="#10233d",
            font=("Segoe UI", 11, "bold"),
        ).grid(row=0, column=0, sticky="w")

        form = tk.Frame(form_card, bg="#f7fafc")
        form.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        for column in range(4):
            form.grid_columnconfigure(column, weight=1)

        tk.Label(form, text="Username", bg="#f7fafc", font=("Segoe UI", 10)).grid(row=0, column=0, sticky="w", padx=(0, 8))
        tk.Label(form, text="Password", bg="#f7fafc", font=("Segoe UI", 10)).grid(row=0, column=1, sticky="w", padx=(0, 8))
        tk.Label(form, text="Role", bg="#f7fafc", font=("Segoe UI", 10)).grid(row=0, column=2, sticky="w", padx=(0, 8))
        tk.Label(form, text="Teacher Class Scope", bg="#f7fafc", font=("Segoe UI", 10)).grid(row=0, column=3, sticky="w")

        username_entry = ttk.Entry(form, textvariable=username_var)
        username_entry.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(4, 0))
        password_entry = ttk.Entry(form, textvariable=password_var, show="*")
        password_entry.grid(row=1, column=1, sticky="ew", padx=(0, 8), pady=(4, 0))
        role_box = ttk.Combobox(
            form,
            textvariable=role_var,
            values=self.repository.USER_ROLES,
            state="readonly",
        )
        role_box.grid(row=1, column=2, sticky="ew", padx=(0, 8), pady=(4, 0))
        class_scope_box = ttk.Combobox(
            form,
            textvariable=class_scope_var,
            values=available_classes,
            state="readonly",
        )
        class_scope_box.grid(row=1, column=3, sticky="ew", pady=(4, 0))

        tk.Label(form, text="Teacher Subject Scope", bg="#f7fafc", font=("Segoe UI", 10)).grid(row=2, column=0, sticky="w", pady=(12, 0))
        tk.Label(
            form,
            text="Leave subject blank to allow all subjects in the selected class.",
            bg="#f7fafc",
            fg="#5b7186",
            font=("Segoe UI", 9),
            wraplength=620,
            justify="left",
        ).grid(row=2, column=1, columnspan=3, sticky="w", pady=(12, 0))
        subject_scope_box = ttk.Combobox(
            form,
            textvariable=subject_scope_var,
            values=[""] + self.repository.list_subjects(class_scope_var.get()),
            state="readonly",
        )
        subject_scope_box.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(4, 0))

        form_actions = tk.Frame(form_card, bg="#f7fafc")
        form_actions.grid(row=2, column=0, sticky="e", pady=(14, 0))

        columns = ("username", "role", "class_name", "subject_name", "created_at")
        table_frame = tk.Frame(frame, bg="white")
        table_frame.grid(row=3, column=0, sticky="nsew", pady=(16, 0))
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        tree.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        y_scroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=y_scroll.set)
        for column, width in (("username", 140), ("role", 100), ("class_name", 120), ("subject_name", 120), ("created_at", 160)):
            tree.heading(column, text=self._format_column_heading(column))
            tree.column(column, width=width, anchor="w")

        def refresh_scope_subjects(_event=None) -> None:
            nonlocal available_classes
            available_classes = self.repository.list_classes()
            class_scope_box["values"] = available_classes
            if role_var.get().strip().lower() == "teacher":
                class_scope_var.set(
                    self._resolve_combo_value(class_scope_var.get(), available_classes, allow_blank=False)
                )
            choices = [""] + self.repository.list_subjects(class_scope_var.get())
            subject_scope_box["values"] = choices
            if subject_scope_var.get() not in choices:
                subject_scope_var.set("")

        def toggle_scope_inputs(_event=None) -> None:
            is_teacher = role_var.get().strip().lower() == "teacher"
            class_scope_box.configure(state="readonly" if is_teacher else "disabled")
            subject_scope_box.configure(state="readonly" if is_teacher else "disabled")
            if not is_teacher:
                subject_scope_var.set("")
            else:
                class_scope_var.set(
                    self._resolve_combo_value(class_scope_var.get(), available_classes, allow_blank=False)
                )
                refresh_scope_subjects()

        class_scope_box.bind("<<ComboboxSelected>>", refresh_scope_subjects)
        role_box.bind("<<ComboboxSelected>>", toggle_scope_inputs)
        refresh_scope_subjects()
        toggle_scope_inputs()

        def refresh_users() -> None:
            tree.delete(*tree.get_children())
            for user in self.repository.list_users():
                tree.insert("", "end", values=(user.username, user.role, user.class_name, user.subject_name or "All", user.created_at))

        def create_user() -> None:
            try:
                self.repository.add_user(
                    username_var.get().strip(),
                    password_var.get(),
                    role_var.get(),
                    class_scope_var.get().strip(),
                    subject_scope_var.get().strip(),
                )
            except ValueError as exc:
                messagebox.showerror("User Not Created", str(exc), parent=dialog)
                return
            self.audit(
                "user.create",
                "user",
                username_var.get().strip(),
                (
                    f"Created user with role {role_var.get().strip().lower()}."
                    + (
                        f" Scope: {class_scope_var.get().strip()} / {subject_scope_var.get().strip() or 'All'}."
                        if role_var.get().strip().lower() == "teacher"
                        else ""
                    )
                ),
            )
            self.log_activity(f"User created: {username_var.get().strip()} ({role_var.get().strip().lower()}).")
            username_var.set("")
            password_var.set("")
            subject_scope_var.set("")
            refresh_users()
            username_entry.focus_set()

        def delete_selected_user() -> None:
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("User Required", "Select a user first.", parent=dialog)
                return
            username = str(tree.item(selected[0], "values")[0])
            if not messagebox.askyesno(
                "Delete User",
                f"Delete the user account '{username}'?",
                parent=dialog,
            ):
                return
            try:
                self.repository.delete_user(username)
            except ValueError as exc:
                messagebox.showerror("User Not Deleted", str(exc), parent=dialog)
                return
            self.audit("user.delete", "user", username, "Deleted user account from manager.")
            self.log_activity(f"User deleted: {username}.")
            refresh_users()

        self._make_button(
            header_actions,
            "Refresh Users",
            refresh_users,
            variant="secondary",
            padx=12,
            pady=6,
        ).pack(side="right")
        self._make_button(
            form_actions,
            "Add User",
            create_user,
            variant="primary",
            padx=16,
            pady=7,
        ).pack(side="right")

        username_entry.bind("<Return>", lambda _event: create_user())
        password_entry.bind("<Return>", lambda _event: create_user())

        button_row = tk.Frame(frame, bg="white")
        button_row.grid(row=4, column=0, sticky="e", pady=(14, 0))
        self._make_button(button_row, "Close", dialog.destroy, variant="secondary", padx=12, pady=6).pack(side="right")
        self._make_button(button_row, "Delete Selected", delete_selected_user, variant="danger", padx=12, pady=6).pack(side="right")
        self._make_button(button_row, "Refresh", refresh_users, variant="secondary", padx=12, pady=6).pack(side="right", padx=(0, 8))
        self._make_button(button_row, "Add User", create_user, variant="primary", padx=12, pady=6).pack(side="right", padx=(0, 8))

        refresh_users()
        username_entry.focus_set()

    def open_change_password_dialog(self) -> None:
        """Allow the signed-in account to change its own password."""
        if not self.current_username:
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Change Password")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(True, False)
        self._style_dialog_shell(dialog)
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        min_width, min_height = clamp_window_size_to_screen(500, 340, screen_width, screen_height)
        dialog.minsize(min_width, min_height)
        self.center_window(dialog, 520, 340)

        current_var = tk.StringVar()
        new_var = tk.StringVar()
        confirm_var = tk.StringVar()
        show_current_var = tk.BooleanVar(value=False)
        show_new_var = tk.BooleanVar(value=False)
        show_confirm_var = tk.BooleanVar(value=False)

        frame = tk.Frame(dialog, bg="white", padx=14, pady=12)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(2, weight=1)
        frame.grid_rowconfigure(3, weight=0)

        tk.Label(frame, text="Change Password", bg="white", fg="#10233d", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        account_label = self.current_username
        if self.is_student_viewer():
            account_label = f"{self.current_username} (student)"
        tk.Label(frame, text=f"User: {account_label}", bg="white", fg="#5b7186", font=("Segoe UI", 10)).grid(
            row=1, column=0, sticky="w", pady=(0, 8)
        )

        form_card = tk.Frame(frame, bg="#f7fafc", padx=12, pady=8, highlightbackground="#d9e4ee", highlightthickness=1)
        form_card.grid(row=2, column=0, sticky="ew")
        form_card.grid_columnconfigure(0, weight=1)

        tk.Label(form_card, text="Current Password", bg="#f7fafc", fg="#10233d", font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        current_row = tk.Frame(form_card, bg="#f7fafc")
        current_row.grid(row=1, column=0, sticky="ew", pady=(2, 6))
        current_row.grid_columnconfigure(0, weight=1)
        current_entry = ttk.Entry(current_row, textvariable=current_var, show="*")
        current_entry.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(
            current_row,
            text="Show",
            variable=show_current_var,
            command=lambda: current_entry.configure(show="" if show_current_var.get() else "*"),
        ).grid(row=0, column=1, padx=(10, 0))

        tk.Label(form_card, text="New Password", bg="#f7fafc", fg="#10233d", font=("Segoe UI", 9)).grid(row=2, column=0, sticky="w")
        new_row = tk.Frame(form_card, bg="#f7fafc")
        new_row.grid(row=3, column=0, sticky="ew", pady=(2, 6))
        new_row.grid_columnconfigure(0, weight=1)
        new_entry = ttk.Entry(new_row, textvariable=new_var, show="*")
        new_entry.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(
            new_row,
            text="Show",
            variable=show_new_var,
            command=lambda: new_entry.configure(show="" if show_new_var.get() else "*"),
        ).grid(row=0, column=1, padx=(10, 0))

        tk.Label(form_card, text="Confirm New Password", bg="#f7fafc", fg="#10233d", font=("Segoe UI", 9)).grid(row=4, column=0, sticky="w")
        confirm_row = tk.Frame(form_card, bg="#f7fafc")
        confirm_row.grid(row=5, column=0, sticky="ew", pady=(2, 0))
        confirm_row.grid_columnconfigure(0, weight=1)
        confirm_entry = ttk.Entry(confirm_row, textvariable=confirm_var, show="*")
        confirm_entry.grid(row=0, column=0, sticky="ew")
        ttk.Checkbutton(
            confirm_row,
            text="Show",
            variable=show_confirm_var,
            command=lambda: confirm_entry.configure(show="" if show_confirm_var.get() else "*"),
        ).grid(row=0, column=1, padx=(10, 0))

        def save_password() -> None:
            if not current_var.get().strip():
                messagebox.showwarning("Password Not Changed", "Enter your current password.", parent=dialog)
                return
            if not new_var.get().strip():
                messagebox.showwarning("Password Not Changed", "Enter a new password.", parent=dialog)
                return
            if not confirm_var.get().strip():
                messagebox.showwarning("Password Not Changed", "Confirm the new password.", parent=dialog)
                return
            if new_var.get() != confirm_var.get():
                messagebox.showerror("Password Not Changed", "The new password entries must match.", parent=dialog)
                return
            try:
                if self.is_student_viewer():
                    if self.repository.authenticate_student(self.current_username, current_var.get()) is None:
                        messagebox.showerror("Password Not Changed", "Current password is incorrect.", parent=dialog)
                        return
                    self.repository.update_student_password(self.current_username, new_var.get())
                else:
                    if self.repository.authenticate_user(self.current_username, current_var.get()) is None:
                        messagebox.showerror("Password Not Changed", "Current password is incorrect.", parent=dialog)
                        return
                    self.repository.update_user_password(self.current_username, new_var.get())
                    if self.current_username == "admin":
                        self.repository.clear_admin_setup_file()
            except ValueError as exc:
                messagebox.showerror("Password Not Changed", str(exc), parent=dialog)
                return
            self.audit("user.password_change", "user", self.current_username, "Password updated from dashboard.")
            self.log_activity(f"Password changed for {self.current_username}.")
            dialog.destroy()
            messagebox.showinfo("Password Updated", "Your password has been updated.", parent=self.root)

        button_row = tk.Frame(frame, bg="white")
        button_row.grid(row=3, column=0, sticky="e", pady=(8, 0))
        self._make_button(button_row, "Cancel", dialog.destroy, variant="secondary", padx=14, pady=6).pack(side="right")
        self._make_button(button_row, "Save", save_password, variant="primary", padx=14, pady=6).pack(side="right", padx=(0, 8))
        current_entry.focus_set()
        current_entry.bind("<Return>", lambda _event: save_password())
        new_entry.bind("<Return>", lambda _event: save_password())
        confirm_entry.bind("<Return>", lambda _event: save_password())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())

    def open_analytics_viewer(self) -> None:
        """Show a simple analytics snapshot for the active class and subject."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Attendance Analytics")
        dialog.transient(self.root)
        dialog.grab_set()
        self._style_dialog_shell(dialog)
        self.center_window(dialog, 620, 420)

        frame = tk.Frame(dialog, bg="white", padx=18, pady=18)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=1)

        tk.Label(frame, text="Attendance Analytics", bg="white", fg="#10233d", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        tk.Label(
            frame,
            text=(
                f"Class: {self.current_class_var.get()}   |   Section: {self.current_section_var.get() or '-'}   |   "
                f"Subject: {self.current_subject_var.get()}   |   Year: {self.current_academic_year_var.get()}   |   "
                f"Semester: {self.current_semester_var.get() or '-'}"
            ),
            bg="white",
            fg="#5b7186",
            font=("Segoe UI", 10),
        ).grid(row=1, column=0, sticky="w", pady=(6, 14))

        summary = self.repository.get_summary(
            self.current_class_var.get(),
            self.current_subject_var.get(),
            academic_year=self.current_academic_year_var.get().strip() or None,
            semester=self.current_semester_var.get().strip() or None,
        )
        stats = [
            f"Total Students in Class: {summary['student_count']}",
            f"Today's Attendance in Subject: {summary['today_attendance']}",
        ]
        for idx, line in enumerate(stats, start=2):
            tk.Label(frame, text=line, bg="white", fg="#10233d", font=("Segoe UI", 11, "bold")).grid(
                row=idx, column=0, sticky="w", pady=(0, 8)
            )

        tk.Label(frame, text="Status Breakdown", bg="white", fg="#10233d", font=("Segoe UI", 12, "bold")).grid(
            row=4, column=0, sticky="w", pady=(10, 8)
        )
        breakdown = self.repository.get_attendance_status_breakdown(
            self.current_class_var.get(),
            self.current_subject_var.get(),
            academic_year=self.current_academic_year_var.get().strip() or None,
            semester=self.current_semester_var.get().strip() or None,
            section_name=self.current_section_var.get().strip() or None,
        )
        listbox = tk.Listbox(frame, height=8, borderwidth=1, highlightthickness=0, font=("Segoe UI", 10))
        listbox.grid(row=5, column=0, sticky="nsew")
        if breakdown:
            for row in breakdown:
                listbox.insert(tk.END, f"{row['status']}: {row['total']}")
        else:
            listbox.insert(tk.END, "No attendance analytics available yet.")

    def open_report_summary_viewer(self) -> None:
        """Show grouped report summaries for the current attendance filter set."""
        if not self.require_roles(EDIT_ROLES, "view report summaries"):
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Report Summary")
        dialog.transient(self.root)
        dialog.grab_set()
        self._style_dialog_shell(dialog)
        self.center_window(dialog, 900, 620)

        frame = tk.Frame(dialog, bg="white", padx=18, pady=18)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_rowconfigure(3, weight=1)

        tk.Label(frame, text="Report Summary", bg="white", fg="#10233d", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        tk.Label(
            frame,
            text=(
                f"Class: {self.current_class_var.get()}   |   Section: {self.current_section_var.get() or '-'}   |   Subject: {self.current_subject_var.get()}   |   "
                f"Year: {self.current_academic_year_var.get()}   |   Semester: {self.current_semester_var.get() or '-'}   |   "
                f"Date: {self.date_filter_var.get().strip() or '-'}   |   "
                f"Range: {self.date_from_var.get().strip() or '-'} to {self.date_to_var.get().strip() or '-'}"
            ),
            bg="white",
            fg="#5b7186",
            font=("Segoe UI", 10),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 14))

        summary = self.repository.get_attendance_report_summary(**self.get_attendance_filter_kwargs())
        overview = summary["overview"]

        overview_card = tk.Frame(
            frame,
            bg="#f8fafc",
            padx=12,
            pady=12,
            highlightbackground="#d9e2ec",
            highlightthickness=1,
        )
        overview_card.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        overview_lines = [
            f"Total Records: {overview['total_records']}",
            f"Unique Students: {overview['unique_students']}",
            f"Class: {overview['class_name']}",
            f"Subject: {overview['subject_name']}",
            f"Section: {overview.get('section_name', 'All Sections')}",
            f"Year/Semester: {overview.get('academic_year', '-') or '-'} / {overview.get('semester', '-') or '-'}",
        ]
        for index, line in enumerate(overview_lines):
            tk.Label(
                overview_card,
                text=line,
                bg="#f8fafc",
                fg="#10233d",
                font=("Segoe UI", 10, "bold" if index < 2 else "normal"),
                anchor="w",
            ).grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 24), pady=2)

        status_frame = tk.Frame(frame, bg="white")
        status_frame.grid(row=3, column=0, sticky="nsew", padx=(0, 8))
        status_frame.grid_columnconfigure(0, weight=1)
        status_frame.grid_rowconfigure(1, weight=1)

        student_frame = tk.Frame(frame, bg="white")
        student_frame.grid(row=3, column=1, sticky="nsew", padx=(8, 0))
        student_frame.grid_columnconfigure(0, weight=1)
        student_frame.grid_rowconfigure(1, weight=1)

        tk.Label(status_frame, text="By Status", bg="white", fg="#10233d", font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )
        tk.Label(student_frame, text="By Student", bg="white", fg="#10233d", font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 8)
        )

        status_list = tk.Listbox(status_frame, borderwidth=0, highlightthickness=0, font=("Segoe UI", 10))
        status_list.grid(row=1, column=0, sticky="nsew")
        student_list = tk.Listbox(student_frame, borderwidth=0, highlightthickness=0, font=("Segoe UI", 10))
        student_list.grid(row=1, column=0, sticky="nsew")

        by_status = summary["by_status"]
        by_student = summary["by_student"]
        if by_status:
            for row in by_status:
                status_list.insert(tk.END, f"{row['status']}: {row['total']}")
        else:
            status_list.insert(tk.END, "No matching attendance records.")

        if by_student:
            for row in by_student[:25]:
                student_list.insert(tk.END, f"{row['student_id']} | {row['name']} | {row['total']}")
            if len(by_student) > 25:
                student_list.insert(tk.END, f"...and {len(by_student) - 25} more student(s)")
        else:
            student_list.insert(tk.END, "No student totals available.")

        button_row = tk.Frame(frame, bg="white")
        button_row.grid(row=4, column=0, columnspan=2, sticky="e", pady=(14, 0))
        self._make_button(button_row, "Close", dialog.destroy, variant="secondary", padx=14, pady=6).pack(side="right")

    def open_low_attendance_alerts_viewer(self) -> None:
        """Show students whose attendance is below the configured threshold."""
        if not self.require_roles(READ_ONLY_ROLES, "view low attendance alerts"):
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Low Attendance Alerts")
        dialog.transient(self.root)
        dialog.grab_set()
        self._style_dialog_shell(dialog)
        self.center_window(dialog, 860, 540)

        frame = tk.Frame(dialog, bg="white", padx=18, pady=18)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(2, weight=1)

        threshold_var = tk.StringVar(value=str(int(self.repository.LOW_ATTENDANCE_THRESHOLD)))
        summary_var = tk.StringVar(value="")

        tk.Label(frame, text="Low Attendance Alerts", bg="white", fg="#10233d", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
        controls = tk.Frame(frame, bg="white")
        controls.grid(row=1, column=0, sticky="ew", pady=(10, 12))
        tk.Label(controls, text="Threshold %", bg="white", font=("Segoe UI", 10)).pack(side="left")
        ttk.Entry(controls, textvariable=threshold_var, width=8).pack(side="left", padx=(8, 12))
        tk.Label(controls, textvariable=summary_var, bg="white", fg="#5b7186", font=("Segoe UI", 10)).pack(side="left")

        tree = ttk.Treeview(
            frame,
            columns=("student_id", "name", "class_name", "section_name", "subject_name", "academic_year", "semester", "attendance_rate", "present_like", "total_records"),
            show="headings",
        )
        tree.grid(row=2, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        scroll.grid(row=2, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        for column, width in (
            ("student_id", 120),
            ("name", 170),
            ("class_name", 120),
            ("section_name", 90),
            ("subject_name", 120),
            ("academic_year", 110),
            ("semester", 100),
            ("attendance_rate", 110),
            ("present_like", 100),
            ("total_records", 100),
        ):
            tree.heading(column, text=self._format_column_heading(column))
            tree.column(column, width=width, anchor="w")

        def reload_alerts() -> None:
            tree.delete(*tree.get_children())
            try:
                threshold_value = float(threshold_var.get().strip() or self.repository.LOW_ATTENDANCE_THRESHOLD)
            except ValueError:
                messagebox.showerror("Invalid Threshold", "Enter a numeric threshold percentage.", parent=dialog)
                return
            rows = self.repository.list_low_attendance_students(
                self.current_class_var.get(),
                None if self.current_role() == "operator" or self.is_root_user() or self.is_teacher_user() else self.current_subject_var.get(),
                threshold=threshold_value,
                section_name=self.current_section_var.get().strip() or None,
                academic_year=self.current_academic_year_var.get().strip() or None,
                semester=self.current_semester_var.get().strip() or None,
            )
            summary_var.set(f"{len(rows)} student(s) below {threshold_value:.1f}% attendance.")
            for row in rows:
                tree.insert(
                    "",
                    "end",
                    values=(
                        row.get("student_id", ""),
                        row.get("name", ""),
                        row.get("class_name", ""),
                        row.get("section_name", ""),
                        row.get("subject_name", ""),
                        row.get("academic_year", ""),
                        row.get("semester", ""),
                        f"{row.get('attendance_rate', 0)}%",
                        row.get("present_like", 0),
                        row.get("total_records", 0),
                    ),
                )

        button_row = tk.Frame(frame, bg="white")
        button_row.grid(row=3, column=0, sticky="e", pady=(14, 0))
        self._make_button(button_row, "Refresh", reload_alerts, variant="secondary", padx=14, pady=6).pack(side="right", padx=(0, 8))
        self._make_button(button_row, "Close", dialog.destroy, variant="secondary", padx=14, pady=6).pack(side="right")
        reload_alerts()

    def open_attendance_change_request_viewer(self) -> None:
        """Show pending attendance correction requests and let root review them."""
        allowed_roles = ("root", "teacher", "operator")
        if not self.require_roles(allowed_roles, "review attendance change requests"):
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Attendance Change Requests")
        dialog.transient(self.root)
        dialog.grab_set()
        self._style_dialog_shell(dialog)
        self.center_window(dialog, 1120, 620)

        frame = tk.Frame(dialog, bg="white", padx=16, pady=16)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(2, weight=1)

        status_var = tk.StringVar(value="Pending")
        summary_var = tk.StringVar(value="")

        tk.Label(frame, text="Attendance Change Requests", bg="white", fg="#10233d", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
        controls = tk.Frame(frame, bg="white")
        controls.grid(row=1, column=0, sticky="ew", pady=(10, 12))
        tk.Label(controls, text="Status", bg="white", font=("Segoe UI", 10)).pack(side="left")
        ttk.Combobox(controls, textvariable=status_var, values=["Pending", "Approved", "Rejected"], state="readonly", width=12).pack(side="left", padx=(8, 12))
        tk.Label(controls, textvariable=summary_var, bg="white", fg="#5b7186", font=("Segoe UI", 10)).pack(side="left")

        columns = (
            "id",
            "attendance_id",
            "requested_by",
            "status",
            "original_date",
            "requested_date",
            "original_status",
            "requested_status",
            "reason",
        )
        tree = ttk.Treeview(frame, columns=columns, show="headings")
        tree.grid(row=2, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        scroll.grid(row=2, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        for column, width in (
            ("id", 60),
            ("attendance_id", 90),
            ("requested_by", 120),
            ("status", 90),
            ("original_date", 100),
            ("requested_date", 100),
            ("original_status", 110),
            ("requested_status", 110),
            ("reason", 260),
        ):
            tree.heading(column, text=self._format_column_heading(column))
            tree.column(column, width=width, anchor="w")

        request_lookup: dict[str, dict[str, str]] = {}

        def reload_requests() -> None:
            tree.delete(*tree.get_children())
            request_lookup.clear()
            rows = self.repository.list_attendance_change_requests(status=status_var.get())
            if not self.is_root_user():
                rows = [row for row in rows if str(row.get("requested_by", "")) == self.current_username]
            summary_var.set(f"Showing {len(rows)} request(s).")
            for row in rows:
                request_lookup[str(row["id"])] = row
                tree.insert(
                    "",
                    "end",
                    values=(
                        row.get("id", ""),
                        row.get("attendance_id", ""),
                        row.get("requested_by", ""),
                        row.get("status", ""),
                        row.get("original_date", ""),
                        row.get("requested_date", ""),
                        row.get("original_status", ""),
                        row.get("requested_status", ""),
                        row.get("reason", ""),
                    ),
                )

        def approve_selected() -> None:
            if not self.is_root_user():
                messagebox.showwarning("Root Required", "Only root can approve correction requests.", parent=dialog)
                return
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("Request Required", "Select a request first.", parent=dialog)
                return
            request_id = str(tree.item(selected[0], "values")[0])
            try:
                self.repository.approve_attendance_change_request(int(request_id), self.current_username or "admin")
            except ValueError as exc:
                messagebox.showerror("Approval Failed", str(exc), parent=dialog)
                return
            self.audit("attendance.change_request_approved", "attendance_change_request", request_id, "Approved attendance correction request.")
            self.refresh_dashboard()
            reload_requests()

        def reject_selected() -> None:
            if not self.is_root_user():
                messagebox.showwarning("Root Required", "Only root can reject correction requests.", parent=dialog)
                return
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("Request Required", "Select a request first.", parent=dialog)
                return
            request_id = str(tree.item(selected[0], "values")[0])
            note = simpledialog.askstring("Reject Request", "Reason for rejection:", parent=dialog)
            if note is None:
                return
            try:
                self.repository.reject_attendance_change_request(int(request_id), self.current_username or "admin", note)
            except ValueError as exc:
                messagebox.showerror("Rejection Failed", str(exc), parent=dialog)
                return
            self.audit("attendance.change_request_rejected", "attendance_change_request", request_id, "Rejected attendance correction request.")
            reload_requests()

        button_row = tk.Frame(frame, bg="white")
        button_row.grid(row=3, column=0, sticky="e", pady=(14, 0))
        self._make_button(button_row, "Close", dialog.destroy, variant="secondary", padx=14, pady=6).pack(side="right")
        self._make_button(button_row, "Reject", reject_selected, variant="danger", padx=14, pady=6).pack(side="right", padx=(0, 8))
        self._make_button(button_row, "Approve", approve_selected, variant="primary", padx=14, pady=6).pack(side="right", padx=(0, 8))
        self._make_button(button_row, "Refresh", reload_requests, variant="secondary", padx=14, pady=6).pack(side="right", padx=(0, 8))
        reload_requests()

    def clean_temporary_credential_files(self) -> None:
        """Remove leftover bootstrap and generated temporary-credential files."""
        if not self.require_roles(("root",), "clean temporary credential files"):
            return
        generated_reports = self.repository.list_generated_credential_reports()
        admin_setup_exists = self.repository.get_admin_setup_file_path().exists()
        if not generated_reports and not admin_setup_exists:
            messagebox.showinfo(
                "Nothing To Clean",
                "No temporary credential files were found.",
                parent=self.root,
            )
            return
        confirm_lines = [
            f"Generated password reports: {len(generated_reports)}",
            f"Admin bootstrap file: {'present' if admin_setup_exists else 'not present'}",
            "",
            "Delete these temporary credential files now?",
        ]
        if not messagebox.askyesno(
            "Clean Temporary Credentials",
            "\n".join(confirm_lines),
            parent=self.root,
        ):
            return
        deleted_reports = self.repository.delete_generated_credential_reports()
        deleted_admin_file = self.repository.clear_admin_setup_file()
        self.status_var.set("Temporary credential files cleaned up.")
        self.log_activity(
            f"Temporary credential cleanup removed {deleted_reports} report(s)"
            f"{' and the admin bootstrap file' if deleted_admin_file else ''}."
        )
        self.audit(
            "security.cleanup_temp_credentials",
            "file",
            "temporary_credentials",
            f"Deleted {deleted_reports} generated report(s); admin bootstrap removed={deleted_admin_file}.",
        )
        messagebox.showinfo(
            "Cleanup Complete",
            (
                f"Deleted generated password reports: {deleted_reports}\n"
                f"Deleted admin bootstrap file: {'Yes' if deleted_admin_file else 'No'}"
            ),
            parent=self.root,
        )

    def open_camera_diagnostics(self) -> None:
        """Inspect available cameras and choose which one the app should use."""
        if not self.require_roles(EDIT_ROLES, "inspect camera diagnostics"):
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Camera Diagnostics")
        dialog.transient(self.root)
        dialog.grab_set()
        self._style_dialog_shell(dialog)
        self.center_window(dialog, 760, 460)

        frame = tk.Frame(dialog, bg="white", padx=16, pady=16)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(2, weight=1)

        selected_var = tk.StringVar(value=str(self.selected_camera_index))
        summary_var = tk.StringVar(value="Scanning available cameras...")

        tk.Label(frame, text="Camera Diagnostics", bg="white", fg="#10233d", font=("Segoe UI", 14, "bold")).grid(row=0, column=0, sticky="w")
        tk.Label(frame, textvariable=summary_var, bg="white", fg="#5b7186", font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", pady=(8, 12))

        list_frame = tk.Frame(frame, bg="white")
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.grid_columnconfigure(0, weight=1)
        list_frame.grid_rowconfigure(0, weight=1)

        tree = ttk.Treeview(list_frame, columns=("camera_index", "opened", "frame_ok", "resolution"), show="headings")
        tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        for column, width in (("camera_index", 110), ("opened", 90), ("frame_ok", 90), ("resolution", 120)):
            tree.heading(column, text=self._format_column_heading(column))
            tree.column(column, width=width, anchor="w")

        def load_cameras() -> None:
            tree.delete(*tree.get_children())
            summary_var.set("Scanning available cameras...")
            dialog.update_idletasks()
            try:
                rows = list_available_cameras(6)
            except Exception as exc:
                summary_var.set(f"Camera scan failed: {exc}")
                return
            if not rows:
                summary_var.set("No working camera was detected in the first 6 indexes.")
                return
            summary_var.set(f"Detected {len(rows)} working camera(s). Active camera index: {self.selected_camera_index}.")
            for row in rows:
                tree.insert("", "end", values=(row["camera_index"], row["opened"], row["frame_ok"], row["resolution"]))

        def use_selected_camera() -> None:
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("Camera Required", "Select a camera first.", parent=dialog)
                return
            values = tree.item(selected[0], "values")
            self.selected_camera_index = int(values[0])
            selected_var.set(str(self.selected_camera_index))
            self.status_var.set(f"Camera {self.selected_camera_index} selected for capture and attendance.")
            self.log_activity(f"Camera diagnostics selected camera {self.selected_camera_index}.")
            dialog.destroy()

        def test_selected_camera() -> None:
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("Camera Required", "Select a camera first.", parent=dialog)
                return
            values = tree.item(selected[0], "values")
            probe = probe_camera(int(values[0]))
            messagebox.showinfo(
                "Camera Test",
                (
                    f"Camera Index: {probe['camera_index']}\n"
                    f"Opened: {probe['opened']}\n"
                    f"Frame Read: {probe['frame_ok']}\n"
                    f"Resolution: {probe['resolution'] or 'Unavailable'}"
                ),
                parent=dialog,
            )

        button_row = tk.Frame(frame, bg="white")
        button_row.grid(row=3, column=0, sticky="e", pady=(14, 0))
        tk.Label(button_row, textvariable=selected_var, bg="white", fg="#5b7186", font=("Segoe UI", 9)).pack(side="left", padx=(0, 12))
        self._make_button(button_row, "Close", dialog.destroy, variant="secondary", padx=12, pady=6).pack(side="right")
        self._make_button(button_row, "Use Selected", use_selected_camera, variant="primary", padx=12, pady=6).pack(side="right")
        self._make_button(button_row, "Test Selected", test_selected_camera, variant="secondary", padx=12, pady=6).pack(side="right", padx=(0, 8))
        self._make_button(button_row, "Refresh", load_cameras, variant="secondary", padx=12, pady=6).pack(side="right", padx=(0, 8))

        dialog.after(10, load_cameras)

    def backup_database_file(self) -> None:
        """Create a timestamped backup of the SQLite database."""
        if not self.require_roles(("root",), "create database backups"):
            return
        backup_name = f"attendance_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        backup_path = BACKUPS_DIR / backup_name
        self.repository.backup_database(backup_path)
        self.status_var.set(f"Database backup created: {backup_name}")
        self.log_activity(f"Database backup created at {backup_path}.")
        self.audit(
            "backup.create",
            "database",
            backup_name,
            f"Database backup created at {backup_path}.",
        )
        messagebox.showinfo("Backup Complete", f"Backup saved to:\n{backup_path}", parent=self.root)

    def open_backup_restore_dialog(self) -> None:
        """Restore the main database from an existing backup file."""
        if not self.require_roles(("root",), "restore database backups"):
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Restore Backup")
        dialog.transient(self.root)
        dialog.grab_set()
        self._style_dialog_shell(dialog)
        self.center_window(dialog, 820, 520)

        frame = tk.Frame(dialog, bg="white", padx=16, pady=16)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(2, weight=1)

        tk.Label(frame, text="Restore Backup", bg="white", fg="#10233d", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        tk.Label(
            frame,
            text="Restoring will replace the active database. A safety backup of the current file will be created first.",
            bg="white",
            fg="#5b7186",
            font=("Segoe UI", 10),
            wraplength=720,
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(8, 12))

        list_frame = tk.Frame(frame, bg="white")
        list_frame.grid(row=2, column=0, sticky="nsew")
        list_frame.grid_columnconfigure(0, weight=1)
        list_frame.grid_rowconfigure(0, weight=1)

        columns = ("name", "modified_at", "size_kb")
        tree = ttk.Treeview(list_frame, columns=columns, show="headings")
        tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        for column, width in (("name", 360), ("modified_at", 180), ("size_kb", 100)):
            tree.heading(column, text=self._format_column_heading(column))
            tree.column(column, width=width, anchor="w")

        backup_lookup: dict[str, Path] = {}

        def refresh_backups() -> None:
            tree.delete(*tree.get_children())
            backup_lookup.clear()
            for backup_path in self.repository.list_backup_files(BACKUPS_DIR):
                stat = backup_path.stat()
                modified_at = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                size_kb = f"{max(1, stat.st_size // 1024)} KB"
                backup_lookup[backup_path.name] = backup_path
                tree.insert("", "end", values=(backup_path.name, modified_at, size_kb))

        def restore_selected_backup() -> None:
            selected = tree.selection()
            if not selected:
                messagebox.showwarning("Backup Required", "Select a backup first.", parent=dialog)
                return
            backup_name = str(tree.item(selected[0], "values")[0])
            backup_path = backup_lookup.get(backup_name)
            if backup_path is None:
                messagebox.showerror("Backup Missing", "Selected backup file was not found.", parent=dialog)
                return
            if not messagebox.askyesno(
                "Confirm Restore",
                f"Restore the active database from '{backup_name}'?\n\nThis will replace current data.",
                parent=dialog,
            ):
                return
            try:
                self.repository.restore_database(backup_path)
            except ValueError as exc:
                messagebox.showerror("Restore Failed", str(exc), parent=dialog)
                return
            self.repository = AttendanceRepository(DB_PATH)
            self.repository.refresh_database()
            self.audit("backup.restore", "database", backup_name, f"Restored database from backup {backup_name}.")
            self.log_activity(f"Database restored from backup {backup_name}.")
            self.refresh_dashboard()
            dialog.destroy()
            messagebox.showinfo("Restore Complete", f"Database restored from:\n{backup_path}", parent=self.root)

        button_row = tk.Frame(frame, bg="white")
        button_row.grid(row=3, column=0, sticky="e", pady=(12, 0))
        self._make_button(button_row, "Restore Selected", restore_selected_backup, variant="primary", padx=14, pady=6).pack(side="right")
        self._make_button(button_row, "Refresh", refresh_backups, variant="secondary", padx=14, pady=6).pack(side="right", padx=(0, 8))
        self._make_button(button_row, "Close", dialog.destroy, variant="secondary", padx=14, pady=6).pack(side="right", padx=(0, 8))

        refresh_backups()

    def add_class_option(
        self,
        class_var: tk.StringVar,
        selected_class_var: tk.StringVar,
        dialog: tk.Toplevel,
        refresh_callback=None,
        class_box: ttk.Combobox | None = None,
    ) -> None:
        """Save a new class option from the manager dialog."""
        class_name = class_var.get().strip()
        if not class_name:
            messagebox.showwarning("Class Required", "Enter a class name.", parent=dialog)
            return
        self.repository.add_class(class_name)
        class_var.set("")
        selected_class_var.set(class_name)
        if class_box is not None:
            class_box["values"] = self.repository.list_classes()
        if refresh_callback is not None:
            refresh_callback()
        self.log_activity(f"Class added: {class_name}.")
        self.audit("class.add", "class", class_name, "Class added from dashboard.")
        messagebox.showinfo("Class Added", f"Class {class_name} is now available.", parent=dialog)

    def add_subject_option(
        self,
        selected_class_var: tk.StringVar,
        subject_var: tk.StringVar,
        academic_year_var: tk.StringVar,
        semester_var: tk.StringVar,
        dialog: tk.Toplevel,
        refresh_callback=None,
        section_name: str = "",
    ) -> None:
        """Save a new subject under the selected class."""
        class_name = selected_class_var.get().strip()
        subject_name = subject_var.get().strip()
        if not class_name:
            messagebox.showwarning("Class Required", "Select a class first.", parent=dialog)
            return
        if not subject_name:
            messagebox.showwarning("Subject Required", "Enter a subject name.", parent=dialog)
            return
        self.repository.add_subject(
            class_name,
            subject_name,
            section_name=section_name,
            academic_year=academic_year_var.get().strip(),
            semester=semester_var.get().strip(),
        )
        subject_var.set("")
        if refresh_callback is not None:
            refresh_callback()
        self.log_activity(f"Subject added: {subject_name} in class {class_name}.")
        self.audit("subject.add", "subject", f"{class_name}:{subject_name}", "Subject added from dashboard.")
        messagebox.showinfo(
            "Subject Added",
            f"Subject {subject_name} is now available in class {class_name}.",
            parent=dialog,
        )

    def add_section_option(
        self,
        selected_class_var: tk.StringVar,
        section_var: tk.StringVar,
        academic_year_var: tk.StringVar,
        semester_var: tk.StringVar,
        dialog: tk.Toplevel,
        refresh_callback=None,
    ) -> None:
        """Save a new section under the selected class."""
        class_name = selected_class_var.get().strip()
        section_name = section_var.get().strip()
        if not class_name:
            messagebox.showwarning("Class Required", "Select a class first.", parent=dialog)
            return
        if not section_name:
            messagebox.showwarning("Section Required", "Enter a section name.", parent=dialog)
            return
        self.repository.add_section(
            class_name,
            section_name,
            academic_year=academic_year_var.get().strip(),
            semester=semester_var.get().strip(),
        )
        section_var.set("")
        if refresh_callback is not None:
            refresh_callback()
        self.log_activity(f"Section added: {section_name} in class {class_name}.")
        self.audit("section.add", "section", f"{class_name}:{section_name}", "Section added from dashboard.")
        messagebox.showinfo(
            "Section Added",
            f"Section {section_name} is now available in class {class_name}.",
            parent=dialog,
        )

    def add_semester_option(
        self,
        selected_class_var: tk.StringVar,
        academic_year_var: tk.StringVar,
        new_semester_var: tk.StringVar,
        selected_semester_var: tk.StringVar | None,
        dialog: tk.Toplevel,
        refresh_callback=None,
    ) -> None:
        """Save a new semester under the selected class and academic year."""
        class_name = selected_class_var.get().strip()
        semester_name = new_semester_var.get().strip()
        if not class_name:
            messagebox.showwarning("Class Required", "Select a class first.", parent=dialog)
            return
        if not semester_name:
            messagebox.showwarning("Semester Required", "Enter a semester name.", parent=dialog)
            return
        self.repository.add_semester(
            class_name,
            academic_year_var.get().strip(),
            semester_name,
        )
        new_semester_var.set("")
        if selected_semester_var is not None:
            selected_semester_var.set(semester_name)
        if refresh_callback is not None:
            refresh_callback()
        self.log_activity(f"Semester added: {semester_name} in class {class_name}.")
        self.audit("semester.add", "semester", f"{class_name}:{semester_name}", "Semester added from dashboard.")
        messagebox.showinfo(
            "Semester Added",
            f"Semester {semester_name} is now available in class {class_name}.",
            parent=dialog,
        )

    def apply_selected_context(
        self,
        dialog: tk.Toplevel,
        class_name: str,
        subject_tree: ttk.Treeview,
    ) -> None:
        """Apply the selected class and subject to the whole dashboard."""
        if not class_name:
            messagebox.showwarning("Class Required", "Select a class first.", parent=dialog)
            return
        selection = subject_tree.selection()
        if not selection:
            messagebox.showwarning("Subject Required", "Select a subject first.", parent=dialog)
            return
        values = subject_tree.item(selection[0], "values")
        subject_name = str(values[0]) if values else ""
        if not subject_name:
            messagebox.showwarning("Subject Required", "Select a subject first.", parent=dialog)
            return
        selected_section = str(values[1]) if len(values) > 1 else ""
        selected_year = str(values[2]) if len(values) > 2 else ""
        selected_semester = str(values[3]) if len(values) > 3 else ""
        self.current_class_var.set(class_name)
        self.current_section_var.set(selected_section)
        if selected_year:
            self.current_academic_year_var.set(selected_year)
        if selected_semester:
            self.current_semester_var.set(selected_semester)
        self.current_subject_var.set(subject_name)
        dialog.destroy()
        self.status_var.set(f"Switched to class {class_name} and subject {subject_name}.")
        self.log_activity(f"Context switched to class {class_name} and subject {subject_name}.")
        self.refresh_dashboard()

    def apply_current_timetable_context(self, show_message: bool = True) -> bool:
        """Use the active timetable slot for the dashboard context when available."""
        active_entry = self.repository.get_active_timetable_entry(
            self.current_user_scope.get("class_name") if self.is_teacher_user() else None
        )
        if (
            active_entry is not None
            and self.is_teacher_user()
            and self.current_user_scope.get("subject_name", "").strip()
            and str(active_entry.get("subject_name", "")) != self.current_user_scope.get("subject_name", "").strip()
        ):
            active_entry = None
        if active_entry is None:
            if show_message:
                upcoming = self.repository.get_next_timetable_entry(
                    self.current_user_scope.get("class_name") if self.is_teacher_user() else None
                )
                if upcoming is None:
                    message = "No active timetable slot is scheduled right now."
                else:
                    message = (
                        "No active timetable slot right now. "
                        f"Next: {upcoming['class_name']} - {upcoming['subject_name']} at {upcoming['start_time']}."
                    )
                self.status_var.set(message)
                messagebox.showinfo("Timetable Suggestion", message, parent=self.root)
            return False

        self.current_class_var.set(active_entry["class_name"])
        self.current_subject_var.set(active_entry["subject_name"])
        self.refresh_dashboard()
        message = (
            f"Using active timetable slot: {active_entry['class_name']} - "
            f"{active_entry['subject_name']} ({active_entry['start_time']} to {active_entry['end_time']})."
        )
        self.status_var.set(message)
        self.log_activity(message)
        if show_message:
            messagebox.showinfo("Timetable Context Applied", message, parent=self.root)
        return True

    def load_attendance_records(self) -> list[dict[str, str]]:
        """Load attendance rows for the active class and subject."""
        return self.repository.list_attendance(
            None,
            self.current_class_var.get(),
            self.current_subject_var.get(),
        )

    def get_attendance_filter_kwargs(self) -> dict[str, str | None]:
        """Build the current attendance filter set for table and export actions."""
        selected_date = self.date_filter_var.get().strip()
        date_from = self.date_from_var.get().strip()
        date_to = self.date_to_var.get().strip()
        status_filter = self.attendance_status_filter_var.get().strip()
        source_filter = self.attendance_source_filter_var.get().strip()
        student_query = self.attendance_search_var.get().strip()
        return {
            "date_filter": selected_date or None,
            "class_name": self.current_class_var.get(),
            "section_name": self.current_section_var.get().strip() or None,
            "academic_year": self.current_academic_year_var.get().strip() or None,
            "semester": self.current_semester_var.get().strip() or None,
            "subject_name": self.current_subject_var.get(),
            "date_from": date_from or None,
            "date_to": date_to or None,
            "status": None if status_filter in ("", "All") else status_filter,
            "source": None if source_filter in ("", "All") else source_filter,
            "student_id": self.current_student_id or None,
            "student_query": student_query or None,
        }

    def get_student_rows(self) -> list[tuple[str, str, int]]:
        """Build the student table rows for the active class roster."""
        if self.current_student_id:
            student = self.repository.get_student_by_student_id(self.current_student_id)
            students = [student] if student is not None else []
        else:
            students = self.repository.list_students_for_context(
                self.current_class_var.get(),
                self.current_subject_var.get(),
                section_name=self.current_section_var.get().strip() or None,
                academic_year=self.current_academic_year_var.get().strip() or None,
                semester=self.current_semester_var.get().strip() or None,
            )
        rows: list[tuple[str, str, int]] = []

        for student in students:
            person_dir = DATASET_DIR / f"{student.student_id}_{student.label_id}"
            sample_count = len(list(person_dir.glob("*.jpg"))) if person_dir.exists() else 0
            rows.append((student.student_id, student.name, sample_count))

        return rows

    def refresh_dashboard(self) -> None:
        """Refresh summary cards, context labels, student table, and attendance table."""
        self.refresh_context_options()
        selected_date = self.date_filter_var.get().strip() or datetime.now().strftime("%Y-%m-%d")
        summary = self.repository.get_summary(
            self.current_class_var.get(),
            self.current_subject_var.get(),
            academic_year=self.current_academic_year_var.get().strip() or None,
            semester=self.current_semester_var.get().strip() or None,
        )
        self.student_count_var.set(str(summary["student_count"]))
        self.today_attendance_var.set(str(summary["today_attendance"]))
        self.model_status_var.set("Ready" if self.get_active_model_path().exists() else "Not Trained")
        model_state = self.repository.get_model_state(self.current_class_var.get())
        if model_state["is_dirty"]:
            self.model_status_var.set("Retrain")
        session = self.repository.get_attendance_session(
            self.current_class_var.get(),
            self.current_subject_var.get(),
            selected_date,
        )
        if session.get("locked_at"):
            self.model_status_var.set("Locked")
        if self.current_student_id:
            student = self.repository.get_student_by_student_id(self.current_student_id)
            student_rows = self.repository.get_student_attendance_history(self.current_student_id, limit=250)
            student_summary = self.repository.get_student_attendance_summary(self.current_student_id)
            self.student_count_var.set(student.class_name if student is not None else "-")
            self.today_attendance_var.set(f"{student_summary['attendance_rate']}%")
            self.model_status_var.set(
                "Low Attendance"
                if float(student_summary["attendance_rate"]) < self.repository.LOW_ATTENDANCE_THRESHOLD and int(student_summary["total_records"]) >= 3
                else "On Track"
            )
            self.context_var.set(
                f"Student ID: {self.current_student_id}   |   Class: {self.current_class_var.get()}   |   Section: {self.current_section_var.get() or '-'}   |   Subject: {self.current_subject_var.get()}   |   Year: {self.current_academic_year_var.get()}   |   Semester: {self.current_semester_var.get() or '-'}"
            )
            self.last_marked_var.set(student_rows[0]["status"] if student_rows else "None")
            if float(student_summary["attendance_rate"]) < self.repository.LOW_ATTENDANCE_THRESHOLD and int(student_summary["total_records"]) >= 3:
                self.status_var.set(
                    f"Attendance warning: your overall attendance is {student_summary['attendance_rate']}%."
                )
        else:
            self.context_var.set(
                f"Class: {self.current_class_var.get()}   |   Section: {self.current_section_var.get() or '-'}   |   Subject: {self.current_subject_var.get()}   |   Year: {self.current_academic_year_var.get()}   |   Semester: {self.current_semester_var.get() or '-'}"
            )
            latest_rows = self.repository.list_attendance(
                None,
                self.current_class_var.get(),
                self.current_subject_var.get(),
                academic_year=self.current_academic_year_var.get().strip() or None,
                semester=self.current_semester_var.get().strip() or None,
                section_name=self.current_section_var.get().strip() or None,
            )
            self.last_marked_var.set(latest_rows[0]["name"] if latest_rows else "None")
        self.refresh_student_table()
        self.refresh_attendance_table()

    def refresh_student_table(self) -> None:
        """Refresh the student list based on search text and active class."""
        for item in self.student_tree.get_children():
            self.student_tree.delete(item)

        query = self.student_search_var.get().strip().lower()
        for student_id, name, samples in self.get_student_rows():
            haystack = f"{student_id} {name}".lower()
            if query and query not in haystack:
                continue
            self.student_tree.insert("", "end", values=(student_id, name, samples))

    def refresh_attendance_table(self) -> None:
        """Refresh the attendance table using the active filters and current context."""
        for item in self.attendance_tree.get_children():
            self.attendance_tree.delete(item)

        selected_date = self.date_filter_var.get().strip()
        rows = self.repository.list_attendance(**self.get_attendance_filter_kwargs())
        is_student_viewer = self.is_student_viewer()

        session_date = self.get_effective_attendance_date()
        if is_student_viewer:
            self.attendance_session_var.set("Session status: Personal attendance view")
        else:
            session = self.repository.get_attendance_session(
                self.current_class_var.get(),
                self.current_subject_var.get(),
                session_date,
            )
            if session.get("locked_at"):
                self.attendance_session_var.set(
                    f"Session status: Locked by {session.get('locked_by') or 'root'} at {session.get('locked_at')}."
                )
            elif session.get("finalized_at"):
                self.attendance_session_var.set(
                    f"Session status: Finalized by {session.get('finalized_by') or 'root'} at {session.get('finalized_at')}."
                )
            else:
                self.attendance_session_var.set("Session status: Open")

        if self.date_from_var.get().strip() or self.date_to_var.get().strip():
            self.attendance_summary_var.set(
                (
                    f"Showing {len(rows)} of your attendance record(s) for range "
                    f"{self.date_from_var.get().strip() or '...'} to {self.date_to_var.get().strip() or '...'}."
                )
                if is_student_viewer
                else (
                    f"Showing {len(rows)} attendance record(s) for range "
                    f"{self.date_from_var.get().strip() or '...'} to {self.date_to_var.get().strip() or '...'}."
                )
            )
        elif selected_date:
            self.attendance_summary_var.set(
                f"Showing {len(rows)} of your attendance record(s) for {selected_date}."
                if is_student_viewer
                else f"Showing {len(rows)} attendance record(s) for {selected_date}."
            )
        else:
            self.attendance_summary_var.set(
                f"Showing all {len(rows)} of your attendance record(s)."
                if is_student_viewer
                else f"Showing all {len(rows)} attendance record(s)."
            )

        rows.sort(key=lambda row: (row.get("date", ""), row.get("time", "")), reverse=True)
        for row in rows:
            self.attendance_tree.insert(
                "",
                "end",
                values=(
                    row.get("id", ""),
                    row.get("student_id", ""),
                    row.get("name", ""),
                    row.get("date", ""),
                    row.get("time", ""),
                    row.get("status", ""),
                    row.get("source", ""),
                ),
            )

    def set_today_filter(self) -> None:
        """Quick action to filter attendance to today."""
        self.date_filter_var.set(datetime.now().strftime("%Y-%m-%d"))
        self.date_from_var.set("")
        self.date_to_var.set("")
        self.refresh_attendance_table()

    def get_effective_attendance_date(self) -> str:
        """Return the dashboard date filter, defaulting to today."""
        return self.date_filter_var.get().strip() or datetime.now().strftime("%Y-%m-%d")

    def auto_mark_absentees_for_selected_date(self) -> None:
        """Root-only helper to mark all missing students absent for the selected date."""
        if not self.require_roles(("root",), "auto-mark absentees"):
            return

        date_value = self.get_effective_attendance_date()
        missing_students = self.repository.get_missing_students_for_attendance(
            self.current_class_var.get(),
            self.current_subject_var.get(),
            date_value,
        )
        if not missing_students:
            messagebox.showinfo(
                "No Missing Students",
                "All students already have attendance for this class, subject, and date.",
                parent=self.root,
            )
            return

        if not messagebox.askyesno(
            "Auto Mark Absentees",
            (
                f"Mark {len(missing_students)} missing student(s) as Absent for "
                f"{self.current_class_var.get()} - {self.current_subject_var.get()} on {date_value}?"
            ),
            parent=self.root,
        ):
            return

        try:
            result = self.repository.auto_mark_absent_students(
                self.current_class_var.get(),
                self.current_subject_var.get(),
                date_value,
                self.current_username,
            )
        except ValueError as exc:
            messagebox.showerror("Absent Completion Failed", str(exc), parent=self.root)
            return

        self.audit(
            "attendance.auto_absent",
            "attendance",
            f"{self.current_class_var.get()}:{self.current_subject_var.get()}:{date_value}",
            f"Auto-marked {result['created']} student(s) absent.",
        )
        self.status_var.set(f"Auto-marked {result['created']} student(s) absent for {date_value}.")
        self.log_activity(f"Auto-marked {result['created']} absentees for {date_value}.")
        self.refresh_dashboard()

    def finalize_attendance_for_selected_date(self) -> None:
        """Root-only helper to finalize and lock attendance for the selected date."""
        if not self.require_roles(("root",), "finalize attendance"):
            return

        date_value = self.get_effective_attendance_date()
        session = self.repository.get_attendance_session(
            self.current_class_var.get(),
            self.current_subject_var.get(),
            date_value,
        )
        if session.get("locked_at"):
            messagebox.showinfo(
                "Already Locked",
                "Attendance for this class, subject, and date is already finalized and locked.",
                parent=self.root,
            )
            return

        missing_students = self.repository.get_missing_students_for_attendance(
            self.current_class_var.get(),
            self.current_subject_var.get(),
            date_value,
        )
        confirm_message = (
            f"Finalize attendance for {self.current_class_var.get()} - {self.current_subject_var.get()} on {date_value}?\n\n"
            f"Missing students to auto-mark Absent: {len(missing_students)}\n"
            "After finalizing, further attendance changes for this date will be locked."
        )
        if not messagebox.askyesno("Finalize Attendance", confirm_message, parent=self.root):
            return

        try:
            result = self.repository.finalize_attendance_session(
                self.current_class_var.get(),
                self.current_subject_var.get(),
                date_value,
                self.current_username or "admin",
            )
        except ValueError as exc:
            messagebox.showerror("Finalize Failed", str(exc), parent=self.root)
            return

        self.audit(
            "attendance.finalize",
            "attendance_session",
            f"{self.current_class_var.get()}:{self.current_subject_var.get()}:{date_value}",
            (
                f"Finalized attendance and locked session. "
                f"Auto-marked {result['created_absent_count']} student(s) absent."
            ),
        )
        self.status_var.set(f"Attendance finalized and locked for {date_value}.")
        self.log_activity(
            f"Attendance finalized for {self.current_class_var.get()} - {self.current_subject_var.get()} on {date_value}."
        )
        self.refresh_dashboard()

    def open_date_picker(self, variable: tk.StringVar, parent: tk.Misc | None = None) -> None:
        """Open the reusable calendar popup for any date field."""
        DatePickerDialog(parent or self.root, variable)

    def prompt_correction_request_reason(self, parent: tk.Misc) -> str | None:
        """Show a small form with an explicit submit button for locked-record correction requests."""
        dialog = tk.Toplevel(parent)
        dialog.title("Request Correction")
        dialog.transient(parent)
        dialog.grab_set()
        dialog.resizable(False, False)
        self._style_dialog_shell(dialog)
        self.center_window(dialog, 430, 230)

        result: dict[str, str | None] = {"reason": None}

        frame = tk.Frame(dialog, bg="white", padx=18, pady=18)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(0, weight=1)

        tk.Label(
            frame,
            text="Locked attendance record",
            bg="white",
            fg="#10233d",
            font=("Segoe UI", 12, "bold"),
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            frame,
            text="Enter the reason for this correction request, then submit it for root approval.",
            bg="white",
            fg="#4f6478",
            font=("Segoe UI", 9),
            justify="left",
            wraplength=360,
        ).grid(row=1, column=0, sticky="w", pady=(8, 10))

        reason_box = tk.Text(frame, height=5, width=42, wrap="word", relief="solid", borderwidth=1)
        reason_box.grid(row=2, column=0, sticky="ew")

        def cancel() -> None:
            dialog.destroy()

        def submit() -> None:
            reason = reason_box.get("1.0", "end").strip()
            if not reason:
                messagebox.showwarning("Reason Required", "Enter a reason before submitting.", parent=dialog)
                return
            result["reason"] = reason
            dialog.destroy()

        button_row = tk.Frame(frame, bg="white")
        button_row.grid(row=3, column=0, sticky="e", pady=(16, 0))
        self._make_button(button_row, "Cancel", cancel, variant="secondary", padx=14, pady=6).pack(side="right")
        self._make_button(button_row, "Submit Request", submit, variant="primary", padx=14, pady=6).pack(side="right", padx=(0, 8))

        reason_box.focus_set()
        reason_box.bind("<Control-Return>", lambda _event: submit())
        dialog.wait_window()
        return result["reason"]

    def center_window(self, window: tk.Toplevel, width: int, height: int) -> None:
        """Center dialogs on top of the main application window."""
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        fitted_width, fitted_height = prepare_modal_size(
            window,
            width,
            height,
            screen_width,
            screen_height,
        )
        root_x = self.root.winfo_rootx()
        root_y = self.root.winfo_rooty()
        root_width = self.root.winfo_width()
        root_height = self.root.winfo_height()
        x_pos, y_pos = compute_centered_position(
            root_x,
            root_y,
            root_width,
            root_height,
            fitted_width,
            fitted_height,
            screen_width,
            screen_height,
        )
        present_modal_window(window, x_pos=x_pos, y_pos=y_pos, width=fitted_width, height=fitted_height)

    def center_dialog_on_screen(self, window: tk.Toplevel, width: int, height: int) -> None:
        """Center a standalone dialog on screen when the main root is still hidden."""
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        fitted_width, fitted_height = prepare_modal_size(
            window,
            width,
            height,
            screen_width,
            screen_height,
        )
        x_pos, y_pos = compute_centered_position(
            0,
            0,
            screen_width,
            screen_height,
            fitted_width,
            fitted_height,
            screen_width,
            screen_height,
        )
        present_modal_window(window, x_pos=x_pos, y_pos=y_pos, width=fitted_width, height=fitted_height)

    def create_scrollable_dialog_body(
        self,
        dialog: tk.Toplevel,
        *,
        bg: str | None = None,
        padx: int = 0,
        pady: int = 0,
    ) -> tuple[tk.Frame, tk.Canvas]:
        """Create a reusable scrollable dialog body for tall content views."""
        shell_bg = bg or self.colors["surface"]
        outer = tk.Frame(dialog, bg=shell_bg)
        outer.pack(fill="both", expand=True)
        outer.grid_rowconfigure(0, weight=1)
        outer.grid_columnconfigure(0, weight=1)

        canvas = tk.Canvas(
            outer,
            bg=shell_bg,
            highlightthickness=0,
            bd=0,
        )
        canvas.grid(row=0, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview, style="App.Vertical.TScrollbar")
        y_scroll.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=y_scroll.set)

        body = tk.Frame(canvas, bg=shell_bg, padx=padx, pady=pady)
        canvas_window = canvas.create_window((0, 0), window=body, anchor="nw")

        def sync_scroll_region(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def sync_body_width(event) -> None:
            canvas.itemconfigure(canvas_window, width=event.width)

        body.bind("<Configure>", sync_scroll_region)
        canvas.bind("<Configure>", sync_body_width)
        canvas.bind(
            "<MouseWheel>",
            lambda event: canvas.yview_scroll(-1 * max(1, int(abs(event.delta) / 120)) if event.delta > 0 else max(1, int(abs(event.delta) / 120)), "units"),
        )
        return body, canvas

    def log_activity(self, message: str) -> None:
        """Push a short event message into the dashboard activity feed."""
        timestamped = f"{datetime.now().strftime('%H:%M:%S')}  {message}"
        self.activity_var.set(message)
        self.activity_list.insert(0, timestamped)
        LOGGER.debug("Dashboard activity updated.")
        while self.activity_list.size() > 8:
            self.activity_list.delete(8)

    def format_cooldown_seconds(self, seconds: int) -> str:
        """Render a compact login cooldown label for staff and student messages."""
        safe_seconds = max(0, int(seconds))
        minutes, remaining_seconds = divmod(safe_seconds, 60)
        return f"{minutes}m {remaining_seconds}s" if minutes else f"{remaining_seconds}s"

    def format_csv_date(self, value: str) -> str:
        """Export dates as text so spreadsheet apps do not auto-format them badly."""
        if not value:
            return ""
        return f"'{value}"

    def format_csv_time(self, value: str) -> str:
        """Export times as text so spreadsheet apps preserve the original value."""
        if not value:
            return ""
        return f"'{value}"

    def sanitize_csv_cell(self, value: str) -> str:
        """Prevent spreadsheet formula execution when CSV exports are opened in office tools."""
        text = str(value or "")
        if text and text[0] in ("=", "+", "-", "@"):
            return f"'{text}"
        return text

    def write_generated_credentials_report(
        self,
        prefix: str,
        credentials: list[dict[str, str]],
        *,
        export_path: Path | None = None,
    ) -> Path:
        """Persist generated student credentials so staff can distribute them safely."""
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        harden_permissions(EXPORTS_DIR, is_dir=True)
        report_path = export_path or (EXPORTS_DIR / f"{prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        harden_permissions(report_path.parent, is_dir=True)
        with report_path.open("w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(["student_id", "name", "temporary_password"])
            for row in credentials:
                writer.writerow(
                    [
                        self.sanitize_csv_cell(row.get("student_id", "")),
                        self.sanitize_csv_cell(row.get("name", "")),
                        self.sanitize_csv_cell(row.get("password", "")),
                    ]
                )
        harden_permissions(report_path, is_dir=False)
        return report_path

    def mark_attendance_live(self, student_id: str, name: str) -> bool:
        """Store one live attendance event and immediately refresh the dashboard table."""
        now = datetime.now()
        status_value = self.repository.suggest_attendance_status(
            self.current_class_var.get(),
            self.current_subject_var.get(),
            now,
        )
        try:
            was_added = self.repository.add_attendance_record(
                student_id,
                name,
                self.current_class_var.get(),
                self.current_subject_var.get(),
                now.strftime("%Y-%m-%d"),
                now.strftime("%H:%M:%S"),
                status_value,
                source="camera",
            )
        except ValueError as exc:
            self.status_var.set(str(exc))
            return False

        if was_added:
            self.last_marked_var.set(name)
            self.status_var.set(f"Attendance marked for {name} as {status_value}.")
            self.log_activity(
                f"Attendance marked for {name} as {status_value} on {now.strftime('%Y-%m-%d %H:%M:%S')}."
            )
            self.audit(
                "attendance.camera_add",
                "attendance",
                student_id,
                (
                    f"Marked {name} as {status_value} in "
                    f"{self.current_class_var.get()} - {self.current_subject_var.get()}."
                ),
            )
            self.date_filter_var.set(now.strftime("%Y-%m-%d"))
            self.today_attendance_var.set(
                str(
                    self.repository.get_summary(
                        self.current_class_var.get(),
                        self.current_subject_var.get(),
                    )["today_attendance"]
                )
            )
            self.refresh_attendance_table()
            try:
                self.root.update_idletasks()
            except tk.TclError:
                pass
        return was_added

    def on_student_select(self, _event=None) -> None:
        """Update the small status line when a student row is selected."""
        selected = self.student_tree.selection()
        if not selected:
            self.selected_student_var.set(
                "Your profile record is shown below."
                if self.is_student_viewer()
                else "No student selected"
            )
            return
        values = self.student_tree.item(selected[0], "values")
        self.selected_student_var.set(
            f"My profile: {values[1]} ({values[0]})"
            if self.is_student_viewer()
            else f"Selected student: {values[1]} ({values[0]})"
        )

    def on_attendance_select(self, _event=None) -> None:
        """Update the small status line when an attendance row is selected."""
        selected = self.attendance_tree.selection()
        if not selected:
            self.selected_attendance_var.set(
                "Select one of your attendance rows to review it."
                if self.is_student_viewer()
                else "No attendance record selected"
            )
            return
        values = self.attendance_tree.item(selected[0], "values")
        self.selected_attendance_var.set(
            f"My attendance: #{values[0]} on {values[3]} ({values[5]})"
            if self.is_student_viewer()
            else f"Selected attendance: #{values[0]} {values[2]} on {values[3]}"
        )

    def get_selected_student(self) -> Student | None:
        """Return the student currently selected in the left table."""
        if self.current_student_id:
            return self.repository.get_student_by_student_id(self.current_student_id)
        selected = self.student_tree.selection()
        if not selected:
            messagebox.showwarning("Student Required", "Select a student first.", parent=self.root)
            return None
        values = self.student_tree.item(selected[0], "values")
        return self.repository.get_student_by_student_id(str(values[0]))

    def ensure_student_edit_scope(self, student: Student, *, parent: tk.Misc | None = None) -> bool:
        """Block scoped staff from editing students outside their assigned class."""
        if not self.is_teacher_user():
            return True
        scoped_class = self.current_user_scope.get("class_name", "").strip()
        if not scoped_class or student.class_name == scoped_class:
            return True
        messagebox.showwarning(
            "Scope Restricted",
            "You can only modify students assigned to your scoped class.",
            parent=parent or self.root,
        )
        return False

    def open_student_profile(self) -> None:
        """Open a richer profile view for the selected student with history details."""
        student = self.get_selected_student()
        if student is None:
            return

        dialog = tk.Toplevel(self.root)
        dialog.title(f"Student Profile - {student.name}")
        dialog.transient(self.root)
        dialog.grab_set()
        self._style_dialog_shell(dialog)
        self.center_window(dialog, 1040, 720)

        person_dir = DATASET_DIR / f"{student.student_id}_{student.label_id}"
        sample_count = len(list(person_dir.glob("*.jpg"))) if person_dir.exists() else 0
        history = self.repository.get_student_attendance_history(student.student_id, limit=250)
        summary = self.repository.get_student_attendance_summary(student.student_id)
        subject_breakdown = self.repository.get_student_subject_breakdown(student.student_id)

        frame, _canvas = self.create_scrollable_dialog_body(dialog, bg=self.colors["surface"], padx=18, pady=18)
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(4, weight=1)

        tk.Label(
            frame,
            text="Student Profile",
            bg=self.colors["surface"],
            fg=self.colors["text"],
            font=("Segoe UI Semibold", 18),
        ).grid(row=0, column=0, sticky="w")

        header_line = (
            f"{student.class_name}  |  {student.section_name or '-'}  |  "
            f"{student.academic_year or '-'}  |  {student.semester or '-'}"
        )
        tk.Label(
            frame,
            text=header_line,
            bg=self.colors["surface"],
            fg=self.colors["muted"],
            font=self.fonts["body"],
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        top_row = tk.Frame(frame, bg=self.colors["surface"])
        top_row.grid(row=2, column=0, sticky="ew", pady=(14, 16))
        top_row.grid_columnconfigure(0, weight=0)
        top_row.grid_columnconfigure(1, weight=1)

        photo_card = tk.Frame(top_row, bg=self.colors["surface_alt"], padx=16, pady=16, highlightbackground=self.colors["border"], highlightthickness=1)
        photo_card.grid(row=0, column=0, sticky="nsw")
        details_area = tk.Frame(top_row, bg=self.colors["surface"])
        details_area.grid(row=0, column=1, sticky="nsew", padx=(14, 0))
        details_area.grid_columnconfigure(0, weight=2)
        details_area.grid_columnconfigure(1, weight=1)

        identity_card = tk.Frame(details_area, bg=self.colors["surface_alt"], padx=16, pady=16, highlightbackground=self.colors["border"], highlightthickness=1)
        identity_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        identity_card.grid_columnconfigure(1, weight=1)
        academic_card = tk.Frame(details_area, bg=self.colors["surface_alt"], padx=16, pady=16, highlightbackground=self.colors["border"], highlightthickness=1)
        academic_card.grid(row=0, column=1, sticky="nsew")
        academic_card.grid_columnconfigure(1, weight=1)

        stats_card = tk.Frame(details_area, bg=self.colors["info_bg"], padx=16, pady=16, highlightbackground=self.colors["border"], highlightthickness=1)
        stats_card.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        for column in range(3):
            stats_card.grid_columnconfigure(column, weight=1)

        def add_fact_row(parent: tk.Frame, row_index: int, label: str, value: str) -> None:
            tk.Label(
                parent,
                text=label,
                bg=parent.cget("bg"),
                fg=self.colors["muted"],
                font=self.fonts["caption"],
                anchor="w",
            ).grid(row=row_index, column=0, sticky="nw", pady=4, padx=(0, 12))
            tk.Label(
                parent,
                text=value or "-",
                bg=parent.cget("bg"),
                fg=self.colors["text"],
                font=self.fonts["body"],
                anchor="w",
                justify="left",
                wraplength=320,
            ).grid(row=row_index, column=1, sticky="nw", pady=4)

        tk.Label(
            photo_card,
            text="Profile Photo",
            bg=self.colors["surface_alt"],
            fg=self.colors["text"],
            font=self.fonts["subheading"],
            anchor="w",
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))
        profile_photo = self._load_student_profile_photo(student)
        if profile_photo is not None:
            photo_label = tk.Label(
                photo_card,
                image=profile_photo,
                bg=self.colors["surface_alt"],
                highlightbackground=self.colors["border"],
                highlightthickness=1,
            )
            photo_label.image = profile_photo
            dialog.profile_photo_image = profile_photo
            photo_label.grid(row=1, column=0, sticky="nw")
        else:
            tk.Label(
                photo_card,
                text="No photo available yet.\nCapture or re-capture a front sample.",
                bg=self.colors["surface_alt"],
                fg=self.colors["muted"],
                font=self.fonts["body"],
                justify="center",
            ).grid(row=1, column=0, sticky="nsew", ipadx=18, ipady=40)

        tk.Label(identity_card, text="Identity", bg=self.colors["surface_alt"], fg=self.colors["text"], font=self.fonts["subheading"]).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        add_fact_row(identity_card, 1, "Name", student.name)
        add_fact_row(identity_card, 2, "TNU ID" if self.is_student_viewer() else "UID", student.student_id)
        add_fact_row(identity_card, 3, "Status", student.status)
        add_fact_row(identity_card, 4, "Face Samples", str(sample_count))
        add_fact_row(identity_card, 5, "Guardian", student.guardian_name or "-")
        if not self.is_student_viewer():
            add_fact_row(identity_card, 6, "Profile Created", student.created_at)
            add_fact_row(identity_card, 7, "Last Updated", student.updated_at)
            add_fact_row(identity_card, 8, "Recognizer Label ID", str(student.label_id))

        tk.Label(academic_card, text="Academic Details", bg=self.colors["surface_alt"], fg=self.colors["text"], font=self.fonts["subheading"]).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        add_fact_row(academic_card, 1, "Class", student.class_name)
        add_fact_row(academic_card, 2, "Section", student.section_name or "-")
        add_fact_row(academic_card, 3, "Batch", student.batch_name or "-")
        add_fact_row(academic_card, 4, "Academic Year", student.academic_year or "-")
        add_fact_row(academic_card, 5, "Semester", student.semester or "-")
        if self.is_root_user():
            face_signature = self.repository.get_student_face_signature(student.student_id)
            add_fact_row(academic_card, 6, "Face Structure", face_signature or "Not generated")

        stat_tiles = [
            ("Attendance Rate", f"{summary['attendance_rate']}%"),
            ("Total Records", str(summary["total_records"])),
            ("Present or Late", str(summary["present_like"])),
            ("Absent", str(summary["absent_count"])),
            ("Late", str(summary["late_count"])),
            ("Latest Date", summary["latest_date"] or "N/A"),
        ]
        for idx, (label, value) in enumerate(stat_tiles):
            tile = tk.Frame(stats_card, bg=self.colors["info_bg"], padx=12, pady=12)
            tile.grid(row=idx // 3, column=idx % 3, sticky="ew", padx=4, pady=4)
            tk.Label(
                tile,
                text=label,
                bg=self.colors["info_bg"],
                fg=self.colors["muted"],
                font=self.fonts["caption"],
            ).pack(anchor="w")
            tk.Label(
                tile,
                text=value,
                bg=self.colors["info_bg"],
                fg=self.colors["text"],
                font=("Segoe UI Semibold", 13),
            ).pack(anchor="w", pady=(2, 0))

        breakdown_frame = tk.Frame(frame, bg=self.colors["surface"])
        breakdown_frame.grid(row=3, column=0, sticky="nsew", pady=(0, 16))
        breakdown_frame.grid_columnconfigure(0, weight=1)
        breakdown_frame.grid_rowconfigure(1, weight=1)

        tk.Label(
            breakdown_frame,
            text="Subject Breakdown",
            bg=self.colors["surface"],
            fg=self.colors["text"],
            font=self.fonts["subheading"],
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        breakdown_tree = ttk.Treeview(
            breakdown_frame,
            columns=("subject_name", "attendance_rate", "total_records", "present_like", "absent_count", "late_count", "excused_count"),
            show="headings",
            height=6,
            style="Card.Treeview",
        )
        breakdown_tree.grid(row=1, column=0, sticky="nsew")
        breakdown_scroll = ttk.Scrollbar(breakdown_frame, orient="vertical", command=breakdown_tree.yview, style="App.Vertical.TScrollbar")
        breakdown_scroll.grid(row=1, column=1, sticky="ns")
        breakdown_tree.configure(yscrollcommand=breakdown_scroll.set)

        for col, width in (
            ("subject_name", 170),
            ("attendance_rate", 120),
            ("total_records", 100),
            ("present_like", 100),
            ("absent_count", 90),
            ("late_count", 90),
            ("excused_count", 100),
        ):
            breakdown_tree.heading(col, text=self._format_column_heading(col))
            breakdown_tree.column(col, width=width, anchor="w")

        if subject_breakdown:
            for row in subject_breakdown:
                breakdown_tree.insert(
                    "",
                    "end",
                    values=(
                        row.get("subject_name", ""),
                        f"{row.get('attendance_rate', 0)}%",
                        row.get("total_records", 0),
                        row.get("present_like", 0),
                        row.get("absent_count", 0),
                        row.get("late_count", 0),
                        row.get("excused_count", 0),
                    ),
                )
        else:
            breakdown_tree.insert("", "end", values=("No subject data", "-", 0, 0, 0, 0, 0))

        history_frame = tk.Frame(frame, bg=self.colors["surface"])
        history_frame.grid(row=4, column=0, sticky="nsew")
        history_frame.grid_columnconfigure(0, weight=1)
        history_frame.grid_rowconfigure(1, weight=1)

        tk.Label(
            history_frame,
            text=f"Attendance History ({len(history)} record(s))",
            bg=self.colors["surface"],
            fg=self.colors["text"],
            font=self.fonts["subheading"],
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))

        tree = ttk.Treeview(
            history_frame,
            columns=("class_name", "subject_name", "date", "time", "status", "source"),
            show="headings",
            style="Card.Treeview",
        )
        tree.grid(row=1, column=0, sticky="nsew")
        y_scroll = ttk.Scrollbar(history_frame, orient="vertical", command=tree.yview, style="App.Vertical.TScrollbar")
        y_scroll.grid(row=1, column=1, sticky="ns")
        tree.configure(yscrollcommand=y_scroll.set)

        for col, width in (
            ("class_name", 130),
            ("subject_name", 130),
            ("date", 100),
            ("time", 90),
            ("status", 90),
            ("source", 90),
        ):
            tree.heading(col, text=self._format_column_heading(col))
            tree.column(col, width=width, anchor="w")

        for row in history:
            tree.insert(
                "",
                "end",
                values=(
                    row.get("class_name", ""),
                    row.get("subject_name", ""),
                    row.get("date", ""),
                    row.get("time", ""),
                    row.get("status", ""),
                    row.get("source", ""),
                ),
            )

        button_row = tk.Frame(frame, bg=self.colors["surface"])
        button_row.grid(row=5, column=0, sticky="e", pady=(14, 0))
        self._make_button(button_row, "Close", dialog.destroy, variant="secondary").pack(side="right")

    def open_student_edit_dialog(self, student: Student) -> None:
        """Open the full student edit form for one concrete student record."""
        if not self.ensure_student_edit_scope(student):
            return
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Edit Student - {student.name}")
        dialog.transient(self.root)
        dialog.grab_set()
        self._style_dialog_shell(dialog)
        dialog.resizable(True, True)
        self.center_window(dialog, 700, 560)

        name_var = tk.StringVar(value=student.name)
        class_var = tk.StringVar(value=student.class_name)
        section_var = tk.StringVar(value=student.section_name)
        batch_var = tk.StringVar(value=student.batch_name)
        academic_year_var = tk.StringVar(value=student.academic_year)
        semester_var = tk.StringVar(value=student.semester)
        guardian_var = tk.StringVar(value=student.guardian_name)
        status_var = tk.StringVar(value=student.status)

        frame, _canvas = self.create_scrollable_dialog_body(
            dialog,
            bg=self.colors["surface"],
            padx=18,
            pady=18,
        )
        frame.grid_columnconfigure(1, weight=1)

        tk.Label(frame, text="Edit Student", bg=self.colors["surface"], fg=self.colors["text"], font=self.fonts["heading"]).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        tk.Label(
            frame,
            text=f"Student ID: {student.student_id}",
            bg=self.colors["surface"],
            fg=self.colors["muted"],
            font=self.fonts["small"],
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 14))

        fields = [
            ("Name", ttk.Entry(frame, textvariable=name_var, style="App.TEntry")),
            ("Class", ttk.Entry(frame, textvariable=class_var, style="App.TEntry")),
            ("Section", ttk.Entry(frame, textvariable=section_var, style="App.TEntry")),
            ("Batch", ttk.Entry(frame, textvariable=batch_var, style="App.TEntry")),
            ("Academic Year", ttk.Entry(frame, textvariable=academic_year_var, style="App.TEntry")),
            ("Semester", ttk.Entry(frame, textvariable=semester_var, style="App.TEntry")),
            ("Guardian", ttk.Entry(frame, textvariable=guardian_var, style="App.TEntry")),
            (
                "Status",
                ttk.Combobox(
                    frame,
                    textvariable=status_var,
                    values=self.repository.VALID_STUDENT_STATUSES,
                    state="readonly",
                    style="App.TCombobox",
                ),
            ),
        ]
        for row_index, (label_text, widget) in enumerate(fields, start=2):
            tk.Label(
                frame,
                text=label_text,
                bg=self.colors["surface"],
                fg=self.colors["text"],
                font=self.fonts["body"],
            ).grid(row=row_index, column=0, sticky="w", pady=6, padx=(0, 12))
            widget.grid(row=row_index, column=1, sticky="ew", pady=6)

        def save_student_changes() -> None:
            try:
                updated = self.repository.update_student_profile(
                    student.student_id,
                    name=name_var.get(),
                    class_name=class_var.get(),
                    section_name=section_var.get(),
                    batch_name=batch_var.get(),
                    academic_year=academic_year_var.get(),
                    semester=semester_var.get(),
                    guardian_name=guardian_var.get(),
                    status=status_var.get(),
                )
            except Exception as exc:
                self.report_exception("Student Update Failed", exc, parent=dialog)
                return

            if updated.class_name != student.class_name:
                self.repository.mark_model_dirty(
                    student.class_name,
                    f"Student {updated.name} moved class or metadata changed. Review and retrain if needed.",
                )
                self.repository.mark_model_dirty(
                    updated.class_name,
                    f"Student {updated.name} moved into this class. Review and retrain if needed.",
                )
            else:
                self.repository.mark_model_dirty(
                    updated.class_name,
                    f"Student profile updated for {updated.name}. Review and retrain if face/curriculum context changed.",
                )

            self.status_var.set(f"Updated student {updated.name}.")
            self.log_activity(f"Student updated: {updated.name} ({updated.student_id}).")
            self.audit(
                "student.edit",
                "student",
                updated.student_id,
                f"Updated student profile fields for {updated.name}.",
            )
            self._write_student_details_snapshot(updated)
            self.refresh_dashboard()
            dialog.destroy()

        button_row = tk.Frame(frame, bg=self.colors["surface"])
        button_row.grid(row=len(fields) + 2, column=0, columnspan=2, sticky="e", pady=(18, 0))
        self._make_button(button_row, "Cancel", dialog.destroy, variant="secondary").pack(side="right")
        self._make_button(button_row, "Save Changes", save_student_changes, variant="primary").pack(side="right", padx=(0, 8))

    def edit_selected_student(self) -> None:
        """Edit the selected student's main academic and profile fields."""
        if not self.require_roles(EDIT_ROLES, "edit students"):
            return
        student = self.get_selected_student()
        if student is None:
            return
        self.open_student_edit_dialog(student)

    def recapture_selected_student(self) -> None:
        """Replace stored face images for the selected student and retrain later."""
        if not self.require_roles(EDIT_ROLES, "re-capture student samples"):
            return
        student = self.get_selected_student()
        if student is None:
            return
        if not self.ensure_student_edit_scope(student):
            return

        confirmed = messagebox.askyesno(
            "Re-Capture Face Samples",
            f"Replace existing face samples for {student.name} ({student.student_id})?",
            parent=self.root,
        )
        if not confirmed:
            return

        person_dir = DATASET_DIR / f"{student.student_id}_{student.label_id}"
        person_dir.mkdir(parents=True, exist_ok=True)
        harden_permissions(person_dir, is_dir=True)

        backup_dir = person_dir.parent / f"_backup_{student.student_id}_{student.label_id}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        existing_images = list(person_dir.glob("*.jpg"))
        for img in existing_images:
            shutil.move(str(img), str(backup_dir / img.name))

        try:
            count, face_signature = self._capture_student_faces(
                PersonRecord(student.label_id, student.student_id, student.name)
            )
        except Exception as exc:
            for img in backup_dir.glob("*.jpg"):
                shutil.move(str(img), str(person_dir / img.name))
            shutil.rmtree(str(backup_dir), ignore_errors=True)
            self.report_exception("Re-Capture Failed", exc, parent=self.root)
            self.status_var.set(f"Re-capture failed: {exc}")
            return

        shutil.rmtree(str(backup_dir), ignore_errors=True)
        self.repository.update_student_face_signature(student.student_id, face_signature)
        refreshed_student = self.repository.get_student_by_student_id(student.student_id)
        if refreshed_student is not None:
            self._write_student_details_snapshot(refreshed_student)

        self.status_var.set(f"Re-captured {count} samples for {student.name}.")
        self.log_activity(f"Face samples refreshed for {student.name} ({student.student_id}).")
        self.audit(
            "student.recapture",
            "student",
            student.student_id,
            f"Re-captured {count} face samples for {student.name}.",
        )
        self.repository.mark_model_dirty(
            student.class_name,
            f"Face samples changed for {student.name}. Retraining is required.",
        )
        self.refresh_dashboard()

    def delete_selected_student(self) -> None:
        """Delete the selected student, related attendance, and that class model file."""
        if not self.require_roles(("root",), "delete students"):
            return
        student = self.get_selected_student()
        if student is None:
            return

        confirmed = messagebox.askyesno(
            "Delete Student",
            f"Delete {student.name} ({student.student_id}) and all related attendance?",
            parent=self.root,
        )
        if not confirmed:
            return

        person_dir = DATASET_DIR / f"{student.student_id}_{student.label_id}"
        if person_dir.exists():
            for file_path in person_dir.iterdir():
                if file_path.is_file():
                    file_path.unlink()
            person_dir.rmdir()

        photo_dir = self._get_student_photo_dir(student)
        if photo_dir.exists():
            for file_path in photo_dir.iterdir():
                if file_path.is_file():
                    file_path.unlink()
            photo_dir.rmdir()

        details_dir = self._get_student_assets_dir(student)
        if details_dir.exists():
            for file_path in details_dir.iterdir():
                if file_path.is_file():
                    file_path.unlink()
            details_dir.rmdir()

        self.repository.delete_student(student.student_id)
        remaining_students = self.repository.list_students(student.class_name)
        model_path = MODEL_DIR / f"face_model_{slugify_name(student.class_name)}.npz"
        if not remaining_students:
            if model_path.exists():
                model_path.unlink()
        self.repository.mark_model_dirty(
            student.class_name,
            f"Student data changed after deleting {student.name}. Retraining is required.",
        )

        self.status_var.set(f"Deleted student {student.name}. Retrain the model before attendance.")
        self.log_activity(f"Student deleted: {student.name} ({student.student_id}).")
        self.audit("student.delete", "student", student.student_id, f"Deleted student {student.name}.")
        self.refresh_dashboard()

    def reset_selected_student_password(self) -> None:
        """Set a fresh student password without exposing generated plaintext credentials."""
        if not self.require_roles(EDIT_ROLES, "reset student passwords"):
            return
        student = self.get_selected_student()
        if student is None:
            return
        if not self.ensure_student_edit_scope(student):
            return
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Reset Password - {student.name}")
        dialog.transient(self.root)
        dialog.grab_set()
        self._style_dialog_shell(dialog)
        self.center_window(dialog, 520, 280)

        password_var = tk.StringVar()
        confirm_var = tk.StringVar()

        frame = tk.Frame(dialog, bg=self.colors["surface"], padx=18, pady=18)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(1, weight=1)

        tk.Label(frame, text="Set New Student Password", bg=self.colors["surface"], fg=self.colors["text"], font=self.fonts["heading"]).grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        tk.Label(
            frame,
            text=f"Student: {student.name} ({student.student_id})",
            bg=self.colors["surface"],
            fg=self.colors["muted"],
            font=self.fonts["small"],
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 14))
        tk.Label(frame, text="New Password", bg=self.colors["surface"], font=self.fonts["body"]).grid(row=2, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=password_var, show="*", style="App.TEntry").grid(row=2, column=1, sticky="ew", pady=6)
        tk.Label(frame, text="Confirm Password", bg=self.colors["surface"], font=self.fonts["body"]).grid(row=3, column=0, sticky="w", pady=6)
        ttk.Entry(frame, textvariable=confirm_var, show="*", style="App.TEntry").grid(row=3, column=1, sticky="ew", pady=6)

        def save_password() -> None:
            if password_var.get() != confirm_var.get():
                messagebox.showerror("Password Mismatch", "The two password fields must match.", parent=dialog)
                return
            try:
                self.repository.update_student_password(student.student_id, password_var.get())
            except ValueError as exc:
                messagebox.showerror("Password Not Updated", str(exc), parent=dialog)
                return
            self.status_var.set(f"Password updated for {student.name}.")
            self.log_activity(f"Student password updated for {student.name} ({student.student_id}).")
            self.audit("student.password_reset", "student", student.student_id, "Student password was manually reset by staff.")
            dialog.destroy()

        button_row = tk.Frame(frame, bg=self.colors["surface"])
        button_row.grid(row=4, column=0, columnspan=2, sticky="e", pady=(18, 0))
        self._make_button(button_row, "Cancel", dialog.destroy, variant="secondary").pack(side="right")
        self._make_button(button_row, "Save Password", save_password, variant="primary").pack(side="right", padx=(0, 8))

    def add_manual_attendance(self) -> None:
        """Open a checklist to add attendance without using face recognition."""
        if not self.require_roles(EDIT_ROLES, "add manual attendance"):
            return
        self.apply_current_timetable_context(show_message=False)
        students = self.repository.list_students_for_context(
            self.current_class_var.get(),
            self.current_subject_var.get(),
        )
        if not students:
            messagebox.showwarning(
                "No Students",
                "Register a student before adding manual attendance.",
                parent=self.root,
            )
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Manual Attendance")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(True, True)
        self._style_dialog_shell(dialog)
        self.center_window(dialog, 680, 640)

        suggested_status = self.repository.suggest_attendance_status(
            self.current_class_var.get(),
            self.current_subject_var.get(),
        )
        status_var = tk.StringVar(value=suggested_status)
        date_var = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d"))
        time_var = tk.StringVar(value=datetime.now().strftime("%H:%M:%S"))
        sorted_students = sorted(students, key=lambda item: (item.name.lower(), item.student_id.lower()))
        student_lookup = {student.student_id: student for student in sorted_students}
        selection_vars = {student.student_id: tk.BooleanVar(value=False) for student in sorted_students}
        selected_count_var = tk.StringVar(value="0 selected")

        frame = tk.Frame(dialog, bg=self.colors["surface"], padx=18, pady=18)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_rowconfigure(5, weight=1)

        tk.Label(frame, text="Manual Attendance", bg=self.colors["surface"], fg=self.colors["text"], font=self.fonts["heading"]).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 14)
        )
        tk.Label(
            frame,
            text="Select one or more students, then save a shared status, date, and time for the checked list.",
            bg=self.colors["surface"],
            fg=self.colors["muted"],
            font=self.fonts["small"],
            justify="left",
            wraplength=600,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 12))

        tk.Label(frame, text="Status", bg=self.colors["surface"], font=self.fonts["body"]).grid(row=2, column=0, sticky="w", pady=6)
        status_box = ttk.Combobox(
            frame,
            textvariable=status_var,
            values=["Present", "Late", "Absent", "Excused"],
            state="readonly",
            style="App.TCombobox",
        )
        status_box.grid(row=2, column=1, columnspan=2, sticky="ew", pady=6)

        tk.Label(frame, text="Date", bg=self.colors["surface"], font=self.fonts["body"]).grid(row=3, column=0, sticky="w", pady=6)
        date_entry = ttk.Entry(frame, textvariable=date_var, style="App.TEntry")
        date_entry.grid(row=3, column=1, sticky="ew", pady=6)
        self._make_button(
            frame,
            "Calendar",
            lambda: self.open_date_picker(date_var, dialog),
            variant="secondary",
            padx=10,
            pady=6,
        ).grid(row=3, column=2, sticky="ew", padx=(8, 0), pady=6)

        tk.Label(frame, text="Time", bg=self.colors["surface"], font=self.fonts["body"]).grid(row=4, column=0, sticky="w", pady=6)
        time_entry = ttk.Entry(frame, textvariable=time_var, style="App.TEntry")
        time_entry.grid(row=4, column=1, columnspan=2, sticky="ew", pady=6)

        list_card = tk.Frame(
            frame,
            bg=self.colors["surface_alt"],
            padx=12,
            pady=12,
            highlightbackground=self.colors["border"],
            highlightthickness=1,
        )
        list_card.grid(row=5, column=0, columnspan=3, sticky="nsew", pady=(14, 0))
        list_card.grid_columnconfigure(0, weight=1)
        list_card.grid_rowconfigure(2, weight=1)

        tk.Label(
            list_card,
            text="Students",
            bg=self.colors["surface_alt"],
            fg=self.colors["text"],
            font=self.fonts["subheading"],
        ).grid(row=0, column=0, sticky="w")
        tk.Label(
            list_card,
            textvariable=selected_count_var,
            bg=self.colors["surface_alt"],
            fg=self.colors["muted"],
            font=self.fonts["small"],
        ).grid(row=0, column=1, sticky="e")

        quick_actions = tk.Frame(list_card, bg=self.colors["surface_alt"])
        quick_actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 10))

        checklist_host = tk.Frame(list_card, bg=self.colors["surface_alt"])
        checklist_host.grid(row=2, column=0, columnspan=2, sticky="nsew")
        checklist_host.grid_columnconfigure(0, weight=1)
        checklist_host.grid_rowconfigure(0, weight=1)

        canvas = tk.Canvas(checklist_host, bg=self.colors["surface"], highlightthickness=0, bd=0)
        scrollbar = ttk.Scrollbar(checklist_host, orient="vertical", command=canvas.yview, style="App.Vertical.TScrollbar")
        checklist_frame = tk.Frame(canvas, bg=self.colors["surface"])
        checklist_frame.grid_columnconfigure(0, weight=1)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas_window = canvas.create_window((0, 0), window=checklist_frame, anchor="nw")

        def refresh_canvas(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def fit_canvas_width(event) -> None:
            canvas.itemconfigure(canvas_window, width=event.width)

        checklist_frame.bind("<Configure>", refresh_canvas)
        canvas.bind("<Configure>", fit_canvas_width)

        def update_selected_count() -> None:
            selected_count = sum(1 for variable in selection_vars.values() if variable.get())
            selected_count_var.set(f"{selected_count} selected")

        def set_all_students(selected: bool) -> None:
            for variable in selection_vars.values():
                variable.set(selected)
            update_selected_count()

        self._make_button(quick_actions, "Select All", lambda: set_all_students(True), variant="secondary", padx=12, pady=6).pack(side="left")
        self._make_button(quick_actions, "Clear All", lambda: set_all_students(False), variant="secondary", padx=12, pady=6).pack(side="left", padx=(8, 0))

        for row_index, student in enumerate(sorted_students):
            student_row = tk.Frame(checklist_frame, bg=self.colors["surface"], padx=8, pady=6)
            student_row.grid(row=row_index, column=0, sticky="ew", pady=(0, 6))
            student_row.grid_columnconfigure(1, weight=1)
            tk.Checkbutton(
                student_row,
                variable=selection_vars[student.student_id],
                command=update_selected_count,
                bg=self.colors["surface"],
                activebackground=self.colors["surface"],
                selectcolor=self.colors["surface"],
                highlightthickness=0,
                bd=0,
            ).grid(row=0, column=0, rowspan=2, sticky="nw")
            tk.Label(
                student_row,
                text=student.name,
                bg=self.colors["surface"],
                fg=self.colors["text"],
                font=self.fonts["body"],
                justify="left",
            ).grid(row=0, column=1, sticky="w")
            tk.Label(
                student_row,
                text=f"{student.student_id}  |  {student.class_name}  |  {student.section_name}",
                bg=self.colors["surface"],
                fg=self.colors["muted"],
                font=self.fonts["small"],
                justify="left",
            ).grid(row=1, column=1, sticky="w", pady=(2, 0))

        def save_manual_attendance() -> None:
            selected_students = [
                student_lookup[student_id]
                for student_id, variable in selection_vars.items()
                if variable.get()
            ]
            if not selected_students:
                messagebox.showwarning("No Students Selected", "Tick at least one student first.", parent=dialog)
                return

            try:
                added_students: list[str] = []
                duplicate_students: list[str] = []
                for student in selected_students:
                    was_added = self.repository.add_attendance_record(
                        student.student_id,
                        student.name,
                        self.current_class_var.get(),
                        self.current_subject_var.get(),
                        date_var.get().strip(),
                        time_var.get().strip(),
                        status_var.get().strip(),
                        source="manual",
                        allow_duplicate_same_day=status_var.get().strip() != "Present",
                    )
                    if was_added:
                        added_students.append(student.name)
                    else:
                        duplicate_students.append(student.name)
            except ValueError as exc:
                messagebox.showerror("Attendance Locked", str(exc), parent=dialog)
                return
            if not added_students:
                messagebox.showwarning(
                    "Duplicate Attendance",
                    f"All selected students already have attendance for {date_var.get().strip()}.",
                    parent=dialog,
                )
                return

            dialog.destroy()
            self.status_var.set(f"Manual attendance added for {len(added_students)} student(s).")
            self.log_activity(
                f"Manual attendance added for {len(added_students)} student(s) in {self.current_class_var.get()} - {self.current_subject_var.get()}."
            )
            self.audit(
                "attendance.manual_add",
                "attendance",
                self.current_class_var.get(),
                (
                    f"Added {status_var.get().strip()} for {len(added_students)} student(s) in "
                    f"{self.current_class_var.get()} - {self.current_subject_var.get()} on {date_var.get().strip()}."
                ),
            )
            if duplicate_students:
                messagebox.showinfo(
                    "Attendance Saved",
                    f"Saved {len(added_students)} record(s). Skipped {len(duplicate_students)} duplicate student(s).",
                    parent=self.root,
                )
            self.refresh_dashboard()

        button_row = tk.Frame(frame, bg=self.colors["surface"])
        button_row.grid(row=6, column=0, columnspan=3, sticky="e", pady=(20, 0))
        self._make_button(button_row, "Cancel", dialog.destroy, variant="secondary", padx=14, pady=6).pack(side="right")
        self._make_button(button_row, "Save", save_manual_attendance, variant="primary", padx=14, pady=6).pack(side="right", padx=(0, 8))
        status_box.focus_set()
        status_box.bind("<<ComboboxSelected>>", lambda _event: date_entry.focus_set())
        date_entry.bind("<Return>", lambda _event: save_manual_attendance())
        time_entry.bind("<Return>", lambda _event: save_manual_attendance())

    def edit_selected_attendance(self) -> None:
        """Open a form to edit the selected attendance row."""
        if not self.require_roles(EDIT_ROLES, "edit attendance"):
            return
        selected = self.attendance_tree.selection()
        if not selected:
            messagebox.showwarning(
                "Attendance Required",
                "Select an attendance record first.",
                parent=self.root,
            )
            return

        values = self.attendance_tree.item(selected[0], "values")
        record = self.repository.get_attendance_by_id(int(values[0]))
        if record is None:
            messagebox.showerror("Missing Record", "Attendance record no longer exists.", parent=self.root)
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Edit Attendance")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)
        self._style_dialog_shell(dialog)
        self.center_window(dialog, 440, 300)

        date_var = tk.StringVar(value=record["date"])
        time_var = tk.StringVar(value=record["time"])
        status_var = tk.StringVar(value=record["status"])

        frame = tk.Frame(dialog, bg="white", padx=18, pady=18)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(1, weight=1)

        tk.Label(frame, text=f"{record['name']} ({record['student_id']})", bg="white", fg="#10233d", font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 14)
        )
        tk.Label(frame, text="Date", bg="white", font=("Segoe UI", 10)).grid(row=1, column=0, sticky="w", pady=6)
        date_entry = ttk.Entry(frame, textvariable=date_var)
        date_entry.grid(row=1, column=1, sticky="ew", pady=6)
        self._make_button(
            frame,
            "Calendar",
            lambda: self.open_date_picker(date_var, dialog),
            variant="secondary",
            padx=10,
            pady=6,
        ).grid(row=1, column=2, sticky="ew", padx=(8, 0), pady=6)
        tk.Label(frame, text="Time", bg="white", font=("Segoe UI", 10)).grid(row=2, column=0, sticky="w", pady=6)
        time_entry = ttk.Entry(frame, textvariable=time_var)
        time_entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=6)
        tk.Label(frame, text="Status", bg="white", font=("Segoe UI", 10)).grid(row=3, column=0, sticky="w", pady=6)
        status_box = ttk.Combobox(
            frame,
            textvariable=status_var,
            values=["Present", "Late", "Absent", "Excused"],
            state="readonly",
        )
        status_box.grid(row=3, column=1, columnspan=2, sticky="ew", pady=6)

        def save_attendance_edit() -> None:
            try:
                self.repository.update_attendance_record(
                    int(record["id"]),
                    record.get("class_name", self.current_class_var.get()),
                    record.get("subject_name", self.current_subject_var.get()),
                    date_var.get().strip(),
                    time_var.get().strip(),
                    status_var.get().strip(),
                )
            except ValueError as exc:
                error_text = str(exc)
                if "locked" in error_text.lower():
                    reason = self.prompt_correction_request_reason(dialog)
                    if reason is None:
                        return
                    try:
                        request_id = self.repository.create_attendance_change_request(
                            int(record["id"]),
                            str(record.get("class_name", self.current_class_var.get())),
                            str(record.get("subject_name", self.current_subject_var.get())),
                            date_var.get().strip(),
                            time_var.get().strip(),
                            status_var.get().strip(),
                            self.current_username,
                            reason,
                        )
                    except ValueError as request_exc:
                        messagebox.showerror("Request Not Created", str(request_exc), parent=dialog)
                        return
                    self.audit(
                        "attendance.change_request_create",
                        "attendance_change_request",
                        str(request_id),
                        f"Requested correction for attendance #{record['id']}.",
                    )
                    self.status_var.set(f"Attendance correction request #{request_id} submitted.")
                    self.log_activity(f"Attendance correction request submitted for record #{record['id']}.")
                    dialog.destroy()
                    return
                messagebox.showerror("Attendance Locked", error_text, parent=dialog)
                return
            dialog.destroy()
            self.status_var.set(f"Attendance record #{record['id']} updated.")
            self.log_activity(f"Attendance record updated for {record['name']} (#{record['id']}).")
            self.audit(
                "attendance.edit",
                "attendance",
                str(record["id"]),
                f"Updated attendance record for {record['name']} to {status_var.get().strip()} on {date_var.get().strip()} {time_var.get().strip()}.",
            )
            self.refresh_dashboard()

        button_row = tk.Frame(frame, bg="white")
        button_row.grid(row=4, column=0, columnspan=3, sticky="e", pady=(20, 0))
        self._make_button(button_row, "Cancel", dialog.destroy, variant="secondary", padx=14, pady=6).pack(side="right")
        self._make_button(button_row, "Update", save_attendance_edit, variant="primary", padx=14, pady=6).pack(side="right", padx=(0, 8))
        date_entry.focus_set()
        time_entry.bind("<Return>", lambda _event: save_attendance_edit())
        status_box.bind("<Return>", lambda _event: save_attendance_edit())

    def export_attendance_report(self) -> None:
        """Export the currently filtered attendance table to a CSV report."""
        if not self.require_roles(EDIT_ROLES, "export attendance reports"):
            return
        rows = self.repository.list_attendance(**self.get_attendance_filter_kwargs())
        if not rows:
            messagebox.showwarning(
                "No Records",
                "There are no attendance records to export for the current filter.",
                parent=self.root,
            )
            return

        default_name = f"attendance_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        export_path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export Attendance Report",
            initialdir=str(EXPORTS_DIR),
            initialfile=default_name,
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv")],
        )
        if not export_path:
            return

        with Path(export_path).open("w", newline="", encoding="utf-8-sig") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(
                ["id", "student_id", "name", "class_name", "section_name", "academic_year", "semester", "subject_name", "date", "time", "status", "source"]
            )
            for row in rows:
                writer.writerow(
                    [
                        row.get("id", ""),
                        self.sanitize_csv_cell(row.get("student_id", "")),
                        self.sanitize_csv_cell(row.get("name", "")),
                        self.sanitize_csv_cell(row.get("class_name", "")),
                        self.sanitize_csv_cell(row.get("section_name", "")),
                        self.sanitize_csv_cell(row.get("academic_year", "")),
                        self.sanitize_csv_cell(row.get("semester", "")),
                        self.sanitize_csv_cell(row.get("subject_name", "")),
                        self.format_csv_date(row.get("date", "")),
                        self.format_csv_time(row.get("time", "")),
                        self.sanitize_csv_cell(row.get("status", "")),
                        self.sanitize_csv_cell(row.get("source", "")),
                    ]
                )

        self.status_var.set(f"Attendance report exported to {Path(export_path).name}.")
        self.log_activity(f"Attendance report exported: {export_path}")
        self.audit("attendance.export_csv", "report", Path(export_path).name, "Attendance CSV report exported.")

    def export_attendance_excel(self) -> None:
        """Export the currently filtered attendance table to a real Excel workbook."""
        if not self.require_roles(EDIT_ROLES, "export attendance to Excel"):
            return
        rows = self.repository.list_attendance(**self.get_attendance_filter_kwargs())
        if not rows:
            messagebox.showwarning(
                "No Records",
                "There are no attendance records to export for the current filter.",
                parent=self.root,
            )
            return

        default_name = f"attendance_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        export_path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export Attendance Report to Excel",
            initialdir=str(EXPORTS_DIR),
            initialfile=default_name,
            defaultextension=".xlsx",
            filetypes=[("Excel Workbook", "*.xlsx")],
        )
        if not export_path:
            return

        columns = ["id", "student_id", "name", "class_name", "section_name", "academic_year", "semester", "subject_name", "date", "time", "status", "source"]
        excel_rows = [
            [
                str(row.get("id", "")),
                str(row.get("student_id", "")),
                str(row.get("name", "")),
                str(row.get("class_name", "")),
                str(row.get("section_name", "")),
                str(row.get("academic_year", "")),
                str(row.get("semester", "")),
                str(row.get("subject_name", "")),
                str(row.get("date", "")),
                str(row.get("time", "")),
                str(row.get("status", "")),
                str(row.get("source", "")),
            ]
            for row in rows
        ]

        export_attendance_to_xlsx(
            Path(export_path),
            columns,
            excel_rows,
            sheet_name="Attendance",
        )

        self.status_var.set(f"Attendance Excel exported to {Path(export_path).name}.")
        self.log_activity(f"Attendance Excel exported: {export_path}")
        self.audit("attendance.export_xlsx", "report", Path(export_path).name, "Attendance Excel report exported.")

    def export_attendance_pdf(self) -> None:
        """Export the currently filtered attendance table to a simple PDF report."""
        if not self.require_roles(EDIT_ROLES, "export attendance to PDF"):
            return
        rows = self.repository.list_attendance(**self.get_attendance_filter_kwargs())
        if not rows:
            messagebox.showwarning(
                "No Records",
                "There are no attendance records to export for the current filter.",
                parent=self.root,
            )
            return

        default_name = f"attendance_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        export_path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export Attendance Report to PDF",
            initialdir=str(EXPORTS_DIR),
            initialfile=default_name,
            defaultextension=".pdf",
            filetypes=[("PDF Files", "*.pdf")],
        )
        if not export_path:
            return

        title = (
            f"Attendance Report - {self.current_class_var.get()} - {self.current_subject_var.get()} - "
            f"{self.current_academic_year_var.get()} - {self.current_semester_var.get() or 'General'}"
        )
        lines = [
            f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Records: {len(rows)}",
            f"Section: {self.current_section_var.get() or '-'}",
            "",
        ]
        for row in rows:
            lines.append(
                " | ".join(
                    [
                        str(row.get("student_id", "")),
                        str(row.get("name", "")),
                        str(row.get("class_name", "")),
                        str(row.get("section_name", "")),
                        str(row.get("academic_year", "")),
                        str(row.get("semester", "")),
                        str(row.get("subject_name", "")),
                        str(row.get("date", "")),
                        str(row.get("time", "")),
                        str(row.get("status", "")),
                    ]
                )
            )

        export_attendance_to_pdf(Path(export_path), title, lines)

        self.status_var.set(f"Attendance PDF exported to {Path(export_path).name}.")
        self.log_activity(f"Attendance PDF exported: {export_path}")
        self.audit("attendance.export_pdf", "report", Path(export_path).name, "Attendance PDF report exported.")


import frontend.admin as _frontend_admin
import frontend.attendance as _frontend_attendance
import frontend.auth as _frontend_auth
import frontend.exports as _frontend_exports
import frontend.students as _frontend_students

FaceRecognitionAttendanceApp.login_user = _frontend_auth.login_user

FaceRecognitionAttendanceApp.open_attendance_filter_dialog = _frontend_attendance.open_attendance_filter_dialog
FaceRecognitionAttendanceApp.train_model = _frontend_attendance.train_model
FaceRecognitionAttendanceApp.start_attendance = _frontend_attendance.start_attendance
FaceRecognitionAttendanceApp.load_attendance_records = _frontend_attendance.load_attendance_records
FaceRecognitionAttendanceApp.refresh_dashboard = _frontend_attendance.refresh_dashboard
FaceRecognitionAttendanceApp.refresh_student_table = _frontend_attendance.refresh_student_table
FaceRecognitionAttendanceApp.refresh_attendance_table = _frontend_attendance.refresh_attendance_table

FaceRecognitionAttendanceApp.register_student = _frontend_students.register_student
FaceRecognitionAttendanceApp.open_register_student_dialog = _frontend_students.open_register_student_dialog
FaceRecognitionAttendanceApp.open_student_profile = _frontend_students.open_student_profile
FaceRecognitionAttendanceApp.open_student_edit_dialog = _frontend_students.open_student_edit_dialog
FaceRecognitionAttendanceApp.recapture_selected_student = _frontend_students.recapture_selected_student
FaceRecognitionAttendanceApp.delete_selected_student = _frontend_students.delete_selected_student
FaceRecognitionAttendanceApp.add_manual_attendance = _frontend_students.add_manual_attendance
FaceRecognitionAttendanceApp.edit_selected_attendance = _frontend_students.edit_selected_attendance

FaceRecognitionAttendanceApp.open_institute_setup_dialog = _frontend_admin.open_institute_setup_dialog
FaceRecognitionAttendanceApp.open_student_promotion_manager = _frontend_admin.open_student_promotion_manager
FaceRecognitionAttendanceApp.open_class_subject_manager = _frontend_admin.open_class_subject_manager
FaceRecognitionAttendanceApp.open_database_viewer = _frontend_admin.open_database_viewer
FaceRecognitionAttendanceApp.open_audit_log_viewer = _frontend_admin.open_audit_log_viewer
FaceRecognitionAttendanceApp.open_timetable_manager = _frontend_admin.open_timetable_manager
FaceRecognitionAttendanceApp.open_user_manager = _frontend_admin.open_user_manager
FaceRecognitionAttendanceApp.open_change_password_dialog = _frontend_admin.open_change_password_dialog
FaceRecognitionAttendanceApp.open_analytics_viewer = _frontend_admin.open_analytics_viewer
FaceRecognitionAttendanceApp.open_report_summary_viewer = _frontend_admin.open_report_summary_viewer
FaceRecognitionAttendanceApp.open_low_attendance_alerts_viewer = _frontend_admin.open_low_attendance_alerts_viewer
FaceRecognitionAttendanceApp.open_attendance_change_request_viewer = _frontend_admin.open_attendance_change_request_viewer
FaceRecognitionAttendanceApp.open_camera_diagnostics = _frontend_admin.open_camera_diagnostics
FaceRecognitionAttendanceApp.backup_database_file = _frontend_admin.backup_database_file
FaceRecognitionAttendanceApp.open_backup_restore_dialog = _frontend_admin.open_backup_restore_dialog

FaceRecognitionAttendanceApp.write_generated_credentials_report = _frontend_exports.write_generated_credentials_report
FaceRecognitionAttendanceApp.export_attendance_report = _frontend_exports.export_attendance_report
FaceRecognitionAttendanceApp.export_attendance_excel = _frontend_exports.export_attendance_excel
FaceRecognitionAttendanceApp.export_attendance_pdf = _frontend_exports.export_attendance_pdf


def main() -> None:
    """Application entry point used by app.py and test.py."""
    configure_logging(LOGS_DIR)
    LOGGER.info("Launching %s", APP_NAME)
    app = None
    try:
        app = FaceRecognitionAttendanceApp()
        if app.login_user():
            app.run()
        else:
            LOGGER.info("Application closed at login screen.")
            try:
                app.root.destroy()
            except tk.TclError:
                pass
    except Exception:
        LOGGER.exception("Unhandled application startup failure.")
        raise


if __name__ == "__main__":
    main()
