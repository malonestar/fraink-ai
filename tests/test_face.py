import math
from core.face import (
    Expression, EXPRESSION_PARAMS, blink_open_factor, breath_offset,
    gaze_drift, mouth_smooth, eye_open_for,
)

def test_expression_params_cover_all_states():
    for e in Expression:
        p = EXPRESSION_PARAMS[e]
        assert 0.0 <= p.eye_open <= 1.0
        assert -1.0 <= p.gaze_y <= 1.0

def test_blink_is_open_most_of_cycle_and_shut_briefly():
    # Fully open mid-cycle
    assert blink_open_factor(0.0, cycle=4.8, close=0.18) == 1.0
    assert blink_open_factor(2.0, cycle=4.8, close=0.18) == 1.0
    # Fully shut inside the closing window (last 0.18s of the cycle)
    assert blink_open_factor(4.75, cycle=4.8, close=0.18) < 0.5

def test_breath_offset_bounded_and_periodic():
    amp = 3.0
    for t in (0.0, 1.3, 2.5, 4.9):
        assert -amp - 0.001 <= breath_offset(t, cycle=5.0, amp=amp) <= amp + 0.001
    assert abs(breath_offset(0.0, 5.0, amp) - breath_offset(5.0, 5.0, amp)) < 1e-6

def test_gaze_drift_within_range():
    for t in (0.0, 0.7, 3.1, 9.9):
        gx, gy = gaze_drift(t)
        assert -1.0 <= gx <= 1.0 and -1.0 <= gy <= 1.0

def test_mouth_smooth_moves_toward_target():
    assert mouth_smooth(0.0, 1.0, dt=1.0) > 0.5      # big dt -> close to target
    assert mouth_smooth(0.0, 1.0, dt=0.0) == 0.0     # no dt -> no move
    assert 0.0 <= mouth_smooth(0.5, 0.0, dt=0.05) <= 0.5

def test_eye_open_for_combines_expression_and_blink():
    # During a blink the eye is shut regardless of expression openness
    assert eye_open_for(Expression.LISTENING, blink=0.0) == 0.0
    # Fully open blink -> expression openness
    assert eye_open_for(Expression.LISTENING, blink=1.0) == 1.0
    assert eye_open_for(Expression.IDLE, blink=1.0) == EXPRESSION_PARAMS[Expression.IDLE].eye_open


from core.face import Face

class _RecordingDisplay:
    """Captures primitive calls so we can assert geometry without a screen."""
    def __init__(self):
        self.ellipses = []   # (x0,y0,x1,y1,color)
        self.rects = []      # (x,y,w,h,color)
    def draw_ellipse(self, x0, y0, x1, y1, color):
        self.ellipses.append((x0, y0, x1, y1, color))
    def draw_rect(self, x, y, w, h, color):
        self.rects.append((x, y, w, h, color))

def test_face_update_advances_clock_and_smooths_mouth():
    f = Face()
    f.set_state(Expression.SPEAKING, audio_level=1.0)
    f.update(0.1)
    assert f.clock > 0
    assert f.mouth_open > 0.0   # moved toward target

def test_face_speaking_opens_mouth_more_than_idle():
    speaking = Face(); speaking.set_state(Expression.SPEAKING, 1.0)
    idle = Face(); idle.set_state(Expression.IDLE, 0.0)
    for _ in range(20):
        speaking.update(0.05); idle.update(0.05)
    assert speaking.mouth_open > idle.mouth_open

def test_face_draw_emits_two_eyes_and_a_mouth():
    f = Face(); f.set_state(Expression.LISTENING, 0.0); f.update(0.016)
    d = _RecordingDisplay()
    f.draw(d)
    # two eye ellipses + one mouth ellipse, all white
    assert len(d.ellipses) >= 3
