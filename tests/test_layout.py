from __future__ import annotations

import pytest

from hf_download_live_monitor.layout import (
    LayoutClass,
    ViewMode,
    layout_policy,
    parse_view_mode,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("compact", ViewMode.COMPACT),
        ("BALANCED", ViewMode.BALANCED),
        (" detailed ", ViewMode.DETAILED),
        (ViewMode.COMPACT, ViewMode.COMPACT),
    ],
)
def test_parse_view_mode(value: str | ViewMode, expected: ViewMode) -> None:
    assert parse_view_mode(value) is expected
    assert ViewMode.parse(value) is expected


def test_parse_view_mode_rejects_unknown_value_clearly() -> None:
    with pytest.raises(ValueError, match=r"invalid view mode 'dense'.*compact, balanced, detailed"):
        parse_view_mode("dense")


@pytest.mark.parametrize("width", [0, -1])
def test_layout_policy_requires_positive_width(width: int) -> None:
    with pytest.raises(ValueError, match="width must be positive"):
        layout_policy(width, ViewMode.BALANCED)


@pytest.mark.parametrize(
    ("width", "expected"),
    [
        (59, LayoutClass.NARROW),
        (60, LayoutClass.NORMAL),
        (109, LayoutClass.NORMAL),
        (110, LayoutClass.WIDE),
    ],
)
def test_balanced_layout_uses_exact_breakpoints(width: int, expected: LayoutClass) -> None:
    assert layout_policy(width, ViewMode.BALANCED).layout_class is expected


@pytest.mark.parametrize("mode", list(ViewMode))
def test_every_mode_is_narrow_below_first_breakpoint(mode: ViewMode) -> None:
    assert layout_policy(59, mode).layout_class is LayoutClass.NARROW


@pytest.mark.parametrize("mode", list(ViewMode))
def test_every_mode_is_normal_between_breakpoints(mode: ViewMode) -> None:
    assert layout_policy(60, mode).layout_class is LayoutClass.NORMAL
    assert layout_policy(109, mode).layout_class is LayoutClass.NORMAL


def test_non_compact_modes_are_wide_at_second_breakpoint() -> None:
    assert layout_policy(110, ViewMode.BALANCED).layout_class is LayoutClass.WIDE
    assert layout_policy(110, ViewMode.DETAILED).layout_class is LayoutClass.WIDE


def test_compact_never_becomes_wide_and_suppresses_detail() -> None:
    policy = layout_policy(200, ViewMode.COMPACT, show_details=True, show_events=True)

    assert policy.layout_class is LayoutClass.NORMAL
    assert policy.columns == 1
    assert not policy.show_sparkline
    assert not policy.show_events
    assert policy.show_preflight
    assert not policy.show_completed_files


def test_narrow_balanced_abbreviates_without_graph() -> None:
    policy = layout_policy(59, ViewMode.BALANCED)

    assert policy.layout_class is LayoutClass.NARROW
    assert policy.columns == 1
    assert not policy.show_sparkline
    assert policy.show_preflight
    assert policy.abbreviate_labels
    assert not policy.show_completed_files


def test_narrow_detailed_keeps_file_and_event_details_without_graph() -> None:
    policy = layout_policy(40, ViewMode.DETAILED)

    assert policy.columns == 1
    assert not policy.show_sparkline
    assert policy.show_completed_files
    assert policy.show_events


def test_normal_balanced_shows_live_summary_only_by_default() -> None:
    policy = layout_policy(80, ViewMode.BALANCED)

    assert policy.columns == 1
    assert policy.show_sparkline
    assert policy.show_preflight
    assert not policy.show_completed_files
    assert not policy.show_events
    assert not policy.abbreviate_labels
    assert policy.animate


def test_wide_balanced_uses_two_columns_and_honors_toggles() -> None:
    policy = layout_policy(
        110,
        ViewMode.BALANCED,
        show_details=True,
        show_events=True,
    )

    assert policy.layout_class is LayoutClass.WIDE
    assert policy.columns == 2
    assert policy.show_sparkline
    assert policy.show_completed_files
    assert policy.show_events


def test_detailed_defaults_to_completed_files_and_events() -> None:
    policy = layout_policy(80, ViewMode.DETAILED)

    assert policy.show_completed_files
    assert policy.show_events
    assert policy.show_sparkline


def test_reduced_motion_always_disables_animation() -> None:
    for mode in ViewMode:
        assert not layout_policy(120, mode, reduced_motion=True).animate


def test_layout_invariants_hold_across_widths_and_modes() -> None:
    for width in range(1, 160):
        for mode in ViewMode:
            policy = layout_policy(width, mode, show_details=True, show_events=True)
            assert policy.columns in (1, 2)
            if policy.layout_class is LayoutClass.NARROW:
                assert policy.columns == 1
                assert not policy.show_sparkline
            if mode is ViewMode.COMPACT:
                assert policy.columns == 1
                assert not policy.show_sparkline
