from core.button import ButtonEvent, TapClassifier


def make(double=False):
    clf = TapClassifier(long_press_sec=0.6, double_tap_sec=0.25, debounce_sec=0.025)
    clf.double_enabled = double
    return clf


def test_single_tap_emits_immediately_when_double_disabled():
    clf = make(double=False)
    clf.press(0.0)
    clf.release(0.1)
    assert clf.drain() == [ButtonEvent.SINGLE]


def test_two_taps_emit_two_singles_when_double_disabled():
    clf = make(double=False)
    clf.press(0.0)
    clf.release(0.1)
    clf.press(0.2)
    clf.release(0.3)
    assert clf.drain() == [ButtonEvent.SINGLE, ButtonEvent.SINGLE]


def test_long_press_fires_on_poll_while_held():
    clf = make(double=False)
    clf.press(0.0)
    clf.poll(0.65)
    assert clf.drain() == [ButtonEvent.LONG]


def test_release_after_long_does_not_emit_single():
    clf = make(double=False)
    clf.press(0.0)
    clf.poll(0.65)
    clf.drain()  # consume the LONG
    clf.release(0.8)
    assert clf.drain() == []


def test_long_press_fires_on_release_if_poll_missed():
    clf = make(double=False)
    clf.press(0.0)
    clf.release(0.7)
    assert clf.drain() == [ButtonEvent.LONG]


def test_debounce_ignores_ultra_short_tap():
    clf = make(double=False)
    clf.press(0.0)
    clf.release(0.01)
    assert clf.drain() == []


def test_single_deferred_until_double_window_when_double_enabled():
    clf = make(double=True)
    clf.press(0.0)
    clf.release(0.1)
    assert clf.drain() == []  # waiting to see if a second tap arrives
    clf.poll(0.4)  # > release + double_tap_sec
    assert clf.drain() == [ButtonEvent.SINGLE]


def test_two_quick_taps_emit_double_when_double_enabled():
    clf = make(double=True)
    clf.press(0.0)
    clf.release(0.1)
    clf.press(0.2)   # within 0.25s of the release at 0.1
    clf.release(0.3)
    assert clf.drain() == [ButtonEvent.DOUBLE]


def test_double_not_triggered_when_second_tap_too_late():
    clf = make(double=True)
    clf.press(0.0)
    clf.release(0.1)
    clf.poll(0.4)  # window expires -> SINGLE emitted
    assert clf.drain() == [ButtonEvent.SINGLE]
    clf.press(0.5)
    clf.release(0.6)
    clf.poll(0.9)
    assert clf.drain() == [ButtonEvent.SINGLE]
