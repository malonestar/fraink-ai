from core.display import Display
from core.launcher import Launcher, ANIM_SECONDS


class FakeApp:
    def __init__(self, name):
        self.name = name
        self.entered = False
        self.exited = False

    def on_enter(self, ctx):
        self.entered = True

    def on_exit(self):
        self.exited = True


def make_launcher(n=4):
    apps = [FakeApp(f"App{i}") for i in range(n)]
    entries = [{"name": a.name, "icon": "voice", "app": a} for a in apps]
    return Launcher(entries), apps


# --- selection / launch ---

def test_initial_highlight_is_zero():
    launcher, _ = make_launcher()
    assert launcher.highlight == 0


def test_next_increments():
    launcher, _ = make_launcher()
    launcher.next()
    assert launcher.highlight == 1


def test_next_wraps():
    launcher, _ = make_launcher(4)
    for _ in range(4):
        launcher.next()
    assert launcher.highlight == 0


def test_prev_wraps_backwards():
    launcher, _ = make_launcher(4)
    launcher.prev()
    assert launcher.highlight == 3


def test_launch_app_sets_active():
    launcher, apps = make_launcher()
    launcher.launch(ctx=None)
    assert launcher.active_app is apps[0]
    assert apps[0].entered


def test_exit_app_returns_to_launcher():
    launcher, apps = make_launcher()
    launcher.launch(ctx=None)
    launcher.exit_app()
    assert launcher.active_app is None
    assert apps[0].exited


def test_launch_different_app():
    launcher, apps = make_launcher()
    launcher.next()
    launcher.next()
    launcher.launch(ctx=None)
    assert launcher.active_app is apps[2]


def test_exit_preserves_highlight():
    launcher, apps = make_launcher()
    launcher.next()
    launcher.launch(ctx=None)
    launcher.exit_app()
    assert launcher.highlight == 1


# --- slide animation ---

def test_next_starts_full_slide():
    launcher, _ = make_launcher()
    launcher.next()
    assert launcher._offset == 1.0


def test_prev_slides_opposite_direction():
    launcher, _ = make_launcher()
    launcher.prev()
    assert launcher._offset == -1.0


def test_update_eases_offset_toward_zero():
    launcher, _ = make_launcher()
    launcher.next()
    launcher.update(ANIM_SECONDS / 2)
    assert 0.0 < launcher._offset < 1.0


def test_update_completes_to_exactly_zero():
    launcher, _ = make_launcher()
    launcher.next()
    launcher.update(ANIM_SECONDS + 0.01)
    assert launcher._offset == 0.0


def test_mash_snaps_and_restarts():
    launcher, _ = make_launcher(4)
    launcher.next()
    launcher.update(0.05)            # mid-flight
    launcher.next()                  # second press
    assert launcher.highlight == 2   # advanced twice
    assert launcher._offset == 1.0   # fresh full slide (old one snapped)


def test_update_always_requests_redraw_for_bob():
    launcher, _ = make_launcher()
    assert launcher.update(0.033) is True


# --- drawing (headless smoke) ---

def test_draw_headless_idle_sliding_and_settled():
    launcher, _ = make_launcher(7)
    d = Display(headless=True)
    launcher.draw(d)                  # idle with bob
    launcher.next()
    launcher.draw(d)                  # mid-slide (offset 1.0)
    launcher.update(0.1)
    launcher.draw(d)                  # easing
    launcher.update(1.0)
    launcher.draw(d)                  # settled
    launcher.prev()
    launcher.draw(d)                  # wrap backwards mid-slide
