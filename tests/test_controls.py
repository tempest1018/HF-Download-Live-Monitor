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
