"""Pure helpers for the Meeting app: speaker alignment + report rendering.

No I/O and no device code here, so it's all unit-testable on the dev box and
reusable by tools/meeting_dryrun.py.

Pipeline:
  whisper segments (absolute seconds)  +  diarization turns ("who spoke when")
      -> assign_speakers()  -> each segment gets a speaker (or None)
      -> merge_turns()      -> consecutive same-speaker lines become one turn
      -> transcript_text() / render_html()
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass
from typing import Optional

# How far (s) a segment with no overlap may sit from a diarized turn and
# still borrow its speaker (whisper and the diarizer disagree at edges).
NEAREST_TOLERANCE = 2.0
# Merge consecutive same-speaker segments unless separated by a long gap.
MERGE_GAP = 4.0

SPEAKER_COLORS = ["#2563eb", "#d97706", "#059669", "#db2777",
                  "#7c3aed", "#0891b2", "#65a30d", "#dc2626"]


@dataclass
class Line:
    start: float
    end: float
    text: str
    speaker: Optional[int] = None


def format_stamp(secs: float) -> str:
    secs = int(secs)
    h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
    return f"[{h}:{m:02d}:{s:02d}]" if h else f"[{m:02d}:{s:02d}]"


def speaker_label(speaker: Optional[int]) -> str:
    return f"Speaker {speaker + 1}" if speaker is not None else "Unknown"


def assign_speakers(lines: list[Line], turns) -> list[Line]:
    """Give each transcript line the speaker it overlaps most.

    turns: objects with .start/.end/.speaker (aiservices.SpeakerTurn).
    Lines with no overlap take the nearest turn within NEAREST_TOLERANCE,
    otherwise stay None.
    """
    for line in lines:
        overlap: dict[int, float] = {}
        for t in turns:
            o = min(line.end, t.end) - max(line.start, t.start)
            if o > 0:
                overlap[t.speaker] = overlap.get(t.speaker, 0.0) + o
        if overlap:
            line.speaker = max(overlap, key=overlap.get)
            continue
        mid = (line.start + line.end) / 2
        best, best_d = None, NEAREST_TOLERANCE
        for t in turns:
            d = 0.0 if t.start <= mid <= t.end else min(abs(mid - t.start), abs(mid - t.end))
            if d <= best_d:
                best, best_d = t.speaker, d
        line.speaker = best
    return lines


def merge_turns(lines: list[Line], gap: float = MERGE_GAP) -> list[Line]:
    out: list[Line] = []
    for line in lines:
        if (out and out[-1].speaker == line.speaker
                and line.start - out[-1].end <= gap):
            out[-1] = Line(out[-1].start, line.end,
                           f"{out[-1].text} {line.text}", out[-1].speaker)
        else:
            out.append(Line(line.start, line.end, line.text, line.speaker))
    return out


def talk_time(turns) -> dict[int, float]:
    totals: dict[int, float] = {}
    for t in turns:
        totals[t.speaker] = totals.get(t.speaker, 0.0) + max(0.0, t.end - t.start)
    return dict(sorted(totals.items()))


def transcript_text(lines: list[Line], with_speakers: bool = True) -> str:
    rows = []
    for line in lines:
        if with_speakers and line.speaker is not None:
            rows.append(f"{format_stamp(line.start)} {speaker_label(line.speaker)}: {line.text}")
        else:
            rows.append(f"{format_stamp(line.start)} {line.text}")
    return "\n".join(rows)


def fmt_duration(secs: float) -> str:
    secs = int(round(secs))
    h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s" if m else f"{s}s"


# --- markdown (the tiny subset the summarizer emits) -> HTML ---------------

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITAL = re.compile(r"(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])")
_CODE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    text = _html.escape(text)
    text = _CODE.sub(r"<code>\1</code>", text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    return _ITAL.sub(r"<em>\1</em>", text)


def md_to_html(md: str) -> str:
    """Headings, bullet/numbered lists, checkboxes, bold/italic/code, paragraphs."""
    out: list[str] = []
    list_tag: Optional[str] = None

    def close_list():
        nonlocal list_tag
        if list_tag:
            out.append(f"</{list_tag}>")
            list_tag = None

    for raw in (md or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            close_list()
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            close_list()
            level = min(len(m.group(1)) + 1, 6)  # '#' -> h2 inside the report
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            continue
        m = re.match(r"^[-*+]\s+(?:\[( |x|X)\]\s+)?(.*)$", stripped)
        if m:
            if list_tag != "ul":
                close_list()
                out.append("<ul>")
                list_tag = "ul"
            box = ""
            if m.group(1) is not None:
                # CSS-drawn checkbox: the U+2610 glyph shows as a "missing
                # character" box in some mail clients (seen on iOS Gmail).
                fill = "#3b82f6" if m.group(1).lower() == "x" else "transparent"
                box = ('<span style="display:inline-block;width:10px;height:10px;'
                       'border:1.5px solid #8a8f98;border-radius:3px;margin-right:7px;'
                       f'vertical-align:-1px;background:{fill}"></span>')
            li = "<li style=\"list-style:none;margin-left:-18px\">" if box else "<li>"
            out.append(f"{li}{box}{_inline(m.group(2))}</li>")
            continue
        m = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if m:
            if list_tag != "ol":
                close_list()
                out.append("<ol>")
                list_tag = "ol"
            out.append(f"<li>{_inline(m.group(1))}</li>")
            continue
        close_list()
        out.append(f"<p>{_inline(stripped)}</p>")
    close_list()
    return "\n".join(out)


# --- report page -----------------------------------------------------------

_CSS = """
body{margin:0;background:#f4f5f7;font:15px/1.55 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1f2328}
.wrap{max-width:760px;margin:0 auto;padding:24px 16px}
.card{background:#fff;border:1px solid #e3e5e8;border-radius:10px;padding:20px 22px;margin-bottom:16px}
h1{font-size:22px;margin:0 0 4px}
.kicker{font-size:11px;letter-spacing:.08em;text-transform:uppercase;color:#2563eb;font-weight:600;margin-bottom:4px}
.card.head{border-top:3px solid #2563eb}
.meta{color:#656d76;font-size:13px}
h2{font-size:17px;margin:18px 0 8px}
h3{font-size:15px;margin:14px 0 6px}
ul,ol{padding-left:22px;margin:6px 0}
li{margin:3px 0}
.chips{margin-top:10px}
.chip{display:inline-block;border-radius:999px;padding:2px 10px;margin:3px 6px 3px 0;font-size:12px;color:#fff}
.turn{margin:0 0 12px}
.who{font-weight:600;font-size:13px}
.ts{color:#8c959f;font-size:12px;font-variant-numeric:tabular-nums;margin-left:6px}
.say{margin-top:2px}
.foot{color:#8c959f;font-size:12px;text-align:center;margin-top:8px}
"""


def _color(speaker: Optional[int]) -> str:
    return "#6e7781" if speaker is None else SPEAKER_COLORS[speaker % len(SPEAKER_COLORS)]


def speaker_chips(talk: dict[int, float]) -> str:
    total = sum(talk.values()) or 1.0
    chips = []
    for spk, secs in talk.items():
        pct = round(100 * secs / total)
        chips.append(f'<span class="chip" style="background:{_color(spk)}">'
                     f'{speaker_label(spk)} &middot; {fmt_duration(secs)} ({pct}%)</span>')
    return f'<div class="chips">{"".join(chips)}</div>' if chips else ""


def render_html(title: str, meta: str, summary_md: str, lines: list[Line],
                talk: dict[int, float], include_transcript: bool = True,
                footer: str = "") -> str:
    """Self-contained HTML (inline CSS) — works as an email body and as an
    attachment opened on a phone."""
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        f"<title>{_html.escape(title)}</title><style>{_CSS}</style></head><body>",
        "<div class='wrap'>",
        f"<div class='card head'><div class='kicker'>Meeting notes</div><h1>{_html.escape(title)}</h1>",
        f"<div class='meta'>{_html.escape(meta)}</div>{speaker_chips(talk)}</div>",
    ]
    if summary_md:
        parts.append(f"<div class='card'>{md_to_html(summary_md)}</div>")
    if include_transcript and lines:
        parts.append("<div class='card'><h2>Transcript</h2>")
        for line in lines:
            who = speaker_label(line.speaker) if line.speaker is not None else ""
            head = (f"<span class='who' style='color:{_color(line.speaker)}'>"
                    f"{_html.escape(who)}</span>") if who else ""
            parts.append(
                f"<div class='turn'>{head}<span class='ts'>{format_stamp(line.start)}</span>"
                f"<div class='say'>{_html.escape(line.text)}</div></div>")
        parts.append("</div>")
    if footer:
        parts.append(f"<div class='foot'>{_html.escape(footer)}</div>")
    parts.append("</div></body></html>")
    return "\n".join(parts)


def render_markdown(title: str, meta: str, summary_md: str, lines: list[Line],
                    talk: dict[int, float]) -> str:
    out = [f"# {title}", "", f"_{meta}_", ""]
    if talk:
        total = sum(talk.values()) or 1.0
        out.append("**Talk time:** " + ", ".join(
            f"{speaker_label(s)} {fmt_duration(t)} ({round(100 * t / total)}%)"
            for s, t in talk.items()))
        out.append("")
    if summary_md:
        out += [summary_md.strip(), ""]
    out += ["## Transcript", "", transcript_text(lines), ""]
    return "\n".join(out)


# --- summary hygiene ----------------------------------------------------------
# Reasoning models sometimes "think out loud" inside a section (seen with
# qwen3.8 on the AMI test: a page of "Let's re-evaluate..." under Speakers).
# Keep the report to what the prompt asked for.

_SPEAKER_LINE = re.compile(r"^\s*[-*]\s*\**Speaker\s+(\d+)\**\s*:\s*(.+)$", re.I)
_NONE_ITEM = re.compile(r"^\s*[-*]\s*(\[[ xX]\]\s*)?(none\.?|no (specific )?(individual )?"
                        r"(tasks|action items).*)$", re.I)
SPEAKER_NOTE_MAX = 140


def _sections(md: str) -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = [("", [])]
    for line in md.splitlines():
        if line.startswith("## "):
            out.append((line, []))
        else:
            out[-1][1].append(line)
    return out


def clean_summary(md: str) -> str:
    """Collapse ## Speakers to one line per speaker (last word wins) and
    ## Action Items that only say 'none' to a single '- None'."""
    if not md:
        return md
    rebuilt: list[str] = []
    for head, body in _sections(md):
        title = head[3:].strip().lower()
        if title == "speakers":
            seen: dict[int, str] = {}
            for line in body:
                m = _SPEAKER_LINE.match(line)
                if m:
                    note = m.group(2).strip()
                    if len(note) > SPEAKER_NOTE_MAX:
                        note = note[:SPEAKER_NOTE_MAX].rsplit(" ", 1)[0] + "…"
                    seen[int(m.group(1))] = note
            body = [f"- Speaker {n}: {seen[n]}" for n in sorted(seen)] or body
            body.append("")
        elif title == "action items":
            items = [l for l in body if l.strip()]
            if items and all(_NONE_ITEM.match(l) for l in items):
                body = ["- None", ""]
            else:  # real items: drop stray "- [ ] None" filler lines
                body = [l for l in body if not _NONE_ITEM.match(l)]
        if head:
            rebuilt.append(head)
        rebuilt.extend(body)
    return "\n".join(rebuilt).strip() + "\n"


_TITLE = re.compile(r"^\s*#\s+(?!#)(.+?)\s*#*\s*$")
TITLE_MAX = 70


def extract_title(md: str) -> tuple[str, str]:
    """Pull the summarizer's leading '# Title' line off the summary.
    -> (title or '', summary without it)."""
    if not md:
        return "", md
    lines = md.splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        m = _TITLE.match(line)
        if not m:
            return "", md
        title = m.group(1).strip().strip('"*_ ').rstrip(".")
        if len(title) > TITLE_MAX:
            title = title[:TITLE_MAX].rsplit(" ", 1)[0]
        return title, "\n".join(lines[i + 1:]).lstrip("\n")
    return "", md


def slugify(text: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-")
