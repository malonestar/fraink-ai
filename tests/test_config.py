import os
import tempfile
import pytest
import yaml

from core.config import load_config, generate_client_id, generate_client_id_if_missing


def test_load_config_reads_yaml():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump({"server": {"device_id": "test-01"}}, f)
        f.flush()
        config = load_config(f.name)
    os.unlink(f.name)
    assert config["server"]["device_id"] == "test-01"


def test_load_config_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/config.yaml")


def test_generate_client_id_creates_uuid():
    cid = generate_client_id()
    assert len(cid) == 36
    assert cid.count("-") == 4


def test_generate_client_id_persists_to_config():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        yaml.dump({"server": {"client_id": ""}}, f)
        path = f.name
    config = load_config(path)
    config = generate_client_id_if_missing(config, path)
    assert config["server"]["client_id"] != ""
    config2 = load_config(path)
    assert config2["server"]["client_id"] == config["server"]["client_id"]
    os.unlink(path)
