from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


class PasswordResetRequiredError(PermissionError):
    """Raised when an account was disabled until its password is reset securely."""

    def __init__(self, identity: str, kind: str) -> None:
        self.identity = identity
        self.kind = kind
        super().__init__(f"{kind.capitalize()} account '{identity}' requires a password reset.")


@dataclass
class Student:
    """Single student record stored once in the database for a class."""
    label_id: int
    student_id: str
    name: str
    class_name: str
    section_name: str
    batch_name: str
    academic_year: str
    semester: str
    guardian_name: str
    status: str
    created_at: str
    updated_at: str


@dataclass
class StudentLogin:
    """Minimal student login identity used for student viewer sign-in."""
    student_id: str
    name: str
    class_name: str
    section_name: str
    batch_name: str
    academic_year: str
    semester: str


@dataclass
class UserAccount:
    """Simple local user account used for app login and role-based actions."""
    id: int
    username: str
    password_hash: str
    role: str
    created_at: str
    class_name: str = ""
    subject_name: str = ""


@dataclass
class LoginThrottleState:
    """Track recent failed login attempts for one identity."""
    failures: int = 0
    blocked_until: datetime | None = None
