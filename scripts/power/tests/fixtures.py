"""Explicitly fictional replay inputs, never observations of a live device."""
from eligibility import prepare_snapshot, BASELINE_TUNABLES


def fixture_snapshot():
    value = prepare_snapshot()
    value["scope"] = "synthetic-fixture"
    value["boot_id"] = "b" * 32
    value["board"] = {"family": "cardputerzero", "hardware_id": "0x06", "overlay": "cardputerzero-v5",
                      "model": "fixture-cardputerzero", "compatible": ["raspberrypi,0-compute-module", "brcm,bcm2837"],
                      "controller_firmware": "fixture-controller"}
    value["policy"]["administrator_override"] = "absent"
    value["policy"]["generated_override"] = "absent"
    value["cpu"].update(policy_path="/sys/devices/system/cpu/cpufreq/policy9", related_cpus=[0, 1, 2, 3],
                        driver="cpufreq-dt", governor="ondemand", min_khz=600000, max_khz=1000000,
                        tunables=dict(BASELINE_TUNABLES))
    controller = "/sys/devices/platform/soc/fixture-usb-controller"
    root = controller + "/usb9"
    hub = root + "/9-1"
    leaf = hub + "/9-1.1"
    def node(path, parent, kind, driver, devpath, control="auto", ids=("none", "none")):
        return {"path": path, "parent": parent, "kind": kind, "driver": driver, "devpath": devpath,
                "control": control, "delay_ms": "2000", "vendor_id": ids[0], "product_id": ids[1]}
    value["usb"] = {"mode": "host", "controller_path": controller, "controller_driver": "dwc2", "nodes": [
        node(root, controller, "root", "usb", "0", ids=("1d6b", "0002")),
        node(hub, root, "hub", "hub", "1", ids=("1a40", "0101")),
        node(leaf, hub, "device", "r8152-cfgselector", "1.1", "on", ("0fe6", "9900")),
        node(leaf + "/9-1.1:1.0", leaf, "interface", "r8152", "1.1"),
    ]}
    return value
