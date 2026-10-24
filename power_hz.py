#!/usr/bin/python3
"""power_hz - lowest refresh rate, capped brightness and a TuneD powersave
profile on battery, for GNOME.

Runs as a systemd --user unit. Talks to D-Bus for the power state and the panel,
and switches the TuneD profile with tuned-adm. Never writes sysfs.
"""

import configparser
import json
import os
import signal
import subprocess
import sys
import time

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("GLibUnix", "2.0")
from gi.repository import Gio, GLib, GLibUnix

MUTTER = "org.gnome.Mutter.DisplayConfig"
MUTTER_PATH = "/org/gnome/Mutter/DisplayConfig"
UPOWER = "org.freedesktop.UPower"
UPOWER_PATH = "/org/freedesktop/UPower"
PROPS = "org.freedesktop.DBus.Properties"
POWER_SCHEMA = "org.gnome.settings-daemon.plugins.power"
TUNED = "/usr/bin/tuned-adm"
SUDO = "/usr/bin/sudo"
PPD_CONF = "/etc/tuned/ppd.conf"

STATE_HOME = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
STATE_FILE = os.path.join(STATE_HOME, "power-hz", "state.json")
CONFIG_FILE = os.path.join(CONFIG_HOME, "power-hz", "config.ini")

DEBOUNCE_SECONDS = 2
BRIGHTNESS_CAP = 70      # percent of max on battery; 100 disables the cap
BATTERY_PROFILE = "powersave"   # TuneD profile on battery; "none" disables


