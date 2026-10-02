"""Tests for the video-first ExecutionProfile fields (Spec 019, Task 7.2).

Covers cases A-J plus a profile critical-values guard:
    A. All video-first fields present on EDGE and FULL.
    B. video_first_enabled is True on both.
    C. recording_target_fps == expected and == camera_fps.
    D. codec candidates == ("mp4v", "avc1") (tuple, ordered).
    E. sparse config values EDGE 1/4, FULL 5/12, min<=max, gate/flow on.
    F. EDGE more conservative: edge.min<=full.min and edge.max<=full.max.
    G. save_annotated_video is False on both.
    H. immutability (frozen dataclass) — light check, existing suite covers more.
    I. profile selection unchanged — covered by test_execution_profile.py (not duplicated).
    J. settings importable without torch/detectron2/cv2/picamera2.

Critical profile values: EDGE camera resolution = 960x720, active EDGE detection
threshold = 0.60 (post-v1.0.0 presentation refinement), EDGE fps stays 5. FULL
keeps its values, including detection threshold 0.80.

Follows the existing torch-mock pattern used by test_execution_profile.py so
settings imports cleanly on machines without torch.
"""

import subprocess
import sys
from pathlib import Path

# settings.py does NOT need heavy backends (torch/torchvision/detectron2/cv2/
# picamera2), so we import it directly here — no mocking. The strict
# "importable without heavy backends" check lives in TestImportability, which
# runs a fresh interpreter with those backends actually blocked.
from src.infrastructure.config.settings import EDGE_PROFILE, FULL_PROFILE


VIDEO_FIRST_FIELDS = [
    "video_first_enabled",
    "recording_target_fps",
    "video_codec_candidates",
    "sparse_min_frames_between_detections",
    "sparse_max_frames_without_detection",
    "sparse_use_scene_gate",
    "sparse_enable_flow_propagation",
    "save_annotated_video",
]


# A. Fields present
class TestFieldsPresent:
    def test_edge_has_all_video_first_fields(self):
        for field in VIDEO_FIRST_FIELDS:
            assert hasattr(EDGE_PROFILE, field), f"EDGE missing {field}"

    def test_full_has_all_video_first_fields(self):
        for field in VIDEO_FIRST_FIELDS:
            assert hasattr(FULL_PROFILE, field), f"FULL missing {field}"


# B. Enabled
class TestVideoFirstEnabled:
    def test_both_enabled(self):
        assert EDGE_PROFILE.video_first_enabled is True
        assert FULL_PROFILE.video_first_enabled is True


# C. Nominal fps
class TestRecordingTargetFps:
    def test_expected_values(self):
        assert EDGE_PROFILE.recording_target_fps == 5.0
        assert FULL_PROFILE.recording_target_fps == 10.0

    def test_aligned_with_camera_fps(self):
        assert EDGE_PROFILE.recording_target_fps == EDGE_PROFILE.camera_fps
        assert FULL_PROFILE.recording_target_fps == FULL_PROFILE.camera_fps

    def test_positive(self):
        assert EDGE_PROFILE.recording_target_fps > 0
        assert FULL_PROFILE.recording_target_fps > 0


# D. Codecs
class TestCodecCandidates:
    def test_exact_tuple(self):
        assert EDGE_PROFILE.video_codec_candidates == ("mp4v", "avc1")
        assert FULL_PROFILE.video_codec_candidates == ("mp4v", "avc1")

    def test_is_tuple_not_list(self):
        assert isinstance(EDGE_PROFILE.video_codec_candidates, tuple)
        assert isinstance(FULL_PROFILE.video_codec_candidates, tuple)

    def test_non_empty(self):
        assert len(EDGE_PROFILE.video_codec_candidates) >= 1
        assert len(FULL_PROFILE.video_codec_candidates) >= 1


