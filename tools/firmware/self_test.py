"""Host-only logic self-test for the exact-six firmware pipeline.

This check exercises raw pulled-up GPIO values, active-low decoding,
debounced semantic control IDs, HID composition, and four-byte serialization.
It does not claim physical harness, USB enumeration, or production evidence.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import dataclass, field

from .gpio_map import CONTROL_IDS, control_states_from_raw
from .hid_report import HID_AXIS_MAX, HID_AXIS_MIN, HID_AXIS_NEUTRAL, HIDReport, compute_hid_report
from .serialization import HID_REPORT_BYTES, deserialize_hid_report, serialize_hid_report


@dataclass(frozen=True)
class SelfTestCase:
    """One raw six-pin sample and its expected report."""

    name: str
    raw_pin_values: tuple[bool, ...]
    expected_x: int
    expected_y: int
    expected_button_mask: int


@dataclass
class CaseResult:
    name: str
    passed: bool
    detail: str
    actual: HIDReport | None = None
    expected: HIDReport | None = None


def _new_case_results() -> list[CaseResult]:
    return []


@dataclass
class SelfTestReport:
    cases: list[CaseResult] = field(default_factory=_new_case_results)

    @property
    def passed(self) -> bool:
        return all(case.passed for case in self.cases)

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def failures(self) -> list[CaseResult]:
        return [case for case in self.cases if not case.passed]

    def render(self) -> str:
        lines = [f"firmware logic self-test: {self.total} cases"]
        for case in self.cases:
            tag = "PASS" if case.passed else "FAIL"
            lines.append(f"  [{tag}] {case.name} :: {case.detail}")
        if self.failures:
            lines.append(f"FAIL: {len(self.failures)} of {self.total} cases failed")
        else:
            lines.append("OK: every case produced the exact expected HID report")
        return "\n".join(lines)


def _raw(released: bool = True) -> tuple[bool, ...]:
    """Return six HIGH released or LOW pressed raw pin readings."""
    return (released,) * len(CONTROL_IDS)


def _only_control(control_id: str) -> tuple[bool, ...]:
    """Return raw pins with exactly one semantic control pressed."""
    raw_values = list(_raw())
    raw_values[CONTROL_IDS.index(control_id)] = False
    return tuple(raw_values)


def _pressed_controls(*control_ids: str) -> tuple[bool, ...]:
    """Return raw pins with the named controls pressed."""
    raw_values = list(_raw())
    for control_id in control_ids:
        raw_values[CONTROL_IDS.index(control_id)] = False
    return tuple(raw_values)


def build_self_test_cases() -> list[SelfTestCase]:
    """Build the exact-six mapping, collision, and combined-state matrix."""
    cases: list[SelfTestCase] = [
        SelfTestCase("baseline: nothing pressed", _raw(), 0, 0, 0),
        SelfTestCase("UP pressed", _only_control("up"), 0, HID_AXIS_MIN, 0),
        SelfTestCase("DOWN pressed", _only_control("down"), 0, HID_AXIS_MAX, 0),
        SelfTestCase("RIGHT pressed", _only_control("right"), HID_AXIS_MAX, 0, 0),
        SelfTestCase("LEFT pressed", _only_control("left"), HID_AXIS_MIN, 0, 0),
        SelfTestCase("ACTION_A pressed", _only_control("action_a"), 0, 0, 1),
        SelfTestCase("ACTION_B pressed", _only_control("action_b"), 0, 0, 2),
        SelfTestCase(
            "opposite directions collapse to neutral",
            _pressed_controls("up", "down", "left", "right"),
            HID_AXIS_NEUTRAL,
            HID_AXIS_NEUTRAL,
            0,
        ),
        SelfTestCase(
            "perpendicular directions pass through",
            _pressed_controls("up", "right"),
            HID_AXIS_MAX,
            HID_AXIS_MIN,
            0,
        ),
        SelfTestCase(
            "both actions report independently",
            _pressed_controls("action_a", "action_b"),
            HID_AXIS_NEUTRAL,
            HID_AXIS_NEUTRAL,
            3,
        ),
        SelfTestCase(
            "action plus cardinal remains distinct",
            _pressed_controls("left", "action_a", "action_b"),
            HID_AXIS_MIN,
            HID_AXIS_NEUTRAL,
            3,
        ),
    ]
    return cases


def _run_case(case: SelfTestCase) -> CaseResult:
    """Run one case through raw decode, composition, and serialization."""
    try:
        states = control_states_from_raw(case.raw_pin_values)
        actual = compute_hid_report(states)
        expected = HIDReport(case.expected_x, case.expected_y, case.expected_button_mask)
        if actual != expected:
            return CaseResult(
                case.name, False, f"got {actual!r}; want {expected!r}", actual, expected
            )
        payload = serialize_hid_report(actual)
        if len(payload) != HID_REPORT_BYTES or deserialize_hid_report(payload) != actual:
            return CaseResult(case.name, False, "serialization round-trip changed the report")
        return CaseResult(
            case.name, True, f"{actual!r}; bytes={payload.hex(' ')}", actual, expected
        )
    except (TypeError, ValueError) as exc:
        return CaseResult(case.name, False, f"case rejected: {exc}")


def run_self_test(cases: Iterable[SelfTestCase] | None = None) -> SelfTestReport:
    """Run the scripted cases and return a structured result."""
    selected: Iterable[SelfTestCase] = build_self_test_cases() if cases is None else cases
    report = SelfTestReport()
    for case in selected:
        report.cases.append(_run_case(case))
    return report


def run() -> int:
    """Print the logic self-test and return a process status code."""
    report = run_self_test()
    print(report.render())
    return 0 if report.passed else 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(run())
