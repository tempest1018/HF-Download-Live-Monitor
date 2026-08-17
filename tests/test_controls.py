import pytest

from hf_download_live_monitor import controls as controls_module
from hf_download_live_monitor.controls import DisplayState, KeyboardController
from hf_download_live_monitor.layout import ViewMode


def test_display_state_keys_are_pure_transitions() -> None:
    original = DisplayState()
    assert original.apply_key("v").view_mode is ViewMode.DETAILED
    assert DisplayState(ViewMode.DETAILED).apply_key("v").view_mode is ViewMode.COMPACT
    assert DisplayState(ViewMode.COMPACT).apply_key("v").view_mode is ViewMode.BALANCED
    assert original.apply_key("d").show_details
    assert original.apply_key("e").show_events
    assert original.apply_key("?").show_help
    assert original.apply_key("q").cancel_requested
    assert original.apply_key("x") == original
    assert original == DisplayState()


def test_controller_polls_injected_nonblocking_reader_and_closes_once() -> None:
    keys = iter((None, "d"))
    closes: list[str] = []
    controller = KeyboardController(
        read_key=lambda: next(keys), close_reader=lambda: closes.append("x")
    )
    state = controller.poll(DisplayState())
    assert state == DisplayState()
    assert controller.poll(state).show_details
    controller.close()
    controller.close()
    assert closes == ["x"]


def test_controller_disables_itself_when_reader_fails() -> None:
    calls = 0

    def broken() -> str | None:
        nonlocal calls
        calls += 1
        raise RuntimeError("terminal disappeared")

    controller = KeyboardController(read_key=broken)
    state = DisplayState()
    assert controller.poll(state) == state
    assert controller.poll(state) == state
    assert calls == 1


def test_controller_initialization_and_close_errors_never_escape(monkeypatch) -> None:
    class Tty:
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(
        controls_module,
        "_platform_reader",
        lambda _: (_ for _ in ()).throw(RuntimeError("init failed")),
    )
    state = DisplayState()
    assert KeyboardController(stream=Tty()).poll(state) == state  # type: ignore[arg-type]
    controller = KeyboardController(
        read_key=lambda: None,
        close_reader=lambda: (_ for _ in ()).throw(RuntimeError("close failed")),
    )
    controller.close()


def test_windows_reader_uses_kbhit_before_getwch() -> None:
    calls: list[str] = []

    class Msvcrt:
        def kbhit(self) -> bool:
            calls.append("kbhit")
            return len(calls) > 1

        def getwch(self) -> str:
            calls.append("getwch")
            return "q"

    read, close = controls_module._windows_reader(Msvcrt())
    assert read() is None
    assert read() == "q"
    close()
    assert calls == ["kbhit", "kbhit", "getwch"]


@pytest.mark.parametrize(("prefix", "scan_code"), [("\x00", "?"), ("\xe0", "D")])
def test_windows_extended_key_sequence_is_consumed_without_display_action(
    prefix: str, scan_code: str
) -> None:
    keys = iter((prefix, scan_code))

    class Msvcrt:
        @staticmethod
        def kbhit() -> bool:
            return True

        @staticmethod
        def getwch() -> str:
            return next(keys)

    read, _ = controls_module._windows_reader(Msvcrt())
    controller = KeyboardController(read_key=read)
    state = DisplayState()
    state = controller.poll(state)
    state = controller.poll(state)
    assert state == DisplayState()


def test_incomplete_windows_extended_key_disables_controls_safely() -> None:
    calls = 0

    class Msvcrt:
        @staticmethod
        def kbhit() -> bool:
            return True

        @staticmethod
        def getwch() -> str:
            nonlocal calls
            calls += 1
            if calls == 1:
                return "\xe0"
            raise OSError("missing scan code")

    read, _ = controls_module._windows_reader(Msvcrt())
    controller = KeyboardController(read_key=read)
    state = DisplayState()
    assert controller.poll(state) == state
    assert controller.poll(state) == state
    assert calls == 2


def test_posix_reader_sets_cbreak_and_restores_exact_settings_after_poll_error() -> None:
    previous = [1, 2, [3]]
    calls: list[object] = []

    class Stream:
        def isatty(self) -> bool:
            return True

        def fileno(self) -> int:
            return 7

        def read(self, count: int) -> str:
            return "q"

    class Termios:
        TCSADRAIN = 9

        def tcgetattr(self, descriptor: int):
            calls.append(("get", descriptor))
            return previous

        def tcsetattr(self, descriptor: int, when: int, settings) -> None:
            calls.append(("restore", descriptor, when, settings))

    class Tty:
        def setcbreak(self, descriptor: int) -> None:
            calls.append(("cbreak", descriptor))

    class Select:
        def select(self, *args):
            raise OSError("poll failed")

    stream = Stream()
    read, close = controls_module._posix_reader(stream, Select(), Termios(), Tty())
    controller = KeyboardController(read_key=read, close_reader=close)
    state = DisplayState()
    assert controller.poll(state) == state
    controller.close()
    assert calls == [
        ("get", 7),
        ("cbreak", 7),
        ("restore", 7, 9, previous),
    ]
