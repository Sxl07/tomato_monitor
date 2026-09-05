"""Unit tests for UndervoltagePresenceProbe (Spec 020, Task 5.3)."""

from __future__ import annotations

from unittest.mock import patch

from src.infrastructure.monitoring.undervoltage_probe import (
    UndervoltagePresenceProbe,
    UndervoltageStatus,
    UNDERVOLTAGE_NOW_BIT,
    UNDERVOLTAGE_OCCURRED_BIT,
)


class TestInterpret:
    def test_bit0_present(self):
        assert UndervoltagePresenceProbe.interpret(UNDERVOLTAGE_NOW_BIT) == UndervoltageStatus.PRESENT

    def test_bit0_plus_others_present(self):
        assert UndervoltagePresenceProbe.interpret(0x50005) == UndervoltageStatus.PRESENT

    def test_historical_bit16_only_is_absent(self):
        # Historical/latched flag must NEVER be reported as a current fault.
        assert UndervoltagePresenceProbe.interpret(UNDERVOLTAGE_OCCURRED_BIT) == UndervoltageStatus.ABSENT

    def test_zero_absent(self):
        assert UndervoltagePresenceProbe.interpret(0x0) == UndervoltageStatus.ABSENT

    def test_none_unavailable(self):
        assert UndervoltagePresenceProbe.interpret(None) == UndervoltageStatus.UNAVAILABLE


class TestReadRaw:
    def test_parses_hex_output(self):
        probe = UndervoltagePresenceProbe()
        completed = type("R", (), {"returncode": 0, "stdout": "throttled=0x50005"})()
        with patch("subprocess.run", return_value=completed):
            assert probe.read_raw() == 0x50005

    def test_parses_zero(self):
        probe = UndervoltagePresenceProbe()
        completed = type("R", (), {"returncode": 0, "stdout": "throttled=0x0\n"})()
        with patch("subprocess.run", return_value=completed):
            assert probe.read_raw() == 0

    def test_command_missing_returns_none(self):
        probe = UndervoltagePresenceProbe()
        with patch("subprocess.run", side_effect=FileNotFoundError()):
            assert probe.read_raw() is None

    def test_nonzero_exit_returns_none(self):
        probe = UndervoltagePresenceProbe()
        completed = type("R", (), {"returncode": 1, "stdout": ""})()
        with patch("subprocess.run", return_value=completed):
            assert probe.read_raw() is None

    def test_unparseable_returns_none(self):
        probe = UndervoltagePresenceProbe()
        completed = type("R", (), {"returncode": 0, "stdout": "garbage"})()
        with patch("subprocess.run", return_value=completed):
            assert probe.read_raw() is None


class TestCheck:
    def test_check_present(self):
        probe = UndervoltagePresenceProbe()
        with patch.object(probe, "read_raw", return_value=0x1):
            assert probe.check() == UndervoltageStatus.PRESENT

    def test_check_absent_historical(self):
        probe = UndervoltagePresenceProbe()
        with patch.object(probe, "read_raw", return_value=0x10000):
            assert probe.check() == UndervoltageStatus.ABSENT

    def test_check_unavailable_off_rpi(self):
        probe = UndervoltagePresenceProbe()
        with patch.object(probe, "read_raw", return_value=None):
            assert probe.check() == UndervoltageStatus.UNAVAILABLE
            # Never PRESENT when unavailable.
            assert probe.check() != UndervoltageStatus.PRESENT
