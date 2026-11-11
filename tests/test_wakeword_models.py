from core import wakeword
from core.wakeword import WakeWordService


def _models(tmp_path, monkeypatch, names):
    for n in names:
        (tmp_path / f"{n}.json").write_text("{}")
    monkeypatch.setattr(wakeword, "MODELS_DIR", tmp_path)


def test_default_model_is_hey_frank():
    assert WakeWordService({}).model_name == "hey_frank_v5"


def test_available_and_set_model(tmp_path, monkeypatch):
    _models(tmp_path, monkeypatch, ["hey_frank_v5", "hey_stackchan_v1"])
    ww = WakeWordService({})
    assert WakeWordService.available_models() == ["hey_frank_v5", "hey_stackchan_v1"]
    assert ww.set_model("hey_stackchan_v1") is True
    assert ww.model_name == "hey_stackchan_v1"
    assert ww.set_model("hey_stackchan_v1") is False      # no-op
    assert ww.set_model("does_not_exist") is False
    assert ww.model_name == "hey_stackchan_v1"


def test_set_model_reloads_armed_listener(tmp_path, monkeypatch):
    _models(tmp_path, monkeypatch, ["hey_frank_v5", "hey_m5_v3"])
    ww = WakeWordService({})
    calls = []
    monkeypatch.setattr(ww, "start", lambda cb: calls.append((ww.model_name, cb)))
    ww._active, ww._callback = True, "cb"
    ww.set_model("hey_m5_v3")
    assert calls == [("hey_m5_v3", "cb")]
