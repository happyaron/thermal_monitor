"""Tests for config loading: expand_host_range and load_config."""
from __future__ import annotations

import pytest
from thermal_monitor.config import expand_host_range, load_config
from thermal_monitor.sources.local_sensors import LocalSensorsSource
from thermal_monitor.sources.redfish import RedfishSource


# ── expand_host_range ──────────────────────────────────────────────────────────

class TestExpandHostRange:
    def _base(self, **kw):
        return {"name": "Host {}", "type": "redfish",
                "user": "admin", "password": "pw", **kw}

    def test_basic_range(self):
        entries = expand_host_range({**self._base(), "host_range": "192.168.1.10-12"})
        assert len(entries) == 3
        assert [e["host"] for e in entries] == [
            "192.168.1.10", "192.168.1.11", "192.168.1.12"
        ]

    def test_name_placeholder_octet(self):
        entries = expand_host_range({**self._base(name="Node {}"),
                                     "host_range": "10.0.0.5-6"})
        assert entries[0]["name"] == "Node 5"
        assert entries[1]["name"] == "Node 6"

    def test_name_placeholder_start_index(self):
        entries = expand_host_range({**self._base(name="Bay {}"), "host_range": "10.0.0.10-12",
                                     "start_index": 1})
        assert [e["name"] for e in entries] == ["Bay 1", "Bay 2", "Bay 3"]

    def test_name_placeholder_host(self):
        entries = expand_host_range({**self._base(name="Node {host}"),
                                     "host_range": "10.0.0.5-5"})
        assert entries[0]["name"] == "Node 10.0.0.5"

    def test_name_placeholder_ip(self):
        entries = expand_host_range({**self._base(name="Node .{ip}"),
                                     "host_range": "10.0.0.5-5"})
        assert entries[0]["name"] == "Node .5"

    def test_enable_hosts_sets_enabled(self):
        entries = expand_host_range({**self._base(), "host_range": "10.0.0.10-12",
                                     "enable_hosts": [10, 12]})
        enabled = {e["host"]: e.get("enabled") for e in entries}
        assert enabled["10.0.0.10"] is True
        assert enabled["10.0.0.12"] is True
        assert "enabled" not in {k: v for k, v in enabled.items() if k == "10.0.0.11"}

    def test_group_derived_from_name(self):
        entries = expand_host_range({**self._base(name="Rack A - Bay {} ({host})"),
                                     "host_range": "10.0.0.1-2", "start_index": 1})
        assert entries[0].get("_group") == "Rack A"

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            expand_host_range({**self._base(), "host_range": "192.168.1.10"})

    def test_start_gt_end_raises(self):
        with pytest.raises(ValueError):
            expand_host_range({**self._base(), "host_range": "192.168.1.15-10"})

    def test_host_range_key_stripped_from_output(self):
        entries = expand_host_range({**self._base(), "host_range": "10.0.0.1-1"})
        assert "host_range" not in entries[0]


# ── load_config ────────────────────────────────────────────────────────────────

class TestLoadConfig:
    def test_load_minimal(self, minimal_config_path):
        sources, alerting, settings, logging_cfg = load_config(minimal_config_path)
        assert len(sources) == 1                    # disabled source excluded
        assert isinstance(sources[0], LocalSensorsSource)

    def test_alerting_section_returned(self, minimal_config_path):
        _, alerting, _, _ = load_config(minimal_config_path)
        assert alerting["mode"] == "webhook"
        assert alerting["alert_cooldown"] == 300

    def test_settings_section_returned(self, minimal_config_path):
        _, _, settings, _ = load_config(minimal_config_path)
        assert settings["max_workers"] == 0

    def test_logging_section_returned(self, minimal_config_path):
        _, _, _, logging_cfg = load_config(minimal_config_path)
        assert logging_cfg["retention_days"] == 7

    def test_defaults_merged_into_source(self, tmp_path):
        cfg = tmp_path / "c.yaml"
        cfg.write_text(
            "defaults:\n  warn: 30\n  crit: 45\n"
            "sources:\n  - name: S\n    type: local_sensors\n"
            "alerting: {}\nsettings: {}\nlogging: {}\n"
        )
        sources, _, _, _ = load_config(str(cfg))
        assert sources[0].warn == 30.0
        assert sources[0].crit == 45.0

    def test_source_overrides_defaults(self, tmp_path):
        cfg = tmp_path / "c.yaml"
        cfg.write_text(
            "defaults:\n  warn: 30\n  crit: 45\n"
            "sources:\n  - name: S\n    type: local_sensors\n    warn: 50\n    crit: 70\n"
            "alerting: {}\nsettings: {}\nlogging: {}\n"
        )
        sources, _, _, _ = load_config(str(cfg))
        assert sources[0].warn == 50.0
        assert sources[0].crit == 70.0

    def test_unknown_type_skipped(self, tmp_path):
        cfg = tmp_path / "c.yaml"
        cfg.write_text(
            "sources:\n  - name: S\n    type: unknown_type\n"
            "alerting: {}\nsettings: {}\nlogging: {}\n"
        )
        sources, _, _, _ = load_config(str(cfg))
        assert sources == []

    def test_missing_file_exits(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_config(str(tmp_path / "nonexistent.yaml"))

    def test_host_range_expanded(self, tmp_path):
        cfg = tmp_path / "c.yaml"
        cfg.write_text(
            "sources:\n"
            "  - name: Node {}\n    type: redfish\n    host_range: '10.0.0.1-3'\n"
            "    user: r\n    password: p\n"
            "alerting: {}\nsettings: {}\nlogging: {}\n"
        )
        sources, _, _, _ = load_config(str(cfg))
        assert len(sources) == 3
        assert all(isinstance(s, RedfishSource) for s in sources)

    def test_sensor_thresholds_deep_merged(self, tmp_path):
        cfg = tmp_path / "c.yaml"
        cfg.write_text(
            "defaults:\n  sensor_thresholds:\n    'P/S': {warn: 45, crit: 60}\n"
            "sources:\n  - name: S\n    type: local_sensors\n"
            "    sensor_thresholds:\n      'PS1': {warn: 50, crit: 65}\n"
            "alerting: {}\nsettings: {}\nlogging: {}\n"
        )
        sources, _, _, _ = load_config(str(cfg))
        assert "P/S" in sources[0].sensor_thresholds
        assert "PS1" in sources[0].sensor_thresholds
