# power_hz — repository audit

Audit of `illnar-io/power_hz` @ `b809183` (single commit), done on Fedora 45 Workstation, Wayland session, Python 3.15.

**Status: the file does not compile. No code was changed by this audit.**

Suggested fix order for the next agent: **F1 → F2 → F8 → F4 → F6 → F7 → F3 → F5**, then F16–F17 (deletions), then F18 (one test + CI). F1 and F2 are prerequisites for seeing any other behaviour; F6 and F7 are the ones that damage a real machine.

---

## What this repo does

A single 955-line Python daemon (`power_manager.py`) that claims to manage laptop power on Fedora: it detects the panel and its refresh rates, picks a power mode from battery level/consumption, then changes refresh rate, brightness, DPMS, GPU clocks, power-profiles-daemon profile, GNOME theme, and stops some user services. README documents install as a root systemd service.

**Load assumed:** one laptop, one user session, one root service. Roughly one sysfs/`xrandr` write per 30–60 s, two threads, no network, no database, no concurrent users. This is not a server — so "scale" findings here mean *state races and work done twice on one machine*, not throughput.

---

## Must fix

### F1. The script cannot run at all — four broken string literals
`power_manager.py:302, 502, 523, 876`

- **What this is:** `modes.split('` then a real newline then `')`. Same in `_get_active_display`, in the ACPI parse, and in the metrics `f.write`. A backslash-stripping step mangled the file before the first commit.
- **Problem:** `python3 -m py_compile power_manager.py` → `SyntaxError: unterminated string literal (detected at line 302)`. Zero lines of this tool have ever executed. The single commit is untested by construction.
- **Fix:** put `\n` back in those four places. Two minutes. Do it before anything else so the rest of the audit becomes executable.
- **If we skip it:** there is no product. Every other finding is theory.

### F2. Every regex lost its backslashes — detection always returns nothing, and two of them crash startup
`power_manager.py:234, 266, 304, 335, 527, 540, 895`

- **What this is:** the intended `(\d+\.?\d*)` became `(d+.?d*)`, `\s` became `s`, `\*` became `*`, `\.` became `.`.
- **Problem:** concrete cases on this machine: `re.findall(r'(d+.?d*)Hz', '1920x1080@60.000Hz')` → `[]` (no letter `d` in the output), so `available_rates` is always empty and every rate falls back to 60. `re.search(r'(d+)(?=%)', 'Battery 0: Discharging, 87%')` → no match, so battery level is **always 100**. Worse, `_detect_with_xrandr` (line 266) raises `re.error: nothing to repeat at position 14`, which is *not* in the caught tuple `(TimeoutExpired, CalledProcessError, FileNotFoundError)` — it escapes `__init__` and kills the process. Same for line 895. So after fixing F1, the program crashes at startup.
- **Fix:** restore the escapes; widen those `except` handlers to `except Exception` (the code already does this everywhere else).
- **If we skip it:** fixing only the syntax gives you a daemon that reports 100% battery forever and dies on the first `xrandr` call.

### F3. The recommended install makes the whole tool a silent no-op
`README.md:72-94`, `power_manager.py:625, 631-661, 786-805`

- **What this is:** the README's unit is `User=root`, `WantedBy=multi-user.target`, `After=graphical-session.target`, no `DISPLAY`, no `XDG_RUNTIME_DIR`, no `DBUS_SESSION_BUS_ADDRESS`.
- **Problem:** three concrete failures. (a) `graphical-session.target` is a *user* target; a system unit ordered after it is ordered after nothing. (b) As root with no `DISPLAY`, `xrandr` prints "Can't open display" and exits non-zero — caught, logged at DEBUG, invisible. (c) `gsettings set` as root writes `/root/.config/dconf`, so the "OLED dark theme" changes nothing for the logged-in user. (d) `powerprofilesctl` is not even installed on this Fedora 45 box (`rpm -q --whatprovides powerprofilesctl` → nothing), so `set_performance_mode` throws `FileNotFoundError` into the same silent DEBUG catch. Net effect: install it, reboot, nothing changes, logs look fine.
- **Fix:** split it the way every comparable project does (see comparison): root part touches `/sys` only; session part (`xrandr`, `gsettings`, `xset`) runs as a `--user` unit inside the desktop session. Smallest version: make the whole thing a user service and drop every root-only write.
- **If we skip it:** the documented install path is cosmetic, and the user blames Fedora.

