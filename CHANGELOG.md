# Changelog

All notable changes to this project are documented here. Dates are release
dates; the project has no tagged versions yet.

## Unreleased

### Added

- `--help` / `-h` usage text. Unknown arguments are rejected with exit code 2.
- Single-instance lock in `$XDG_RUNTIME_DIR/power-hz.lock`, so a second copy
  exits instead of fighting the first over `ApplyMonitorsConfig`.

### Changed

- README documents the CLI flags, the single-instance behaviour, the actual
  configuration defaults and the event-driven (not load-driven) policy.

### Fixed

- Brightness cap not holding on battery: `_reassert_cap` compared against
  Mutter's cached Backlight value, so when gsd-power (or another writer)
  overridden the hardware register back to 100 %, the daemon saw its own cache
  at the cap and never re-applied. The re-assert now reads the actual sysfs
  brightness (read-only) and re-applies the cap every 2 seconds while on
  battery, cancelled on AC.

## 2026-10-25

### Changed

- Power-profile switching now goes through the PowerProfiles D-Bus interface
  (`org.freedesktop.UPower.PowerProfiles`) instead of `tuned-adm` and sudo.
  No root and no external command.
- On AC the profile active before unplugging is restored; a profile the user
  changes while on battery is kept.
- `install.sh` no longer installs a sudoers rule or a root helper.

### Fixed

- Profile detection no longer depends on `tuned-adm` being installed.

## 2026-10-24

### Added

- GNOME/Wayland daemon that drops the internal panel to its lowest refresh
  rate, caps brightness and switches the PPD profile on battery, then restores
  everything on AC.
- `systemctl --user` unit, no-root install script, pytest suite with a captured
  Mutter `GetCurrentState` fixture, and a CI workflow.

### Removed

- The old 955-line root `power_manager.py` (X11/`xrandr`, never ran).
