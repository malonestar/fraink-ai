import pytest

from core.network import is_home_network


def test_home_network_match():
    assert is_home_network("192.168.0.42", "192.168.0.0/24") is True


def test_home_network_mismatch():
    assert is_home_network("10.0.0.5", "192.168.0.0/24") is False


def test_home_network_different_subnet():
    assert is_home_network("192.168.1.100", "192.168.0.0/24") is False


def test_home_network_wider_mask():
    assert is_home_network("192.168.1.100", "192.168.0.0/16") is True


def test_home_network_tailscale_ip():
    assert is_home_network("100.64.0.1", "192.168.0.0/24") is False