### F4. Mode is chosen once and never re-checked while on battery
`power_manager.py:902-936`

- **What this is:** the loop sleeps 60 s and re-applies **only when `get_power_status()` flips**.
- **Problem:** the headline feature in the README ("automatic mode switching based on changing conditions", "critical <20%") does not exist. Discharge 95% → 8% over two hours and nothing happens; you stay in standard mode with no ultra savings until you unplug/replug the charger. The `while True` loop is a cable-change watcher, not a monitor.
- **Fix:** every 60 s, recompute mode and re-apply only if it changed. Three lines; `apply_enhanced_battery_settings` is already idempotent-ish (after fixing F6 and F7).
- **If we skip it:** the only reason to run a daemon at all is gone — a udev rule on `power_supply` would do the same job with less code.

### F5. Nothing stops two copies from fighting over the same sysfs files
`power_manager.py:902-936`, `README.md:96-108`

- **What this is:** no PID file, no lock, no D-Bus name ownership. The README tells users to run it by hand *and* install a service.
- **Problem:** start the service, then `python3 power_manager.py` to debug → two processes each writing `gt_boost_freq_mhz` (F7 compounds twice as fast), each fighting over brightness and refresh rate. Compare: power-profiles-daemon is a D-Bus singleton, TLP is a oneshot with a lock, tuned holds a daemon lock. This project has none.
- **Fix:** one `fcntl.flock` on `/run/power-manager.lock`, exit if held. Five lines.
- **If we skip it:** non-reproducible bug reports ("sometimes my screen flickers"), which is exactly what a power daemon must never produce.

### F6. Brightness is overwritten and never given back
`power_manager.py:679, 703-710, 844`

- **What this is:** `brightnessctl set 40%` / `20%` / `100%`, absolute, at every apply.
- **Problem:** plug in → your 15% late-night brightness jumps to 100%. Unplug → jumps to 40%. The user's chosen level is destroyed with no restore, and there is no "already at target" check, so the daemon yanks the slider every time the cable moves. Every comparable project leaves brightness to the desktop precisely because of this.
- **Fix:** delete these calls (the smallest complete change — GNOME/KDE already manage brightness and DPMS), or save the value once and restore on exit.
- **If we skip it:** the tool is actively hostile on the one setting users touch most.

### F7. GPU boost is multiplied down on every run and never restored
`power_manager.py:749-773, 833-862`

- **What this is:** ultra mode reads `gt_boost_freq_mhz`, writes `int(current * 0.8)`. The AC path restores only the **AMD** file, never the Intel one.
- **Problem:** 1000 → 800 → 640 → 512 MHz. Ten cable cycles and the GPU is crippled for the rest of the boot, including on AC. There is no saved baseline anywhere. Also both paths are hardcoded to `card0`, so on an AMD laptop with an Intel iGPU (or Intel with external GPU) you touch the wrong card or nothing.
- **Fix:** read the file once at startup into `self._gt_boost_base` and always compute from the base; write the base back on AC. Delete the whole block if you don't want to own it.
- **If we skip it:** a "battery saver" that silently halves a laptop's GPU until reboot. This is the worst bug in the file after F1.

### F8. Four of the five documented settings do nothing, and one of them turns `0` into "on"
`power_manager.py:102-106, 132-166`, `README.md:110-131`

- **What this is:** `bool(os.environ["DISABLE_VRR"])` — any non-empty string is `True`. And `grep` for the readers: `disable_vrr`, `force_refresh_rate`, `enable_oled_optimizations`, `debug_mode` appear **only** in `_load_config`. Nothing consumes them.
- **Problem:** `DISABLE_VRR=0` enables VRR (inverted), and `DEBUG=1` does not change logging at all because `Logger.__init__` hardcodes `setLevel(DEBUG)` + console `INFO`. `FORCE_REFRESH_RATE` is documented as "force specific rate" and is never passed to `set_display_refresh_rate`. Also, the file parser runs *after* env, so `/etc/power-manager.conf` silently overrides env — the opposite of what the README implies.
- **Fix:** parse with a small `_env_bool` (`in ("1","true","yes")`), then either wire the four settings into the code or delete them from `SystemConfig` and the README. Deleting is cheaper and honest.
- **If we skip it:** a config surface that lies. Users tune it, nothing happens, they file bugs you can't reproduce.

