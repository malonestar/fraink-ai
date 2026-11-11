from core.screensaver import IdleScreensaver


def test_defaults():
    s = IdleScreensaver()
    assert s.dim_after == 15.0
    assert s.off_after == 60.0
    assert s.stage == s.AWAKE
    assert not s.active


def test_dims_at_15s_then_off_at_60s():
    s = IdleScreensaver()
    assert s.tick(14.9) is False
    assert s.stage == s.AWAKE
    assert s.tick(0.2) is True
    assert s.stage == s.DIM
    assert s.tick(44.0) is False        # 59.1s total — still dim
    assert s.tick(1.0) is True          # 60.1s -> off
    assert s.stage == s.OFF


def test_stays_off_until_woken():
    s = IdleScreensaver()
    s.tick(61)
    assert s.stage == s.OFF
    assert s.tick(600) is False
    assert s.stage == s.OFF


def test_button_wakes_from_dim_and_is_consumed():
    s = IdleScreensaver()
    s.tick(16)
    assert s.stage == s.DIM
    assert s.on_activity() is True       # waking press must be consumed
    assert s.stage == s.AWAKE
    assert s.on_activity() is False      # next press behaves normally


def test_button_wakes_from_off_and_is_consumed():
    s = IdleScreensaver()
    s.tick(61)
    assert s.on_activity() is True
    assert s.stage == s.AWAKE


def test_activity_resets_idle_timer():
    s = IdleScreensaver()
    s.tick(10)
    s.on_activity()
    assert s.tick(10) is False           # only 10s since activity
    assert s.stage == s.AWAKE
    assert s.tick(5.1) is True
    assert s.stage == s.DIM


def test_keep_awake_blocks_and_snaps_awake():
    # Wake-on-wakeword path: app turns busy while dim/off.
    s = IdleScreensaver()
    assert s.tick(61, allow=False) is False   # busy app never engages
    assert s.stage == s.AWAKE
    s.tick(61)
    assert s.stage == s.OFF
    assert s.tick(0.1, allow=False) is True   # flipped -> caller redraws
    assert s.stage == s.AWAKE
