import json

from core.settings_store import SettingsStore


def test_get_returns_default_when_file_missing(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    assert store.get("listen_mode", "auto") == "auto"
    assert store.get("missing") is None


def test_set_then_get_roundtrip(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.set("listen_mode", "manual")
    assert store.get("listen_mode", "auto") == "manual"


def test_set_persists_across_instances(tmp_path):
    path = tmp_path / "settings.json"
    SettingsStore(path).set("wakeword_enabled", False)
    fresh = SettingsStore(path)
    assert fresh.get("wakeword_enabled", True) is False


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json!!", encoding="utf-8")
    store = SettingsStore(path)
    assert store.get("listen_mode", "auto") == "auto"
    # And it can still write afterwards.
    store.set("listen_mode", "manual")
    assert SettingsStore(path).get("listen_mode") == "manual"


def test_non_dict_json_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    store = SettingsStore(path)
    assert store.get("anything", "x") == "x"


def test_set_creates_parent_dir(tmp_path):
    path = tmp_path / "data" / "settings.json"  # data/ does not exist yet
    SettingsStore(path).set("k", "v")
    assert json.loads(path.read_text(encoding="utf-8")) == {"k": "v"}


def test_write_is_atomic_no_tmp_left_behind(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.set("k", 1)
    store.set("k", 2)
    leftovers = [p for p in tmp_path.iterdir() if p.name != "settings.json"]
    assert leftovers == []
    assert json.loads(path.read_text(encoding="utf-8")) == {"k": 2}


def test_set_rolls_back_in_memory_on_write_failure(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.set("k", "initial")

    def boom(self, target):
        raise OSError("disk full")
    monkeypatch.setattr(type(path), "replace", boom)

    store.set("k", "updated")

    # In-memory must reflect the on-disk truth, not the failed write.
    assert store.get("k") == "initial"
    assert SettingsStore(path).get("k") == "initial"
