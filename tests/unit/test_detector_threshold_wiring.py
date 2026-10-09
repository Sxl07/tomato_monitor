"""Tests: profile detection threshold is wired into the RetinaNet detector.

Verifies (without loading real Detectron2/torch or running inference):
- EDGE/FULL profile threshold values;
- build_tomato_detector applies score_threshold to SCORE_THRESH_TEST;
- build_tomato_detector default stays DETECTION_SCORE_THRESHOLD (0.80);
- build_pipeline_components passes ACTIVE_PROFILE.detection_score_threshold.

Heavy backends (Detectron2/torch/cv2) are mocked inside the detector_mod
fixture via monkeypatch and restored after each test, so nothing leaks into
sys.modules at collection time or across tests.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# Heavy backends that detectron_detector pulls in transitively. They are mocked
# ONLY inside the detector_mod fixture (via monkeypatch.setitem), so nothing is
# left in sys.modules after each test — no global collection-time pollution.
_MOCK_MODULES = [
    "torch", "torch.nn", "torch.nn.functional", "torch.utils",
    "torch.utils.data", "torch.cuda", "torch.hub",
    "torchvision", "torchvision.transforms", "torchvision.transforms.functional",
    "torchvision.models", "torchvision.ops",
    "cv2",
]


class _FakeCfgNode(SimpleNamespace):
    """Minimal cfg node supporting attribute access + merge_from_file()."""

    def merge_from_file(self, *_a, **_k):
        return None


def _make_fake_cfg():
    """A fake Detectron2 cfg with the attributes build_tomato_detector sets."""
    cfg = _FakeCfgNode()
    cfg.MODEL = _FakeCfgNode()
    cfg.MODEL.RETINANET = _FakeCfgNode()
    cfg.MODEL.RETINANET.NUM_CLASSES = None
    cfg.MODEL.RETINANET.SCORE_THRESH_TEST = None
    cfg.MODEL.WEIGHTS = None
    cfg.MODEL.DEVICE = None
    return cfg


@pytest.fixture
def detector_mod(monkeypatch):
    """Import detectron_detector with detectron2 mocked; expose captured cfg.

    All heavy-backend mocks (torch/torchvision/cv2 + detectron2) are installed
    via monkeypatch.setitem, so they are scoped to this test and restored on
    teardown — no permanent sys.modules mutation from module scope.
    """
    # Scope the heavy-backend mocks to this test only.
    for _mod_name in _MOCK_MODULES:
        monkeypatch.setitem(sys.modules, _mod_name, MagicMock())

    # Build a fake 'detectron2' package with the submodules the module imports.
    fake_detectron2 = types.ModuleType("detectron2")
    fake_detectron2.__file__ = __file__  # os.path.dirname(...) must work
    fake_config = types.ModuleType("detectron2.config")
    fake_engine = types.ModuleType("detectron2.engine")

    captured = {"cfg": None}

    def _get_cfg():
        cfg = _make_fake_cfg()
        return cfg

    def _default_predictor(cfg):
        # Capture the cfg so tests can inspect SCORE_THRESH_TEST.
        captured["cfg"] = cfg
        return SimpleNamespace(cfg=cfg)  # stand-in predictor

    fake_config.get_cfg = _get_cfg
    fake_engine.DefaultPredictor = _default_predictor

    monkeypatch.setitem(sys.modules, "detectron2", fake_detectron2)
    monkeypatch.setitem(sys.modules, "detectron2.config", fake_config)
    monkeypatch.setitem(sys.modules, "detectron2.engine", fake_engine)

    # Force a FRESH import of the target module under our fake detectron2,
    # regardless of prior state left by other tests (which may have replaced
    # the module with a MagicMock — reloading that would fail). monkeypatch
    # restores sys.modules after the test.
    import importlib

    mod_name = "src.infrastructure.vision.detectron_detector"
    monkeypatch.delitem(sys.modules, mod_name, raising=False)
    det = importlib.import_module(mod_name)
    det._captured = captured  # attach for assertions
    return det


# --------------------------------------------------------------------------- #
# Profile threshold values
# --------------------------------------------------------------------------- #

class TestProfileThresholds:
    def test_edge_threshold_is_070(self):
        from src.infrastructure.config.settings import EDGE_PROFILE
        assert EDGE_PROFILE.detection_score_threshold == 0.70

    def test_full_threshold_is_080(self):
        from src.infrastructure.config.settings import FULL_PROFILE
        assert FULL_PROFILE.detection_score_threshold == 0.80


# --------------------------------------------------------------------------- #
# build_tomato_detector applies the threshold
# --------------------------------------------------------------------------- #

class TestBuildDetectorThreshold:
    def test_explicit_threshold_060(self, detector_mod):
        detector_mod.build_tomato_detector(score_threshold=0.60)
        cfg = detector_mod._captured["cfg"]
        assert cfg.MODEL.RETINANET.SCORE_THRESH_TEST == 0.60

    def test_explicit_threshold_080(self, detector_mod):
        detector_mod.build_tomato_detector(score_threshold=0.80)
        cfg = detector_mod._captured["cfg"]
        assert cfg.MODEL.RETINANET.SCORE_THRESH_TEST == 0.80

    def test_default_threshold_is_legacy_080(self, detector_mod):
        # No score_threshold -> default DETECTION_SCORE_THRESHOLD (0.80).
        from src.infrastructure.config.thresholds import DETECTION_SCORE_THRESHOLD
        assert DETECTION_SCORE_THRESHOLD == 0.80
        detector_mod.build_tomato_detector()
        cfg = detector_mod._captured["cfg"]
        assert cfg.MODEL.RETINANET.SCORE_THRESH_TEST == 0.80

    def test_num_classes_and_device_still_set(self, detector_mod):
        from src.infrastructure.config.settings import EDGE_PROFILE

        predictor = detector_mod.build_tomato_detector(
            score_threshold=EDGE_PROFILE.detection_score_threshold,
        )
        cfg = detector_mod._captured["cfg"]
        assert predictor.cfg is cfg
        assert cfg.MODEL.RETINANET.SCORE_THRESH_TEST == 0.70
        assert cfg.MODEL.RETINANET.NUM_CLASSES == 1
        assert cfg.MODEL.DEVICE == "cpu"


# --------------------------------------------------------------------------- #
# build_pipeline_components passes the ACTIVE_PROFILE threshold
# --------------------------------------------------------------------------- #

class TestPipelineWiring:
    def test_passes_active_profile_threshold(self, monkeypatch):
        import src.infrastructure.vision.pipeline_orchestrator as orch

        captured = {}

        def _fake_build_tomato_detector(model_weights=None, score_threshold=None):
            captured["score_threshold"] = score_threshold
            return object()

        def _fake_import_detector():
            return _fake_build_tomato_detector, object(), object()

        def _fake_import_health():
            return (lambda: (object(), object())), object()

        monkeypatch.setattr(orch, "_import_detector", _fake_import_detector)
        monkeypatch.setattr(orch, "_import_health", _fake_import_health)

        orch.build_pipeline_components()

        # The threshold passed must equal the ACTIVE_PROFILE value.
        from src.infrastructure.config.settings import ACTIVE_PROFILE
        assert captured["score_threshold"] == ACTIVE_PROFILE.detection_score_threshold
