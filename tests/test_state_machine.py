"""Drive the real sync() through a fake panel and a fake settings store.

The fixture is the same real mutter GetCurrentState capture the policy tests use,
so the modes and connectors are the ones the daemon sees on the laptop.
"""

import copy
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import power_hz

FIXTURE = json.loads((pathlib.Path(__file__).parent / "fixtures" / "display_state.json").read_text())
MAX = 65535
BASELINE = "2560x1600@120.000+vrr"
LOW = "2560x1600@60.002"
CAP = 45874  # 70 % of MAX


class FakeSettings:
    def __init__(self, enabled):
        self.enabled = enabled

    def get_boolean(self, _key):
        return self.enabled

    def set_boolean(self, _key, value):
        self.enabled = value


class FakePanel:
    def __init__(self, current=BASELINE, value=MAX):
        self.current = current
        self.value = value
        self.battery = False
        self.modes_applied = []
        self.brightness_set = []

    def display(self):
        monitors = copy.deepcopy(FIXTURE["monitors"])
        for mode in monitors[0][1]:
            mode[6].pop("is-current", None)
            if mode[0] == self.current:
                mode[6]["is-current"] = True
        return (FIXTURE["serial"], monitors, FIXTURE["logical"], FIXTURE["props"])

    def apply_mode(self, _display, _connector, mode_id):
        self.current = mode_id
        self.modes_applied.append(mode_id)

    def set_brightness(self, _connector, value):
        self.value = value
        self.brightness_set.append(value)


def make(panel, ambient=True, cap=70, state=None, battery_profile="powersave"):
    daemon = power_hz.PowerHz.__new__(power_hz.PowerHz)
    daemon.dry_run = False
    daemon.cap = cap
    daemon.battery_profile = battery_profile
    daemon.tuned_ok = True
    daemon.state = dict(state or {})
    daemon.debounce_id = 0
    daemon.reassert_id = 0
    daemon.power_settings = FakeSettings(ambient)
    daemon.read_display = panel.display
    daemon.backlight = lambda: ("eDP-1", panel.value, MAX)
    daemon.on_battery = lambda: panel.battery
    daemon.apply_mode = panel.apply_mode
    daemon.set_brightness = panel.set_brightness
    daemon.saves = 0
    daemon.reasserts = 0
    daemon._save_state = lambda: setattr(daemon, "saves", daemon.saves + 1)
    daemon._schedule_reassert = lambda: setattr(daemon, "reasserts", daemon.reasserts + 1)
    daemon.tuned_active = lambda: "throughput-performance"
    daemon.set_tuned_profile = lambda name: setattr(daemon, "tuned_set", name) or True
    daemon.tuned_set = None
    return daemon


def battery(panel):
    panel.battery = True
    return make(panel)


def test_unplug_drops_to_lowest_rate_caps_and_parks_ambient():
    panel = FakePanel()
    daemon = battery(panel)
    daemon.sync()

    assert panel.current == LOW
    assert panel.brightness_set == [CAP]
    assert daemon.power_settings.enabled is False
    assert daemon.state["baseline_mode"] == BASELINE
    assert daemon.state["baseline_brightness"] == MAX
    assert daemon.state["applied_brightness"] == CAP
    assert daemon.state["ambient_disabled"] is True
    assert daemon.reasserts == 1
    assert daemon.saves == 1


def test_battery_never_raises_a_brightness_below_the_cap():
    panel = FakePanel(value=30000)
    daemon = battery(panel)
    daemon.sync()

    assert panel.brightness_set == []
    assert panel.value == 30000
    assert daemon.state["applied_brightness"] is None
    assert daemon.reasserts == 0


def test_replug_restores_mode_brightness_and_ambient():
    panel = FakePanel()
    daemon = battery(panel)
    daemon.sync()

    panel.battery = False
    daemon.sync()

    assert panel.current == BASELINE
    assert panel.value == MAX
    assert panel.modes_applied == [LOW, BASELINE]
    assert daemon.power_settings.enabled is True
    assert daemon.state["applied_mode"] is None
    assert daemon.state["applied_brightness"] is None


