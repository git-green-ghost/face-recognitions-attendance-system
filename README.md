# Face Attendance & Management System

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg?logo=python&logoColor=white)](https://www.python.org/)
[![OpenCV](https://img.shields.io/badge/OpenCV-4.8%2B-5C3EE8.svg?logo=opencv&logoColor=white)](https://opencv.org/)
[![SQLite](https://img.shields.io/badge/Database-SQLite3-003B57.svg?logo=sqlite&logoColor=white)](https://www.sqlite.org/)
[![UI](https://img.shields.io/badge/GUI-Tkinter-brightgreen.svg)]()
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A production-ready, desktop facial recognition attendance management system built with **Python**, **OpenCV**, **Tkinter**, and **SQLite3**. Designed for schools, universities, and organizations to automate student attendance tracking with anti-spoofing liveness checks, illumination-invariant recognition, and comprehensive export reporting.

---

## Key Highlights

- **High-Accuracy v3 Recognition Pipeline**: Combines **CLAHE** (Contrast Limited Adaptive Histogram Equalization) with **Affine Eye Alignment**, unit-normalized **Local Binary Patterns (LBP)**, and **Sobel Gradient Orientations** to maintain high recognition accuracy across harsh lighting shifts and head tilt angles.
- **Fast 25-Sample Guided Registration**: Sequential 5-direction face capture (`front`, `left`, `right`, `up`, `down`) with real-time on-screen guidance and non-blocking fallback that finishes registration in **under 3.5 seconds**.
- **Liveness & Anti-Spoofing Detection**: Multi-frame temporal motion analysis and continuous stability verification prevents photo/screen spoofing attacks.
- **Role-Based Access Control (RBAC)**: Secure multi-tier authentication (`root`, `admin`, `teacher`, `viewer`) with salted password hashing and audit event logging.
- **Native Reporting**: Generates native Excel (`.xlsx`) and paginated PDF (`.pdf`) attendance reports directly using Python standard libraries without external heavy dependencies.
- **Roster Management**: Bulk student CSV import with automated secure password generation, photo snapshots, and per-class model training.
- **Atomic Database Operations**: SQLite transaction isolation with live hot backups and integrity-checked restore routines.

---

## System Architecture

```
                          ┌──────────────────────────┐
                          │   Live Web Camera Feed   │
                          └─────────────┬────────────┘
                                        │
                                        ▼
                          ┌──────────────────────────┐
                          │  Haar Cascade Detector   │
                          │ (Scale 1.1, minNeigh=4)  │
                          └─────────────┬────────────┘
                                        │
                                        ▼
                          ┌──────────────────────────┐
                          │  Affine Eye Deskewing &  │
                          │   CLAHE Preprocessing    │
                          └─────────────┬────────────┘
                                        │
                                        ▼
                          ┌──────────────────────────┐
                          │ Feature Pipeline v3      │
                          │ • 50% LBP Micro-Texture  │
                          │ • 30% Sobel Gradients    │
                          │ • 20% Spatial Layout     │
                          └─────────────┬────────────┘
                                        │
                                        ▼
    ┌───────────────────────────────────┴───────────────────────────────────┐
    │                                                                       │
    ▼                                                                       ▼
┌──────────────────────────┐                            ┌──────────────────────────┐
│ Face Registration Mode   │                            │ Live Attendance Mode     │
│ • 25 Balanced Views      │                            │ • Multi-Sample Top-K     │
│ • Visual Progress Gauge  │                            │ • Dynamic Thresholding   │
│ • ~3.5s Guided Capture   │                            │ • Liveness Verification  │
└───────────┬──────────────┘                            └───────────┬──────────────┘
            │                                                       │
            ▼                                                       ▼
┌──────────────────────────┐                            ┌──────────────────────────┐
│  Per-Class .npz Models   │                            │ SQLite Attendance Ledger │
└──────────────────────────┘                            └──────────────────────────┘
```

---

## Directory Structure

```text
FaceAttendance/
├── backend/
│   ├── camera.py             # Video capture abstraction and camera enumeration
│   ├── logging_utils.py      # Structured audit and operational logging
│   ├── recognition.py        # CLAHE, affine deskewing, v3 descriptors, recognizer
│   └── reporting.py          # Native XLSX and paginated PDF report generators
├── database/
│   ├── models.py             # Dataclass entities (Student, Attendance, User, Class)
│   └── repository.py         # SQLite schema, migrations, queries, and hot backup
├── frontend/
│   ├── admin.py              # User management, system configuration, audit viewer
│   ├── app.py                # Main desktop dashboard and registration workflow
│   ├── attendance.py         # Live attendance camera session and tracking mixin
│   ├── auth.py               # Authentication dialogs and session manager
│   ├── dialogs.py            # Modals, confirmation prompts, and filters
│   ├── exports.py            # Attendance reporting export dialogs
│   └── students.py           # Student enrollment, face registration, class rosters
├── data/                     # Local data root (ignored by git, kept via .gitkeep)
│   ├── backups/              # SQLite database backups
│   ├── dataset/              # Captured training face crops
│   ├── exports/              # Exported XLSX and PDF reports
│   ├── photos/               # Profile front photos
│   └── trainer/              # Compiled per-class model weights (.npz)
├── app.py                    # Application launch entry point
├── config.py                 # File system paths, constants, and permissions
├── requirements.txt          # Python dependencies
├── run.bat                   # Windows one-click startup batch script
├── LICENSE                   # MIT License
└── README.md                 # Project documentation
```

---

## Prerequisites

- **Python**: Version `3.11` or newer (compatible with Python 3.11, 3.12, 3.13)
- **Webcam**: Any built-in or USB webcam supported by your operating system

---

## Installation & Setup

### 1. Clone the Repository
```bash
git clone https://github.com/<your-username>/FaceAttendance.git
cd FaceAttendance
```

### 2. Create and Activate a Virtual Environment (Recommended)

**On Windows:**
```powershell
python -m venv .venv
.venv\Scripts\activate
```

**On macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

---

## Running the Application

### Option A: Via Python Command
```bash
python app.py
```

### Option B: Windows One-Click Launcher
Double-click `run.bat` in the project root directory.

---

## Default Roles & Permissions

When the application is launched for the first time, it automatically creates an empty SQLite database with the following built-in accounts:

| Username   | Default Password | Role       | Permissions                                       |
| :--------- | :--------------- | :--------- | :------------------------------------------------ |
| `root`     | `root`           | `root`     | Full system administration, DB backup/restore     |
| `admin`    | `admin`          | `operator` | Student registration, attendance, training, audit |
| `teacher`  | `teacher`        | `teacher`  | Student management, attendance session tracking   |
| `viewer`   | `viewer`         | `viewer`   | Read-only dashboard and attendance report viewing |

> **Security Note**: Change default passwords immediately upon deployment via the **Admin -> User Management** tab.

---

## Typical Workflow

1. **Sign In**: Log in using your designated account credentials.
2. **Setup Class & Subjects**: Create the target Class, Section, and Subjects in the system.
3. **Register Students**:
   - Click **Register Student**, fill in student info, and start face capture.
   - Follow the on-screen prompts: look straight, then slightly left, right, up, and down. Capture completes in ~3 seconds.
4. **Train Model**:
   - Navigate to the class dashboard and click **Train Model**.
   - The system compiles the face dataset into an optimized `.npz` vector database.
5. **Take Attendance**:
   - Select Class and Subject, then click **Take Attendance**.
   - The live camera window tracks students, verifies liveness, and logs timestamps into SQLite in real-time.
6. **Export Reports**:
   - Export filtered attendance logs to formatted `.xlsx` spreadsheets or multi-page `.pdf` summaries.

---

## License

This project is licensed under the [MIT License](LICENSE).
