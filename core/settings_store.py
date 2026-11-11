"""Persistent user-settings overlay (data/settings.json).

Overlay wins over config.yaml. The app NEVER writes config.yaml — the
device's hand-edited copy stays authoritative. data/ is gitignored, so
the device's settings.json survives deploys (the deploy tar never
contains it).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Union

log = logging.getLogger(__name__)

_APP_DIR = Path(__file__).resolve().parent.parent  # core/ -> repo root
_MISSING = object()


class SettingsStore:
    def __init__(self, path: Union[str, Path] = _APP_DIR / "data" / "settings.json"):
        self._path = Path(path)
        self._data = self._load()

    def _load(self) -> dict:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            if self._path.exists():
                log.warning("Settings file unreadable (%s) — using defaults", e)
            return {}
        if not isinstance(data, dict):
            log.warning("Settings file is not a JSON object — using defaults")
            return {}
        return data

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any):
        old = self._data.get(key, _MISSING)
        self._data[key] = value
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except OSError as e:
            # Roll back so get() stays consistent with what's on disk.
            if old is _MISSING:
                self._data.pop(key, None)
            else:
                self._data[key] = old
            log.error("Failed to persist settings: %s", e)
