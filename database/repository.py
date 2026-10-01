from __future__ import annotations

import csv
import hashlib
import hmac
import logging
import secrets
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from config import harden_permissions
from database.models import (
    LoginThrottleState,
    PasswordResetRequiredError,
    Student,
    StudentLogin,
    UserAccount,
)


LOGGER = logging.getLogger(__name__)
class AttendanceRepository:
    """Central database layer for students, class/subject options, and attendance records."""

    VIEWABLE_TABLES = ("students", "attendance", "subjects", "users", "timetable", "audit_logs", "institute_settings")
    ROOT_ROLE = "root"
    USER_ROLES = ("operator", "teacher", "viewer")
    VALID_ROLES = (ROOT_ROLE,) + USER_ROLES
    ATTENDANCE_STATUSES = ("Present", "Late", "Absent", "Excused")
    LOW_ATTENDANCE_THRESHOLD = 75.0
    PASSWORD_PREFIX = "pbkdf2_sha256"
    PASSWORD_ITERATIONS = 600_000
    PASSWORD_SALT_BYTES = 16
    PASSWORD_MIN_LENGTH = 12
    ADMIN_USERNAME = "admin"
    ADMIN_SETUP_FILE = "admin_setup_credentials.txt"
    CREDENTIAL_REPORT_GLOB = "imported_student_passwords_*.csv"
    CREDENTIAL_REPORT_SUBDIR = "reports"
    LOGIN_MAX_FAILURES = 5
    LOGIN_BLOCK_MINUTES = 10
    DISABLED_PASSWORD_PREFIX = "reset_required"
    VALID_STUDENT_STATUSES = ("Active", "Promoted", "Passed Out", "Dropped", "Inactive")
    VALID_INSTITUTE_TYPES = ("School", "College")
    SENSITIVE_TABLE_COLUMNS = {
        "users": {"password_hash"},
        "students": {"password_hash", "face_signature"},
    }

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        harden_permissions(self.db_path.parent, is_dir=True)
        LOGGER.info("Opening repository with database at %s", self.db_path)
        self._initialize()

    @contextmanager
    def _connect(self):
        """Open a SQLite connection, commit work, and always close it cleanly."""
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA secure_delete = ON")
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialize(self) -> None:
        """Create tables and patch older databases with missing columns/default data."""
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS institute_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS students (
                    label_id INTEGER PRIMARY KEY,
                    student_id TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL,
                    class_name TEXT NOT NULL DEFAULT 'General',
                    section_name TEXT NOT NULL DEFAULT '',
                    batch_name TEXT NOT NULL DEFAULT '',
                    academic_year TEXT NOT NULL DEFAULT '',
                    semester TEXT NOT NULL DEFAULT '',
                    guardian_name TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'Active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS attendance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    student_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    class_name TEXT NOT NULL DEFAULT 'General',
                    section_name TEXT NOT NULL DEFAULT '',
                    batch_name TEXT NOT NULL DEFAULT '',
                    academic_year TEXT NOT NULL DEFAULT '',
                    semester TEXT NOT NULL DEFAULT '',
                    subject_name TEXT NOT NULL DEFAULT 'General',
                    date TEXT NOT NULL,
                    time TEXT NOT NULL,
                    status TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'camera',
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS subjects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    class_name TEXT NOT NULL,
                    section_name TEXT NOT NULL DEFAULT '',
                    academic_year TEXT NOT NULL DEFAULT '',
                    semester TEXT NOT NULL DEFAULT '',
                    subject_name TEXT NOT NULL,
                    UNIQUE(class_name, section_name, academic_year, semester, subject_name)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    class_name TEXT NOT NULL DEFAULT '',
                    subject_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS timetable (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    class_name TEXT NOT NULL,
                    section_name TEXT NOT NULL DEFAULT '',
                    academic_year TEXT NOT NULL DEFAULT '',
                    semester TEXT NOT NULL DEFAULT '',
                    subject_name TEXT NOT NULL,
                    day_name TEXT NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    room_name TEXT NOT NULL DEFAULT '',
                    teacher_name TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS section_options (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    class_name TEXT NOT NULL,
                    section_name TEXT NOT NULL,
                    academic_year TEXT NOT NULL DEFAULT '',
                    semester TEXT NOT NULL DEFAULT '',
                    UNIQUE(class_name, section_name, academic_year, semester)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS semester_options (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    class_name TEXT NOT NULL,
                    academic_year TEXT NOT NULL DEFAULT '',
                    semester TEXT NOT NULL DEFAULT '',
                    UNIQUE(class_name, academic_year, semester)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_username TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL DEFAULT '',
                    details TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS attendance_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    class_name TEXT NOT NULL,
                    subject_name TEXT NOT NULL,
                    date TEXT NOT NULL,
                    finalized_by TEXT NOT NULL DEFAULT '',
                    finalized_at TEXT NOT NULL DEFAULT '',
                    locked_by TEXT NOT NULL DEFAULT '',
                    locked_at TEXT NOT NULL DEFAULT '',
                    UNIQUE(class_name, subject_name, date)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS model_state (
                    class_name TEXT PRIMARY KEY,
                    is_dirty INTEGER NOT NULL DEFAULT 1,
                    dirty_reason TEXT NOT NULL DEFAULT '',
                    last_trained_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS attendance_change_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    attendance_id INTEGER NOT NULL,
                    original_class_name TEXT NOT NULL,
                    original_subject_name TEXT NOT NULL,
                    original_date TEXT NOT NULL,
                    original_time TEXT NOT NULL,
                    original_status TEXT NOT NULL,
                    requested_class_name TEXT NOT NULL,
                    requested_subject_name TEXT NOT NULL,
                    requested_date TEXT NOT NULL,
                    requested_time TEXT NOT NULL,
                    requested_status TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'Pending',
                    reviewed_by TEXT NOT NULL DEFAULT '',
                    reviewed_at TEXT NOT NULL DEFAULT '',
                    review_note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS login_throttle (
                    identity_key TEXT PRIMARY KEY,
                    failures INTEGER NOT NULL DEFAULT 0,
                    blocked_until TEXT NOT NULL DEFAULT ''
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_attendance_student_date
                ON attendance(student_id, class_name, subject_name, date)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at
                ON audit_logs(created_at DESC, id DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_attendance_sessions_lookup
                ON attendance_sessions(class_name, subject_name, date)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_change_requests_status_created
                ON attendance_change_requests(status, created_at DESC, id DESC)
                """
            )
            self._ensure_column(connection, "students", "class_name", "TEXT NOT NULL DEFAULT 'General'")
            self._ensure_column(connection, "students", "section_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "students", "batch_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "students", "academic_year", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "students", "semester", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "students", "guardian_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "students", "status", "TEXT NOT NULL DEFAULT 'Active'")
            self._ensure_column(connection, "students", "password_hash", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "students", "face_signature", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "students", "password_reset_required", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "attendance", "class_name", "TEXT NOT NULL DEFAULT 'General'")
            self._ensure_column(connection, "attendance", "section_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "attendance", "batch_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "attendance", "academic_year", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "attendance", "semester", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "attendance", "subject_name", "TEXT NOT NULL DEFAULT 'General'")
            self._ensure_column(connection, "subjects", "section_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "subjects", "academic_year", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "subjects", "semester", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "timetable", "section_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "timetable", "academic_year", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "timetable", "semester", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "users", "class_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "users", "subject_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "users", "password_reset_required", "INTEGER NOT NULL DEFAULT 0")
            self._deduplicate_attendance_records(connection)
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_attendance_unique_session
                ON attendance(student_id, class_name, subject_name, date)
                """
            )
            blank_password_rows = connection.execute(
                """
                SELECT student_id
                FROM students
                WHERE COALESCE(password_hash, '') = ''
                """
            ).fetchall()
            for row in blank_password_rows:
                student_id = str(row["student_id"])
                connection.execute(
                    "UPDATE students SET password_hash = ? WHERE student_id = ?",
                    (self.hash_password(self.generate_temporary_password()), student_id),
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO subjects(class_name, subject_name)
                VALUES('General', 'General')
                """
            )
            self._ensure_default_institute_settings(connection)
            self._disable_legacy_password_hashes(connection)
            self._ensure_default_admin(connection)
        if self.db_path.exists():
            harden_permissions(self.db_path, is_dir=False)

    def refresh_database(self) -> None:
        """Run lightweight SQLite maintenance without deleting user data."""
        with self._connect() as connection:
            connection.execute("PRAGMA optimize")
            connection.execute("VACUUM")
        if self.db_path.exists():
            harden_permissions(self.db_path, is_dir=False)

    def _ensure_default_institute_settings(self, connection: sqlite3.Connection) -> None:
        """Bootstrap the small institute configuration used across records and reports."""
        defaults = {
            "institute_name": "Face Attendance Institute",
            "institute_type": "School",
            "academic_year": f"{datetime.now().year}-{datetime.now().year + 1}",
            "attendance_mode": "yearly",
        }
        for key, value in defaults.items():
            connection.execute(
                "INSERT OR IGNORE INTO institute_settings(key, value) VALUES(?, ?)",
                (key, value),
            )

    def get_institute_settings(self) -> dict[str, str]:
        """Return the current institute configuration with safe defaults."""
        defaults = {
            "institute_name": "Face Attendance Institute",
            "institute_type": "School",
            "academic_year": f"{datetime.now().year}-{datetime.now().year + 1}",
            "attendance_mode": "yearly",
        }
        with self._connect() as connection:
            rows = connection.execute("SELECT key, value FROM institute_settings").fetchall()
        result = defaults.copy()
        for row in rows:
            key = str(row["key"] or "")
            if key in result:
                result[key] = str(row["value"] or defaults[key])
        if result["institute_type"] == "College":
            result["attendance_mode"] = "semester"
        elif result["attendance_mode"] not in {"yearly", "semester"}:
            result["attendance_mode"] = "yearly"
        return result

    def update_institute_settings(
        self,
        institute_name: str,
        institute_type: str,
        academic_year: str,
    ) -> dict[str, str]:
        """Save the root institute profile used as the default academic context."""
        normalized_name = institute_name.strip() or "Face Attendance Institute"
        normalized_type = institute_type.strip().title()
        normalized_year = academic_year.strip() or f"{datetime.now().year}-{datetime.now().year + 1}"
        if normalized_type not in self.VALID_INSTITUTE_TYPES:
            raise ValueError("Institute type must be School or College.")
        attendance_mode = "semester" if normalized_type == "College" else "yearly"
        with self._connect() as connection:
            for key, value in (
                ("institute_name", normalized_name),
                ("institute_type", normalized_type),
                ("academic_year", normalized_year),
                ("attendance_mode", attendance_mode),
            ):
                connection.execute(
                    """
                    INSERT INTO institute_settings(key, value)
                    VALUES(?, ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (key, value),
                )
        return self.get_institute_settings()

    def mark_model_dirty(self, class_name: str, reason: str) -> None:
        """Mark a class model as outdated until it is trained again."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO model_state(class_name, is_dirty, dirty_reason, last_trained_at, updated_at)
                VALUES(?, 1, ?, '', ?)
                ON CONFLICT(class_name) DO UPDATE SET
                    is_dirty = 1,
                    dirty_reason = excluded.dirty_reason,
                    updated_at = excluded.updated_at
                """,
                (class_name.strip(), reason.strip(), timestamp),
            )

    def clear_model_dirty(self, class_name: str) -> None:
        """Mark a class model as freshly trained and ready for attendance."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO model_state(class_name, is_dirty, dirty_reason, last_trained_at, updated_at)
                VALUES(?, 0, '', ?, ?)
                ON CONFLICT(class_name) DO UPDATE SET
                    is_dirty = 0,
                    dirty_reason = '',
                    last_trained_at = excluded.last_trained_at,
                    updated_at = excluded.updated_at
                """,
                (class_name.strip(), timestamp, timestamp),
            )

    def get_model_state(self, class_name: str) -> dict[str, str | int | bool]:
        """Return whether the class model is ready or must be retrained."""
        normalized_class = class_name.strip()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM model_state WHERE class_name = ?",
                (normalized_class,),
            ).fetchone()
        if row is None:
            return {
                "class_name": normalized_class,
                "is_dirty": True,
                "dirty_reason": "Model has not been trained yet.",
                "last_trained_at": "",
                "updated_at": "",
            }
        return {
            "class_name": normalized_class,
            "is_dirty": bool(row["is_dirty"]),
            "dirty_reason": str(row["dirty_reason"] or ""),
            "last_trained_at": str(row["last_trained_at"] or ""),
            "updated_at": str(row["updated_at"] or ""),
        }

    def _ensure_default_admin(self, connection: sqlite3.Connection) -> None:
        """Create and normalize the default root account shell for first-time use."""
        row = connection.execute(
            "SELECT id, role, password_hash FROM users WHERE username = ?",
            (self.ADMIN_USERNAME,),
        ).fetchone()
        if row is None:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            connection.execute(
                """
                INSERT INTO users(username, password_hash, role, created_at)
                VALUES(?, ?, ?, ?)
                """,
                (self.ADMIN_USERNAME, "", self.ROOT_ROLE, timestamp),
            )
        elif self._canonicalize_role(str(row["role"])) != self.ROOT_ROLE:
            connection.execute(
                "UPDATE users SET role = ? WHERE username = ?",
                (self.ROOT_ROLE, self.ADMIN_USERNAME),
            )

    def is_root_account_configured(self) -> bool:
        """Return whether the built-in root account already has a usable password."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT password_hash, password_reset_required FROM users WHERE username = ?",
                (self.ADMIN_USERNAME,),
            ).fetchone()
        if row is None:
            return False
        return bool(str(row["password_hash"] or "").strip()) and not int(row["password_reset_required"] or 0)

    def initialize_root_account(self, password: str) -> None:
        """Set the first root password without writing bootstrap credentials to disk."""
        self._validate_new_password(password)
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM users WHERE username = ?",
                (self.ADMIN_USERNAME,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO users(username, password_hash, role, created_at)
                    VALUES(?, ?, ?, ?)
                    """,
                    (self.ADMIN_USERNAME, self.hash_password(password), self.ROOT_ROLE, timestamp),
                )
            else:
                connection.execute(
                    "UPDATE users SET password_hash = ?, password_reset_required = 0, role = ? WHERE username = ?",
                    (self.hash_password(password), self.ROOT_ROLE, self.ADMIN_USERNAME),
                )

    def _ensure_column(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        column_name: str,
        column_definition: str,
    ) -> None:
        """Add a column only if the current database file does not already have it."""
        rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        existing_columns = {row["name"] for row in rows}
        if column_name not in existing_columns:
            connection.execute(
                f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
            )

    def get_next_label_id(self, connection: sqlite3.Connection | None = None) -> int:
        """Generate the next numeric label used by the face recognizer."""
        if connection is None:
            with self._connect() as local_connection:
                row = local_connection.execute(
                    "SELECT COALESCE(MAX(label_id), 0) + 1 AS next_id FROM students"
                ).fetchone()
        else:
            row = connection.execute(
                "SELECT COALESCE(MAX(label_id), 0) + 1 AS next_id FROM students"
            ).fetchone()
        return int(row["next_id"])

    def _resolve_academic_dimensions(
        self,
        *,
        section_name: str = "",
        batch_name: str = "",
        academic_year: str = "",
        semester: str = "",
    ) -> tuple[str, str, str, str]:
        """Normalize optional academic fields using institute defaults where helpful."""
        settings = self.get_institute_settings()
        normalized_section = section_name.strip()
        normalized_batch = batch_name.strip()
        normalized_year = academic_year.strip() or settings["academic_year"]
        normalized_semester = semester.strip()
        if settings["attendance_mode"] != "semester":
            normalized_semester = normalized_semester or "Year"
        return normalized_section, normalized_batch, normalized_year, normalized_semester

    def upsert_student(
        self,
        label_id: int,
        student_id: str,
        name: str,
        class_name: str,
        password: str | None = None,
        *,
        section_name: str = "",
        batch_name: str = "",
        academic_year: str = "",
        semester: str = "",
        guardian_name: str = "",
        status: str = "Active",
    ) -> Student:
        """Insert a new student or update the existing one without duplicating student IDs."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        normalized_section, normalized_batch, normalized_year, normalized_semester = self._resolve_academic_dimensions(
            section_name=section_name,
            batch_name=batch_name,
            academic_year=academic_year,
            semester=semester,
        )
        if password:
            self._validate_new_password(password)
        normalized_status = status.strip().title() or "Active"
        if normalized_status not in self.VALID_STUDENT_STATUSES:
            raise ValueError(f"Student status must be one of: {', '.join(self.VALID_STUDENT_STATUSES)}.")
        with self._connect() as connection:
            resolved_label_id, created_at = self._upsert_student_in_connection(
                connection,
                label_id=label_id,
                student_id=student_id,
                name=name,
                class_name=class_name,
                password=password,
                section_name=normalized_section,
                batch_name=normalized_batch,
                academic_year=normalized_year,
                semester=normalized_semester,
                guardian_name=guardian_name,
                status=normalized_status,
                timestamp=timestamp,
            )

        return Student(
            label_id=resolved_label_id,
            student_id=student_id,
            name=name,
            class_name=class_name,
            section_name=normalized_section,
            batch_name=normalized_batch,
            academic_year=normalized_year,
            semester=normalized_semester,
            guardian_name=guardian_name.strip(),
            status=normalized_status,
            created_at=created_at,
            updated_at=timestamp,
        )

    def import_students_from_csv(
        self,
        csv_path: Path,
        default_class_name: str,
    ) -> dict[str, int | list[str]]:
        """Import students from CSV using student_id and name, with optional class_name."""
        LOGGER.info("Importing students from CSV: %s", csv_path)
        imported = 0
        updated = 0
        skipped = 0
        errors: list[str] = []
        generated_credentials: list[dict[str, str]] = []

        with csv_path.open("r", newline="", encoding="utf-8-sig") as csv_file:
            reader = csv.DictReader(csv_file)
            required = {"student_id", "name"}
            headers = set(reader.fieldnames or [])
            if not required.issubset(headers):
                raise ValueError("CSV must contain at least: student_id, name")
            with self._connect() as connection:
                for line_number, row in enumerate(reader, start=2):
                    student_id = (row.get("student_id") or "").strip()
                    name = (row.get("name") or "").strip()
                    class_name = (row.get("class_name") or default_class_name or "General").strip()
                    section_name = (row.get("section_name") or "").strip()
                    batch_name = (row.get("batch_name") or "").strip()
                    academic_year = (row.get("academic_year") or "").strip()
                    semester = (row.get("semester") or "").strip()
                    guardian_name = (row.get("guardian_name") or "").strip()
                    status = (row.get("status") or "Active").strip()
                    password = (row.get("password") or "").strip()

                    if not student_id or not name:
                        skipped += 1
                        errors.append(f"Line {line_number}: missing student_id or name")
                        continue

                    existing = connection.execute(
                        "SELECT label_id, created_at, password_hash FROM students WHERE student_id = ?",
                        (student_id,),
                    ).fetchone()
                    label_id = int(existing["label_id"]) if existing is not None else self.get_next_label_id(connection)
                    generated_password = password

                    resolved_section, resolved_batch, resolved_year, resolved_semester = self._resolve_academic_dimensions(
                        section_name=section_name,
                        batch_name=batch_name,
                        academic_year=academic_year,
                        semester=semester,
                    )
                    normalized_status = status.strip().title() or "Active"
                    if existing is None and not generated_password:
                        generated_password = self.generate_initial_student_password()
                        generated_credentials.append(
                            {
                                "student_id": student_id,
                                "name": name,
                                "class_name": class_name,
                                "password": generated_password,
                            }
                        )
                    if generated_password:
                        self._validate_new_password(generated_password)
                    if normalized_status not in self.VALID_STUDENT_STATUSES:
                        raise ValueError(f"Line {line_number}: student status must be one of: {', '.join(self.VALID_STUDENT_STATUSES)}.")

                    self._upsert_student_in_connection(
                        connection,
                        label_id=label_id,
                        student_id=student_id,
                        name=name,
                        class_name=class_name,
                        password=generated_password or None,
                        section_name=resolved_section,
                        batch_name=resolved_batch,
                        academic_year=resolved_year,
                        semester=resolved_semester,
                        guardian_name=guardian_name,
                        status=normalized_status,
                        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        existing_row=existing,
                    )

                    if existing is None:
                        imported += 1
                    else:
                        updated += 1

        report_path = (
            self.write_generated_credentials_report("csv", generated_credentials)
            if generated_credentials
            else None
        )
        result = {
            "imported": imported,
            "updated": updated,
            "skipped": skipped,
            "errors": errors,
            "generated_credentials": generated_credentials,
            "report_path": str(report_path) if report_path else "",
        }
        LOGGER.info(
            "Student CSV import complete. imported=%s updated=%s skipped=%s",
            imported,
            updated,
            skipped,
        )
        return result

    def hash_password(self, password: str) -> str:
        """Hash a local password for storage in SQLite."""
        salt = secrets.token_hex(self.PASSWORD_SALT_BYTES)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt),
            self.PASSWORD_ITERATIONS,
        ).hex()
        return f"{self.PASSWORD_PREFIX}${self.PASSWORD_ITERATIONS}${salt}${digest}"

    def authenticate_user(self, username: str, password: str) -> UserAccount | None:
        """Validate login credentials against the users table."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM users
                WHERE username = ?
                """,
                (username,),
            ).fetchone()
            if row is None:
                return None
            if int(row["password_reset_required"] or 0):
                raise PasswordResetRequiredError(username.strip(), "user")
            if not self._verify_password(password, str(row["password_hash"] or "")):
                return None
        return self._user_from_row(row)

    def authenticate_student(self, student_id: str, password: str) -> StudentLogin | None:
        """Validate a student viewer login using student_id and password."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT student_id, name, class_name, password_hash, password_reset_required
                       , section_name, batch_name, academic_year, semester
                FROM students
                WHERE student_id = ?
                """,
                (student_id.strip(),),
            ).fetchone()
        if row is None:
            return None
        if int(row["password_reset_required"] or 0):
            raise PasswordResetRequiredError(student_id.strip(), "student")
        if not self._verify_password(password, str(row["password_hash"] or "")):
            return None
        return StudentLogin(
            student_id=str(row["student_id"]),
            name=str(row["name"]),
            class_name=str(row["class_name"]),
            section_name=str(row["section_name"] or ""),
            batch_name=str(row["batch_name"] or ""),
            academic_year=str(row["academic_year"] or ""),
            semester=str(row["semester"] or ""),
        )

    def update_student_face_signature(self, student_id: str, face_signature: str) -> None:
        """Store the current face-structure signature for one student."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE students
                SET face_signature = ?, updated_at = ?
                WHERE student_id = ?
                """,
                (face_signature.strip(), timestamp, student_id.strip()),
            )

    def get_student_face_signature(self, student_id: str) -> str:
        """Load the stored face-structure signature for one student."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT face_signature FROM students WHERE student_id = ?",
                (student_id.strip(),),
            ).fetchone()
        if row is None:
            return ""
        return str(row["face_signature"] or "")

    def list_users(self) -> list[UserAccount]:
        """Return all configured application users."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM users ORDER BY username"
            ).fetchall()
        return [self._user_from_row(row) for row in rows if row is not None]

    def add_user(
        self,
        username: str,
        password: str,
        role: str,
        class_name: str = "",
        subject_name: str = "",
    ) -> None:
        """Create a new local login account."""
        normalized_username = username.strip()
        normalized_role = self._normalize_role(role)
        normalized_class, normalized_subject = self._normalize_user_scope(normalized_role, class_name, subject_name)
        if not normalized_username:
            raise ValueError("Username is required.")
        self._validate_new_password(password)
        if normalized_role == self.ROOT_ROLE:
            raise ValueError("Only the default root account can have full control.")

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT id FROM users WHERE username = ?",
                (normalized_username,),
            ).fetchone()
            if existing is not None:
                raise ValueError("A user with that username already exists.")
            connection.execute(
                """
                INSERT INTO users(username, password_hash, role, class_name, subject_name, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_username,
                    self.hash_password(password),
                    normalized_role,
                    normalized_class,
                    normalized_subject,
                    timestamp,
                ),
            )

    def update_user_password(self, username: str, new_password: str) -> None:
        """Update an existing user's password."""
        normalized_username = username.strip()
        if not normalized_username:
            raise ValueError("Username is required.")
        self._validate_new_password(new_password)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM users WHERE username = ?",
                (normalized_username,),
            ).fetchone()
            if row is None:
                raise ValueError("User does not exist.")
            connection.execute(
                "UPDATE users SET password_hash = ?, password_reset_required = 0 WHERE username = ?",
                (self.hash_password(new_password), normalized_username),
            )

    def update_student_password(self, student_id: str, new_password: str) -> None:
        """Update an existing student's viewer password."""
        normalized_student_id = student_id.strip()
        if not normalized_student_id:
            raise ValueError("Student ID is required.")
        self._validate_new_password(new_password)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT label_id FROM students WHERE student_id = ?",
                (normalized_student_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Student does not exist.")
            connection.execute(
                "UPDATE students SET password_hash = ?, password_reset_required = 0, updated_at = ? WHERE student_id = ?",
                (
                    self.hash_password(new_password),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    normalized_student_id,
                ),
            )

    def update_student_academic_record(
        self,
        student_id: str,
        *,
        class_name: str | None = None,
        section_name: str | None = None,
        batch_name: str | None = None,
        academic_year: str | None = None,
        semester: str | None = None,
        guardian_name: str | None = None,
        status: str | None = None,
    ) -> None:
        """Update the academic placement or lifecycle status for one student."""
        student = self.get_student_by_student_id(student_id)
        if student is None:
            raise ValueError("Student does not exist.")
        resolved_status = (status if status is not None else student.status).strip() or student.status
        if resolved_status not in self.VALID_STUDENT_STATUSES:
            raise ValueError(f"Student status must be one of: {', '.join(self.VALID_STUDENT_STATUSES)}.")
        resolved_section, resolved_batch, resolved_year, resolved_semester = self._resolve_academic_dimensions(
            section_name=section_name if section_name is not None else student.section_name,
            batch_name=batch_name if batch_name is not None else student.batch_name,
            academic_year=academic_year if academic_year is not None else student.academic_year,
            semester=semester if semester is not None else student.semester,
        )
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE students
                SET class_name = ?, section_name = ?, batch_name = ?, academic_year = ?, semester = ?,
                    guardian_name = ?, status = ?, updated_at = ?
                WHERE student_id = ?
                """,
                (
                    (class_name if class_name is not None else student.class_name).strip() or student.class_name,
                    resolved_section,
                    resolved_batch,
                    resolved_year,
                    resolved_semester,
                    (guardian_name if guardian_name is not None else student.guardian_name).strip(),
                    resolved_status,
                    timestamp,
                    student_id.strip(),
                ),
            )

    def reset_student_password(self, student_id: str) -> str:
        """Generate and store a fresh temporary password for a student."""
        temporary_password = self.generate_temporary_password()
        self.update_student_password(student_id, temporary_password)
        return temporary_password

    def delete_user(self, username: str) -> None:
        """Delete a non-admin user account."""
        normalized_username = username.strip()
        if normalized_username == "admin":
            raise ValueError("The default root account cannot be deleted.")
        with self._connect() as connection:
            connection.execute("DELETE FROM users WHERE username = ?", (normalized_username,))

    def get_user_scope(self, username: str) -> dict[str, str]:
        """Return the stored class/subject scope for one user account."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT role, class_name, subject_name FROM users WHERE username = ?",
                (username.strip(),),
            ).fetchone()
        if row is None:
            return {"role": "", "class_name": "", "subject_name": ""}
        return {
            "role": self._canonicalize_role(str(row["role"] or "")),
            "class_name": str(row["class_name"] or ""),
            "subject_name": str(row["subject_name"] or ""),
        }

    def log_audit_event(
        self,
        actor_username: str,
        action: str,
        entity_type: str,
        entity_id: str = "",
        details: str = "",
    ) -> None:
        """Persist one audit trail entry."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_logs(actor_username, action, entity_type, entity_id, details, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    actor_username.strip() or "system",
                    action.strip(),
                    entity_type.strip(),
                    entity_id.strip(),
                    details.strip(),
                    timestamp,
                ),
            )

    def list_audit_logs(self, limit: int = 200) -> list[dict[str, str]]:
        """Return recent audit trail rows."""
        safe_limit = max(1, min(limit, 500))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM audit_logs
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (safe_limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_student_by_student_id(self, student_id: str) -> Student | None:
        """Load one student using the UID entered in the app."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM students WHERE student_id = ?",
                (student_id,),
            ).fetchone()
        return self._student_from_row(row)

    def get_student_by_label_id(self, label_id: int) -> Student | None:
        """Load one student using the recognizer label ID."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM students WHERE label_id = ?",
                (label_id,),
            ).fetchone()
        return self._student_from_row(row)

    def list_students(
        self,
        class_name: str | None = None,
        *,
        section_name: str | None = None,
        academic_year: str | None = None,
        semester: str | None = None,
        active_only: bool = False,
    ) -> list[Student]:
        """Return the class roster, or all students if no class filter is provided."""
        query = "SELECT * FROM students"
        params: list[str] = []
        clauses: list[str] = []
        if class_name:
            clauses.append("class_name = ?")
            params.append(class_name)
        if section_name:
            clauses.append("section_name = ?")
            params.append(section_name)
        if academic_year:
            clauses.append("academic_year = ?")
            params.append(academic_year)
        if semester:
            clauses.append("semester = ?")
            params.append(semester)
        if active_only:
            clauses.append("status = 'Active'")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY LOWER(name), student_id"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [self._student_from_row(row) for row in rows if row is not None]

    def list_students_for_context(
        self,
        class_name: str,
        subject_name: str | None = None,
        *,
        section_name: str | None = None,
        academic_year: str | None = None,
        semester: str | None = None,
    ) -> list[Student]:
        """
        Students belong to a class, not to individual subjects.
        Switching subjects within the same class should still show the same class roster.
        """
        return self.list_students(
            class_name,
            section_name=section_name,
            academic_year=academic_year,
            semester=semester,
            active_only=True,
        )

    def list_classes(self) -> list[str]:
        """Return all known classes from student data and saved class/subject options."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT class_name FROM (
                    SELECT DISTINCT class_name FROM students
                    UNION
                    SELECT DISTINCT class_name FROM subjects
                )
                ORDER BY LOWER(class_name)
                """
            ).fetchall()
        values = [str(row["class_name"]) for row in rows if row["class_name"]]
        return values or ["General"]

    def list_sections(
        self,
        class_name: str | None = None,
        academic_year: str | None = None,
        semester: str | None = None,
    ) -> list[str]:
        """Return known sections, optionally filtered by class."""
        query = """
            SELECT section_name FROM (
                SELECT DISTINCT class_name, section_name, academic_year, semester FROM students
                UNION
                SELECT DISTINCT class_name, section_name, academic_year, semester FROM subjects
                UNION
                SELECT DISTINCT class_name, section_name, academic_year, semester FROM timetable
                UNION
                SELECT DISTINCT class_name, section_name, academic_year, semester FROM section_options
            )
            WHERE COALESCE(section_name, '') <> ''
        """
        params: list[str] = []
        if class_name:
            query += " AND class_name = ?"
            params.append(class_name)
        if academic_year:
            query += " AND academic_year = ?"
            params.append(academic_year)
        if semester:
            query += " AND semester = ?"
            params.append(semester)
        query += " ORDER BY LOWER(section_name)"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [str(row["section_name"]) for row in rows if row["section_name"]]

    def list_academic_years(self) -> list[str]:
        """Return configured and stored academic years."""
        settings = self.get_institute_settings()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT academic_year FROM (
                    SELECT DISTINCT academic_year FROM students
                    UNION
                    SELECT DISTINCT academic_year FROM subjects
                    UNION
                    SELECT DISTINCT academic_year FROM attendance
                )
                WHERE COALESCE(academic_year, '') <> ''
                ORDER BY academic_year DESC
                """
            ).fetchall()
        values = [str(row["academic_year"]) for row in rows if row["academic_year"]]
        if settings["academic_year"] not in values:
            values.insert(0, settings["academic_year"])
        return values

    def list_semesters(self, class_name: str | None = None, academic_year: str | None = None) -> list[str]:
        """Return available semesters, or a default yearly value for schools."""
        settings = self.get_institute_settings()
        if settings["attendance_mode"] != "semester":
            return ["Year"]
        query = """
            SELECT semester FROM (
                SELECT DISTINCT class_name, academic_year, semester FROM students
                UNION
                SELECT DISTINCT class_name, academic_year, semester FROM subjects
                UNION
                SELECT DISTINCT class_name, academic_year, semester FROM timetable
                UNION
                SELECT DISTINCT class_name, academic_year, semester FROM section_options
                UNION
                SELECT DISTINCT class_name, academic_year, semester FROM semester_options
            )
            WHERE COALESCE(semester, '') <> ''
        """
        params: list[str] = []
        if class_name:
            query += " AND class_name = ?"
            params.append(class_name)
        if academic_year:
            query += " AND academic_year = ?"
            params.append(academic_year)
        query += " ORDER BY semester"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        values = [str(row["semester"]) for row in rows if row["semester"]]
        return values or ["Semester 1"]

    def list_subjects(
        self,
        class_name: str | None = None,
        *,
        section_name: str | None = None,
        academic_year: str | None = None,
        semester: str | None = None,
    ) -> list[str]:
        """Return all saved subjects, optionally limited to one class."""
        query = "SELECT DISTINCT subject_name FROM subjects"
        params: list[str] = []
        clauses: list[str] = []
        if class_name:
            clauses.append("class_name = ?")
            params.append(class_name)
        if section_name:
            clauses.append("section_name = ?")
            params.append(section_name)
        if academic_year:
            clauses.append("academic_year = ?")
            params.append(academic_year)
        if semester:
            clauses.append("semester = ?")
            params.append(semester)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY LOWER(subject_name)"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        values = [str(row["subject_name"]) for row in rows if row["subject_name"]]
        return values or ["General"]

    def list_subject_records(
        self,
        class_name: str | None = None,
        *,
        section_name: str | None = None,
        academic_year: str | None = None,
        semester: str | None = None,
    ) -> list[dict[str, str]]:
        """Return saved subject rows with their academic context for management UIs."""
        query = """
            SELECT class_name, section_name, academic_year, semester, subject_name
            FROM subjects
        """
        params: list[str] = []
        clauses: list[str] = []
        if class_name:
            clauses.append("class_name = ?")
            params.append(class_name)
        if section_name:
            clauses.append("section_name = ?")
            params.append(section_name)
        if academic_year:
            clauses.append("academic_year = ?")
            params.append(academic_year)
        if semester:
            clauses.append("semester = ?")
            params.append(semester)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY LOWER(subject_name), LOWER(section_name), academic_year, semester"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def add_class(self, class_name: str) -> None:
        """Create a class option by saving a default subject row for it."""
        normalized = class_name.strip() or "General"
        self.add_subject(normalized, "General")

    def add_section(
        self,
        class_name: str,
        section_name: str,
        *,
        academic_year: str = "",
        semester: str = "",
    ) -> None:
        """Create a selectable section by storing a default subject row for that class section."""
        normalized_class = class_name.strip() or "General"
        normalized_section = section_name.strip()
        if not normalized_section:
            raise ValueError("Section name is required.")
        _normalized_section, _normalized_batch, normalized_year, normalized_semester = self._resolve_academic_dimensions(
            section_name=normalized_section,
            academic_year=academic_year,
            semester=semester,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO section_options(class_name, section_name, academic_year, semester)
                VALUES(?, ?, ?, ?)
                """,
                (normalized_class, normalized_section, normalized_year, normalized_semester),
            )
            if normalized_semester:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO semester_options(class_name, academic_year, semester)
                    VALUES(?, ?, ?)
                    """,
                    (normalized_class, normalized_year, normalized_semester),
                )

    def add_semester(
        self,
        class_name: str,
        academic_year: str,
        semester: str,
    ) -> None:
        """Create a selectable semester for one class and academic year."""
        normalized_class = class_name.strip() or "General"
        _section, _batch, normalized_year, normalized_semester = self._resolve_academic_dimensions(
            academic_year=academic_year,
            semester=semester,
        )
        if not normalized_semester:
            raise ValueError("Semester name is required.")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO semester_options(class_name, academic_year, semester)
                VALUES(?, ?, ?)
                """,
                (normalized_class, normalized_year, normalized_semester),
            )

    def add_subject(
        self,
        class_name: str,
        subject_name: str,
        *,
        section_name: str = "",
        academic_year: str = "",
        semester: str = "",
    ) -> None:
        """Create a selectable subject under a specific class."""
        normalized_class = class_name.strip() or "General"
        normalized_subject = subject_name.strip() or "General"
        normalized_section, _normalized_batch, normalized_year, normalized_semester = self._resolve_academic_dimensions(
            section_name=section_name,
            academic_year=academic_year,
            semester=semester,
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO subjects(class_name, section_name, academic_year, semester, subject_name)
                VALUES(?, ?, ?, ?, ?)
                """,
                (normalized_class, normalized_section, normalized_year, normalized_semester, normalized_subject),
            )

    def delete_subject(
        self,
        class_name: str,
        subject_name: str,
        *,
        section_name: str = "",
        academic_year: str = "",
        semester: str = "",
    ) -> bool:
        """Remove a subject row under a specific class/academic context."""
        normalized_class = class_name.strip() or "General"
        normalized_subject = subject_name.strip()
        query = "DELETE FROM subjects WHERE class_name = ? AND subject_name = ?"
        params: list[str] = [normalized_class, normalized_subject]
        if section_name:
            query += " AND section_name = ?"
            params.append(section_name.strip())
        if academic_year:
            query += " AND academic_year = ?"
            params.append(academic_year.strip())
        if semester:
            query += " AND semester = ?"
            params.append(semester.strip())
        with self._connect() as connection:
            cursor = connection.execute(query, tuple(params))
            return cursor.rowcount > 0

    def delete_section(
        self,
        class_name: str,
        section_name: str,
        *,
        academic_year: str = "",
        semester: str = "",
    ) -> bool:
        """Remove a section option from the active catalog."""
        normalized_class = class_name.strip() or "General"
        normalized_section = section_name.strip()
        query = "DELETE FROM section_options WHERE class_name = ? AND section_name = ?"
        params: list[str] = [normalized_class, normalized_section]
        if academic_year:
            query += " AND academic_year = ?"
            params.append(academic_year.strip())
        if semester:
            query += " AND semester = ?"
            params.append(semester.strip())
        with self._connect() as connection:
            cursor = connection.execute(query, tuple(params))
            return cursor.rowcount > 0

    def delete_semester(
        self,
        class_name: str,
        academic_year: str,
        semester: str,
    ) -> bool:
        """Remove a semester option from the active catalog."""
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM semester_options WHERE class_name = ? AND academic_year = ? AND semester = ?",
                (class_name.strip(), academic_year.strip(), semester.strip()),
            )
            return cursor.rowcount > 0

    def delete_class(self, class_name: str) -> bool:
        """Safely remove a class if no students are assigned to it."""
        normalized_class = class_name.strip()
        with self._connect() as connection:
            active_students = connection.execute(
                "SELECT COUNT(*) AS count FROM students WHERE class_name = ?",
                (normalized_class,),
            ).fetchone()["count"]
            if active_students > 0:
                raise ValueError(
                    f"Cannot delete class '{normalized_class}': {active_students} student(s) are currently enrolled."
                )
            connection.execute("DELETE FROM subjects WHERE class_name = ?", (normalized_class,))
            connection.execute("DELETE FROM section_options WHERE class_name = ?", (normalized_class,))
            connection.execute("DELETE FROM semester_options WHERE class_name = ?", (normalized_class,))
            connection.execute("DELETE FROM timetable WHERE class_name = ?", (normalized_class,))
            connection.execute("DELETE FROM model_state WHERE class_name = ?", (normalized_class,))
            return True

    def rename_class(self, old_name: str, new_name: str) -> bool:
        """Rename a class across all academic references and models."""
        old_norm = old_name.strip()
        new_norm = new_name.strip()
        if not old_norm or not new_norm:
            raise ValueError("Class names cannot be empty.")
        if old_norm == new_norm:
            return True
        with self._connect() as connection:
            connection.execute("UPDATE students SET class_name = ? WHERE class_name = ?", (new_norm, old_norm))
            connection.execute("UPDATE subjects SET class_name = ? WHERE class_name = ?", (new_norm, old_norm))
            connection.execute("UPDATE section_options SET class_name = ? WHERE class_name = ?", (new_norm, old_norm))
            connection.execute("UPDATE semester_options SET class_name = ? WHERE class_name = ?", (new_norm, old_norm))
            connection.execute("UPDATE timetable SET class_name = ? WHERE class_name = ?", (new_norm, old_norm))
            connection.execute("UPDATE attendance SET class_name = ? WHERE class_name = ?", (new_norm, old_norm))
            connection.execute("UPDATE attendance_sessions SET class_name = ? WHERE class_name = ?", (new_norm, old_norm))
            connection.execute("UPDATE model_state SET class_name = ? WHERE class_name = ?", (new_norm, old_norm))
            connection.execute("UPDATE users SET class_name = ? WHERE class_name = ?", (new_norm, old_norm))
            return True

    def add_timetable_entry(
        self,
        class_name: str,
        subject_name: str,
        day_name: str,
        start_time: str,
        end_time: str,
        room_name: str = "",
        teacher_name: str = "",
        *,
        section_name: str = "",
        academic_year: str = "",
        semester: str = "",
    ) -> None:
        """Save one timetable slot for a class and subject."""
        normalized_class = class_name.strip() or "General"
        normalized_subject = subject_name.strip() or "General"
        normalized_day = day_name.strip()
        normalized_start = start_time.strip()
        normalized_end = end_time.strip()
        normalized_section, _normalized_batch, normalized_year, normalized_semester = self._resolve_academic_dimensions(
            section_name=section_name,
            academic_year=academic_year,
            semester=semester,
        )
        self._validate_time_range(normalized_start, normalized_end)
        self.add_subject(
            normalized_class,
            normalized_subject,
            section_name=normalized_section,
            academic_year=normalized_year,
            semester=normalized_semester,
        )
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT id
                FROM timetable
                WHERE class_name = ? AND section_name = ? AND academic_year = ? AND semester = ? AND day_name = ? AND (
                    (? < end_time AND ? > start_time)
                )
                LIMIT 1
                """,
                (
                    normalized_class,
                    normalized_section,
                    normalized_year,
                    normalized_semester,
                    normalized_day,
                    normalized_start,
                    normalized_end,
                ),
            ).fetchone()
            if existing is not None:
                raise ValueError("This class already has an overlapping timetable slot.")
            connection.execute(
                """
                INSERT INTO timetable(class_name, section_name, academic_year, semester, subject_name, day_name, start_time, end_time, room_name, teacher_name)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_class,
                    normalized_section,
                    normalized_year,
                    normalized_semester,
                    normalized_subject,
                    normalized_day,
                    normalized_start,
                    normalized_end,
                    room_name.strip(),
                    teacher_name.strip(),
                ),
            )

    def list_timetable(self, class_name: str | None = None) -> list[dict[str, str]]:
        """Return timetable rows, optionally limited to one class."""
        query = "SELECT * FROM timetable"
        params: tuple[str, ...] = ()
        if class_name:
            query += " WHERE class_name = ?"
            params = (class_name,)
        query += " ORDER BY class_name, section_name, academic_year, semester, day_name, start_time"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def delete_timetable_entry(self, entry_id: int) -> None:
        """Delete one timetable row."""
        with self._connect() as connection:
            connection.execute("DELETE FROM timetable WHERE id = ?", (entry_id,))

    def get_active_timetable_entry(
        self,
        class_name: str | None = None,
        moment: datetime | None = None,
    ) -> dict[str, str] | None:
        """Return the current timetable slot for now, optionally limited to one class."""
        moment = moment or datetime.now()
        day_name = moment.strftime("%A")
        current_time = moment.strftime("%H:%M")
        query = """
            SELECT *
            FROM timetable
            WHERE day_name = ? AND start_time <= ? AND end_time >= ?
        """
        params: list[str] = [day_name, current_time, current_time]
        if class_name:
            query += " AND class_name = ?"
            params.append(class_name)
        query += " AND academic_year = ?"
        params.append(self.get_institute_settings()["academic_year"])
        query += " ORDER BY class_name, section_name, semester, start_time LIMIT 1"
        with self._connect() as connection:
            row = connection.execute(query, tuple(params)).fetchone()
        return dict(row) if row is not None else None

    def get_next_timetable_entry(
        self,
        class_name: str | None = None,
        moment: datetime | None = None,
    ) -> dict[str, str] | None:
        """Return the next timetable slot later today, optionally limited to one class."""
        moment = moment or datetime.now()
        day_name = moment.strftime("%A")
        current_time = moment.strftime("%H:%M")
        query = """
            SELECT *
            FROM timetable
            WHERE day_name = ? AND start_time > ?
        """
        params: list[str] = [day_name, current_time]
        if class_name:
            query += " AND class_name = ?"
            params.append(class_name)
        query += " AND academic_year = ?"
        params.append(self.get_institute_settings()["academic_year"])
        query += " ORDER BY start_time, class_name, section_name, semester LIMIT 1"
        with self._connect() as connection:
            row = connection.execute(query, tuple(params)).fetchone()
        return dict(row) if row is not None else None

    def suggest_attendance_status(
        self,
        class_name: str,
        subject_name: str,
        moment: datetime | None = None,
        late_grace_minutes: int = 10,
    ) -> str:
        """Suggest Present or Late from the active timetable slot."""
        moment = moment or datetime.now()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM timetable
                WHERE class_name = ? AND subject_name = ? AND day_name = ? AND academic_year = ?
                ORDER BY start_time
                LIMIT 1
                """,
                (class_name, subject_name, moment.strftime("%A"), self.get_institute_settings()["academic_year"]),
            ).fetchone()
        if row is None:
            return "Present"

        start_text = str(row["start_time"])
        try:
            slot_start = datetime.strptime(
                f"{moment.strftime('%Y-%m-%d')} {start_text}",
                "%Y-%m-%d %H:%M",
            )
        except ValueError:
            return "Present"
        return "Present" if moment <= slot_start + timedelta(minutes=late_grace_minutes) else "Late"

    def delete_student(self, student_id: str) -> None:
        """Delete a student and all attendance rows linked to that UID."""
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM attendance_change_requests
                WHERE attendance_id IN (SELECT id FROM attendance WHERE student_id = ?)
                """,
                (student_id,),
            )
            connection.execute("DELETE FROM attendance WHERE student_id = ?", (student_id,))
            connection.execute("DELETE FROM students WHERE student_id = ?", (student_id,))
            connection.execute("DELETE FROM login_throttle WHERE identity_key = ?", (f"student:{student_id}",))

    def attendance_exists(
        self,
        student_id: str,
        class_name: str,
        subject_name: str,
        date_value: str,
    ) -> bool:
        """Check whether this student is already marked for the same class/subject/day."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT 1
                FROM attendance
                WHERE student_id = ? AND class_name = ? AND subject_name = ? AND date = ?
                LIMIT 1
                """,
                (student_id, class_name, subject_name, date_value),
            ).fetchone()
        return row is not None

    def get_attendance_session(
        self,
        class_name: str,
        subject_name: str,
        date_value: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> dict[str, str]:
        """Return attendance session state for one class/subject/day."""
        def _fetch(conn: sqlite3.Connection) -> dict[str, str]:
            row = conn.execute(
                """
                SELECT *
                FROM attendance_sessions
                WHERE class_name = ? AND subject_name = ? AND date = ?
                """,
                (class_name, subject_name, date_value),
            ).fetchone()
            if row is None:
                return {
                    "class_name": class_name,
                    "subject_name": subject_name,
                    "date": date_value,
                    "finalized_by": "",
                    "finalized_at": "",
                    "locked_by": "",
                    "locked_at": "",
                }
            return dict(row)

        if connection is not None:
            return _fetch(connection)
        with self._connect() as conn:
            return _fetch(conn)

    def is_attendance_locked(
        self,
        class_name: str,
        subject_name: str,
        date_value: str,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        """Return whether this attendance session is locked against further edits."""
        session = self.get_attendance_session(class_name, subject_name, date_value, connection=connection)
        return bool(session.get("locked_at"))

    def create_attendance_change_request(
        self,
        record_id: int,
        requested_class_name: str,
        requested_subject_name: str,
        requested_date: str,
        requested_time: str,
        requested_status: str,
        requested_by: str,
        reason: str,
    ) -> int:
        """Store an approval request for changing a locked attendance record."""
        record = self.get_attendance_by_id(record_id)
        if record is None:
            raise ValueError("Attendance record was not found.")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT id
                FROM attendance_change_requests
                WHERE attendance_id = ? AND status = 'Pending'
                LIMIT 1
                """,
                (record_id,),
            ).fetchone()
            if existing is not None:
                raise ValueError("A pending change request already exists for this attendance record.")
            cursor = connection.execute(
                """
                INSERT INTO attendance_change_requests(
                    attendance_id,
                    original_class_name,
                    original_subject_name,
                    original_date,
                    original_time,
                    original_status,
                    requested_class_name,
                    requested_subject_name,
                    requested_date,
                    requested_time,
                    requested_status,
                    requested_by,
                    reason,
                    status,
                    created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?)
                """,
                (
                    record_id,
                    str(record.get("class_name", "")),
                    str(record.get("subject_name", "")),
                    str(record.get("date", "")),
                    str(record.get("time", "")),
                    str(record.get("status", "")),
                    requested_class_name.strip(),
                    requested_subject_name.strip(),
                    requested_date.strip(),
                    requested_time.strip(),
                    requested_status.strip(),
                    requested_by.strip(),
                    reason.strip(),
                    timestamp,
                ),
            )
        return int(cursor.lastrowid)

    def list_attendance_change_requests(self, status: str | None = None) -> list[dict[str, str]]:
        """Return attendance change requests, optionally filtered by workflow status."""
        query = "SELECT * FROM attendance_change_requests"
        params: list[str] = []
        if status:
            query += " WHERE status = ?"
            params.append(status.strip())
        query += " ORDER BY created_at DESC, id DESC"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def get_missing_students_for_attendance(
        self,
        class_name: str,
        subject_name: str,
        date_value: str,
    ) -> list[Student]:
        """Return students in the class who do not yet have attendance for this subject/date."""
        existing_student_ids = {
            str(row["student_id"])
            for row in self.list_attendance(date_value, class_name, subject_name)
        }
        return [
            student
            for student in self.list_students_for_context(class_name, subject_name)
            if student.student_id not in existing_student_ids
        ]

    def auto_mark_absent_students(
        self,
        class_name: str,
        subject_name: str,
        date_value: str,
        actor_username: str = "",
    ) -> dict[str, int | list[str]]:
        """Mark all missing students absent for the selected class/subject/day."""
        if self.is_attendance_locked(class_name, subject_name, date_value):
            raise ValueError("Attendance is locked for this class, subject, and date.")

        missing_students = self.get_missing_students_for_attendance(class_name, subject_name, date_value)
        if not missing_students:
            return {
                "created": 0,
                "missing_before": 0,
                "student_ids": [],
                "actor": actor_username,
            }

        settings = self.get_institute_settings()
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        created_count = 0
        created_student_ids: list[str] = []

        with self._connect() as connection:
            for student in missing_students:
                academic_year = student.academic_year if student.academic_year else settings["academic_year"]
                semester = student.semester if student.semester else ("Year" if settings["attendance_mode"] == "yearly" else "")
                section_name = student.section_name or ""
                batch_name = student.batch_name or ""
                connection.execute(
                    """
                    INSERT INTO attendance(
                        student_id, name, class_name, section_name, batch_name, academic_year, semester,
                        subject_name, date, time, status, source, created_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        student.student_id,
                        student.name,
                        class_name,
                        section_name,
                        batch_name,
                        academic_year,
                        semester,
                        subject_name,
                        date_value,
                        "23:59:00",
                        "Absent",
                        "system",
                        created_at,
                    ),
                )
                created_count += 1
                created_student_ids.append(student.student_id)

        return {
            "created": created_count,
            "missing_before": len(missing_students),
            "student_ids": created_student_ids,
            "actor": actor_username,
        }

    def finalize_attendance_session(
        self,
        class_name: str,
        subject_name: str,
        date_value: str,
        actor_username: str,
        lock_session: bool = True,
    ) -> dict[str, int | str | bool]:
        """Auto-complete missing absences and finalize the attendance session."""
        if self.is_attendance_locked(class_name, subject_name, date_value):
            raise ValueError("Attendance is already locked for this class, subject, and date.")

        absent_result = self.auto_mark_absent_students(
            class_name,
            subject_name,
            date_value,
            actor_username=actor_username,
        )
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO attendance_sessions(class_name, subject_name, date, finalized_by, finalized_at, locked_by, locked_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(class_name, subject_name, date)
                DO UPDATE SET
                    finalized_by = excluded.finalized_by,
                    finalized_at = excluded.finalized_at,
                    locked_by = excluded.locked_by,
                    locked_at = excluded.locked_at
                """,
                (
                    class_name,
                    subject_name,
                    date_value,
                    actor_username.strip(),
                    timestamp,
                    actor_username.strip() if lock_session else "",
                    timestamp if lock_session else "",
                ),
            )
        return {
            "created_absent_count": int(absent_result["created"]),
            "finalized": True,
            "locked": lock_session,
            "timestamp": timestamp,
        }

    def add_attendance_record(
        self,
        student_id: str,
        name: str,
        class_name: str,
        subject_name: str,
        date: str,
        time_value: str,
        status: str,
        source: str = "camera",
        allow_duplicate_same_day: bool = False,
    ) -> bool:
        """Store one attendance row and block duplicates for the same subject on the same date."""
        if self.is_attendance_locked(class_name, subject_name, date):
            raise ValueError("Attendance is locked for this class, subject, and date.")
        if not allow_duplicate_same_day and self.attendance_exists(student_id, class_name, subject_name, date):
            return False

        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        student = self.get_student_by_student_id(student_id)
        settings = self.get_institute_settings()
        academic_year = student.academic_year if student and student.academic_year else settings["academic_year"]
        semester = student.semester if student and student.semester else ("Year" if settings["attendance_mode"] == "yearly" else "")
        section_name = student.section_name if student is not None else ""
        batch_name = student.batch_name if student is not None else ""
        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO attendance(
                        student_id, name, class_name, section_name, batch_name, academic_year, semester,
                        subject_name, date, time, status, source, created_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        student_id,
                        name,
                        class_name,
                        section_name,
                        batch_name,
                        academic_year,
                        semester,
                        subject_name,
                        date,
                        time_value,
                        status,
                        source,
                        created_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if "UNIQUE constraint failed: attendance.student_id, attendance.class_name, attendance.subject_name, attendance.date" in str(exc):
                    return False
                raise
        return True

    def update_attendance_record(
        self,
        record_id: int,
        class_name: str,
        subject_name: str,
        date: str,
        time_value: str,
        status: str,
    ) -> None:
        """Edit an existing attendance row from the dashboard."""
        self._update_attendance_record_internal(
            record_id,
            class_name,
            subject_name,
            date,
            time_value,
            status,
            ignore_original_lock=False,
            ignore_destination_lock=False,
        )

    def approve_attendance_change_request(
        self,
        request_id: int,
        reviewer_username: str,
        review_note: str = "",
    ) -> None:
        """Approve one pending attendance change request and apply the stored edit."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM attendance_change_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Change request was not found.")
            if str(row["status"]) != "Pending":
                raise ValueError("Only pending change requests can be approved.")
            self._update_attendance_record_internal(
                int(row["attendance_id"]),
                str(row["requested_class_name"]),
                str(row["requested_subject_name"]),
                str(row["requested_date"]),
                str(row["requested_time"]),
                str(row["requested_status"]),
                ignore_original_lock=True,
                ignore_destination_lock=True,
                connection=connection,
            )
            connection.execute(
                """
                UPDATE attendance_change_requests
                SET status = 'Approved', reviewed_by = ?, reviewed_at = ?, review_note = ?
                WHERE id = ?
                """,
                (
                    reviewer_username.strip(),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    review_note.strip(),
                    request_id,
                ),
            )

    def reject_attendance_change_request(
        self,
        request_id: int,
        reviewer_username: str,
        review_note: str,
    ) -> None:
        """Reject one pending attendance change request."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM attendance_change_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Change request was not found.")
            if str(row["status"]) != "Pending":
                raise ValueError("Only pending change requests can be rejected.")
            connection.execute(
                """
                UPDATE attendance_change_requests
                SET status = 'Rejected', reviewed_by = ?, reviewed_at = ?, review_note = ?
                WHERE id = ?
                """,
                (
                    reviewer_username.strip(),
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    review_note.strip(),
                    request_id,
                ),
            )

    def list_attendance(
        self,
        date_filter: str | None = None,
        class_name: str | None = None,
        subject_name: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        status: str | None = None,
        source: str | None = None,
        student_id: str | None = None,
        student_query: str | None = None,
        section_name: str | None = None,
        academic_year: str | None = None,
        semester: str | None = None,
    ) -> list[dict[str, str]]:
        """Return attendance rows for the selected date/class/subject filters."""
        query = "SELECT * FROM attendance"
        params: list[str] = []
        clauses: list[str] = []
        if date_filter:
            clauses.append("date = ?")
            params.append(date_filter)
        if date_from:
            clauses.append("date >= ?")
            params.append(date_from)
        if date_to:
            clauses.append("date <= ?")
            params.append(date_to)
        if class_name:
            clauses.append("class_name = ?")
            params.append(class_name)
        if section_name:
            clauses.append("section_name = ?")
            params.append(section_name)
        if academic_year:
            clauses.append("academic_year = ?")
            params.append(academic_year)
        if semester:
            clauses.append("semester = ?")
            params.append(semester)
        if subject_name:
            clauses.append("subject_name = ?")
            params.append(subject_name)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if source:
            clauses.append("source = ?")
            params.append(source)
        if student_id:
            clauses.append("student_id = ?")
            params.append(student_id)
        if student_query:
            clauses.append("(LOWER(student_id) LIKE ? OR LOWER(name) LIKE ?)")
            wildcard = f"%{student_query.strip().lower()}%"
            params.extend([wildcard, wildcard])
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY date DESC, time DESC, id DESC"

        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def get_attendance_by_id(self, record_id: int) -> dict[str, str] | None:
        """Fetch a single attendance row for the edit dialog."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM attendance WHERE id = ?",
                (record_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def _update_attendance_record_internal(
        self,
        record_id: int,
        class_name: str,
        subject_name: str,
        date: str,
        time_value: str,
        status: str,
        *,
        ignore_original_lock: bool,
        ignore_destination_lock: bool,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Central attendance edit implementation used by direct edits and approvals."""
        def _execute(conn: sqlite3.Connection) -> None:
            row = conn.execute(
                "SELECT * FROM attendance WHERE id = ?",
                (record_id,),
            ).fetchone()
            if row is None:
                raise ValueError("Attendance record was not found.")
            original_class = str(row["class_name"])
            original_subject = str(row["subject_name"])
            original_date = str(row["date"])
            student_id = str(row["student_id"])

            if (not ignore_original_lock) and self.is_attendance_locked(original_class, original_subject, original_date, connection=conn):
                raise ValueError("Attendance is locked for the original class, subject, and date.")
            if (
                (original_class != class_name or original_subject != subject_name or original_date != date)
                and (not ignore_destination_lock)
                and self.is_attendance_locked(class_name, subject_name, date, connection=conn)
            ):
                raise ValueError("Attendance is locked for the new class, subject, and date.")
            duplicate = conn.execute(
                """
                SELECT id
                FROM attendance
                WHERE student_id = ? AND class_name = ? AND subject_name = ? AND date = ? AND id <> ?
                LIMIT 1
                """,
                (student_id, class_name, subject_name, date, record_id),
            ).fetchone()
            if duplicate is not None:
                raise ValueError("This student already has attendance for that class, subject, and date.")
            conn.execute(
                """
                UPDATE attendance
                SET class_name = ?, subject_name = ?, date = ?, time = ?, status = ?
                WHERE id = ?
                """,
                (class_name, subject_name, date, time_value, status, record_id),
            )

        if connection is not None:
            _execute(connection)
        else:
            with self._connect() as conn:
                _execute(conn)

    def get_summary(
        self,
        class_name: str | None = None,
        subject_name: str | None = None,
        *,
        academic_year: str | None = None,
        semester: str | None = None,
    ) -> dict[str, int | str]:
        """Compute dashboard counts for the active class and subject."""
        today = datetime.now().strftime("%Y-%m-%d")
        student_query = "SELECT COUNT(*) AS count FROM students"
        student_params: list[str] = []
        student_clauses: list[str] = []
        if class_name:
            student_clauses.append("class_name = ?")
            student_params.append(class_name)
        if academic_year:
            student_clauses.append("academic_year = ?")
            student_params.append(academic_year)
        if semester:
            student_clauses.append("semester = ?")
            student_params.append(semester)
        if student_clauses:
            student_query += " WHERE " + " AND ".join(student_clauses)

        attendance_query = "SELECT COUNT(*) AS count FROM attendance WHERE date = ?"
        attendance_params: list[str] = [today]
        if class_name:
            attendance_query += " AND class_name = ?"
            attendance_params.append(class_name)
        if academic_year:
            attendance_query += " AND academic_year = ?"
            attendance_params.append(academic_year)
        if semester:
            attendance_query += " AND semester = ?"
            attendance_params.append(semester)
        if subject_name:
            attendance_query += " AND subject_name = ?"
            attendance_params.append(subject_name)

        with self._connect() as connection:
            student_count = connection.execute(student_query, tuple(student_params)).fetchone()["count"]
            attendance_count = connection.execute(
                attendance_query,
                tuple(attendance_params),
            ).fetchone()["count"]
        return {
            "student_count": int(student_count),
            "today_attendance": int(attendance_count),
            "today": today,
            "academic_year": academic_year or "",
            "semester": semester or "",
        }

    def get_attendance_status_breakdown(
        self,
        class_name: str | None = None,
        subject_name: str | None = None,
        *,
        section_name: str | None = None,
        academic_year: str | None = None,
        semester: str | None = None,
    ) -> list[dict[str, str]]:
        """Return grouped attendance totals for dashboard analytics."""
        query = "SELECT status, COUNT(*) AS total FROM attendance"
        params: list[str] = []
        clauses: list[str] = []
        if class_name:
            clauses.append("class_name = ?")
            params.append(class_name)
        if section_name:
            clauses.append("section_name = ?")
            params.append(section_name)
        if academic_year:
            clauses.append("academic_year = ?")
            params.append(academic_year)
        if semester:
            clauses.append("semester = ?")
            params.append(semester)
        if subject_name:
            clauses.append("subject_name = ?")
            params.append(subject_name)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " GROUP BY status ORDER BY total DESC, status"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(params)).fetchall()
        return [dict(row) for row in rows]

    def get_attendance_report_summary(
        self,
        class_name: str | None = None,
        subject_name: str | None = None,
        date_filter: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        status: str | None = None,
        source: str | None = None,
        student_id: str | None = None,
        student_query: str | None = None,
        section_name: str | None = None,
        academic_year: str | None = None,
        semester: str | None = None,
    ) -> dict[str, object]:
        """Return grouped attendance reporting summaries for the current filter set."""
        rows = self.list_attendance(
            date_filter=date_filter,
            class_name=class_name,
            subject_name=subject_name,
            date_from=date_from,
            date_to=date_to,
            status=status,
            source=source,
            student_id=student_id,
            student_query=student_query,
            section_name=section_name,
            academic_year=academic_year,
            semester=semester,
        )

        by_status: dict[str, int] = {}
        by_student: dict[tuple[str, str], int] = {}
        for row in rows:
            status_name = str(row.get("status", ""))
            by_status[status_name] = by_status.get(status_name, 0) + 1
            student_key = (str(row.get("student_id", "")), str(row.get("name", "")))
            by_student[student_key] = by_student.get(student_key, 0) + 1

        student_rows = [
            {"student_id": student_id, "name": name, "total": total}
            for (student_id, name), total in sorted(
                by_student.items(),
                key=lambda item: (-item[1], item[0][1].lower(), item[0][0].lower()),
            )
        ]
        status_rows = [
            {"status": status_name, "total": total}
            for status_name, total in sorted(by_status.items(), key=lambda item: (-item[1], item[0]))
        ]

        return {
            "overview": {
                "total_records": len(rows),
                "unique_students": len(by_student),
                "class_name": class_name or "All Classes",
                "subject_name": subject_name or "All Subjects",
                "section_name": section_name or "All Sections",
                "academic_year": academic_year or "",
                "semester": semester or "",
                "date_filter": date_filter or "",
                "date_from": date_from or "",
                "date_to": date_to or "",
            },
            "by_status": status_rows,
            "by_student": student_rows,
        }

    def get_student_attendance_history(self, student_id: str, limit: int = 200) -> list[dict[str, str]]:
        """Return recent attendance rows for one student across subjects."""
        safe_limit = max(1, min(limit, 500))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM attendance
                WHERE student_id = ?
                ORDER BY date DESC, time DESC, id DESC
                LIMIT ?
                """,
                (student_id, safe_limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_student_attendance_summary(self, student_id: str) -> dict[str, int | float | str]:
        """Return a compact attendance summary for one student across all subjects."""
        rows = self.get_student_attendance_history(student_id, limit=500)
        total_records = len(rows)
        present_like = sum(1 for row in rows if str(row.get("status", "")) in ("Present", "Late"))
        absent_count = sum(1 for row in rows if str(row.get("status", "")) == "Absent")
        late_count = sum(1 for row in rows if str(row.get("status", "")) == "Late")
        excused_count = sum(1 for row in rows if str(row.get("status", "")) == "Excused")
        attendance_rate = (present_like / total_records * 100.0) if total_records else 0.0
        latest_date = str(rows[0].get("date", "")) if rows else ""
        return {
            "total_records": total_records,
            "present_like": present_like,
            "absent_count": absent_count,
            "late_count": late_count,
            "excused_count": excused_count,
            "attendance_rate": round(attendance_rate, 1),
            "latest_date": latest_date,
        }

    def get_student_attendance_percentage(
        self,
        student_id: str,
        *,
        academic_year: str | None = None,
        semester: str | None = None,
        subject_name: str | None = None,
    ) -> dict[str, int | float | str]:
        """Calculate attendance percentage for a student within the requested academic scope."""
        rows = self.get_student_attendance_history(student_id, limit=1000)
        filtered = [
            row
            for row in rows
            if (not academic_year or str(row.get("academic_year", "")) == academic_year)
            and (not semester or str(row.get("semester", "")) == semester)
            and (not subject_name or str(row.get("subject_name", "")) == subject_name)
        ]
        total_records = len(filtered)
        present_like = sum(1 for row in filtered if str(row.get("status", "")) in ("Present", "Late"))
        attendance_rate = round((present_like / total_records * 100.0) if total_records else 0.0, 1)
        return {
            "student_id": student_id,
            "academic_year": academic_year or "",
            "semester": semester or "",
            "subject_name": subject_name or "",
            "total_records": total_records,
            "present_like": present_like,
            "attendance_rate": attendance_rate,
        }

    def get_student_subject_breakdown(self, student_id: str) -> list[dict[str, int | float | str]]:
        """Return per-subject attendance totals and rates for one student."""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT subject_name, status, COUNT(*) AS total
                FROM attendance
                WHERE student_id = ?
                GROUP BY subject_name, status
                ORDER BY LOWER(subject_name), status
                """,
                (student_id,),
            ).fetchall()
        grouped: dict[str, dict[str, int]] = {}
        for row in rows:
            subject_name = str(row["subject_name"] or "General")
            subject_group = grouped.setdefault(subject_name, {})
            subject_group[str(row["status"])] = int(row["total"])
        breakdown: list[dict[str, int | float | str]] = []
        for subject_name in sorted(grouped, key=str.lower):
            counts = grouped[subject_name]
            total_records = sum(counts.values())
            present_like = counts.get("Present", 0) + counts.get("Late", 0)
            breakdown.append(
                {
                    "subject_name": subject_name,
                    "total_records": total_records,
                    "present_like": present_like,
                    "absent_count": counts.get("Absent", 0),
                    "late_count": counts.get("Late", 0),
                    "excused_count": counts.get("Excused", 0),
                    "attendance_rate": round((present_like / total_records * 100.0) if total_records else 0.0, 1),
                }
            )
        return breakdown

    def list_low_attendance_students(
        self,
        class_name: str | None = None,
        subject_name: str | None = None,
        threshold: float | None = None,
        min_records: int = 3,
        *,
        section_name: str | None = None,
        academic_year: str | None = None,
        semester: str | None = None,
    ) -> list[dict[str, int | float | str]]:
        """Return students whose attendance rate falls below the configured threshold."""
        threshold = float(self.LOW_ATTENDANCE_THRESHOLD if threshold is None else threshold)
        students = self.list_students(
            class_name,
            section_name=section_name,
            academic_year=academic_year,
            semester=semester,
            active_only=True,
        )
        alerts: list[dict[str, int | float | str]] = []
        for student in students:
            if subject_name:
                history = [
                    row
                    for row in self.get_student_attendance_history(student.student_id, limit=500)
                    if str(row.get("subject_name", "")) == subject_name
                    and (not academic_year or str(row.get("academic_year", "")) == academic_year)
                    and (not semester or str(row.get("semester", "")) == semester)
                ]
                total_records = len(history)
                present_like = sum(1 for row in history if str(row.get("status", "")) in ("Present", "Late"))
                rate = round((present_like / total_records * 100.0) if total_records else 0.0, 1)
            else:
                summary = self.get_student_attendance_percentage(
                    student.student_id,
                    academic_year=academic_year,
                    semester=semester,
                )
                total_records = int(summary["total_records"])
                present_like = int(summary["present_like"])
                rate = float(summary["attendance_rate"])
            if total_records < max(1, min_records):
                continue
            if rate < threshold:
                alerts.append(
                    {
                        "student_id": student.student_id,
                        "name": student.name,
                        "class_name": student.class_name,
                        "section_name": student.section_name,
                        "academic_year": student.academic_year,
                        "semester": student.semester,
                        "subject_name": subject_name or "All Subjects",
                        "total_records": total_records,
                        "present_like": present_like,
                        "attendance_rate": rate,
                    }
                )
        alerts.sort(key=lambda row: (float(row["attendance_rate"]), str(row["name"]).lower(), str(row["student_id"]).lower()))
        return alerts

    def get_table_names(self) -> tuple[str, ...]:
        """Return the list of tables that are safe to expose in the built-in viewer."""
        return self.VIEWABLE_TABLES

    def get_table_preview(
        self,
        table_name: str,
        limit: int = 200,
        *,
        hide_empty_columns: bool = False,
    ) -> tuple[list[str], list[dict[str, str]]]:
        """Return column names and a limited set of rows for the selected database table."""
        if table_name not in self.VIEWABLE_TABLES:
            raise ValueError(f"Unsupported table: {table_name}")

        safe_limit = max(1, min(limit, 500))
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM {table_name} LIMIT ?",
                (safe_limit,),
            ).fetchall()

        columns = list(rows[0].keys()) if rows else self._get_table_columns(table_name)
        masked_rows = [self._mask_sensitive_table_row(table_name, dict(row)) for row in rows]
        if hide_empty_columns and columns:
            visible_columns: list[str] = []
            for column in columns:
                if any(str(row.get(column, "")).strip() for row in masked_rows):
                    visible_columns.append(column)
            columns = visible_columns or columns
            masked_rows = [{column: row.get(column, "") for column in columns} for row in masked_rows]
        return columns, masked_rows

    def update_student_profile(
        self,
        student_id: str,
        *,
        name: str | None = None,
        class_name: str | None = None,
        section_name: str | None = None,
        batch_name: str | None = None,
        academic_year: str | None = None,
        semester: str | None = None,
        guardian_name: str | None = None,
        status: str | None = None,
    ) -> Student:
        """Update core editable student fields and return the fresh record."""
        student = self.get_student_by_student_id(student_id)
        if student is None:
            raise ValueError("Student does not exist.")

        resolved_name = (name if name is not None else student.name).strip()
        if not resolved_name:
            raise ValueError("Student name is required.")

        resolved_class_name = (class_name if class_name is not None else student.class_name).strip()
        if not resolved_class_name:
            raise ValueError("Class name is required.")

        resolved_status = (status if status is not None else student.status).strip() or student.status
        if resolved_status not in self.VALID_STUDENT_STATUSES:
            raise ValueError(f"Student status must be one of: {', '.join(self.VALID_STUDENT_STATUSES)}.")

        resolved_section, resolved_batch, resolved_year, resolved_semester = self._resolve_academic_dimensions(
            section_name=section_name if section_name is not None else student.section_name,
            batch_name=batch_name if batch_name is not None else student.batch_name,
            academic_year=academic_year if academic_year is not None else student.academic_year,
            semester=semester if semester is not None else student.semester,
        )
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE students
                SET name = ?, class_name = ?, section_name = ?, batch_name = ?, academic_year = ?,
                    semester = ?, guardian_name = ?, status = ?, updated_at = ?
                WHERE student_id = ?
                """,
                (
                    resolved_name,
                    resolved_class_name,
                    resolved_section,
                    resolved_batch,
                    resolved_year,
                    resolved_semester,
                    (guardian_name if guardian_name is not None else student.guardian_name).strip(),
                    resolved_status,
                    timestamp,
                    student_id.strip(),
                ),
            )
        updated_student = self.get_student_by_student_id(student_id)
        if updated_student is None:
            raise ValueError("Student update did not persist.")
        return updated_student

    def _get_table_columns(self, table_name: str) -> list[str]:
        """Load the column list when a table preview has no rows yet."""
        with self._connect() as connection:
            rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        return [str(row["name"]) for row in rows]

    def backup_database(self, destination_path: Path) -> Path:
        """Create a consistent backup of the current SQLite database using sqlite3 backup API."""
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        harden_permissions(destination_path.parent, is_dir=True)
        with self._connect() as src:
            try:
                src.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            dst = sqlite3.connect(destination_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
        harden_permissions(destination_path, is_dir=False)
        return destination_path

    def list_backup_files(self, backup_dir: Path) -> list[Path]:
        """Return known SQLite backup files sorted from newest to oldest."""
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_files = sorted(
            (path for path in backup_dir.glob("*.db") if path.is_file()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        LOGGER.info("Listed %s backup file(s) from %s", len(backup_files), backup_dir)
        return backup_files

    def restore_database(self, backup_path: Path, create_safety_backup: bool = True) -> Path:
        """Restore the main database file from a backup and optionally preserve the current state first."""
        if not backup_path.exists() or not backup_path.is_file():
            raise ValueError("Backup file was not found.")

        if create_safety_backup and self.db_path.exists():
            safety_name = f"{self.db_path.stem}_pre_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}{self.db_path.suffix}"
            safety_path = backup_path.parent / safety_name
            self.backup_database(safety_path)
            LOGGER.info("Created safety backup at %s", safety_path)

        # Remove stale WAL / SHM sidecars so the restored database doesn't replay mismatched logs
        wal_path = self.db_path.with_name(self.db_path.name + "-wal")
        shm_path = self.db_path.with_name(self.db_path.name + "-shm")
        if wal_path.exists():
            try:
                wal_path.unlink()
            except Exception:
                pass
        if shm_path.exists():
            try:
                shm_path.unlink()
            except Exception:
                pass

        shutil.copy2(backup_path, self.db_path)
        harden_permissions(self.db_path, is_dir=False)
        self.refresh_database()
        LOGGER.info("Database restored from backup %s", backup_path)
        return self.db_path

    def write_generated_credentials_report(
        self,
        prefix: str,
        credentials: list[dict[str, str]],
    ) -> Path | None:
        """Write a temporary password report CSV so administrators can distribute credentials."""
        if not credentials:
            return None
        reports_dir = self.db_path.parent / self.CREDENTIAL_REPORT_SUBDIR
        reports_dir.mkdir(parents=True, exist_ok=True)
        harden_permissions(reports_dir, is_dir=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = reports_dir / f"imported_student_passwords_{prefix}_{timestamp}.csv"
        with report_path.open("w", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(
                csv_file,
                fieldnames=["student_id", "name", "class_name", "temporary_password"],
            )
            writer.writeheader()
            for item in credentials:
                writer.writerow(
                    {
                        "student_id": item.get("student_id", ""),
                        "name": item.get("name", ""),
                        "class_name": item.get("class_name", ""),
                        "temporary_password": item.get("password", ""),
                    }
                )
        harden_permissions(report_path, is_dir=False)
        return report_path

    def list_generated_credential_reports(self) -> list[Path]:
        """Return generated temporary-password CSV reports sorted from newest to oldest."""
        seen: set[Path] = set()
        candidates: list[Path] = []
        for directory in (self.db_path.parent / self.CREDENTIAL_REPORT_SUBDIR, self.db_path.parent):
            if not directory.exists():
                continue
            for path in directory.glob(self.CREDENTIAL_REPORT_GLOB):
                if not path.is_file():
                    continue
                resolved = path.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                candidates.append(path)
        return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)

    def delete_generated_credential_reports(self) -> int:
        """Delete generated temporary-password CSV reports and return the count removed."""
        deleted_count = 0
        for path in self.list_generated_credential_reports():
            path.unlink(missing_ok=True)
            deleted_count += 1
        return deleted_count

    def clear_admin_setup_file(self) -> bool:
        """Remove the bootstrap admin credentials file once it is no longer needed."""
        path = self.get_admin_setup_file_path()
        if not path.exists():
            return False
        path.unlink()
        return True

    def get_login_cooldown_remaining(self, identity_key: str, now: datetime | None = None) -> int:
        """Return how many whole seconds remain before this identity may try again."""
        now = now or datetime.now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT failures, blocked_until FROM login_throttle WHERE identity_key = ?",
                (identity_key.strip().lower(),),
            ).fetchone()
            if row is None or not str(row["blocked_until"] or "").strip():
                return 0
            blocked_until = datetime.strptime(str(row["blocked_until"]), "%Y-%m-%d %H:%M:%S")
        remaining = int((blocked_until - now).total_seconds())
        if remaining <= 0:
            self.clear_login_failures(identity_key)
            return 0
        return remaining

    def register_login_failure(self, identity_key: str, now: datetime | None = None) -> int:
        """Record a failed login and return the remaining cooldown in seconds, if any."""
        now = now or datetime.now()
        normalized_key = identity_key.strip().lower()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT failures, blocked_until FROM login_throttle WHERE identity_key = ?",
                (normalized_key,),
            ).fetchone()
            failures = int(row["failures"]) if row is not None else 0
            blocked_until_text = str(row["blocked_until"] or "") if row is not None else ""
            blocked_until = datetime.strptime(blocked_until_text, "%Y-%m-%d %H:%M:%S") if blocked_until_text else None
            if blocked_until is not None and blocked_until > now:
                return int((blocked_until - now).total_seconds())
            if blocked_until is not None and blocked_until <= now:
                failures = 0
                blocked_until = None
            failures += 1
            blocked_until_text = ""
            if failures >= self.LOGIN_MAX_FAILURES:
                blocked_until = now + timedelta(minutes=self.LOGIN_BLOCK_MINUTES)
                blocked_until_text = blocked_until.strftime("%Y-%m-%d %H:%M:%S")
            connection.execute(
                """
                INSERT INTO login_throttle(identity_key, failures, blocked_until)
                VALUES(?, ?, ?)
                ON CONFLICT(identity_key) DO UPDATE SET
                    failures = excluded.failures,
                    blocked_until = excluded.blocked_until
                """,
                (normalized_key, failures, blocked_until_text),
            )
            if blocked_until is not None:
                return int((blocked_until - now).total_seconds())
            return 0

    def clear_login_failures(self, identity_key: str) -> None:
        """Forget failed-login history for one identity after a successful sign-in."""
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM login_throttle WHERE identity_key = ?",
                (identity_key.strip().lower(),),
            )

    def _normalize_role(self, role: str) -> str:
        """Validate and normalize a user role."""
        normalized = self._canonicalize_role(role)
        if normalized not in self.VALID_ROLES:
            raise ValueError(f"Role must be one of: {', '.join(self.VALID_ROLES)}")
        return normalized

    def _normalize_user_scope(self, role: str, class_name: str, subject_name: str) -> tuple[str, str]:
        """Validate stored class/subject scope fields for restricted accounts."""
        normalized_class = class_name.strip()
        normalized_subject = subject_name.strip()
        if role == "teacher":
            if not normalized_class:
                raise ValueError("Teacher accounts must be assigned to a class.")
            return normalized_class, normalized_subject
        return "", ""

    def _canonicalize_role(self, role: str) -> str:
        """Map legacy role names onto the current permission model."""
        normalized = role.strip().lower()
        if normalized == "admin":
            return self.ROOT_ROLE
        return normalized

    def _validate_time_range(self, start_time: str, end_time: str) -> None:
        """Ensure timetable times are valid and ordered."""
        try:
            start_value = datetime.strptime(start_time, "%H:%M")
            end_value = datetime.strptime(end_time, "%H:%M")
        except ValueError as exc:
            raise ValueError("Times must use HH:MM format.") from exc
        if end_value <= start_value:
            raise ValueError("End time must be later than start time.")

    def _validate_new_password(self, password: str) -> None:
        """Apply a small baseline password policy to local accounts."""
        if not password:
            raise ValueError("Password is required.")
        if len(password) < self.PASSWORD_MIN_LENGTH:
            raise ValueError(f"Password must be at least {self.PASSWORD_MIN_LENGTH} characters long.")
        if not any(character.islower() for character in password):
            raise ValueError("Password must include at least one lowercase letter.")
        if not any(character.isupper() for character in password):
            raise ValueError("Password must include at least one uppercase letter.")
        if not any(character.isdigit() for character in password):
            raise ValueError("Password must include at least one number.")
        if not any(not character.isalnum() for character in password):
            raise ValueError("Password must include at least one symbol.")

    def generate_temporary_password(self, length: int = 12) -> str:
        """Create a readable temporary password for local account handoff."""
        safe_length = max(self.PASSWORD_MIN_LENGTH, length)
        lowercase = "abcdefghijkmnopqrstuvwxyz"
        uppercase = "ABCDEFGHJKLMNPQRSTUVWXYZ"
        digits = "23456789"
        symbols = "!@$%?"
        all_characters = lowercase + uppercase + digits + symbols
        password_chars = [
            secrets.choice(lowercase),
            secrets.choice(uppercase),
            secrets.choice(digits),
            secrets.choice(symbols),
        ]
        while len(password_chars) < safe_length:
            password_chars.append(secrets.choice(all_characters))
        secrets.SystemRandom().shuffle(password_chars)
        return "".join(password_chars)

    def generate_initial_student_password(self) -> str:
        """Create a temporary student password that is not derived from the student ID."""
        return self.generate_temporary_password(length=12)

    def get_admin_setup_file_path(self) -> Path:
        """Return the local bootstrap credentials note for the root account."""
        return self.db_path.parent / self.ADMIN_SETUP_FILE

    def _write_admin_setup_file(self, password_or_note: str) -> None:
        """Persist bootstrap admin access details for legacy cleanup compatibility only."""
        path = self.get_admin_setup_file_path()
        if password_or_note.startswith("Legacy admin password detected."):
            body = password_or_note
        else:
            body = (
                "FaceAttendancePro root account bootstrap\n"
                "Username: admin\n"
                f"Temporary password: {password_or_note}\n\n"
                "Sign in and change this password from the dashboard immediately.\n"
            )
        path.write_text(body, encoding="utf-8")
        harden_permissions(path.parent, is_dir=True)
        harden_permissions(path, is_dir=False)
        LOGGER.info("Bootstrap admin credentials note written to %s", path)

    def _is_legacy_password_hash(self, stored_hash: str) -> bool:
        """Recognize older plain SHA-256 password hashes so they can be remediated."""
        return len(stored_hash) == 64 and all(character in "0123456789abcdef" for character in stored_hash.lower())

    def _verify_password(self, password: str, stored_hash: str) -> bool:
        """Check a plain password against the current PBKDF2 storage format only."""
        if not stored_hash:
            return False
        if stored_hash.startswith(f"{self.DISABLED_PASSWORD_PREFIX}$"):
            return False
        if self._is_legacy_password_hash(stored_hash):
            return False
        try:
            prefix, iteration_text, salt_hex, digest_hex = stored_hash.split("$", 3)
        except ValueError:
            return False
        if prefix != self.PASSWORD_PREFIX:
            return False
        derived = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iteration_text),
        ).hex()
        return hmac.compare_digest(derived, digest_hex)

    def _upgrade_password_hash_if_needed(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        key_column: str,
        key_value: str,
        password: str,
        stored_hash: str,
    ) -> None:
        """Replace a legacy hash with the stronger PBKDF2 format after a successful login."""
        if not self._is_legacy_password_hash(stored_hash):
            return
        connection.execute(
            f"UPDATE {table_name} SET password_hash = ? WHERE {key_column} = ?",
            (self.hash_password(password), key_value),
        )

    def _disable_legacy_password_hashes(self, connection: sqlite3.Connection) -> None:
        """Invalidate legacy SHA-256 credentials so they must be reset before reuse."""
        user_rows = connection.execute("SELECT username, password_hash FROM users").fetchall()
        for row in user_rows:
            username = str(row["username"] or "")
            password_hash = str(row["password_hash"] or "")
            if not self._is_legacy_password_hash(password_hash):
                continue
            if username == self.ADMIN_USERNAME:
                connection.execute(
                    "UPDATE users SET password_hash = '', password_reset_required = 1 WHERE username = ?",
                    (username,),
                )
            else:
                connection.execute(
                    "UPDATE users SET password_hash = ?, password_reset_required = 1 WHERE username = ?",
                    (f"{self.DISABLED_PASSWORD_PREFIX}$legacy", username),
                )
            LOGGER.warning("Disabled insecure legacy password hash for user '%s'.", username)

        student_rows = connection.execute("SELECT student_id, password_hash FROM students").fetchall()
        for row in student_rows:
            student_id = str(row["student_id"] or "")
            password_hash = str(row["password_hash"] or "")
            if not self._is_legacy_password_hash(password_hash):
                continue
            connection.execute(
                "UPDATE students SET password_hash = ?, password_reset_required = 1 WHERE student_id = ?",
                (f"{self.DISABLED_PASSWORD_PREFIX}$legacy", student_id),
            )
            LOGGER.warning("Disabled insecure legacy password hash for student '%s'.", student_id)

    def _upsert_student_in_connection(
        self,
        connection: sqlite3.Connection,
        *,
        label_id: int,
        student_id: str,
        name: str,
        class_name: str,
        password: str | None,
        section_name: str,
        batch_name: str,
        academic_year: str,
        semester: str,
        guardian_name: str,
        status: str,
        timestamp: str,
        existing_row: sqlite3.Row | None = None,
    ) -> tuple[int, str]:
        """Insert or update one student using an existing SQLite transaction."""
        existing = existing_row
        if existing is None:
            existing = connection.execute(
                "SELECT label_id, created_at, password_hash FROM students WHERE student_id = ?",
                (student_id,),
            ).fetchone()

        if existing:
            resolved_label_id = int(existing["label_id"])
            connection.execute(
                """
                UPDATE students
                SET name = ?, class_name = ?, section_name = ?, batch_name = ?, academic_year = ?, semester = ?, guardian_name = ?, status = ?, updated_at = ?, password_hash = ?
                WHERE student_id = ?
                """,
                (
                    name,
                    class_name,
                    section_name,
                    batch_name,
                    academic_year,
                    semester,
                    guardian_name.strip(),
                    status,
                    timestamp,
                    self.hash_password(password) if password else existing["password_hash"],
                    student_id,
                ),
            )
            return resolved_label_id, str(existing["created_at"])

        resolved_label_id = label_id
        connection.execute(
            """
            INSERT OR REPLACE INTO students(
                label_id, student_id, name, class_name, section_name, batch_name, academic_year, semester,
                guardian_name, status, password_hash, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                resolved_label_id,
                student_id,
                name,
                class_name,
                section_name,
                batch_name,
                academic_year,
                semester,
                guardian_name.strip(),
                status,
                self.hash_password(password or self.generate_initial_student_password()),
                timestamp,
                timestamp,
            ),
        )
        return resolved_label_id, timestamp

    def _deduplicate_attendance_records(self, connection: sqlite3.Connection) -> None:
        """Keep the oldest attendance row when older databases contain duplicate daily marks."""
        duplicate_rows = connection.execute(
            """
            SELECT student_id, class_name, subject_name, date, MIN(id) AS keep_id
            FROM attendance
            GROUP BY student_id, class_name, subject_name, date
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        for row in duplicate_rows:
            connection.execute(
                """
                DELETE FROM attendance
                WHERE student_id = ? AND class_name = ? AND subject_name = ? AND date = ? AND id <> ?
                """,
                (
                    str(row["student_id"]),
                    str(row["class_name"]),
                    str(row["subject_name"]),
                    str(row["date"]),
                    int(row["keep_id"]),
                ),
            )

    def _mask_sensitive_table_row(self, table_name: str, row: dict[str, object]) -> dict[str, str]:
        """Hide sensitive values when tables are previewed inside the dashboard."""
        sensitive_columns = self.SENSITIVE_TABLE_COLUMNS.get(table_name, set())
        masked: dict[str, str] = {}
        for key, value in row.items():
            if key in sensitive_columns:
                text = str(value or "")
                masked[key] = "[hidden]" if text else ""
            else:
                masked[key] = "" if value is None else str(value)
        return masked

    def _student_from_row(self, row: sqlite3.Row | None) -> Student | None:
        """Convert a SQLite row into the Student dataclass used by the app."""
        if row is None:
            return None
        return Student(
            label_id=int(row["label_id"]),
            student_id=str(row["student_id"]),
            name=str(row["name"]),
            class_name=str(row["class_name"]),
            section_name=str(row["section_name"] or ""),
            batch_name=str(row["batch_name"] or ""),
            academic_year=str(row["academic_year"] or ""),
            semester=str(row["semester"] or ""),
            guardian_name=str(row["guardian_name"] or ""),
            status=str(row["status"] or "Active"),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def _user_from_row(self, row: sqlite3.Row | None) -> UserAccount | None:
        """Convert a SQLite row into the UserAccount dataclass."""
        if row is None:
            return None
        return UserAccount(
            id=int(row["id"]),
            username=str(row["username"]),
            password_hash=str(row["password_hash"]),
            role=self._canonicalize_role(str(row["role"])),
            created_at=str(row["created_at"]),
            class_name=str(row["class_name"] or ""),
            subject_name=str(row["subject_name"] or ""),
        )
