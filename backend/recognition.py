from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from config import harden_permissions

CASCADE_DIR = Path(cv2.data.haarcascades)
EYE_CASCADE = cv2.CascadeClassifier(str(CASCADE_DIR / "haarcascade_eye.xml"))
SMILE_CASCADE = cv2.CascadeClassifier(str(CASCADE_DIR / "haarcascade_smile.xml"))
FACE_IMAGE_SIZE = (128, 128)
FEATURE_IMAGE_SIZE = (64, 64)
LEGACY_FEATURE_VERSION = 1
CURRENT_FEATURE_VERSION = 3


def align_face_crop(face_crop: np.ndarray) -> np.ndarray:
    """Rotate face crop to level eyes horizontally if both eyes are clearly detected."""
    if face_crop is None or face_crop.size == 0:
        return face_crop

    height, width = face_crop.shape[:2]
    if width < 30 or height < 30:
        return face_crop

    gray = cv2.cvtColor(face_crop, cv2.COLOR_BGR2GRAY) if len(face_crop.shape) == 3 else face_crop
    eyes = EYE_CASCADE.detectMultiScale(
        gray,
        scaleFactor=1.1,
        minNeighbors=3,
        minSize=(max(10, int(width * 0.12)), max(10, int(height * 0.12))),
    )
    if len(eyes) >= 2:
        sorted_eyes = sorted(eyes, key=lambda b: b[0])[:2]
        left_eye, right_eye = sorted_eyes[0], sorted_eyes[1]
        left_center = (left_eye[0] + left_eye[2] / 2.0, left_eye[1] + left_eye[3] / 2.0)
        right_center = (right_eye[0] + right_eye[2] / 2.0, right_eye[1] + right_eye[3] / 2.0)

        dy = right_center[1] - left_center[1]
        dx = right_center[0] - left_center[0]
        angle = math.degrees(math.atan2(dy, dx))

        if -35.0 <= angle <= 35.0 and abs(angle) > 1.5:
            center = ((left_center[0] + right_center[0]) / 2.0, (left_center[1] + right_center[1]) / 2.0)
            rot_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
            aligned = cv2.warpAffine(
                face_crop,
                rot_matrix,
                (width, height),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_REPLICATE,
            )
            return aligned

    return face_crop


def preprocess_face(face_image: np.ndarray, size: tuple[int, int] = FACE_IMAGE_SIZE) -> np.ndarray:
    """Normalize a detected face crop using CLAHE and smoothing for illumination invariance."""
    if face_image is None or face_image.size == 0:
        raise ValueError("Face image is empty.")

    if len(face_image.shape) == 3:
        face_gray = cv2.cvtColor(face_image, cv2.COLOR_BGR2GRAY)
    else:
        face_gray = face_image

    resized = cv2.resize(face_gray, size)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    equalized = clahe.apply(resized)
    smoothed = cv2.GaussianBlur(equalized, (3, 3), 0)
    return smoothed


