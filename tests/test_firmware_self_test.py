"""Tests for the host exact-six firmware self-test."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_SESSION = _HERE.parent
sys.path.insert(0, str(_SESSION / "tools"))

from firmware.gpio_map import CONTROL_IDS  # noqa: E402
from firmware.self_test import (  # noqa: E402
    SelfTestCase,
    build_self_test_cases,
    run,
    run_self_test,
)


def test_case_matrix_covers_all_six_semantic_controls() -> None:
    cases = build_self_test_cases()
    pressed_ids: set[str] = set()
    for case in cases:
        for control_id, raw_value in zip(CONTROL_IDS, case.raw_pin_values, strict=True):
            if raw_value is False:
                pressed_ids.add(control_id)
    assert pressed_ids == set(CONTROL_IDS)


def test_case_matrix_covers_cardinals_and_actions() -> None:
    cases = build_self_test_cases()
    assert {case.name for case in cases} >= {
        "UP pressed",
        "DOWN pressed",
        "RIGHT pressed",
        "LEFT pressed",
        "ACTION_A pressed",
        "ACTION_B pressed",
    }
    assert any(case.expected_button_mask == 1 for case in cases)
    assert any(case.expected_button_mask == 2 for case in cases)
    assert any(case.expected_button_mask == 3 for case in cases)


def test_case_matrix_includes_opposite_and_perpendicular_policies() -> None:
    cases = build_self_test_cases()
    opposite = next(case for case in cases if "opposite" in case.name)
    assert (opposite.expected_x, opposite.expected_y) == (0, 0)
    diagonal = next(case for case in cases if "perpendicular" in case.name)
    assert (diagonal.expected_x, diagonal.expected_y) == (127, -127)


def test_case_matrix_includes_idle_baseline() -> None:
    assert any(
        case.expected_x == 0 and case.expected_y == 0 and case.expected_button_mask == 0
        for case in build_self_test_cases()
    )


def test_run_self_test_passes_against_real_pipeline() -> None:
    report = run_self_test()
    assert report.passed is True
    assert report.failures == []
    assert report.total == len(build_self_test_cases())


def test_run_self_test_reports_wrong_expected_output() -> None:
    case = SelfTestCase("forced failure", (False,) * 6, 127, 0, 0)
    report = run_self_test([case])
    assert report.passed is False
    assert report.failures[0].name == "forced failure"


def test_run_self_test_rejects_wrong_raw_sample_shape() -> None:
    case = SelfTestCase("short sample", (True, False), 0, 0, 0)
    report = run_self_test([case])
    assert report.passed is False
    assert "exactly 6" in report.failures[0].detail


def test_cli_run_prints_success(capsys: pytest.CaptureFixture[str]) -> None:
    assert run() == 0
    assert "OK: every case produced the exact expected HID report" in capsys.readouterr().out


def test_cli_is_invokable_via_python_m_firmware() -> None:
    proc = subprocess.run(
        [sys.executable, "-W", "error::RuntimeWarning", "-m", "firmware"],
        cwd=str(_SESSION / "tools"),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "OK: every case produced the exact expected HID report" in proc.stdout