---

## Should fix

### F9. The metrics log records stale numbers forever and grows without limit
`power_manager.py:864-888`

- **What this is:** a thread appends one CSV line every 30 s to `/var/log/power-manager-metrics.log`.
- **Problem:** it reads `self.battery_status`, which is only refreshed in `intelligent_battery_mode_selection` — i.e. at boot and on AC→battery. While on AC the log says `battery:100%,power:0.0W` forever; on battery it freezes at whatever the first reading was. `_get_current_refresh_rate` hits the broken regex (line 895) and returns `None` every time, so the line is literally `refresh:NoneHz`. And nothing rotates the file: ~2,880 lines/day, ~1 MB/month, root-owned, permanent.
- **Fix:** call `get_enhanced_battery_status()` inside the loop; delete the metrics thread entirely if nobody reads the file (that is the lean answer — it is write-only data with no consumer in the repo).
- **If we skip it:** a growing root-owned log full of lies, plus a thread that exists only to produce it.

### F10. Every hardware path is hardcoded to `card0` / `eDP-1`
`power_manager.py:387, 406, 417, 652, 753, 766, 781`

- **What this is:** `"/sys/class/drm/card0/card0-eDP-1/vrr_enabled"`, `i915_edp_psr_debug`, `card0/gt_boost_freq_mhz`, `card0-eDP-1/content_protection`.
- **Problem:** on AMD-only laptops the card is `card1`; on laptops with an external monitor the connector is `DP-1`/`HDMI-A-1`; on hybrid graphics `card0` is often the dGPU. So VRR, PSR and content-protection writes are silently skipped on most machines — the `os.path.exists` guard turns a wrong path into a no-op instead of an error. Related: `_get_active_display` returns the *first* connected output, so with a monitor attached the refresh rate is set on the external screen, not the laptop panel.
- **Fix:** `glob` the connector and match on the panel: `glob("/sys/class/drm/card*-eDP-*/vrr_enabled")`. Same for `card*/device/power_dpm_force_performance_level`.
- **If we skip it:** the OLED/VRR/PSR features only work on the author's exact machine, which is the definition of an untested feature.

### F11. "Disable non-essential services" cannot work, and would be permanent if it did
`power_manager.py:822-831`

- **What this is:** `systemctl --user stop evolution-data-server tracker-miner-fs`, called from a root system service in ultra mode.
- **Problem:** as root with no `XDG_RUNTIME_DIR`, `systemctl --user` fails with "Failed to connect to bus: No medium found" — caught, logged, ignored. So the feature is a no-op. If you "fix" it by running as the user, it stops the user's mail and file indexers and **never starts them again** — the mail client silently stops syncing for the rest of the session. That is a data-freshness bug sold as a power feature.
- **Fix:** delete the function and its call. Turning off indexing is not worth 9 lines and a support ticket.
- **If we skip it:** harmless no-op today, landmine tomorrow.

### F12. `Logger` is built at import time, before any permission check, and the log dir is created later
`power_manager.py:38-63, 173-174`

- **What this is:** `logger = Logger()` at module level opens `/var/log/power-manager.log` with a `FileHandler`. `os.makedirs(os.path.dirname(LOG_FILE))` runs inside `_initialize_system`, i.e. *after*.
- **Problem:** `/var/log` is not writable by users, so the README's first usage example (`python3 power_manager.py`, line 101) dies with an uncaught `PermissionError: [Errno 13]` traceback instead of a friendly "run me with sudo". The `makedirs` calls are dead code because `/var/log` always exists. And the ordering proves the author never ran it as a non-root user.
- **Fix:** `logging.basicConfig(level=..., handlers=[...])` inside `main()` after a `if os.geteuid() != 0` check with a clear message; drop the `Logger` class and `LogLevel` enum (see F17). Delete the two `makedirs` lines.
- **If we skip it:** the very first command in the README produces a Python traceback.