def detect_facial_geometry(face_image: np.ndarray) -> np.ndarray:
    """Extract a small geometry signature from eye and mouth positions."""
    processed = preprocess_face(face_image)
    geometry_frame = cv2.resize(processed, (96, 96))
    height, width = geometry_frame.shape[:2]
    eyes = EYE_CASCADE.detectMultiScale(
        geometry_frame,
        scaleFactor=1.05,
        minNeighbors=3,
        minSize=(12, 12),
    )
    smiles = SMILE_CASCADE.detectMultiScale(
        geometry_frame,
        scaleFactor=1.2,
        minNeighbors=12,
        minSize=(20, 14),
    )

    eye_boxes = sorted(eyes, key=lambda box: box[0])[:2]
    smile_box = max(smiles, key=lambda box: box[2] * box[3]) if len(smiles) else None

    if len(eye_boxes) >= 2:
        left_eye, right_eye = eye_boxes[0], eye_boxes[1]
        left_center = (left_eye[0] + left_eye[2] / 2.0, left_eye[1] + left_eye[3] / 2.0)
        right_center = (right_eye[0] + right_eye[2] / 2.0, right_eye[1] + right_eye[3] / 2.0)
        eye_mid_x = (left_center[0] + right_center[0]) / 2.0 / width
        eye_mid_y = (left_center[1] + right_center[1]) / 2.0 / height
        eye_distance = abs(right_center[0] - left_center[0]) / width
        raw_eye_angle = (right_center[1] - left_center[1]) / max(1.0, abs(right_center[0] - left_center[0]))
        eye_angle = float(np.clip(0.5 + (raw_eye_angle * 0.5), 0.0, 1.0))
    else:
        eye_mid_x = 0.0
        eye_mid_y = 0.0
        eye_distance = 0.0
        eye_angle = 0.0

    if smile_box is not None:
        mouth_center_x = (smile_box[0] + smile_box[2] / 2.0) / width
        mouth_center_y = (smile_box[1] + smile_box[3] / 2.0) / height
        mouth_width = smile_box[2] / width
        mouth_detected = 1.0
    else:
        mouth_center_x = 0.0
        mouth_center_y = 0.0
        mouth_width = 0.0
        mouth_detected = 0.0

    eye_to_mouth = float(np.clip(mouth_center_y - eye_mid_y, 0.0, 1.0)) if mouth_detected and eye_distance > 0 else 0.0

    return np.array(
        [
            eye_mid_x,
            eye_mid_y,
            eye_distance,
            eye_angle,
            mouth_center_x,
            mouth_center_y,
            mouth_width,
            eye_to_mouth,
            1.0 if len(eye_boxes) >= 2 else 0.0,
            mouth_detected,
        ],
        dtype=np.float32,
    )


def face_to_feature_vector(
    face_image: np.ndarray,
    geometry: np.ndarray | None = None,
    size: tuple[int, int] = FEATURE_IMAGE_SIZE,
    feature_version: int = CURRENT_FEATURE_VERSION,
) -> np.ndarray:
    """Flatten a processed face image into a numeric vector for matching."""
    aligned = align_face_crop(face_image)
    processed = preprocess_face(aligned, size=size)
    if int(feature_version) <= LEGACY_FEATURE_VERSION:
        appearance = processed.astype(np.float32).reshape(-1) / 255.0
    else:
        appearance = extract_appearance_descriptor(processed, feature_version=feature_version)
    geometry = detect_facial_geometry(processed) if geometry is None else geometry.astype(np.float32)
    return np.concatenate([appearance, geometry], dtype=np.float32)


def extract_appearance_descriptor(
    face_image: np.ndarray,
    feature_version: int = CURRENT_FEATURE_VERSION,
) -> np.ndarray:
    """Build a compact appearance descriptor that is more stable than raw pixels alone."""
    processed = preprocess_face(face_image, size=FEATURE_IMAGE_SIZE)
    lbp_features = compute_lbp_histogram_grid(processed)
    gradient_features = compute_gradient_histogram_grid(processed)

    if int(feature_version) <= 2:
        normalized = processed.astype(np.float32) / 255.0
        raw_features = normalized.reshape(-1)
        return np.concatenate([raw_features, lbp_features, gradient_features], dtype=np.float32)

    # Feature version 3: Balanced, illumination-normalized multi-feature descriptor
    lbp_norm = lbp_features / (np.linalg.norm(lbp_features) + 1e-8)
    grad_norm = gradient_features / (np.linalg.norm(gradient_features) + 1e-8)
    spatial_32 = cv2.resize(processed, (32, 32)).astype(np.float32) / 255.0
    spatial_flat = spatial_32.reshape(-1)
    spatial_norm = spatial_flat / (np.linalg.norm(spatial_flat) + 1e-8)

    weighted = np.concatenate(
        [
            lbp_norm * 0.50,
            grad_norm * 0.30,
            spatial_norm * 0.20,
        ],
        dtype=np.float32,
    )
    return (weighted / (np.linalg.norm(weighted) + 1e-8)).astype(np.float32)


