from __future__ import annotations
from typing import Dict
from thermal_monitor.sources.local_sensors import LocalSensorsSource
from thermal_monitor.sources.ssh_sensors import SSHSensorsSource
from thermal_monitor.sources.ipmi import IPMISource
from thermal_monitor.sources.redfish import RedfishSource
from thermal_monitor.sources.snmp import SNMPSource

SOURCE_TYPES: Dict[str, type] = {
    "local_sensors": LocalSensorsSource,
    "ssh_sensors":   SSHSensorsSource,
    "ipmi":          IPMISource,
    "redfish":       RedfishSource,
    "snmp":          SNMPSource,
}
