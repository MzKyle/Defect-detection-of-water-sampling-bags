from waterbag_inspection.fault_injection import run_fault_injections


def test_fault_injections_report_timeout_retry_and_stale(tmp_path):
    payload = run_fault_injections(
        config_path="configs/demo.yaml",
        scenario="all",
        output_root=str(tmp_path),
    )

    reports = {item["name"]: item for item in payload["scenarios"]}

    assert payload["scenario_count"] == 3
    assert reports["timeout"]["metrics"]["timeout_events"] >= 1
    assert reports["ack-retry"]["metrics"]["ack_retry_events"] >= 1
    assert reports["out-of-order"]["metrics"]["stale_frame_events"] >= 1