def log(message):
    print(f"power-hz: {message}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- pure policy

def panel_modes(monitors):
    """{connector: [(mode_id, width, height, rate, props)]}"""
    return {m[0][0]: [(md[0], md[1], md[2], md[3], md[6]) for md in m[1]]
            for m in monitors}


def pick_panel(modes):
    return next((c for c in modes if c.startswith(("eDP-", "LVDS-", "DSI-"))), None)


def current_mode(modes):
    return next((mid for mid, _w, _h, _r, props in modes if props.get("is-current")), None)


def mode_size(modes, mode_id):
    return next(((w, h) for mid, w, h, _r, _p in modes if mid == mode_id), None)


def lowest_rate_mode(modes, size):
    """Lowest refresh rate at this resolution, never a +vrr variant."""
    candidates = [(rate, mid) for mid, w, h, rate, _p in modes
                  if (w, h) == size and "+vrr" not in mid]
    return min(candidates)[1] if candidates else None


def cap_brightness(value, maximum, cap_percent=BRIGHTNESS_CAP):
    """Only ever dims: never raises a brightness the user chose below the cap."""
    return min(value, round(maximum * cap_percent / 100))


# ---------------------------------------------------------------------- daemon

class PowerHz:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.cap, self.battery_profile = self._load_config()
        self.tuned_ok = True
        self.state = self._load_state()
        self.system = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        self.session = self._session_bus()
        self.power_settings = Gio.Settings.new(POWER_SCHEMA)
        self.debounce_id = 0
        self.reassert_id = 0

    # -- settings and state

    def _load_config(self):
        parser = configparser.ConfigParser()
        try:
            parser.read(CONFIG_FILE)
            cap = int(parser.get("power-hz", "battery_brightness_cap",
                                  fallback=BRIGHTNESS_CAP))
            profile = parser.get("power-hz", "battery_power_profile",
                                 fallback=BATTERY_PROFILE).strip()
        except (ValueError, configparser.Error) as exc:
            log(f"ignoring bad {CONFIG_FILE}: {exc}")
            return BRIGHTNESS_CAP, BATTERY_PROFILE
        return cap, None if profile.lower() in ("", "none", "off") else profile

    def _load_state(self):
        try:
            with open(STATE_FILE) as handle:
                return json.load(handle)
        except (OSError, ValueError):
            return {}

    def _save_state(self):
        if self.dry_run:
            return
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, "w") as handle:
                json.dump(self.state, handle, indent=1)
        except OSError as exc:
            log(f"cannot save state: {exc}")

    # -- dbus plumbing

    @staticmethod
    def _call(bus, dest, path, iface, method, params=None):
        reply = bus.call_sync(dest, path, iface, method, params, None,
                              Gio.DBusCallFlags.NONE, 5000, None)
        return reply.unpack()

    def _property(self, bus, dest, path, iface, name):
        result = self._call(bus, dest, path, PROPS, "Get",
                            GLib.Variant("(ss)", (iface, name)))
        return result[0]

    def _session_bus(self):
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        for _ in range(20):  # mutter may still be starting
            try:
                self._call(bus, MUTTER, MUTTER_PATH, "org.freedesktop.DBus.Peer", "Ping")
                return bus
            except GLib.Error:
                time.sleep(0.5)
        log("Mutter DisplayConfig not available - GNOME session only, exiting")
        sys.exit(0)

    def on_battery(self):
        return bool(self._property(self.system, UPOWER, UPOWER_PATH, UPOWER, "OnBattery"))

    def read_display(self):
        return self._call(self.session, MUTTER, MUTTER_PATH, MUTTER, "GetCurrentState")

    def backlight(self):
        _serial, outputs = self._property(self.session, MUTTER, MUTTER_PATH, MUTTER, "Backlight")
        for output in outputs:
            if output.get("active"):
                return output["connector"], output["value"], output["max"]
        return None

    def apply_mode(self, display, connector, mode_id):
        serial, monitors, logical, props = display
        current = {}
        for mon in monitors:
            all_modes = panel_modes([mon])[mon[0][0]]
            current[mon[0][0]] = current_mode(all_modes) or (all_modes[0][0] if all_modes else None)
        groups = []
        for x, y, scale, transform, primary, members, _p in logical:
            subs = [(c[0], mode_id if c[0] == connector else current.get(c[0]), {})
                    for c in members]
            groups.append((x, y, scale, transform, primary, subs))
        if self.dry_run:
            log(f"dry-run: apply mode {mode_id} on {connector}")
            return
        params = GLib.Variant(
            "(uua(iiduba(ssa{sv}))a{sv})",
            (serial, 1, groups,
             {"layout-mode": GLib.Variant("u", int(props.get("layout-mode", 1)))}))
        self._call(self.session, MUTTER, MUTTER_PATH, MUTTER, "ApplyMonitorsConfig", params)

    def set_brightness(self, connector, value):
        serial, _outputs = self._property(self.session, MUTTER, MUTTER_PATH, MUTTER, "Backlight")
        if self.dry_run:
            log(f"dry-run: set backlight {value}")
            return
        self._call(self.session, MUTTER, MUTTER_PATH, MUTTER, "SetBacklight",
                   GLib.Variant("(usi)", (serial, connector, int(value))))

    def set_ambient(self, enabled):
        """GNOME's automatic brightness recomputes the level whenever the light
        changes, which overrides any cap within seconds. Park it while capped."""
        if self.dry_run:
            log(f"dry-run: ambient auto-brightness -> {enabled}")
            return
        if self.power_settings.get_boolean("ambient-enabled") != enabled:
            self.power_settings.set_boolean("ambient-enabled", enabled)
            log(f"ambient auto-brightness {'on' if enabled else 'off'}")

    # -- tuned profile

    def ac_profile(self, profile):
        """The AC-side profile for one TuneD picked on battery.

        tuned-ppd swaps balanced -> balanced-battery (and laptop-ac-powersave ->
        laptop-battery-powersave) on unplug, so the profile visible on battery
        is not the one to put back on AC. Reverse its own mapping."""
        parser = configparser.ConfigParser()
        try:
            parser.read(PPD_CONF)
            for key, battery_profile in parser.items("battery"):
                if battery_profile == profile:
                    return parser.get("profiles", key, fallback=profile)
        except (configparser.Error, OSError):
            pass
        return profile

    def tuned_active(self):
        try:
            result = subprocess.run([TUNED, "active"], capture_output=True, text=True)
        except OSError as exc:
            log(f"cannot run tuned-adm: {exc}")
            return None
        if result.returncode != 0:
            log(f"tuned-adm active failed: {result.stderr.strip()}")
            return None
        return result.stdout.rsplit(":", 1)[-1].strip() or None

    def set_tuned_profile(self, name):
        if self.dry_run:
            log(f"dry-run: tuned-adm profile {name}")
            return True
        if not self.tuned_ok:
            return False
        try:
            result = subprocess.run([SUDO, "-n", TUNED, "profile", name],
                                    capture_output=True, text=True)
        except OSError as exc:
            log(f"cannot run sudo: {exc}")
            self.tuned_ok = False
            return False
        if result.returncode != 0:
            log(f"tuned profile {name} failed: "
                f"{(result.stderr or result.stdout).strip()}")
            self.tuned_ok = False
            return False
        log(f"tuned profile -> {name}")
        return True

    def _sync_profile(self, battery):
        if not self.battery_profile:
            return
        applied = self.state.get("applied_profile")
        if battery:
            current = self.tuned_active()
            if current is None or current == self.battery_profile:
                return
            baseline = self.state.get("baseline_profile") or self.ac_profile(current)
            if self.set_tuned_profile(self.battery_profile):
                self.state.update(baseline_profile=baseline,
                                  applied_profile=self.battery_profile)
                log(f"battery: tuned {current} -> {self.battery_profile}")
            return
        if not applied:
            return
        current = self.tuned_active()
        if current is None:
            return
        if current == applied:
            baseline = self.state.get("baseline_profile")
            if baseline and baseline != current:
                if not self.set_tuned_profile(baseline):
                    return
                log(f"ac: tuned {current} -> {baseline}")
        else:
            log(f"ac: tuned profile is {current} (changed while on battery) - keeping it")
        self.state.update(baseline_profile=None, applied_profile=None)

    def _restore_profile(self):
        applied = self.state.get("applied_profile")
        if not applied:
            return
        current = self.tuned_active()
        if current != applied:
            return
        baseline = self.state.get("baseline_profile")
        if baseline and baseline != current:
            self.set_tuned_profile(baseline)
            log(f"stop: tuned {current} -> {baseline}")
        self.state.update(baseline_profile=None, applied_profile=None)

    # -- the decision

    def sync(self, restoring=False):
        display = self.read_display()
        modes = panel_modes(display[1])
        connector = pick_panel(modes)
        if not connector:
            log("no internal panel found, nothing to do")
            return

        modes = modes[connector]
        current = current_mode(modes)
        size = mode_size(modes, current)
        backlight = self.backlight()
        state = self.state
        value, maximum = (backlight[1], backlight[2]) if backlight else (None, None)

        if restoring:
            self._restore(display, connector, current, value)
            return

        battery = self.on_battery()
        applied_mode = state.get("applied_mode")
        entering = battery and applied_mode is None

        if battery:
            if entering:
                state["baseline_mode"] = current
                state["baseline_brightness"] = value
                state["applied_brightness"] = None
                if self.cap < 100:
                    was_on = self.power_settings.get_boolean("ambient-enabled")
                    if was_on:
                        self.set_ambient(False)
                    state["ambient_disabled"] = was_on
                if backlight:
                    capped = cap_brightness(value, maximum, self.cap)
                    if capped < value:
                        self.set_brightness(connector, capped)
                        state["applied_brightness"] = capped
            target = lowest_rate_mode(modes, size) or current
            if state.get("ambient_disabled"):
                self.set_ambient(False)   # restart on battery: keep it parked
            if target != current:
                self.apply_mode(display, connector, target)
            state["applied_mode"] = target
            if state.get("applied_brightness") is not None:
                self._schedule_reassert()
            log(f"battery: {current} -> {target}, brightness {value}"
                f" -> {state.get('applied_brightness') or value}")
            self._sync_profile(battery=True)
        else:
            if applied_mode and current == applied_mode:
                target = state.get("baseline_mode") or current
            else:
                target = current      # user changed it while unplugged: theirs wins
            if target != current:
                self.apply_mode(display, connector, target)
            restored = None
            applied_brightness = state.get("applied_brightness")
            saved = state.get("baseline_brightness")
            if applied_brightness is not None and saved is not None and saved != value:
                self.set_brightness(connector, saved)
                restored = saved
            if state.get("ambient_disabled"):
                self.set_ambient(True)
                state["ambient_disabled"] = False
            self._cancel_reassert()
            state.update(baseline_mode=target, applied_mode=None, applied_brightness=None,
                         baseline_brightness=restored if restored is not None else value)
            log(f"ac: {current} -> {target}, brightness {value}"
                f" -> {restored if restored is not None else value}")
            self._sync_profile(battery=False)

        self._save_state()

    def _restore(self, display, connector, current, value):
        baseline = self.state.get("baseline_mode")
        if baseline and current != baseline:
            self.apply_mode(display, connector, baseline)
            log(f"stop: restore mode {current} -> {baseline}")
        saved = self.state.get("baseline_brightness")
        if value is not None and self.state.get("applied_brightness") is not None and saved is not None:
            self.set_brightness(connector, saved)
            log(f"stop: restore brightness {value} -> {saved}")
        if self.state.get("ambient_disabled"):
            self.set_ambient(True)
            self.state["ambient_disabled"] = False
        self._restore_profile()
        self._cancel_reassert()
        self.state.update(applied_mode=None, applied_brightness=None)
        self._save_state()

    # -- the cap is written once, but gsd can still land its own value right
    # after we park the ambient sensor; lower it back once, never raise it

    def _schedule_reassert(self):
        self._cancel_reassert()
        self.reassert_id = GLib.timeout_add_seconds(DEBOUNCE_SECONDS, self._reassert_cap)

    def _cancel_reassert(self):
        if self.reassert_id:
            GLib.source_remove(self.reassert_id)
            self.reassert_id = 0

    def _reassert_cap(self):
        self.reassert_id = 0
        applied = self.state.get("applied_brightness")
        backlight = self.backlight()
        if applied is not None and backlight and backlight[1] > applied:
            self.set_brightness(backlight[0], applied)
            log(f"cap re-asserted: {backlight[1]} -> {applied}")
        return GLib.SOURCE_REMOVE

    # -- event loop

    def _on_properties_changed(self, _bus, _sender, _path, _iface, _signal, params):
        _iface, changed, _invalidated = params.unpack()
        if "OnBattery" in changed:
            self._schedule()

    def _schedule(self):
        if self.debounce_id:
            GLib.source_remove(self.debounce_id)
        self.debounce_id = GLib.timeout_add_seconds(DEBOUNCE_SECONDS, self._apply)

    def _apply(self):
        self.debounce_id = 0
        self.sync()
        return GLib.SOURCE_REMOVE

    def run(self):
        self.system.signal_subscribe(UPOWER, PROPS, "PropertiesChanged", UPOWER_PATH,
                                     None, Gio.DBusSignalFlags.NONE,
                                     self._on_properties_changed)
        try:
            self.sync()
        except GLib.Error as exc:
            log(f"cannot read power state ({exc.message}) - is UPower running?")
            sys.exit(1)
        loop = GLib.MainLoop()
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, lambda *_: self._stop(loop))
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, lambda *_: self._stop(loop))
        loop.run()

    def _stop(self, loop):
        try:
            self.sync(restoring=True)
        except GLib.Error as exc:
            log(f"restore failed: {exc}")
        loop.quit()
        return GLib.SOURCE_REMOVE


def main():
    PowerHz(dry_run="--dry-run" in sys.argv).run()


if __name__ == "__main__":
    main()
