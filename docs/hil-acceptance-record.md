# HIL Acceptance Record

Date: 2026-07-31

Branch: `phase1/fail-safe-runtime`

Commit: phase1 PR HEAD

Configuration:

- Demo/CI: `config/cpp_backend/verify.ini`
- Production reference: `config/cpp_backend/hardware_hik_mvs_modbus.ini`
- PLC heartbeat: 500 ms
- PLC heartbeat failure threshold: 3 consecutive software failures
- PLC watchdog target: physical line stop within 2000 ms after heartbeat loss
- Line stop command: Modbus coil `21`, action code `30`

Automatic evidence:

- `make verify`
- CTest includes runtime fault latch, station queue saturation, listener/storage failure, `/dev/full`, Modbus heartbeat/line-stop, Modbus fault-register precheck, and presence checkpoint restart/regression coverage.

HIL evidence:

- Status: Pending physical site execution.
- Required physical check: start production watch with `config/cpp_backend/hardware_hik_mvs_modbus.ini`, confirm heartbeat at PLC, then stop C++ heartbeat/process and verify the PLC watchdog latches physical line stop within 2000 ms.
- Required operator reset check: after HMI/PLC reset and hardware precheck, manually restart the C++ process; software does not clear PLC fault latch in-process.

Acceptance:

- Automatic fail-safe tests: Passed locally.
- Physical PLC watchdog HIL: Not accepted until the pending physical check is recorded with timestamp, operator, PLC program version, and measured stop latency.
