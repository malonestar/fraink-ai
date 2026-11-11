from apps.ha_controller import HAControllerApp, HAState
from core.context import AppContext
from core.display import Display


def _app_with_entities(entities):
    app = HAControllerApp()
    display = Display(headless=True)
    app.ctx = AppContext(display=display, config={})
    app.state = HAState.IDLE
    app._entity_ids = entities
    app.entity_states = {}
    return app, display


def test_draw_entity_list_does_not_raise():
    # Regression: draw() called font.size(text) as if it were a method, but in
    # Pillow >=10 font.size is an int attribute -> TypeError froze HA Controller.
    app, display = _app_with_entities(["light.kitchen", "sensor.temperature"])
    app.draw(display)


def test_draw_no_entities_does_not_raise():
    app, display = _app_with_entities([])
    app.draw(display)
