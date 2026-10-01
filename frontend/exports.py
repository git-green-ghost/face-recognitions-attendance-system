from frontend.app import *

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
