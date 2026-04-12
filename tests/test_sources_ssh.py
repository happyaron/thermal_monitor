"""Tests for SSHSensorsSource."""
from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest
from thermal_monitor.sources.ssh_sensors import SSHSensorsSource


def _src(**kw) -> SSHSensorsSource:
    return SSHSensorsSource({"name": "ssh-test", "host": "10.0.0.1",
                             "warn": 40, "crit": 55, **kw})


class TestCollect:
    def test_success_builds_ssh_command(self, sensors_json):
        src = _src()
        mock = MagicMock(returncode=0, stdout=json.dumps(sensors_json), stderr="")
        with patch("subprocess.run", return_value=mock) as mock_run:
            src.collect()
        cmd = mock_run.call_args[0][0]
        assert "ssh" in cmd
        assert "10.0.0.1" in " ".join(cmd)
        assert "sensors -j" in " ".join(cmd)

    def test_key_file_adds_i_flag(self, sensors_json):
        src = _src(key_file="~/.ssh/id_rsa")
        mock = MagicMock(returncode=0, stdout=json.dumps(sensors_json), stderr="")
        with patch("subprocess.run", return_value=mock) as mock_run:
            src.collect()
        cmd = mock_run.call_args[0][0]
        assert "-i" in cmd

    def test_ssh_not_found(self):
        src = _src()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            readings = src.collect()
        assert "not found" in readings[0].error

    def test_ssh_timeout(self):
        src = _src()
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("ssh", 25)):
            readings = src.collect()
        assert "timed out" in readings[0].error

    def test_ssh_nonzero_exit(self):
        src = _src()
        mock = MagicMock(returncode=255, stderr="Connection refused", stdout="")
        with patch("subprocess.run", return_value=mock):
            readings = src.collect()
        assert readings[0].error is not None
