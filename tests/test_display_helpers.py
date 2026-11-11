from core.display import Display, COLOR_HIGHLIGHT, COLOR_DIM


def test_draw_session_dots_runs_headless():
    d = Display(headless=True)
    # 4 dots, 2 filled — should not raise and should stay on-canvas
    d.draw_session_dots(cx=120, y=180, total=4, filled=2)


def test_draw_pause_glyph_runs_headless():
    d = Display(headless=True)
    d.draw_pause_glyph(cx=120, cy=100, size=40, paused=True)
    d.draw_pause_glyph(cx=120, cy=100, size=40, paused=False)  # play triangle


def test_set_led_runs_headless():
    d = Display(headless=True)
    d.set_led(255, 0, 0)  # no board -> no-op, must not raise


def test_set_backlight_runs_headless():
    d = Display(headless=True)
    d.set_backlight(False)
    d.set_backlight(True)  # no board -> no-op, must not raise
