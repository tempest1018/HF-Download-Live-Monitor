"""Pure terminal layout policy selection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ViewMode(str, Enum):
    """Requested amount of information in the terminal view."""

    COMPACT = "compact"
    BALANCED = "balanced"
    DETAILED = "detailed"

    @classmethod
    def parse(cls, value: str | ViewMode) -> ViewMode:
        """Parse a view mode name, accepting case and surrounding whitespace."""

        return parse_view_mode(value)


class LayoutClass(str, Enum):
    """Width class used by renderers to arrange terminal content."""

    NARROW = "narrow"
    NORMAL = "normal"
    WIDE = "wide"


@dataclass(frozen=True, slots=True)
class LayoutPolicy:
    """Renderer-independent decisions for a terminal layout."""

    layout_class: LayoutClass
    columns: int
    show_sparkline: bool
    show_events: bool
    show_preflight: bool
    show_completed_files: bool
    abbreviate_labels: bool
    animate: bool


def parse_view_mode(value: str | ViewMode) -> ViewMode:
    """Return a normalized view mode or raise a descriptive error."""

    if isinstance(value, ViewMode):
        return value
    try:
        return ViewMode(value.strip().lower())
    except (AttributeError, ValueError) as exc:
        choices = ", ".join(mode.value for mode in ViewMode)
        raise ValueError(f"invalid view mode {value!r}; expected one of: {choices}") from exc


def choose_layout(width: int, mode: ViewMode) -> LayoutClass:
    """Classify a positive terminal width for the selected view mode."""

    if width <= 0:
        raise ValueError("width must be positive")
    if not isinstance(mode, ViewMode):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ValueError("mode must be a ViewMode")
    if width < 60:
        return LayoutClass.NARROW
    if width >= 110 and mode is not ViewMode.COMPACT:
        return LayoutClass.WIDE
    return LayoutClass.NORMAL


def layout_policy(
    width: int,
    mode: str | ViewMode,
    reduced_motion: bool = False,
    show_details: bool = False,
    show_events: bool = False,
) -> LayoutPolicy:
    """Choose presentation capabilities from width and user preferences."""

    view_mode = parse_view_mode(mode)
    layout_class = choose_layout(width, view_mode)

    compact = view_mode is ViewMode.COMPACT
    detailed = view_mode is ViewMode.DETAILED
    narrow = layout_class is LayoutClass.NARROW

    return LayoutPolicy(
        layout_class=layout_class,
        columns=2 if layout_class is LayoutClass.WIDE else 1,
        show_sparkline=not compact and not narrow,
        show_events=not compact and (detailed or show_events),
        show_preflight=True,
        show_completed_files=not compact and (detailed or show_details),
        abbreviate_labels=narrow,
        animate=not reduced_motion,
    )
