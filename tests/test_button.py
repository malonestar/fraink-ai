import asyncio
import pytest

from core.button import ButtonEvent, parse_pisugar_event, ButtonListener


def test_parse_single_tap():
    assert parse_pisugar_event("single") == ButtonEvent.SINGLE


def test_parse_double_tap():
    assert parse_pisugar_event("double") == ButtonEvent.DOUBLE


def test_parse_long_press():
    assert parse_pisugar_event("long") == ButtonEvent.LONG


def test_parse_unknown_returns_none():
    assert parse_pisugar_event("triple") is None


def test_parse_strips_whitespace():
    assert parse_pisugar_event("  single \n") == ButtonEvent.SINGLE


@pytest.mark.asyncio
async def test_button_listener_emits_with_source():
    received = []

    async def on_event(event, source):
        received.append((event, source))

    listener = ButtonListener(on_event, source="pisugar")
    await listener._emit(ButtonEvent.SINGLE)
    assert received == [(ButtonEvent.SINGLE, "pisugar")]


@pytest.mark.asyncio
async def test_button_listener_default_source_is_pisugar():
    received = []

    async def on_event(event, source):
        received.append((event, source))

    listener = ButtonListener(on_event)
    await listener._emit(ButtonEvent.LONG)
    assert received == [(ButtonEvent.LONG, "pisugar")]
