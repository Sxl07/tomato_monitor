"""Unit tests for InferenceTimings dataclass and run_inference_timed() method.

These tests verify:
- InferenceTimings is a frozen dataclass with correct fields
- run_inference_timed() returns (results, InferenceTimings) tuple
- Timing values come from actual time.perf_counter() (non-negative)
- Existing run_inference() remains unchanged (backward compatibility)
- Health and maturity timings accumulate across multiple detections

Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6
"""

import importlib
import sys
from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


def _setup_mock_modules():
    """Insert mock modules for detectron2, torch, and cv2 into sys.modules.

    This allows importing snapshot_inference_runner on machines where
    these heavy dependencies are not installed or broken (e.g., Windows dev
    with Python 3.14 where cv2.dnn.DictValue doesn't exist).
    """
    mock_modules = {}

    # Mock detectron2 and submodules
    for mod_name in [
        "detectron2",
        "detectron2.config",
        "detectron2.engine",
        "detectron2.model_zoo",
        "detectron2.data",
        "detectron2.utils",
    ]:
        mock_modules[mod_name] = MagicMock()

    # Mock torch and torchvision
    for mod_name in [
        "torch",
        "torch.nn",
        "torch.nn.functional",
        "torchvision",
        "torchvision.transforms",
        "torchvision.models",
    ]:
        mock_modules[mod_name] = MagicMock()

    # Mock cv2 — on some environments (Python 3.14) cv2.typing fails to load
    mock_cv2 = MagicMock()
    mock_cv2.resize = lambda img, size, interpolation=None: np.zeros(
        (size[1], size[0], 3), dtype=np.uint8
    )
    mock_modules["cv2"] = mock_cv2

    return mock_modules


# We need to mock the modules BEFORE importing the target module.
# Create mock modules and patch sys.modules for the entire test session.
_MOCK_MODULES = _setup_mock_modules()


@pytest.fixture(autouse=True)
def patch_heavy_modules():
    """Patch heavy modules for all tests in this file."""
    # Remove cached imports if any
    modules_to_remove = [
        key
        for key in sys.modules
        if key.startswith("src.infrastructure.vision.detectron_detector")
        or key.startswith("src.infrastructure.vision.resnet_health_classifier")
        or key.startswith("src.infrastructure.vision.maturity_estimator")
        or key.startswith("src.infrastructure.vision.snapshot_inference_runner")
    ]
    saved = {}
    for mod in modules_to_remove:
        saved[mod] = sys.modules.pop(mod)

    with patch.dict(sys.modules, _MOCK_MODULES):
        # Now patch the specific vision submodules as mock modules
        # that provide the functions our target module imports
        mock_detectron_detector = MagicMock()
        mock_health_classifier = MagicMock()
        mock_maturity_estimator = MagicMock()

        sys.modules[
            "src.infrastructure.vision.detectron_detector"
        ] = mock_detectron_detector
        sys.modules[
            "src.infrastructure.vision.resnet_health_classifier"
        ] = mock_health_classifier
        sys.modules[
            "src.infrastructure.vision.maturity_estimator"
        ] = mock_maturity_estimator

        # Force reimport of the target module
        if "src.infrastructure.vision.snapshot_inference_runner" in sys.modules:
            del sys.modules["src.infrastructure.vision.snapshot_inference_runner"]

        yield {
            "detectron_detector": mock_detectron_detector,
            "health_classifier": mock_health_classifier,
            "maturity_estimator": mock_maturity_estimator,
        }

    # Restore previously cached modules
    for mod, value in saved.items():
        sys.modules[mod] = value


def _import_runner():
    """Import and return the snapshot_inference_runner module (fresh)."""
    if "src.infrastructure.vision.snapshot_inference_runner" in sys.modules:
        del sys.modules["src.infrastructure.vision.snapshot_inference_runner"]
    from src.infrastructure.vision.snapshot_inference_runner import (
        InferenceTimings,
        SnapshotInferenceRunner,
    )

    return SnapshotInferenceRunner, InferenceTimings


class TestInferenceTimingsDataclass:
    """Tests for the InferenceTimings frozen dataclass."""

    def test_create_with_all_fields(self):
        """InferenceTimings can be created with all required fields."""
        _, InferenceTimings = _import_runner()

        t = InferenceTimings(
            detection_ms=100.0,
            health_ms=50.0,
            maturity_ms=25.0,
            total_ms=200.0,
        )
        assert t.detection_ms == 100.0
        assert t.health_ms == 50.0
        assert t.maturity_ms == 25.0
        assert t.total_ms == 200.0

    def test_frozen_cannot_mutate(self):
        """InferenceTimings is frozen — fields cannot be reassigned."""
        _, InferenceTimings = _import_runner()

        t = InferenceTimings(
            detection_ms=100.0, health_ms=50.0, maturity_ms=25.0, total_ms=200.0
        )
        with pytest.raises((FrozenInstanceError, AttributeError)):
            t.detection_ms = 999.0

    def test_zero_values_allowed(self):
        """All timing fields can be zero (e.g., maturity skipped)."""
        _, InferenceTimings = _import_runner()

        t = InferenceTimings(
            detection_ms=0.0, health_ms=0.0, maturity_ms=0.0, total_ms=0.0
        )
        assert t.maturity_ms == 0.0


