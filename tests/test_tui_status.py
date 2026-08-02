from cli.tui import INITIAL_STATUS_CHECK_DELAY, STATUS_COLUMN_WIDTH, RunListScreen


def test_running_status_fits_and_initial_check_waits_three_seconds():
    assert INITIAL_STATUS_CHECK_DELAY == 3.0
    assert STATUS_COLUMN_WIDTH >= len("running")


def test_process_missing_reason_is_rendered_in_status_column():
    screen = object.__new__(RunListScreen)
    row = {
        "status": "error",
        "runtime_info": '{"error_reason":"process_missing"}',
    }
    screen._rows = [row]

    assert screen._status_for_row(row) == "error|lost"
    assert screen._status_column_width() >= len("error|lost")


def test_info_column_marks_each_runs_actual_pro_and_fast_flags():
    screen = object.__new__(RunListScreen)

    pro_fast = {
        "status": "completed",
        "runtime_info": '{"pro":true,"fast":true}',
        "resume_index": 1,
    }
    pro = {
        "status": "completed",
        "runtime_info": '{"pro":true,"fast":false}',
    }
    fast = {
        "status": "completed",
        "runtime_info": '{"pro":false,"fast":true}',
    }

    assert screen._run_for_row("0801-cx-08-wiki-retire", pro_fast, 40) == (
        "0801-cx-08-wiki-retire↩"
    )
    assert screen._info_for_row(pro_fast) == "Pro|Fast"
    assert screen._info_for_row(pro) == "Pro"
    assert screen._info_for_row(fast) == "Fast"


def test_info_column_expands_instead_of_ellipsizing():
    screen = object.__new__(RunListScreen)
    row = {
        "status": "error",
        "runtime_info": '{"pro":true,"fast":true,"error_reason":"process_missing"}',
        "resume_index": 1,
        "first_session_run_id": "0801-cx-08-first-run",
    }
    screen._rows = [row]

    info = screen._info_for_row(row)

    assert info == "↩cx-08|Pro|Fast"
    assert screen._info_column_width() >= len(info)
    assert "..." not in info