# E. Sparse config
class TestSparseConfig:
    def test_edge_values(self):
        # Validated baseline after the Scene Gate wiring fix (Raspberry
        # monitoring 16): EDGE adopts 1/4 to prioritize recall in the field.
        assert EDGE_PROFILE.sparse_min_frames_between_detections == 1
        assert EDGE_PROFILE.sparse_max_frames_without_detection == 4

    def test_full_values(self):
        assert FULL_PROFILE.sparse_min_frames_between_detections == 5
        assert FULL_PROFILE.sparse_max_frames_without_detection == 12

    def test_min_le_max_both(self):
        assert (
            EDGE_PROFILE.sparse_min_frames_between_detections
            <= EDGE_PROFILE.sparse_max_frames_without_detection
        )
        assert (
            FULL_PROFILE.sparse_min_frames_between_detections
            <= FULL_PROFILE.sparse_max_frames_without_detection
        )

    def test_min_non_negative(self):
        assert EDGE_PROFILE.sparse_min_frames_between_detections >= 0
        assert FULL_PROFILE.sparse_min_frames_between_detections >= 0

    def test_scene_gate_and_flow_on(self):
        assert EDGE_PROFILE.sparse_use_scene_gate is True
        assert FULL_PROFILE.sparse_use_scene_gate is True
        assert EDGE_PROFILE.sparse_enable_flow_propagation is True
        assert FULL_PROFILE.sparse_enable_flow_propagation is True


# F. EDGE more conservative (frame-frequency, NOT fps)
class TestEdgeMoreConservative:
    def test_edge_min_le_full_min(self):
        assert (
            EDGE_PROFILE.sparse_min_frames_between_detections
            <= FULL_PROFILE.sparse_min_frames_between_detections
        )

    def test_edge_max_le_full_max(self):
        assert (
            EDGE_PROFILE.sparse_max_frames_without_detection
            <= FULL_PROFILE.sparse_max_frames_without_detection
        )

    def test_edge_strictly_smaller_gaps_than_full(self):
        # Smaller gaps => the detector is scheduled more often => higher frame
        # coverage/recall. EDGE (1/4) must sample strictly more aggressively
        # than FULL (5/12).
        assert (
            EDGE_PROFILE.sparse_min_frames_between_detections
            < FULL_PROFILE.sparse_min_frames_between_detections
        )
        assert (
            EDGE_PROFILE.sparse_max_frames_without_detection
            < FULL_PROFILE.sparse_max_frames_without_detection
        )


# G. Annotated video off
class TestAnnotatedVideoOff:
    def test_both_false(self):
        assert EDGE_PROFILE.save_annotated_video is False
        assert FULL_PROFILE.save_annotated_video is False


# H. Immutability (frozen dataclass)
class TestImmutability:
    def test_cannot_mutate_new_field(self):
        import dataclasses
        import pytest

        with pytest.raises(dataclasses.FrozenInstanceError):
            EDGE_PROFILE.recording_target_fps = 99.0  # type: ignore[misc]


# J. Importability without heavy backends (strict: fresh interpreter, real block)
class TestImportability:
    def test_settings_importable_when_heavy_backends_are_unavailable(self):
        """settings must import in a fresh interpreter with heavy backends blocked.

        Blocking torch/torchvision/detectron2/cv2/picamera2 via an import guard
        (rather than mocking) proves settings does not depend on any of them —
        an accidental `import torch` in settings would make this fail.
        """
        repo_root = Path(__file__).resolve().parents[2]

        script = r'''
import builtins

blocked = {"torch", "torchvision", "detectron2", "cv2", "picamera2"}

real_import = builtins.__import__

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".", 1)[0]
    if root in blocked:
        raise ImportError("blocked heavy backend: " + name)
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = guarded_import

import src.infrastructure.config.settings as settings

assert settings.EDGE_PROFILE.recording_target_fps == 5.0
assert settings.FULL_PROFILE.recording_target_fps == 10.0
assert settings.EDGE_PROFILE.video_first_enabled is True
assert settings.FULL_PROFILE.video_first_enabled is True
'''

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=repo_root,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stderr


# Critical profile values: EDGE camera resolution was raised to 960x720 and the
# active EDGE detection threshold is 0.60 (post-v1.0.0 refinement);
# EDGE fps stays 5. FULL values remain unchanged (threshold 0.80).
class TestProfileCriticalValues:
    def test_edge_critical_values(self):
        # EDGE capture at 960x720 and active detection threshold 0.60;
        # fps stays 5.
        assert EDGE_PROFILE.camera_width == 960
        assert EDGE_PROFILE.camera_height == 720
        assert EDGE_PROFILE.camera_fps == 5
        assert EDGE_PROFILE.detection_score_threshold == 0.60

    def test_full_critical_values_unchanged(self):
        assert FULL_PROFILE.camera_width == 640
        assert FULL_PROFILE.camera_height == 480
        assert FULL_PROFILE.camera_fps == 10
        assert FULL_PROFILE.detection_score_threshold == 0.80
