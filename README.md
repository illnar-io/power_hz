# power_hz

[![tests](https://github.com/illnar-io/power_hz/actions/workflows/tests.yml/badge.svg)](https://github.com/illnar-io/power_hz/actions/workflows/tests.yml)

Drops the laptop panel to its lowest refresh rate when you unplug, and puts it
back when you plug in. Optionally caps brightness on battery, and switches the
TuneD profile to a powersave variant.

Verified on Fedora 45 Workstation, GNOME 51 (Mutter 51), AMD Radeon iGPU,
2560x1600 @ 120 / 90 / 60 Hz panel with VRR.

**This is a GNOME/Wayland tool.** It talks to Mutter's `DisplayConfig` D-Bus API.
X11, KDE, wlroots, COSMIC and Hyprland need a different backend and are not
implemented yet.

## Why this exists

Every other power tool owns CPU and GPU knobs, and none of them touch the panel:

| Project | Refresh rate | Brightness | CPU/GPU |
|---|---|---|---|
| power-profiles-daemon | no (deliberately) | no | 3 profiles |
| tuned / tuned-ppd | panel ABM only (`[video]`) | no | yes |
| TLP | no | no | yes |
| auto-cpufreq | no | no | yes |
| GNOME (`gsd-power`) | no | idle-dim, low-battery profile | via ppd |

So `power_hz` owns the display mode (and optionally brightness), and delegates
CPU/GPU to `tuned-ppd` by switching its profile via `tuned-adm` on battery.
It never writes `/sys` and never calls `powerprofilesctl`.

## Install

```sh
./install.sh
```

That symlinks `power_hz.py` to `~/.local/bin/power-hz`, installs the
`--user` unit, enables and starts it. It also adds a sudoers rule so the
daemon can run `tuned-adm profile` for the configured battery profile
(and the AC restore profiles). No other root access, nothing written to `/sys`.

```sh
systemctl --user status power-hz
journalctl --user -u power-hz -f
./install.sh                    # idempotent
systemctl --user disable --now power-hz   # uninstall (restores the display first)
```

## Behaviour

| State | Refresh rate | Brightness | TuneD profile |
|---|---|---|---|
| On battery | lowest rate at the current resolution, VRR off | capped at 70 % of max, only if currently higher | `powersave` (or configured `battery_power_profile`) |
| On AC | the mode you had before unplugging (any rate, VRR included) | restored, if the daemon dimmed it | restored to the pre-unplug profile |
| `systemctl --user stop` | same restore | same restore | same restore |
| Daemon killed and restarted on battery | baseline kept in `~/.local/state/power-hz/state.json` | same | baseline kept |

* Applies 2 s after the power event, so a flapping USB-C cable causes one change.
* Never raises brightness, never forces max refresh on AC, never touches an
  external monitor.
* If you change the mode or brightness yourself while unplugged, your choice
  wins on re-plug.
* If you change the TuneD profile yourself while unplugged, your choice
  wins on re-plug.
* On battery with a cap configured (below 100), GNOME's *Automatic Screen
  Brightness* is switched off, and back on when you plug in or stop the daemon.
  Without that, the ambient-light sensor recomputes the level within seconds and
  overrides the cap. It is the only setting the daemon changes outside the
  display; with `battery_brightness_cap = 100` it is never touched.

## Configuration

Optional `~/.config/power-hz/config.ini`:

```ini
[power-hz]
battery_brightness_cap = 70
battery_power_profile = powersave
```

* `battery_brightness_cap`: percent of max brightness on battery. `100` disables the
  cap (and with it the auto-brightness switch). Bad values fall back to 70.
* `battery_power_profile`: TuneD profile to activate on battery. Default is
  `powersave` which maps to `laptop-battery-powersave` on battery (EPP=power,
  boost=0, platform low-power, panel ABM=3). Set to `none` to disable profile
  switching. The profile is switched via `tuned-adm` which requires root; the
  install script adds a restricted `NOPASSWD` sudoers rule for the configured
  profile(s).

## Panel

The verified panel is a BOE **NE140QDM-NX1/NX2** (the EDID reports `NX2`),
2560x1600, 14", on eDP. Its real range is **48–120 Hz, continuous**, but the
kernel only exposes three discrete modes:

| Mode | Pixel clock | Source |
|---|---|---|
| 2560x1600 @ 120.000 Hz | 538.56 MHz | EDID detailed timing |
| 2560x1600 @ 90.000 Hz | 403.92 MHz | EDID detailed timing |
| 2560x1600 @ 60.002 Hz | 269.29 MHz | DisplayID 1.3 |

Mutter reports `min-refresh-rate: 48` for VRR, which is where Settings' "48–120
Hz" comes from. 48 Hz is the panel's floor, not a mode: the daemon's policy
already picks the lowest available rate, so if the kernel ever offers 48, it is
selected with no code change. To offer it, add a custom mode (root, reboot):

```
# /etc/kernel/cmdline or GRUB_CMDLINE_LINUX, then regenerate the boot entry
video=eDP-1:2560x1600MR@48
```

DRM's cmdline parser adds such a mode even when the EDID does not list it. Verify
it locks before trusting it: a panel that refuses a non-standard timing can boot
to a black internal screen. Drop the parameter and rebuild the boot entry to
recover.

`+vrr` buys nothing at an equal rate: +0.36 W against fixed 60 Hz, inside the
±0.4 W run-to-run drift. The daemon stays on the fixed lowest rate because it
is predictable, and at an equal rate VRR is free but not better.

Measured on this panel with the screen held awake (energy integral, mode and
DPMS checked every sample, brightness pinned at 70 %):

| Mode | Draw |
|---|---|
| 2560x1600 @ 60.002 | 7.4 – 10.0 W |
| 2560x1600 @ 60.002+vrr | 10.4 W |
| 2560x1600 @ 120.000+vrr | 10.1 – 10.2 W (two runs, 0.06 W apart) |

The 120 Hz penalty was **1–3 W on an idle desktop**, where VRR can drop the
panel to its 48 Hz floor. A single busier run read 4.8 W, but that machine's
background load moved by the same amount between segments, so it is not a
number to quote. Treat the saving as "a couple of watts, more when something on
screen is actually animating" — which is exactly the case a fixed 60 Hz cap
removes.

## Tests

```sh
python3 -m pytest -q tests
```

The policy and the power-state machine are tested against a real
`GetCurrentState` capture from a 120/90/60 Hz VRR panel
(`tests/fixtures/display_state.json`), with a fake panel and settings store, so no
hardware is needed. CI runs the suite on every push.

## Known limits

* GNOME's idle-dim still runs on battery. If the daemon caps while the screen is
  already dimmed, waking the screen can bring back the pre-dim level; the next
  plug/unplug re-applies the cap.
* VRR is switched by choosing the `+vrr` mode variant; there is no separate
  toggle in Mutter's API.
* The gain depends entirely on the panel: the LCD controller draws power per
  refresh, so 120→60 Hz helps on a high-refresh panel and does nothing on a
  60 Hz one. This is not a "2x battery" feature; measure it before believing it.

## History

The previous `power_manager.py` (955 lines, root systemd service, `xrandr`,
GPU/PSR/CABC/theme/systemctl writes) never ran: it had a syntax error and
mangled regexes. See `AUDIT.md` for the full write-up; everything it attempted is
either deleted here or already owned by tuned-ppd/GNOME.
