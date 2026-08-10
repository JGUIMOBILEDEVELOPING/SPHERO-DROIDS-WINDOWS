# Changelog

All notable changes to Control Deck will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses semantic versioning for published releases.

## [1.1.0] - 2026-08-10

### Added

- Complete BB-8 motion-path catalog with 51 animations.
- Missing original R2-D2 and BB-9E animations.
- BB-8 animations in the Gesture Matrix and Macro Editor.
- Remembered BLE device addresses for improved Windows reconnection.
- Direct WinRT reconnection attempts before advertisement scanning.

### Fixed

- Corrected mislabeled R2-D2 firmware animation IDs.
- Existing macros automatically map legacy animation names.
- Missing robots or unsupported animations no longer stop a macro.
- Macro steps for different robots can overlap while commands for the same robot remain queued.
- Existing BB-9E LED controls remain available.

### Completed gestures / animations

| Robot |           Animations |    Spherov2 |
| ----- | -------------------: | ----------: |
| R2-D2 |                   55 |          51 |
| R2-Q5 |                   50 |          50 |
| BB-9E |                   44 |          47 |
| BB-8  |             50 moves |           0 |


## [1.0.0] - 2026-08-08

### Added

- Live Bluetooth discovery and control for supported Sphero Star Wars droids.
- Two device slots per robot model.
- System log, Gesture Matrix, Telemetry, and Settings tabs.
- Native animation discovery and standard movement commands.
- Battery, orientation, connection, LED, and activity telemetry.
- Connection recovery and detailed error diagnostics.
- Macros Matrix with modal scene editing and JSON export.
- Persistent application settings and session logging.
- Simulation backend for hardware-free interface testing.