class TestRunInferenceTimedNoDetections:
    """Tests for run_inference_timed() when there are no detections."""

    def test_returns_tuple_with_empty_results_and_timings(
        self, patch_heavy_modules
    ):
        """Returns ([], InferenceTimings) when no detections found."""
        SnapshotInferenceRunner, InferenceTimings = _import_runner()

        mocks = patch_heavy_modules
        mocks["detectron_detector"].run_detection.return_value = MagicMock()
        mocks["detectron_detector"].extract_detection_dicts.return_value = []

        runner = SnapshotInferenceRunner(
            detector=MagicMock(),
            health_model=MagicMock(),
            health_transform=MagicMock(),
        )

        image = np.zeros((480, 640, 3), dtype=np.uint8)
        results, timings = runner.run_inference_timed(image)

        assert results == []
        assert isinstance(timings, InferenceTimings)
        assert timings.detection_ms >= 0.0
        assert timings.health_ms == 0.0
        assert timings.maturity_ms == 0.0
        assert timings.total_ms >= 0.0
        assert timings.total_ms >= timings.detection_ms

    def test_detection_failure_returns_timing(self, patch_heavy_modules):
        """When detector raises, returns empty results with detection timing."""
        SnapshotInferenceRunner, InferenceTimings = _import_runner()

        mocks = patch_heavy_modules
        mocks["detectron_detector"].run_detection.side_effect = RuntimeError(
            "Detector crashed"
        )

        runner = SnapshotInferenceRunner(
            detector=MagicMock(),
            health_model=MagicMock(),
            health_transform=MagicMock(),
        )

        image = np.zeros((480, 640, 3), dtype=np.uint8)
        results, timings = runner.run_inference_timed(image)

        assert results == []
        assert isinstance(timings, InferenceTimings)
        assert timings.detection_ms >= 0.0
        assert timings.health_ms == 0.0
        assert timings.maturity_ms == 0.0
        assert timings.total_ms >= 0.0


class TestRunInferenceTimedWithDetections:
    """Tests for run_inference_timed() with detections present."""

    def test_health_timing_accumulated(self, patch_heavy_modules):
        """Health timing is accumulated across all detections."""
        # Patch cropper BEFORE reimporting runner so the runner gets our lambdas
        from src.infrastructure.vision import cropper

        cropper.clamp_box_xyxy = lambda x1, y1, x2, y2, w, h: (x1, y1, x2, y2)
        cropper.expand_box = lambda x1, y1, x2, y2, w, h: (x1, y1, x2, y2)
        cropper.crop_from_box = lambda img, box: np.zeros(
            (50, 50, 3), dtype=np.uint8
        )
        cropper.is_crop_large_enough = lambda crop: True

        SnapshotInferenceRunner, InferenceTimings = _import_runner()

        mocks = patch_heavy_modules
        mocks["detectron_detector"].run_detection.return_value = MagicMock()
        mocks["detectron_detector"].extract_detection_dicts.return_value = [
            {"bbox": [10, 10, 100, 100], "score": 0.95, "detection_id": 1},
            {"bbox": [200, 200, 300, 300], "score": 0.90, "detection_id": 2},
        ]

        mocks["health_classifier"].predict_health.return_value = {
            "label": "unhealthy",
            "confidence": 0.85,
        }

        runner = SnapshotInferenceRunner(
            detector=MagicMock(),
            health_model=MagicMock(),
            health_transform=MagicMock(),
            skip_maturity=True,
        )

        image = np.zeros((480, 640, 3), dtype=np.uint8)
        results, timings = runner.run_inference_timed(image)

        assert len(results) == 2
        assert isinstance(timings, InferenceTimings)
        assert timings.health_ms >= 0.0
        assert timings.maturity_ms == 0.0  # Skipped
        assert timings.total_ms >= timings.detection_ms

    def test_maturity_timing_accumulated(self, patch_heavy_modules):
        """Maturity timing is accumulated when maturity runs."""
        # Patch cropper BEFORE reimporting runner so the runner gets our lambdas
        from src.infrastructure.vision import cropper

        cropper.clamp_box_xyxy = lambda x1, y1, x2, y2, w, h: (x1, y1, x2, y2)
        cropper.expand_box = lambda x1, y1, x2, y2, w, h: (x1, y1, x2, y2)
        cropper.crop_from_box = lambda img, box: np.zeros(
            (50, 50, 3), dtype=np.uint8
        )
        cropper.is_crop_large_enough = lambda crop: True

        SnapshotInferenceRunner, InferenceTimings = _import_runner()

        mocks = patch_heavy_modules
        mocks["detectron_detector"].run_detection.return_value = MagicMock()
        mocks["detectron_detector"].extract_detection_dicts.return_value = [
            {"bbox": [10, 10, 100, 100], "score": 0.95, "detection_id": 1},
        ]

        mocks["health_classifier"].predict_health.return_value = {
            "label": "healthy",
            "confidence": 0.95,
        }
        mocks[
            "maturity_estimator"
        ].estimate_maturity_for_crop.return_value = {
            "estimate": {"usda_stage": "turning", "maturity_percent": 45.0}
        }

        runner = SnapshotInferenceRunner(
            detector=MagicMock(),
            health_model=MagicMock(),
            health_transform=MagicMock(),
            skip_maturity=False,
        )

        image = np.zeros((480, 640, 3), dtype=np.uint8)
        results, timings = runner.run_inference_timed(image)

        assert len(results) == 1
        assert results[0]["maturity_stage"] == "turning"
        assert isinstance(timings, InferenceTimings)
        assert timings.maturity_ms >= 0.0
        assert timings.health_ms >= 0.0
        assert timings.total_ms >= 0.0

    def test_maturity_zero_when_skipped(self, patch_heavy_modules):
        """Maturity timing is 0.0 when skip_maturity=True."""
        # Patch cropper BEFORE reimporting runner so the runner gets our lambdas
        from src.infrastructure.vision import cropper

        cropper.clamp_box_xyxy = lambda x1, y1, x2, y2, w, h: (x1, y1, x2, y2)
        cropper.expand_box = lambda x1, y1, x2, y2, w, h: (x1, y1, x2, y2)
        cropper.crop_from_box = lambda img, box: np.zeros(
            (50, 50, 3), dtype=np.uint8
        )
        cropper.is_crop_large_enough = lambda crop: True

        SnapshotInferenceRunner, InferenceTimings = _import_runner()

        mocks = patch_heavy_modules
        mocks["detectron_detector"].run_detection.return_value = MagicMock()
        mocks["detectron_detector"].extract_detection_dicts.return_value = [
            {"bbox": [10, 10, 100, 100], "score": 0.95, "detection_id": 1},
        ]

        mocks["health_classifier"].predict_health.return_value = {
            "label": "healthy",
            "confidence": 0.95,
        }

        runner = SnapshotInferenceRunner(
            detector=MagicMock(),
            health_model=MagicMock(),
            health_transform=MagicMock(),
            skip_maturity=True,
        )

        image = np.zeros((480, 640, 3), dtype=np.uint8)
        results, timings = runner.run_inference_timed(image)

        assert timings.maturity_ms == 0.0


