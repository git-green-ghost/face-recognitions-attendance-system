import shutil
from frontend.app import *

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
