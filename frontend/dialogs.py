from __future__ import annotations

import calendar
from datetime import datetime
import tkinter as tk

APP_BG = "#eef3f1"
SURFACE = "#fbfcfb"
SURFACE_ALT = "#f4f8f6"
TEXT = "#173038"
MUTED = "#5d7175"
PRIMARY = "#1f6f78"
PRIMARY_SOFT = "#e6f3f6"
SUCCESS_SOFT = "#e7f6ec"


def compute_centered_position(
    anchor_x: int,
    anchor_y: int,
    anchor_width: int,
    anchor_height: int,
    window_width: int,
    window_height: int,
    screen_width: int,
    screen_height: int,
    margin: int = 20,
) -> tuple[int, int]:
    """Keep popups centered while clamping them to the visible screen."""
    safe_margin = max(0, margin)
    max_x = max(safe_margin, screen_width - window_width - safe_margin)
    max_y = max(safe_margin, screen_height - window_height - safe_margin)
    centered_x = anchor_x + max((anchor_width - window_width) // 2, 0)
    centered_y = anchor_y + max((anchor_height - window_height) // 2, 0)
    clamped_x = min(max(centered_x, safe_margin), max_x)
    clamped_y = min(max(centered_y, safe_margin), max_y)
    return clamped_x, clamped_y


def clamp_window_size_to_screen(
    window_width: int,
    window_height: int,
    screen_width: int,
    screen_height: int,
    margin: int = 20,
    min_width: int = 240,
    min_height: int = 180,
) -> tuple[int, int]:
    """Limit a popup size so it stays within the visible screen area."""
    safe_margin = max(0, margin)
    max_width = max(min_width, screen_width - (safe_margin * 2))
    max_height = max(min_height, screen_height - (safe_margin * 2))
    return min(window_width, max_width), min(window_height, max_height)


def prepare_modal_size(
    window: tk.Toplevel,
    width: int,
    height: int,
    screen_width: int,
    screen_height: int,
    margin: int = 20,
) -> tuple[int, int]:
    """Clamp a dialog size and relax oversized min/max constraints when needed."""
    fitted_width, fitted_height = clamp_window_size_to_screen(
        width,
        height,
        screen_width,
        screen_height,
        margin=margin,
    )
    try:
        min_width, min_height = window.minsize()
        if min_width > fitted_width or min_height > fitted_height:
            window.minsize(min(min_width, fitted_width), min(min_height, fitted_height))
        window.maxsize(screen_width - (max(0, margin) * 2), screen_height - (max(0, margin) * 2))
    except tk.TclError:
        pass
    return fitted_width, fitted_height


def present_modal_window(
    window: tk.Toplevel,
    *,
    x_pos: int,
    y_pos: int,
    width: int | None = None,
    height: int | None = None,
) -> None:
    """Show a dialog above its parent and bring focus to it."""
    geometry = f"{width}x{height}+{x_pos}+{y_pos}" if width and height else f"+{x_pos}+{y_pos}"
    window.geometry(geometry)
    window.update_idletasks()
    window.lift()
    try:
        window.attributes("-topmost", True)
        window.after_idle(lambda: window.attributes("-topmost", False))
    except tk.TclError:
        pass
    window.after_idle(window.focus_force)


class DatePickerDialog:
    """Reusable calendar popup used by filters and edit forms."""

    def __init__(self, parent: tk.Misc, variable: tk.StringVar) -> None:
        self.parent = parent
        self.variable = variable
        initial_value = variable.get().strip()
        try:
            current = datetime.strptime(initial_value, "%Y-%m-%d")
        except ValueError:
            current = datetime.now()

        self.year = current.year
        self.month = current.month
        self.window = tk.Toplevel(parent)
        self.window.title("Select Date")
        self.window.transient(parent)
        self.window.grab_set()
        self.window.resizable(False, False)
        self.window.configure(bg=SURFACE)
        self.header_var = tk.StringVar()
        self._build_ui()
        self._draw_calendar()
        self.window.update_idletasks()
        parent_x = parent.winfo_rootx()
        parent_y = parent.winfo_rooty()
        parent_w = parent.winfo_width()
        parent_h = parent.winfo_height()
        x_pos, y_pos = compute_centered_position(
            parent_x,
            parent_y,
            parent_w,
            parent_h,
            self.window.winfo_width(),
            self.window.winfo_height(),
            self.window.winfo_screenwidth(),
            self.window.winfo_screenheight(),
        )
        present_modal_window(self.window, x_pos=x_pos, y_pos=y_pos)

    def _build_ui(self) -> None:
        frame = tk.Frame(self.window, bg=SURFACE, padx=16, pady=16, highlightbackground="#d5e2dd", highlightthickness=1)
        frame.pack(fill="both", expand=True)

        header = tk.Frame(frame, bg=SURFACE)
        header.pack(fill="x")
        tk.Button(
            header,
            text="<",
            command=self._prev_month,
            width=3,
            relief="flat",
            bg=PRIMARY_SOFT,
            fg=TEXT,
            activebackground=PRIMARY,
            activeforeground="white",
            bd=0,
            cursor="hand2",
        ).pack(side="left")
        tk.Label(
            header,
            textvariable=self.header_var,
            bg=SURFACE,
            fg=TEXT,
            font=("Segoe UI Semibold", 11),
        ).pack(side="left", expand=True)
        tk.Button(
            header,
            text=">",
            command=self._next_month,
            width=3,
            relief="flat",
            bg=PRIMARY_SOFT,
            fg=TEXT,
            activebackground=PRIMARY,
            activeforeground="white",
            bd=0,
            cursor="hand2",
        ).pack(side="right")

        weekday_row = tk.Frame(frame, bg=SURFACE)
        weekday_row.pack(fill="x", pady=(12, 6))
        for weekday in ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]:
            tk.Label(
                weekday_row,
                text=weekday,
                width=4,
                bg=SURFACE,
                fg=MUTED,
                font=("Segoe UI Semibold", 9),
            ).pack(side="left", expand=True)

        self.days_frame = tk.Frame(frame, bg=SURFACE)
        self.days_frame.pack()

        actions = tk.Frame(frame, bg=SURFACE)
        actions.pack(fill="x", pady=(12, 0))
        tk.Button(
            actions,
            text="Today",
            command=self._pick_today,
            relief="flat",
            bg=SUCCESS_SOFT,
            fg=TEXT,
            activebackground="#d7f0e0",
            bd=0,
            cursor="hand2",
            padx=12,
            pady=6,
        ).pack(side="left")
        tk.Button(
            actions,
            text="Close",
            command=self.window.destroy,
            relief="flat",
            bg=SURFACE_ALT,
            fg=TEXT,
            activebackground=PRIMARY_SOFT,
            bd=0,
            cursor="hand2",
            padx=12,
            pady=6,
        ).pack(side="right")

    def _draw_calendar(self) -> None:
        for child in self.days_frame.winfo_children():
            child.destroy()

        self.header_var.set(f"{calendar.month_name[self.month]} {self.year}")
        month_matrix = calendar.monthcalendar(self.year, self.month)

        for week_index, week in enumerate(month_matrix):
            for day_index, day in enumerate(week):
                if day == 0:
                    tk.Label(self.days_frame, text="", width=4, bg=SURFACE).grid(
                        row=week_index, column=day_index, padx=2, pady=2
                    )
                    continue

                date_value = f"{self.year:04d}-{self.month:02d}-{day:02d}"
                is_selected = self.variable.get().strip() == date_value
                tk.Button(
                    self.days_frame,
                    text=str(day),
                    width=4,
                    relief="flat",
                    bg=PRIMARY if is_selected else PRIMARY_SOFT,
                    fg="white" if is_selected else TEXT,
                    activebackground=PRIMARY,
                    activeforeground="white",
                    bd=0,
                    cursor="hand2",
                    command=lambda value=date_value: self._select(value),
                ).grid(row=week_index, column=day_index, padx=2, pady=2)

    def _select(self, value: str) -> None:
        self.variable.set(value)
        self.window.destroy()

    def _pick_today(self) -> None:
        self.variable.set(datetime.now().strftime("%Y-%m-%d"))
        self.window.destroy()

    def _prev_month(self) -> None:
        if self.month == 1:
            self.month = 12
            self.year -= 1
        else:
            self.month -= 1
        self._draw_calendar()

    def _next_month(self) -> None:
        if self.month == 12:
            self.month = 1
            self.year += 1
        else:
            self.month += 1
        self._draw_calendar()
