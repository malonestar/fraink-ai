import uuid
from pathlib import Path

import yaml


def load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with open(p) as f:
        return yaml.safe_load(f)


def generate_client_id() -> str:
    return str(uuid.uuid4())


def generate_client_id_if_missing(config: dict, path: str) -> dict:
    if not config.get("server", {}).get("client_id"):
        config.setdefault("server", {})["client_id"] = generate_client_id()
        with open(path, "w") as f:
            yaml.dump(config, f, default_flow_style=False)
    return config
