"""Where frAInk keeps its runtime data: next to the code (the repo checkout)."""

import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def app_path(*parts: str) -> str:
    return os.path.join(APP_DIR, *parts)