def test_mode_chosen_while_unplugged_wins_on_replug():
    panel = FakePanel()
    daemon = battery(panel)
    daemon.sync()

    panel.current = "2560x1600@90.000"  # user picked it in Settings
    panel.battery = False
    daemon.sync()

    assert panel.current == "2560x1600@90.000"
    assert panel.modes_applied == [LOW]
    assert daemon.state["baseline_mode"] == "2560x1600@90.000"


def test_restart_on_battery_keeps_baseline_and_reparks_ambient():
    state = {
        "baseline_mode": BASELINE,
        "baseline_brightness": MAX,
        "applied_mode": LOW,
        "applied_brightness": CAP,
        "ambient_disabled": True,
    }
    panel = FakePanel(current=LOW, value=CAP)
    panel.battery = True
    daemon = make(panel, ambient=True, state=state)  # user turned ambient back on
    daemon.sync()

    assert panel.current == LOW
    assert panel.modes_applied == []
    assert panel.brightness_set == []
    assert daemon.power_settings.enabled is False
    assert daemon.state["baseline_mode"] == BASELINE
    assert daemon.reasserts == 1


def test_battery_already_at_the_lowest_rate_does_not_reapply_it():
    panel = FakePanel(current=LOW)
    daemon = battery(panel)
    daemon.sync()

    assert panel.modes_applied == []
    assert daemon.state["applied_mode"] == LOW
    assert daemon.state["baseline_mode"] == LOW


def test_cap_100_leaves_ambient_and_brightness_alone():
    panel = FakePanel()
    daemon = battery(panel)
    daemon.cap = 100
    daemon.sync()

    assert daemon.power_settings.enabled is True
    assert panel.brightness_set == []
    assert daemon.state.get("applied_brightness") is None
    assert "ambient_disabled" not in daemon.state


def test_stop_restores_what_the_daemon_changed():
    panel = FakePanel()
    daemon = battery(panel)
    daemon.sync()

    daemon.sync(restoring=True)

    assert panel.current == BASELINE
    assert panel.value == MAX
    assert daemon.power_settings.enabled is True
    assert daemon.state["applied_mode"] is None
    assert daemon.state["applied_brightness"] is None


def test_reassert_only_ever_lowers():
    panel = FakePanel(value=MAX)
    panel.battery = True
    daemon = make(panel)
    daemon.state["applied_brightness"] = CAP

    daemon._reassert_cap()
    assert panel.value == CAP
    assert panel.brightness_set == [CAP]

    panel.value = 30000
    daemon._reassert_cap()
    assert panel.brightness_set == [CAP]  # below the cap: left alone


def test_battery_switches_tuned_profile():
    panel = FakePanel()
    daemon = battery(panel)
    daemon.sync()

    assert daemon.tuned_set == "powersave"
    # Current mock returns throughput-performance which has no battery mapping
    assert daemon.state["baseline_profile"] == "throughput-performance"
    assert daemon.state["applied_profile"] == "powersave"


def test_ac_restores_tuned_profile():
    panel = FakePanel()
    daemon = battery(panel)
    daemon.sync()

    panel.battery = False
    # On AC, the current profile is still the one we set (powersave)
    daemon.tuned_active = lambda: "powersave"
    daemon.sync()

    # Should restore the baseline (throughput-performance from our mock)
    assert daemon.tuned_set == "throughput-performance"
    assert daemon.state["applied_profile"] is None
    assert daemon.state["baseline_profile"] is None


def test_user_profile_change_on_battery_wins():
    panel = FakePanel()
    daemon = battery(panel)
    daemon.sync()

    # User changes profile while on battery
    daemon.tuned_active = lambda: "performance"
    panel.battery = False
    daemon.sync()

    # Daemon should keep user's choice, not restore baseline
    assert daemon.tuned_set == "powersave"  # unchanged from first sync
    # On AC, we don't override if user changed it


def test_battery_profile_none_disables_switching():
    panel = FakePanel()
    daemon = battery(panel)
    daemon.battery_profile = None
    daemon.sync()

    assert daemon.tuned_set is None
    assert "baseline_profile" not in daemon.state
