"""Verification script for camera frame source implementations.

Validates that both VideoFileFrameSource and RaspberryCameraFrameSource
can be imported and instantiated, even without picamera2 installed.
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def verify_imports():
    """Verify that all camera frame source classes import correctly."""
    print("=" * 60)
    print("Camera Frame Source Import Verification")
    print("=" * 60)

    # 1. Verify FrameSource interface
    print("\n[1] Importing FrameSource interface...")
    from src.domain.interfaces.frame_source import FrameSource
    print(f"    OK: {FrameSource}")

    # 2. Verify VideoFileFrameSource
    print("\n[2] Importing VideoFileFrameSource...")
    from src.infrastructure.camera.video_file_frame_source import VideoFileFrameSource
    print(f"    OK: {VideoFileFrameSource}")
    assert issubclass(VideoFileFrameSource, FrameSource), "VideoFileFrameSource must implement FrameSource"
    print("    Implements FrameSource: YES")

    # 3. Verify RaspberryCameraFrameSource
    print("\n[3] Importing RaspberryCameraFrameSource...")
    from src.infrastructure.camera.raspberry_camera_frame_source import (
        RaspberryCameraFrameSource,
        PICAMERA2_AVAILABLE,
    )
    print(f"    OK: {RaspberryCameraFrameSource}")
    assert issubclass(RaspberryCameraFrameSource, FrameSource), "RaspberryCameraFrameSource must implement FrameSource"
    print("    Implements FrameSource: YES")
    print(f"    picamera2 available: {PICAMERA2_AVAILABLE}")

    # 4. Verify __init__.py exports
    print("\n[4] Importing from camera package __init__...")
    from src.infrastructure.camera import VideoFileFrameSource as VFS
    from src.infrastructure.camera import RaspberryCameraFrameSource as RCFS
    assert VFS is VideoFileFrameSource
    assert RCFS is RaspberryCameraFrameSource
    print("    Package exports: OK")

    # 5. Test instantiation of VideoFileFrameSource
    print("\n[5] Instantiating VideoFileFrameSource...")
    vfs = VideoFileFrameSource("dummy_path.mp4")
    print(f"    Instance: {vfs}")
    print(f"    is_available (dummy path): {vfs.is_available()}")
    vfs.release()
    print("    release(): OK")

    # 6. Test instantiation of RaspberryCameraFrameSource (without picamera2)
    print("\n[6] Instantiating RaspberryCameraFrameSource...")
    rcfs = RaspberryCameraFrameSource(width=640, height=480, fps=5)
    print(f"    Instance: {rcfs}")
    print(f"    is_available (no picamera2): {rcfs.is_available()}")
    success, frame = rcfs.read()
    print(f"    read() -> success={success}, frame={'None' if frame is None else frame.shape}")
    rcfs.release()
    print("    release(): OK")

    print("\n" + "=" * 60)
    print("ALL VERIFICATIONS PASSED")
    print("=" * 60)


if __name__ == "__main__":
    verify_imports()
