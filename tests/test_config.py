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

    def test_rejects_host_starting_with_dash(self, tmp_path, caplog):
        """H2: argv-injection defense — a host beginning with '-' would be
        parsed as an option flag by snmpget/ssh/ipmitool."""
        cfg = tmp_path / "c.yaml"
        cfg.write_text(
            "sources:\n"
            "  - name: S\n    type: snmp\n    host: '-oProxyCommand=evil'\n"
            "    community: public\n    version: '2c'\n    oids: []\n"
            "alerting: {}\nsettings: {}\nlogging: {}\n"
        )
        with caplog.at_level("WARNING"):
            sources, *_ = load_config(str(cfg))
        assert sources == []
        assert any("argv-injection" in rec.getMessage() for rec in caplog.records)

    def test_rejects_user_starting_with_dash(self, tmp_path, caplog):
        """A user field like '-oProxyCommand=...' would turn ssh user@host
        into an option flag + @host remainder → local RCE via ProxyCommand."""
        cfg = tmp_path / "c.yaml"
        cfg.write_text(
            "sources:\n"
            "  - name: S\n    type: ssh_sensors\n    host: 10.0.0.1\n"
            "    user: '-oProxyCommand=/bin/sh 1>&2'\n"
            "alerting: {}\nsettings: {}\nlogging: {}\n"
        )
        with caplog.at_level("WARNING"):
            sources, *_ = load_config(str(cfg))
        assert sources == []
        assert any("argv-injection" in rec.getMessage() for rec in caplog.records)

    def test_rejects_community_starting_with_dash(self, tmp_path, caplog):
        cfg = tmp_path / "c.yaml"
        cfg.write_text(
            "sources:\n"
            "  - name: S\n    type: snmp\n    host: 10.0.0.1\n"
            "    community: '-m ALL'\n    version: '2c'\n    oids: []\n"
            "alerting: {}\nsettings: {}\nlogging: {}\n"
        )
        with caplog.at_level("WARNING"):
            sources, *_ = load_config(str(cfg))
        assert sources == []

    def test_rejects_interface_starting_with_dash(self, tmp_path, caplog):
        cfg = tmp_path / "c.yaml"
        cfg.write_text(
            "sources:\n"
            "  - name: S\n    type: ipmi\n    host: 10.0.0.1\n"
            "    user: admin\n    password: pw\n    interface: '-foo'\n"
            "alerting: {}\nsettings: {}\nlogging: {}\n"
        )
        with caplog.at_level("WARNING"):
            sources, *_ = load_config(str(cfg))
        assert sources == []

    def test_accepts_normal_host_values(self, tmp_path):
        """Sanity check — the validator mustn't reject legitimate config."""
        cfg = tmp_path / "c.yaml"
        cfg.write_text(
            "sources:\n"
            "  - name: S\n    type: snmp\n    host: '10.0.0.1'\n"
            "    community: public\n    version: '2c'\n    oids:\n"
            "      - {name: T, oid: '1.2.3.4'}\n"
            "alerting: {}\nsettings: {}\nlogging: {}\n"
        )
        sources, *_ = load_config(str(cfg))
        assert len(sources) == 1

    def test_env_state_file_override_missing_does_not_crash(self, tmp_path):
        """Regression test: THERMAL_MONITOR_STATE_FILE env var is consumed
        in cli.py, not here, but loading config with state_file set in YAML
        should still round-trip."""
        cfg = tmp_path / "c.yaml"
        cfg.write_text(
            "sources:\n  - name: S\n    type: local_sensors\n"
            "alerting:\n  state_file: /tmp/custom_state.json\n"
            "settings: {}\nlogging: {}\n"
        )
        _, alerting, *_ = load_config(str(cfg))
        assert alerting["state_file"] == "/tmp/custom_state.json"

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