### F13. The low-refresh-rate probe can never succeed
`power_manager.py:370-384`

- **What this is:** `xrandr --output <d> --mode auto --rate <n> --dryrun`, five times, at startup.
- **Problem:** verified on this box: `xrandr: cannot find mode auto`, exit 1. `--mode` takes a mode *name*; there is no `auto` mode (that's `--auto`). So all five rates report "not supported" and `low_power_rates` is always `[]` — the entire "24/30/48/50 Hz" feature in the README rests on a call that always fails. It also burns 5 × up to 3 s of timeout at every start, on a path that a Wayland compositor can't honour anyway.
- **Fix:** derive candidate rates from the mode list you already parsed (`available_rates`) instead of probing; or delete `_detect_low_refresh_rates` and use fixed 60/30 as fallbacks.
- **If we skip it:** a "test" that always answers no, plus up to 15 s of startup lag.

### F14. The DRM parser reads a file format that doesn't contain what it searches for, and reads one file twice
`power_manager.py:295-311, 418-423`

- **What this is:** it opens `/sys/class/drm/*-*/modes` and regexes `(\d+)x(\d+)@(\d+…)`. Separately, FBC does `"enabled" in f.read() or "supported" in f.read()`.
- **Problem:** `modes` contains one resolution per line (`1920x1080`), no `@Hz` — so that branch can never find a rate, even with the regex fixed. It also `break`s out of the connector loop after the first connector, so a docked laptop reads the dock's EDID. In FBC, the first `f.read()` consumes the file, so the second read returns `""` and FBC is detected only if the literal word "enabled" is present — the "supported" half is dead.
- **Fix:** read the file once into a variable and test both strings; drop the impossible `@Hz` parse and take rates from EDID/`xrandr` only.
- **If we skip it:** two features that look implemented and are not.

### F15. On AC it forces max refresh rate, 100% brightness, and never undoes VRR settings
`power_manager.py:833-862, 631-661`

- **What this is:** `apply_ac_settings` applies `optimal_ac_rate = max(rates)` (240 Hz on a 240 Hz panel), `100%` brightness, `xset dpms 600 900 1200`.
- **Problem:** plugging in a charger should not set the panel to its maximum refresh and full brightness permanently — that is the *opposite* of what most users want (fan noise, heat, eye strain). There is no config knob for it; `FORCE_REFRESH_RATE` exists but is unused (F8). Also VRR is written as `"on"` on AC and `"off"` on battery regardless of `disable_vrr`, and never restored to the user's original `sysfs` value on exit.
- **Fix:** on AC, keep the user's current rate/brightness and only relax the power-profile and DPMS; honour `force_refresh_rate` if set.
- **If we skip it:** a power tool that makes the laptop hotter and louder the moment you plug it in.

---

## Nice to have

### F16. Three near-identical detection functions that overwrite each other
`power_manager.py:225-321`

- **What this is:** `_detect_with_gnome_randr`, `_detect_with_xrandr`, `_detect_with_drm` — same shape (run, regex, assign `min_rate`/`max_rate`), called in sequence, each overwriting the previous result wholesale.
- **Problem:** last writer wins, so a partial result from a later method erases a good one from an earlier method. Three copies of the same 30 lines also means three places to fix F2, and one of them (`variable_backlight`, line 273) just tests whether `xrandr --verbose` printed `BACKLIGHT:` — which it always does — so that field is always `True` and nothing reads it.
- **Fix:** one function taking a list of probes, merging (`max(max_rate, r)`) instead of replacing; delete `variable_backlight`.
- **If we skip it:** fixing the regexes three times, and a detection layer whose result depends on call order.

### F17. Dead code and dead configuration
`power_manager.py:22, 25, 32-63, 65-69, 89, 102-106`

- **What this is:** `import psutil` (never used), `typing.Dict/Tuple/Any` (unused), `PowerMode` enum (never referenced), `LogLevel` + `Logger` (30 lines wrapping `logging`), `SystemConfig` fields never read, `PanelType.LED` set but never checked.
- **Problem:** each one is a thing a reader must check and dismiss. `psutil` in particular is a hard `pip install` for zero functionality, and `requirements.txt` pins `psutil==5.9.0` — a 2022 release that has no wheel for this machine's Python 3.15 (installed psutil is 7.2.2).
- **Fix:** delete them; replace `Logger` with `logging.basicConfig`. Delete `requirements.txt` entirely — stdlib only.
- **If we skip it:** an unused dependency that breaks `pip install` on new Pythons, for no benefit.

### F18. No tests, no CI, no packaging — the reason F1 and F2 shipped
`(repo root)`

- **What this is:** one commit, five files, zero tests, no `.github/`, no `pyproject.toml`, no systemd unit file (the README asks users to `tee` one).
- **Problem:** the only file that matters is 955 lines of parsing and hardware IO with no test. A single `python3 -m py_compile` in CI would have caught F1; a single fixture string of `xrandr` output would have caught F2. Compare: power-profiles-daemon and tuned ship real test suites, TLP tests battery thresholds against simulated `/sys` trees.
- **Fix:** one test file with three cases — compile, parse a canned `xrandr`/`acpi` string, pick a mode from a fake battery level. Add `python -m py_compile` + `pytest` to CI. Then extract the pure logic (parsing, mode selection) so it *can* be tested.
- **If we skip it:** the next edit re-introduces the same class of bug, and you find out from a bug report.

### F19. Config parsing crashes on bad input and hardware writes are unvalidated
`power_manager.py:138, 142, 156, 160, 625-626, 753-771`

- **What this is:** `int(os.environ[...])` and `int(parser[...])` with no guard; sysfs writes assume the value is in range (`gt_boost` with no clamp, `vrr_enabled` with no capability check).
- **Problem:** `/etc/power-manager.conf` with `force_refresh_rate = auto` → `ValueError` traceback at startup, before logging is configured. A brightness or boost write that the kernel rejects raises inside a broad `except`, so a mis-detected path looks identical to a permissions problem.
- **Fix:** wrap each `int()` in a try that falls back to the default and logs once; `max(800, int(base*0.8))` for the boost.
- **If we skip it:** a typo in a config file becomes a crash loop under systemd (`Restart=on-failure`), which is hard to diagnose from a journal.

### F20. README overclaims what the code does
`README.md:8-46, 158-176, 219-229`

- **What this is:** claims of NVIDIA support, KDE support, Wayland support, "30–50% longer battery life", OLED pixel shifting.
- **Problem:** `grep -ic nvidia` → 0, `kde` → 0, `wayland` → 0. All display code is `xrandr`/`xset`/`gsettings`, which on this Fedora 45 Wayland box reduces to XWayland (verified: `WARNING: running xrandr against an Xwayland server`) where **mode changes are not possible**. The battery percentages have no measurement behind them. Threshold is documented as "<20%" on line 9, 15 in the config example, and 15 in the code.
- **Fix:** cut the claims to what runs on one machine you tested; state "X11 only" until a Wayland path exists (see comparison — the real fix is to let the compositor do it).
- **If we skip it:** users on the default Fedora session install it, see nothing, and conclude the project is broken. They'd be right.

---

## Research: how the four reference projects differ

Method note: REA only opens binaries/archives — `open_binary` rejected every source file, and these four projects are source-only. `power-profiles-daemon` and `tlp` are not installed on this host; `tuned` here is a Python script. `inspect_web_page` needs a loopback CDP target and only Firefox is installed. So this comparison comes from the shipped source trees, cloned at their current HEAD, not from binary RE.

| | power_hz | auto-cpufreq | TLP | tuned | power-profiles-daemon |
|---|---|---|---|---|---|
| Language / size | Python, 955 lines, 1 file | Python, ~8k lines, 18 modules | Shell+Perl, ~3.8k lines | Python, ~21k lines, 160+ files | C, ~9k lines, 16 files |
| Process model | root `while True` loop, 60 s poll | root daemon, loop | **oneshot** systemd + udev + sleep hook, no polling | root daemon + D-Bus | root daemon, **D-Bus singleton** |
| Owns | refresh rate, brightness, DPMS, GPU, profile, theme | CPU governor/EPP/EPB/turbo/platform_profile, charge threshold | everything `/sys`: CPU, disk, radio devices, charge thresholds | profiles via plugins (incl. Radeon panel power) | 3 profiles via `net.hadess.PowerProfiles` |
| Refresh rate / brightness | **yes, from root** | no | no | no | no |
| Tests | none | none (CI build only) | unit tests on **simulated sysfs** | beakerlib + unit | python integration test against D-Bus |
| Deps | psutil (unused) + 5 external commands | click, distro, psutil, requests | none (shell) | python + pygobject | glib, gudev, polkit, upower |
| Config | env + ini, 4/5 fields unused | `[charger]`/`[battery]` ini | `/etc/tlp.conf`, 3 profiles | profile files | none (D-Bus only) |

**Four lessons that map directly onto the findings:**

1. **One owner per knob.** auto-cpufreq's installer *masks* power-profiles-daemon and TuneD because they conflict; TLP's README says the same. `power_manager.py` calls `powerprofilesctl set` (owned by ppd/tuned — and **not installed on this Fedora 45 box at all**, so that call is a silent `FileNotFoundError`) while also writing CPU/GPU knobs ppd already owns. Pick one: either delegate the profile decision to ppd and only do the display work, or own the CPU/GPU side and don't call `powerprofilesctl`.
2. **Root never touches the desktop.** None of the four set brightness, DPMS, refresh rate or `gsettings`. ppd exposes a D-Bus API and lets GNOME/KDE react. That is exactly why F3 exists: the project crossed the root/session boundary and the boundary won. The lean fix is to delete the desktop half, or move it to a `--user` unit.
3. **Apply and restore are symmetric.** TLP's `tlp init stop` and tuned's profile unload both put hardware back. This tool has no `stop` path at all — brightness, GPU boost and VRR are one-way (F6, F7, F15). A `systemctl stop` currently leaves the machine degraded.
4. **Hardware logic is tested against fake `/sys`.** TLP's `unit-tests/` contains one directory per laptop family with `start_threshold`/`stop_threshold` files. That is the pattern needed to make F2, F13, F14 and the mode-selection logic testable without hardware — and it costs a directory of text files, not a framework.

What is genuinely unique and worth keeping: **nobody else changes refresh rate.** ppd deliberately refuses (it's the compositor's job), TLP and tuned don't. The idea is real. It just has to be implemented where the compositor can hear it — GNOME's own low-power behaviour, or `gsettings`/mutter experimental features — not from a root service calling XWayland.

**Also worth knowing:** on this host `acpi` is not installed (the code's own startup check would exit), `psutil` installed is 7.2.2 while `requirements.txt` pins `5.9.0`, and `xrandr` connects to XWayland where mode changes are refused.

---

## Verdict

Do not ship or install this. It has never run — a syntax error at line 302 is the first thing any user meets — and behind it sit backslash-mangled regexes that crash startup or report a 100% battery forever, plus a root-service design that cannot reach a Wayland/GNOME session, a GPU-boost write that permanently degrades the machine, and brightness writes that fight the user. The idea (refresh-rate-aware power management for Fedora laptops) is the one thing none of the four comparable projects do, so it is worth keeping — but the correct smallest version is ~150 lines: a `--user` service that reads battery from `power_supply`, delegates profiles to ppd/tuned, and changes refresh rate through the desktop, with three pytest cases against canned `xrandr`/`acpi` strings. Everything else in these 955 lines deletes itself.

**Lean: -50 lines, -1 dependency possible** (Logger+LogLevel ≈ 30, PowerMode 5, dead config fields ≈ 8, `variable_backlight` ≈ 5, unused imports 2; drop `psutil` and `requirements.txt`). Larger but honest: deleting the root-only GPU/PSR/CABC/service blocks removes ~120 more lines and every finding from F7 down.

## Not checked

- Could not execute any code path — the file does not compile, so every runtime behaviour is read, not observed.
- Did not test on real OLED / Intel debugfs / AMD hardware; F10 and F14 hardware branches are read-level only.
- Did not run the README install path end-to-end (it cannot succeed here: `acpi` missing, ppd absent, Wayland session).
- Read the four reference projects' READMEs, build files, units, tests and driver/plugin inventories, but not tuned's 30 plugin bodies or auto-cpufreq's GUI.
- `debates/` is empty and ignored.