def compute_lbp_histogram_grid(
    face_image: np.ndarray,
    grid_shape: tuple[int, int] = (8, 8),
    bins: int = 16,
) -> np.ndarray:
    """Capture local texture patterns using a lightweight LBP-style histogram grid."""
    frame = preprocess_face(face_image, size=FEATURE_IMAGE_SIZE)
    center = frame[1:-1, 1:-1]
    codes = np.zeros_like(center, dtype=np.uint8)
    codes |= ((frame[:-2, :-2] >= center) << 7).astype(np.uint8)
    codes |= ((frame[:-2, 1:-1] >= center) << 6).astype(np.uint8)
    codes |= ((frame[:-2, 2:] >= center) << 5).astype(np.uint8)
    codes |= ((frame[1:-1, 2:] >= center) << 4).astype(np.uint8)
    codes |= ((frame[2:, 2:] >= center) << 3).astype(np.uint8)
    codes |= ((frame[2:, 1:-1] >= center) << 2).astype(np.uint8)
    codes |= ((frame[2:, :-2] >= center) << 1).astype(np.uint8)
    codes |= ((frame[1:-1, :-2] >= center) << 0).astype(np.uint8)

    height, width = codes.shape
    grid_rows, grid_cols = grid_shape
    row_bounds = np.linspace(0, height, grid_rows + 1, dtype=int)
    col_bounds = np.linspace(0, width, grid_cols + 1, dtype=int)
    histograms: list[np.ndarray] = []
    for grid_row in range(grid_rows):
        r_start, r_end = int(row_bounds[grid_row]), int(row_bounds[grid_row + 1])
        for grid_col in range(grid_cols):
            c_start, c_end = int(col_bounds[grid_col]), int(col_bounds[grid_col + 1])
            cell = codes[r_start:r_end, c_start:c_end]
            histogram, _ = np.histogram(cell, bins=bins, range=(0, 256))
            histogram = histogram.astype(np.float32)
            histogram /= histogram.sum() + 1e-6
            histograms.append(histogram)
    return np.concatenate(histograms, dtype=np.float32)


def compute_gradient_histogram_grid(
    face_image: np.ndarray,
    cell_size: int = 16,
    bins: int = 8,
) -> np.ndarray:
    """Capture edge orientation energy so identities survive mild lighting changes."""
    frame = preprocess_face(face_image, size=FEATURE_IMAGE_SIZE)
    sobel_x = cv2.Sobel(frame, cv2.CV_32F, 1, 0, ksize=3)
    sobel_y = cv2.Sobel(frame, cv2.CV_32F, 0, 1, ksize=3)
    magnitude, angle = cv2.cartToPolar(sobel_x, sobel_y, angleInDegrees=False)

    height, width = frame.shape
    histograms: list[np.ndarray] = []
    for top in range(0, height, cell_size):
        for left in range(0, width, cell_size):
            cell_magnitude = magnitude[top : top + cell_size, left : left + cell_size].reshape(-1)
            cell_angle = angle[top : top + cell_size, left : left + cell_size].reshape(-1)
            bucket_indexes = np.floor((cell_angle % (2.0 * np.pi)) / (2.0 * np.pi) * bins).astype(int)
            bucket_indexes = np.clip(bucket_indexes, 0, bins - 1)
            histogram = np.bincount(bucket_indexes, weights=cell_magnitude, minlength=bins).astype(np.float32)
            histogram /= np.linalg.norm(histogram) + 1e-6
            histograms.append(histogram)
    return np.concatenate(histograms, dtype=np.float32)


def infer_feature_image_size(
    feature_width: int,
    geometry_width: int = 10,
    default_size: tuple[int, int] = FACE_IMAGE_SIZE,
) -> tuple[int, int]:
    """Infer the square face size that produced a stored feature width."""
    appearance_width = int(feature_width) - int(geometry_width)
    if appearance_width <= 0:
        return default_size
    side_length = math.isqrt(appearance_width)
    if side_length * side_length != appearance_width:
        return default_size
    return (side_length, side_length)