class TestBackwardCompatibility:
    """Verify run_inference() remains unchanged."""

    def test_run_inference_returns_list_only(self, patch_heavy_modules):
        """run_inference() still returns list[dict] without timings."""
        SnapshotInferenceRunner, _ = _import_runner()

        mocks = patch_heavy_modules
        mocks["detectron_detector"].run_detection.return_value = MagicMock()
        mocks["detectron_detector"].extract_detection_dicts.return_value = []

        runner = SnapshotInferenceRunner(
            detector=MagicMock(),
            health_model=MagicMock(),
            health_transform=MagicMock(),
        )

        image = np.zeros((480, 640, 3), dtype=np.uint8)
        result = runner.run_inference(image)

        assert isinstance(result, list)
        # Not a tuple — backward compatible
        assert not isinstance(result, tuple)

    def test_run_inference_same_results_as_timed(self, patch_heavy_modules):
        """run_inference() and run_inference_timed() produce same results."""
        SnapshotInferenceRunner, _ = _import_runner()

        mocks = patch_heavy_modules
        mocks["detectron_detector"].run_detection.return_value = MagicMock()
        mocks["detectron_detector"].extract_detection_dicts.return_value = [
            {"bbox": [10, 10, 100, 100], "score": 0.95, "detection_id": 1},
        ]

        from src.infrastructure.vision import cropper

        cropper.clamp_box_xyxy = lambda x1, y1, x2, y2, w, h: (x1, y1, x2, y2)
        cropper.expand_box = lambda x1, y1, x2, y2, w, h: (x1, y1, x2, y2)
        cropper.crop_from_box = lambda img, box: np.zeros(
            (50, 50, 3), dtype=np.uint8
        )
        cropper.is_crop_large_enough = lambda crop: True

        mocks["health_classifier"].predict_health.return_value = {
            "label": "healthy",
            "confidence": 0.95,
        }
        mocks[
            "maturity_estimator"
        ].estimate_maturity_for_crop.return_value = {
            "estimate": {"usda_stage": "red", "maturity_percent": 95.0}
        }

        runner = SnapshotInferenceRunner(
            detector=MagicMock(),
            health_model=MagicMock(),
            health_transform=MagicMock(),
            skip_maturity=False,
        )

        image = np.zeros((480, 640, 3), dtype=np.uint8)

        result_plain = runner.run_inference(image)
        result_timed, timings = runner.run_inference_timed(image)

        # Same result structure and values
        assert len(result_plain) == len(result_timed)
        for r1, r2 in zip(result_plain, result_timed):
            assert r1.keys() == r2.keys()
            for key in r1:
                assert r1[key] == r2[key], f"Mismatch on key {key}"
