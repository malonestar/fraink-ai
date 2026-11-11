import pytest

from apps.pomodoro import PomodoroApp, PomState, PomPhase, session_dots


def test_session_dots_counts_completed_work_in_cycle():
    # session counts completed work sessions; in a 4-cycle:
    assert session_dots(0, 4) == (4, 0)
    assert session_dots(2, 4) == (4, 2)
    assert session_dots(4, 4) == (4, 0)   # wraps at cycle boundary
    assert session_dots(5, 4) == (4, 1)


def test_reset_restores_full_phase_and_pauses():
    app = PomodoroApp()
    app.work_secs = 1500
    app.phase = PomPhase.WORK
    app.remaining = 12.0
    app.pom_state = PomState.RUNNING
    app.reset_phase()
    assert app.remaining == 1500.0
    assert app.pom_state == PomState.PAUSED


def test_on_secondary_long_resets():
    app = PomodoroApp()
    app.work_secs = 1500
    app.remaining = 3.0
    app.on_secondary("long")
    assert app.remaining == 1500.0


def test_initial_state_paused():
    app = PomodoroApp()
    assert app.pom_state == PomState.PAUSED


def test_initial_phase_work():
    app = PomodoroApp()
    assert app.phase == PomPhase.WORK


def test_default_durations():
    app = PomodoroApp()
    assert app.work_secs == 25 * 60
    assert app.short_break_secs == 5 * 60
    assert app.long_break_secs == 15 * 60


def test_toggle_pause_resume():
    app = PomodoroApp()
    app.toggle_pause()
    assert app.pom_state == PomState.RUNNING
    app.toggle_pause()
    assert app.pom_state == PomState.PAUSED


def test_tick_decrements():
    app = PomodoroApp()
    app.toggle_pause()
    app.remaining = 10.0
    app.tick(1.0)
    assert app.remaining == pytest.approx(9.0)


def test_tick_does_not_go_negative():
    app = PomodoroApp()
    app.toggle_pause()
    app.remaining = 0.5
    app.tick(1.0)
    assert app.remaining == 0.0


def test_phase_transition_work_to_short_break():
    app = PomodoroApp()
    app.session = 1
    next_phase, next_dur = app.next_phase()
    assert next_phase == PomPhase.SHORT_BREAK
    assert next_dur == app.short_break_secs


def test_phase_transition_after_4_sessions():
    app = PomodoroApp()
    app.session = 4
    next_phase, next_dur = app.next_phase()
    assert next_phase == PomPhase.LONG_BREAK


def test_skip_phase():
    app = PomodoroApp()
    app.toggle_pause()
    app.skip_phase()
    assert app.phase == PomPhase.SHORT_BREAK
    assert app.session == 1


def test_session_count_increments_on_work_complete():
    app = PomodoroApp()
    app.skip_phase()
    assert app.session == 1


def test_wakeword_disabled():
    app = PomodoroApp()
    assert app.uses_wakeword is False


# ---------------------------------------------------------------------------
# LED convention: red = focus running, green = break running, off = paused.
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock

from apps.pomodoro import PomodoroApp as _PomApp, PomPhase as _PomPhase


def _led_app():
    app = _PomApp()
    app.ctx = MagicMock()
    return app


def test_led_red_during_work_running():
    app = _led_app()
    app.phase = _PomPhase.WORK
    app.toggle_pause()  # PAUSED -> RUNNING
    app.ctx.display.set_led.assert_called_with(255, 0, 0)


def test_led_green_during_break_running():
    app = _led_app()
    app.phase = _PomPhase.SHORT_BREAK
    app.toggle_pause()
    app.ctx.display.set_led.assert_called_with(0, 255, 0)


def test_led_off_when_paused():
    app = _led_app()
    app.toggle_pause()   # RUNNING
    app.toggle_pause()   # PAUSED again
    app.ctx.display.set_led.assert_called_with(0, 0, 0)


def test_led_off_on_exit():
    app = _led_app()
    ctx = app.ctx
    app.toggle_pause()
    app.on_exit()
    ctx.display.set_led.assert_called_with(0, 0, 0)


def test_led_follows_skip_phase():
    app = _led_app()
    app.phase = _PomPhase.WORK
    app.toggle_pause()  # running, red
    app.skip_phase()    # -> break
    app.ctx.display.set_led.assert_called_with(0, 255, 0)
