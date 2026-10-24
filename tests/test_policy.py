"""Policy tests. The display fixture is a real GetCurrentState from mutter 51
on a 2560x1600 @ 120/90/60 panel with VRR variants."""

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import power_hz

FIXTURE = json.loads((pathlib.Path(__file__).parent / "fixtures" / "display_state.json").read_text())


def modes():
    return power_hz.panel_modes(FIXTURE["monitors"])["eDP-1"]


def test_reads_current_mode_from_real_state():
    assert power_hz.current_mode(modes()) == "2560x1600@120.000+vrr"
    assert power_hz.pick_panel(power_hz.panel_modes(FIXTURE["monitors"])) == "eDP-1"


def test_battery_mode_is_lowest_rate_without_vrr():
    assert power_hz.mode_size(modes(), "2560x1600@120.000+vrr") == (2560, 1600)
    assert power_hz.lowest_rate_mode(modes(), (2560, 1600)) == "2560x1600@60.002"


def test_cap_only_ever_dims():
    assert power_hz.cap_brightness(65535, 65535, 70) == 45874
    assert power_hz.cap_brightness(30000, 65535, 70) == 30000
    assert power_hz.cap_brightness(65535, 65535, 100) == 65535  # cap disabled