def compute_face_structure_signature(face_images: list[np.ndarray]) -> str:
    """Build a stable per-student face-structure signature from captured samples."""
    if not face_images:
        return ""
    feature_rows = np.vstack([face_to_feature_vector(face) for face in face_images]).astype(np.float32)
    averaged = np.mean(feature_rows, axis=0)
    rounded = np.round(averaged, 4).astype(np.float32)
    return hashlib.sha256(rounded.tobytes()).hexdigest()[:16].upper()


def slugify_name(value: str) -> str:
    """Turn a class name into a safe file name for per-class model storage."""
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", value.strip())
    return normalized.strip("_").lower() or "general"


@dataclass
class LivenessState:
    """Track recognition stability and small natural movement across frames."""
    frames_seen: int = 0
    total_movement: float = 0.0
    last_center: tuple[float, float] | None = None


class LivenessTracker:
    """Basic anti-spoofing helper that requires repeated recognition plus motion."""

    def __init__(self, min_frames: int = 5, min_movement: float = 6.0) -> None:
        self.min_frames = min_frames
        self.min_movement = min_movement
        self._states: dict[str, LivenessState] = {}

    def update(self, identity_key: str, box: tuple[int, int, int, int]) -> tuple[bool, LivenessState]:
        """Update liveness state for one identity and return whether it passed."""
        state = self._states.setdefault(identity_key, LivenessState())
        center = (box[0] + box[2] / 2.0, box[1] + box[3] / 2.0)
        state.frames_seen += 1

        if state.last_center is not None:
            dx = center[0] - state.last_center[0]
            dy = center[1] - state.last_center[1]
            state.total_movement += float((dx * dx + dy * dy) ** 0.5)

        state.last_center = center
        passed = state.frames_seen >= self.min_frames and state.total_movement >= self.min_movement
        return passed, state

    def reset_identity(self, identity_key: str) -> None:
        """Forget liveness tracking for one identity."""
        self._states.pop(identity_key, None)


