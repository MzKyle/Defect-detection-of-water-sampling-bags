# Capability Matrix

Date: 2026-07-31

| Capability | Build | Automated | HIL | Accepted | Evidence command | Commit |
| --- | --- | --- | --- | --- | --- | --- |
| Mock | Pass | Pass | Not required for demo | Demo Accepted | `make verify` | phase0/freeze-baseline PR HEAD |
| MVS | SDK build evidence only | Stub path covered in CI; real SDK compile must be recorded per host | Not recorded | Not Accepted | `cmake -S cpp_backend -B build/verify/cpp_backend_build -DCMAKE_BUILD_TYPE=RelWithDebInfo` with Hikvision MVS SDK installed | phase0/freeze-baseline PR HEAD |
| Modbus | Pass | Fake-controller tests in CTest | Not recorded | Not Accepted | `ctest --test-dir build/verify/cpp_backend_build --output-on-failure` | phase0/freeze-baseline PR HEAD |
| ONNX | Not complete | Not complete | Not recorded | Not Accepted | `cmake -S cpp_backend -B build/onnx -DWATERBAG_ENABLE_ONNXRUNTIME=ON` remains blocked until ONNX Runtime headers/library and consistency checks are present | phase0/freeze-baseline PR HEAD |
| Dashboard | Pass | Pass | Not required for demo | Demo Accepted | `make verify` builds wheel/wheelhouse and runs installed smoke outside the repo | phase0/freeze-baseline PR HEAD |

Notes:

- `make verify` generates deterministic mock images under `build/generated_demo/` and writes JSONL, SQLite, uploads, and logs under `build/verify/`.
- MVS is not marked accepted without a site HIL record. The matrix only records build evidence when a host has the real Hikvision SDK.
- ONNX remains unaccepted until the C++ ONNX Runtime build and model consistency validation are both present.
