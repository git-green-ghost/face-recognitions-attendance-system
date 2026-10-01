from frontend.app import *

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
            src_class = class_var.get().strip()
            dest_class = next_class_var.get().strip() or src_class
            for item in selected:
                values = tree.item(item, "values")
                self.repository.update_student_academic_record(
                    str(values[0]),
                    class_name=dest_class,
                    section_name=next_section_var.get().strip(),
                    academic_year=next_year_var.get().strip(),
                    semester=next_semester_var.get().strip(),
                    status=status_var.get().strip(),
                )
            if src_class:
                self.repository.mark_model_dirty(
                    src_class,
                    f"Student roster changed after status/class update for {len(selected)} student(s). Retraining required.",
                )
            if dest_class and dest_class != src_class:
                self.repository.mark_model_dirty(
                    dest_class,
                    f"Student roster expanded after transfer of {len(selected)} student(s). Retraining required.",
                )
            self.audit("student.promote", "student", src_class, f"Updated {len(selected)} student academic record(s) to status {status_var.get().strip()}.")
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
        section_var = tk.StringVar(value=self.current_section_var.get())
        year_var = tk.StringVar(value=self.current_academic_year_var.get())
        semester_var = tk.StringVar(value=self.current_semester_var.get())

        frame = tk.Frame(dialog, bg="white", padx=16, pady=16)
        frame.pack(fill="both", expand=True)
        frame.grid_columnconfigure(1, weight=1)
        frame.grid_rowconfigure(2, weight=1)

        tk.Label(frame, text="Timetable Manager", bg="white", fg="#10233d", font=("Segoe UI", 14, "bold")).grid(
            row=0, column=0, columnspan=8, sticky="w"
        )

        fields = [
            ("Class", class_var, self.repository.list_classes()),
            ("Section", section_var, None),
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
        list_frame.grid(row=3, column=0, columnspan=8, sticky="nsew", pady=(16, 0))
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
                    section_name=section_var.get().strip(),
                    academic_year=year_var.get().strip(),
                    semester=semester_var.get().strip(),
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

            def scan_worker() -> None:
                try:
                    rows = list_available_cameras(4)
                except Exception as exc:
                    try:
                        dialog.after(0, lambda: summary_var.set(f"Camera scan failed: {exc}"))
                    except Exception:
                        pass
                    return

                def update_ui() -> None:
                    if not rows:
                        summary_var.set("No working camera was detected in the first 4 indexes.")
                        return
                    summary_var.set(f"Detected {len(rows)} working camera(s). Active camera index: {self.selected_camera_index}.")
                    for row in rows:
                        tree.insert("", "end", values=(row["camera_index"], row["opened"], row["frame_ok"], row["resolution"]))

                try:
                    dialog.after(0, update_ui)
                except Exception:
                    pass

            import threading
            threading.Thread(target=scan_worker, daemon=True).start()

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
