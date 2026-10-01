from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import stat


@dataclass(frozen=True)
class AppPaths:
    base_dir: Path
    runtime_dir: Path
    data_dir: Path
    dataset_dir: Path
    model_dir: Path
    db_path: Path
    exports_dir: Path
    photos_dir: Path
    students_dir: Path
    reports_dir: Path
    backups_dir: Path
    logs_dir: Path

    @classmethod
    def from_base_dir(cls, base_dir: Path) -> "AppPaths":
        runtime_dir = resolve_runtime_dir(base_dir)
        data_dir = runtime_dir / "data"
        return cls(
            base_dir=base_dir,
            runtime_dir=runtime_dir,
            data_dir=data_dir,
            dataset_dir=data_dir / "dataset",
            model_dir=data_dir / "trainer",
            db_path=data_dir / "face_attendance.db",
            exports_dir=data_dir / "exports",
            photos_dir=data_dir / "photos",
            students_dir=data_dir / "students",
            reports_dir=data_dir / "exports",
            backups_dir=data_dir / "backups",
            logs_dir=runtime_dir / "logs",
        )

    def ensure_runtime_dirs(self) -> None:
        for path in (
            self.runtime_dir,
            self.data_dir,
            self.dataset_dir,
            self.model_dir,
            self.exports_dir,
            self.photos_dir,
            self.students_dir,
            self.reports_dir,
            self.backups_dir,
            self.logs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
            harden_permissions(path, is_dir=True)


def resolve_runtime_dir(base_dir: Path) -> Path:
    override = os.environ.get("FACE_ATTENDANCE_RUNTIME_DIR", "").strip()
    if override:
        requested = Path(override).expanduser().resolve()
        return requested if runtime_dir_is_writable(requested) else (base_dir / ".runtime")

    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        requested = Path(local_app_data).expanduser().resolve() / "FaceAttendancePro"
        if runtime_dir_is_writable(requested):
            return requested

    local_runtime = base_dir / ".runtime"
    if runtime_dir_is_writable(local_runtime):
        return local_runtime

    home_dir = Path.home().resolve() / ".faceattendancepro"
    return home_dir


def runtime_dir_is_writable(path: Path) -> bool:
    """Check whether the app can create and write inside a runtime directory."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def harden_permissions(path: Path, *, is_dir: bool) -> None:
    """Best-effort permission tightening for local runtime artifacts."""
    if os.name == "nt":
        # Windows ACL handling is different; chmod here can make files unusable.
        return
    try:
        path.chmod(stat.S_IRWXU if is_dir else stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


APP_NAME = "Face Recognition Attendance System"
APP_SLUG = "face-attendance-pro"
SUPPORTED_PYTHON = "3.11+"
BASE_DIR = Path(__file__).resolve().parent
PATHS = AppPaths.from_base_dir(BASE_DIR)
ALLOW_IN_APP_ROOT_BOOTSTRAP = os.environ.get("FACE_ATTENDANCE_ENABLE_ROOT_BOOTSTRAP", "").strip() == "1"
