"""Property tests conftest.

Pre-imports real modules that must not be replaced by sys.modules mocks
in test files that mock heavy dependencies (torch, detectron2, cv2).
"""

# Import real capture_gate FIRST so that test files using sys.modules.setdefault
# won't overwrite it with a mock (setdefault is a no-op if key already exists).
import src.infrastructure.vision.capture_gate  # noqa: F401
import src.infrastructure.vision.cropper  # noqa: F401
