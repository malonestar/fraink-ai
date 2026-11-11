import pytest

from core.icons import render, _CACHE

KNOWN = ["voice", "notes", "timer", "ha", "wifi", "settings", "tailscale"]
WHITE = (255, 255, 255)
DIM = (128, 128, 128)


@pytest.mark.parametrize("name", KNOWN)
def test_known_icons_render_at_requested_size(name):
    img = render(name, 96, WHITE)
    assert img.size == (96, 96)
    assert img.mode == "RGBA"
    assert img.getbbox() is not None  # something was actually drawn


@pytest.mark.parametrize("name", KNOWN)
def test_icons_render_small_and_dim(name):
    img = render(name, 56, DIM)
    assert img.size == (56, 56)
    assert img.getbbox() is not None


def test_unknown_name_falls_back_without_raising():
    img = render("definitely-not-an-icon", 56, WHITE)
    assert img.size == (56, 56)
    assert img.getbbox() is not None


def test_cache_returns_same_object():
    a = render("voice", 96, WHITE)
    b = render("voice", 96, WHITE)
    assert a is b


def test_distinct_variants_are_distinct_objects():
    a = render("voice", 96, WHITE)
    b = render("voice", 56, DIM)
    assert a is not b
    assert ("voice", 96, WHITE) in _CACHE
    assert ("voice", 56, DIM) in _CACHE
