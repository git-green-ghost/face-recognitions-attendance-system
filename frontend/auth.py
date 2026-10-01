from frontend.app import *

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