class SimpleFaceRecognizer:
    """A lightweight cosine-similarity recognizer that does not depend on cv2.face."""

    def __init__(self) -> None:
        self.features: np.ndarray | None = None
        self.labels: np.ndarray | None = None
        self.normalized_features: np.ndarray | None = None
        self.appearance_size: tuple[int, int] = FEATURE_IMAGE_SIZE
        self.feature_version: int = CURRENT_FEATURE_VERSION
        self.unique_labels: np.ndarray | None = None
        self.label_centroids: dict[int, np.ndarray] = {}
        self.label_thresholds: dict[int, float] = {}
        self.geometry_features: np.ndarray | None = None
        self.label_geometry_centroids: dict[int, np.ndarray] = {}
        self.label_feature_rows: dict[int, np.ndarray] = {}
        self.label_sample_counts: dict[int, int] = {}

    def train(self, faces: list[np.ndarray], labels: list[int] | np.ndarray) -> None:
        """Build the in-memory feature database from captured training samples."""
        if not faces:
            raise ValueError("No faces were provided for training.")

        self.appearance_size = FEATURE_IMAGE_SIZE
        self.feature_version = CURRENT_FEATURE_VERSION
        aligned_faces = [align_face_crop(face) for face in faces]
        feature_rows = [
            face_to_feature_vector(face, size=self.appearance_size, feature_version=self.feature_version)
            for face in aligned_faces
        ]
        self.features = np.vstack(feature_rows).astype(np.float32)
        self.labels = np.array(labels, dtype=np.int32)
        self.geometry_features = np.vstack([detect_facial_geometry(face) for face in aligned_faces]).astype(np.float32)
        self._rebuild_label_profiles()

    def predict(self, face_image: np.ndarray) -> tuple[int, float]:
        """Compare one detected face against trained samples and return the best match."""
        label, confidence, _margin = self.predict_with_details(face_image)
        return label, confidence

    def predict_with_details(self, face_image: np.ndarray) -> tuple[int, float, float]:
        """Return the best label, its score, and the gap from the second-best label."""
        evaluation = self.evaluate_prediction(face_image)
        return (
            int(evaluation["label_id"]),
            float(evaluation["confidence"]),
            float(evaluation["margin"]),
        )

    def evaluate_prediction(self, face_image: np.ndarray) -> dict[str, object]:
        """Return detailed recognition metrics so callers can reject unknown faces safely."""
        if self.features is None or self.labels is None or len(self.features) == 0:
            raise ValueError("Recognizer has not been trained.")

        aligned_probe = align_face_crop(face_image)
        probe_geometry = detect_facial_geometry(aligned_probe)
        processed_face = preprocess_face(aligned_probe, size=self.appearance_size)
        probe = face_to_feature_vector(
            processed_face,
            geometry=probe_geometry,
            size=self.appearance_size,
            feature_version=self.feature_version,
        )
        normalized_features = self._get_normalized_features()
        probe_norm = np.linalg.norm(probe) + 1e-8
        normalized_probe = probe / probe_norm
        similarities = np.dot(normalized_features, normalized_probe)

        label_scores: list[dict[str, object]] = []
        for label in self.unique_labels if self.unique_labels is not None else np.unique(self.labels):
            label_similarities = similarities[self.labels == label]
            top_matches = np.sort(label_similarities)[-3:]
            centroid = self.label_centroids.get(int(label))
            centroid_score = float(np.dot(centroid, normalized_probe)) if centroid is not None else float(np.mean(top_matches))
            sample_mean = float(np.mean(top_matches))
            label_rows = self.label_feature_rows.get(int(label))
            if label_rows is None:
                label_rows = normalized_features[self.labels == label]
            support_score = float(np.max(np.dot(label_rows, normalized_probe)))
            geometry_score = self._geometry_similarity(int(label), probe_geometry)
            use_geometry = geometry_score > 0.0
            weight_total = 0.90 + (0.10 if use_geometry else 0.0)
            combined_score = (
                (centroid_score * 0.30)
                + (sample_mean * 0.25)
                + (support_score * 0.35)
                + (geometry_score * 0.10 if use_geometry else 0.0)
            ) / weight_total
            label_scores.append(
                {
                    "label_id": int(label),
                    "confidence": float(combined_score),
                    "centroid_score": centroid_score,
                    "sample_mean": sample_mean,
                    "support_score": support_score,
                    "geometry_score": geometry_score,
                }
            )

        label_scores.sort(key=lambda item: float(item["confidence"]), reverse=True)
        best = label_scores[0]
        second_score = float(label_scores[1]["confidence"]) if len(label_scores) > 1 else 0.0
        return {
            "label_id": int(best["label_id"]),
            "confidence": float(best["confidence"]),
            "margin": float(best["confidence"]) - second_score,
            "support_score": float(best["support_score"]),
            "geometry_score": float(best["geometry_score"]),
            "probe_geometry": probe_geometry,
            "label_count": len(label_scores),
            "sample_count": self.label_sample_counts.get(int(best["label_id"]), 0),
        }

    def get_acceptance_threshold(self, label_id: int) -> float:
        """Return the minimum score this label should reach before being accepted."""
        default_thresh = 0.88 if self.feature_version >= 3 else 0.93
        return float(self.label_thresholds.get(int(label_id), default_thresh))

    def get_label_count(self) -> int:
        """Return how many distinct identities are present in the trained model."""
        return int(len(self.unique_labels) if self.unique_labels is not None else 0)

    def needs_model_upgrade(self) -> bool:
        """Return whether this recognizer was trained with an older feature pipeline."""
        return int(self.feature_version) != CURRENT_FEATURE_VERSION

    def _get_normalized_features(self) -> np.ndarray:
        """Normalize stored feature rows for centroid and support comparisons."""
        if self.normalized_features is not None:
            return self.normalized_features
        if self.features is None:
            raise ValueError("Recognizer has not been trained.")
        feature_norms = np.linalg.norm(self.features, axis=1, keepdims=True) + 1e-8
        self.normalized_features = self.features / feature_norms
        return self.normalized_features

    def _rebuild_label_profiles(self) -> None:
        """Build one stable profile and acceptance threshold per student label."""
        if self.features is None or self.labels is None:
            self.normalized_features = None
            self.unique_labels = None
            self.label_centroids = {}
            self.label_thresholds = {}
            self.label_geometry_centroids = {}
            self.label_feature_rows = {}
            self.label_sample_counts = {}
            return

        normalized_features = self._get_normalized_features()
        self.unique_labels = np.unique(self.labels)
        self.label_centroids = {}
        self.label_thresholds = {}
        self.label_geometry_centroids = {}
        self.label_feature_rows = {}
        self.label_sample_counts = {}

        for label in self.unique_labels:
            label = int(label)
            rows = normalized_features[self.labels == label]
            self.label_feature_rows[label] = rows
            self.label_sample_counts[label] = int(len(rows))
            centroid = np.mean(rows, axis=0)
            centroid /= np.linalg.norm(centroid) + 1e-8
            self.label_centroids[label] = centroid.astype(np.float32)
            if self.geometry_features is not None:
                geometry_rows = self.geometry_features[self.labels == label]
                self.label_geometry_centroids[label] = np.mean(geometry_rows, axis=0).astype(np.float32)

            within_scores = np.dot(rows, centroid)
            if len(within_scores) > 1:
                threshold = float(np.percentile(within_scores, 10) - 0.035)
            else:
                threshold = float(within_scores[0] - 0.035)
            if self.feature_version >= 3:
                self.label_thresholds[label] = max(0.85, min(0.96, threshold))
            else:
                self.label_thresholds[label] = max(0.90, min(0.992, threshold))

    def _geometry_similarity(self, label_id: int, probe_geometry: np.ndarray) -> float:
        """Compare a live geometry signature against the stored label geometry profile."""
        centroid = self.label_geometry_centroids.get(label_id)
        if centroid is None:
            return 0.0
        if float(probe_geometry[8]) == 0.0 or float(centroid[8]) == 0.0:
            return 0.0
        distance = float(np.linalg.norm(probe_geometry - centroid))
        return max(0.0, 1.0 - (distance / 0.6))

    def save(self, path: Path) -> None:
        """Persist the trained recognizer for the active class."""
        if self.features is None or self.labels is None:
            raise ValueError("Recognizer has not been trained.")
        path.parent.mkdir(parents=True, exist_ok=True)
        harden_permissions(path.parent, is_dir=True)
        geometry_features = self.geometry_features if self.geometry_features is not None else np.zeros((len(self.features), 10), dtype=np.float32)
        np.savez_compressed(
            path,
            features=self.features,
            labels=self.labels,
            geometry_features=geometry_features,
            appearance_size=np.array(self.appearance_size, dtype=np.int32),
            feature_version=np.array([self.feature_version], dtype=np.int32),
        )
        harden_permissions(path, is_dir=False)

    @classmethod
    def load(cls, path: Path) -> "SimpleFaceRecognizer":
        """Load a previously trained model from disk."""
        with np.load(path, allow_pickle=False) as payload:
            recognizer = cls()
            recognizer.features = payload["features"].astype(np.float32)
            recognizer.labels = payload["labels"].astype(np.int32)
            if "feature_version" in payload:
                recognizer.feature_version = int(payload["feature_version"].reshape(-1)[0])
            else:
                recognizer.feature_version = LEGACY_FEATURE_VERSION
            if "appearance_size" in payload:
                appearance_size = tuple(int(value) for value in payload["appearance_size"].tolist())
                if len(appearance_size) == 2 and all(value > 0 for value in appearance_size):
                    recognizer.appearance_size = appearance_size
            elif recognizer.feature_version <= LEGACY_FEATURE_VERSION and recognizer.features.ndim == 2 and recognizer.features.shape[1] > 10:
                recognizer.appearance_size = infer_feature_image_size(recognizer.features.shape[1])
            if "geometry_features" in payload:
                recognizer.geometry_features = payload["geometry_features"].astype(np.float32)
            else:
                recognizer.geometry_features = np.zeros((len(recognizer.features), 10), dtype=np.float32)
            recognizer._rebuild_label_profiles()
            return recognizer
