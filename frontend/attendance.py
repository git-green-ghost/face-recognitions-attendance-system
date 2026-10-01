from frontend.app import *

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

def load_attendance_records(self) -> list[dict[str, str]]:
        """Load attendance rows for the active class and subject."""
        return self.repository.list_attendance(
            None,
            self.current_class_var.get(),
            self.current_subject_var.get(),
        )

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
