"""Unit tests for BoundingBox value object immutability and utility methods."""

import dataclasses

import pytest

from src.domain.value_objects.bounding_box import BoundingBox


class TestBoundingBoxConstruction:
    """Test that BoundingBox construction with valid values succeeds."""

    def test_construction_with_positive_coordinates(self):
        bbox = BoundingBox(x1=10, y1=20, x2=100, y2=200)
        assert bbox.x1 == 10
        assert bbox.y1 == 20
        assert bbox.x2 == 100
        assert bbox.y2 == 200

    def test_construction_with_zero_origin(self):
        bbox = BoundingBox(x1=0, y1=0, x2=50, y2=50)
        assert bbox.x1 == 0
        assert bbox.y1 == 0
        assert bbox.x2 == 50
        assert bbox.y2 == 50

    def test_construction_with_equal_coordinates(self):
        bbox = BoundingBox(x1=5, y1=5, x2=5, y2=5)
        assert bbox.x1 == 5
        assert bbox.x2 == 5

    def test_construction_with_inverted_coordinates(self):
        # BoundingBox allows construction with x2 < x1 (validity checked via is_valid)
        bbox = BoundingBox(x1=100, y1=100, x2=10, y2=10)
        assert bbox.x1 == 100
        assert bbox.x2 == 10


class TestBoundingBoxImmutability:
    """Test that attribute assignment after construction raises FrozenInstanceError."""

    def test_cannot_assign_x1(self):
        bbox = BoundingBox(x1=10, y1=20, x2=100, y2=200)
        with pytest.raises(dataclasses.FrozenInstanceError):
            bbox.x1 = 50

    def test_cannot_assign_y1(self):
        bbox = BoundingBox(x1=10, y1=20, x2=100, y2=200)
        with pytest.raises(dataclasses.FrozenInstanceError):
            bbox.y1 = 50

    def test_cannot_assign_x2(self):
        bbox = BoundingBox(x1=10, y1=20, x2=100, y2=200)
        with pytest.raises(dataclasses.FrozenInstanceError):
            bbox.x2 = 50

    def test_cannot_assign_y2(self):
        bbox = BoundingBox(x1=10, y1=20, x2=100, y2=200)
        with pytest.raises(dataclasses.FrozenInstanceError):
            bbox.y2 = 50


class TestBoundingBoxWidth:
    """Test width() utility method."""

    def test_width_normal_box(self):
        bbox = BoundingBox(x1=10, y1=0, x2=110, y2=50)
        assert bbox.width() == 100

    def test_width_zero_when_equal(self):
        bbox = BoundingBox(x1=50, y1=0, x2=50, y2=50)
        assert bbox.width() == 0

    def test_width_zero_when_inverted(self):
        bbox = BoundingBox(x1=100, y1=0, x2=10, y2=50)
        assert bbox.width() == 0


class TestBoundingBoxHeight:
    """Test height() utility method."""

    def test_height_normal_box(self):
        bbox = BoundingBox(x1=0, y1=10, x2=50, y2=210)
        assert bbox.height() == 200

    def test_height_zero_when_equal(self):
        bbox = BoundingBox(x1=0, y1=50, x2=50, y2=50)
        assert bbox.height() == 0

    def test_height_zero_when_inverted(self):
        bbox = BoundingBox(x1=0, y1=100, x2=50, y2=10)
        assert bbox.height() == 0


class TestBoundingBoxArea:
    """Test area() utility method."""

    def test_area_normal_box(self):
        bbox = BoundingBox(x1=0, y1=0, x2=10, y2=20)
        assert bbox.area() == 200

    def test_area_zero_for_zero_width(self):
        bbox = BoundingBox(x1=5, y1=0, x2=5, y2=100)
        assert bbox.area() == 0

    def test_area_zero_for_zero_height(self):
        bbox = BoundingBox(x1=0, y1=5, x2=100, y2=5)
        assert bbox.area() == 0

    def test_area_zero_for_inverted_box(self):
        bbox = BoundingBox(x1=100, y1=100, x2=10, y2=10)
        assert bbox.area() == 0


class TestBoundingBoxCenter:
    """Test center() utility method."""

    def test_center_normal_box(self):
        bbox = BoundingBox(x1=0, y1=0, x2=100, y2=200)
        assert bbox.center() == (50.0, 100.0)

    def test_center_offset_box(self):
        bbox = BoundingBox(x1=10, y1=20, x2=30, y2=40)
        assert bbox.center() == (20.0, 30.0)

    def test_center_returns_floats(self):
        bbox = BoundingBox(x1=0, y1=0, x2=1, y2=1)
        cx, cy = bbox.center()
        assert isinstance(cx, float)
        assert isinstance(cy, float)
        assert cx == 0.5
        assert cy == 0.5


class TestBoundingBoxToTuple:
    """Test to_tuple() utility method."""

    def test_to_tuple_returns_correct_values(self):
        bbox = BoundingBox(x1=10, y1=20, x2=30, y2=40)
        assert bbox.to_tuple() == (10, 20, 30, 40)

    def test_to_tuple_returns_tuple_type(self):
        bbox = BoundingBox(x1=0, y1=0, x2=1, y2=1)
        result = bbox.to_tuple()
        assert isinstance(result, tuple)
        assert len(result) == 4


class TestBoundingBoxFromTuple:
    """Test from_tuple() static method."""

    def test_from_tuple_creates_bbox(self):
        bbox = BoundingBox.from_tuple((10, 20, 30, 40))
        assert bbox.x1 == 10
        assert bbox.y1 == 20
        assert bbox.x2 == 30
        assert bbox.y2 == 40

    def test_from_tuple_roundtrip(self):
        original = BoundingBox(x1=5, y1=15, x2=25, y2=35)
        restored = BoundingBox.from_tuple(original.to_tuple())
        assert restored == original


class TestBoundingBoxIsValid:
    """Test is_valid() utility method."""

    def test_valid_box(self):
        bbox = BoundingBox(x1=0, y1=0, x2=10, y2=20)
        assert bbox.is_valid() is True

    def test_invalid_when_x2_equals_x1(self):
        bbox = BoundingBox(x1=5, y1=0, x2=5, y2=10)
        assert bbox.is_valid() is False

    def test_invalid_when_y2_equals_y1(self):
        bbox = BoundingBox(x1=0, y1=5, x2=10, y2=5)
        assert bbox.is_valid() is False

    def test_invalid_when_x2_less_than_x1(self):
        bbox = BoundingBox(x1=100, y1=0, x2=10, y2=50)
        assert bbox.is_valid() is False

    def test_invalid_when_y2_less_than_y1(self):
        bbox = BoundingBox(x1=0, y1=100, x2=50, y2=10)
        assert bbox.is_valid() is False

    def test_invalid_when_both_inverted(self):
        bbox = BoundingBox(x1=100, y1=100, x2=10, y2=10)
        assert bbox.is_valid() is False
