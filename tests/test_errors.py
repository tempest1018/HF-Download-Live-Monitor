from hf_download_live_monitor.errors import ErrorCategory, exit_code_for


def test_every_error_category_has_a_unique_nonzero_exit_code() -> None:
    exit_codes = [exit_code_for(category) for category in ErrorCategory]

    assert all(code != 0 for code in exit_codes)
    assert len(exit_codes) == len(set(exit_codes))
