from types import SimpleNamespace as T

from apps.meeting.report import (
    Line, assign_speakers, format_stamp, md_to_html, merge_turns,
    render_html, render_markdown, speaker_label, talk_time, transcript_text,
)


def turns():
    return [T(start=0.0, end=10.0, speaker=0),
            T(start=10.0, end=20.0, speaker=1),
            T(start=20.0, end=30.0, speaker=0)]


def test_stamp_and_label():
    assert format_stamp(65) == "[01:05]"
    assert format_stamp(3725) == "[1:02:05]"
    assert speaker_label(0) == "Speaker 1"
    assert speaker_label(None) == "Unknown"


def test_assign_by_max_overlap():
    lines = [Line(1, 9, "hi"), Line(8, 19, "mostly B"), Line(21, 29, "A again")]
    assign_speakers(lines, turns())
    assert [l.speaker for l in lines] == [0, 1, 0]


def test_assign_nearest_within_tolerance_else_none():
    ts = [T(start=0.0, end=5.0, speaker=2)]
    near, far = Line(6.0, 6.5, "near"), Line(40.0, 41.0, "far")
    assign_speakers([near, far], ts)
    assert near.speaker == 2
    assert far.speaker is None


def test_merge_consecutive_same_speaker():
    lines = [Line(0, 3, "a", 0), Line(4, 6, "b", 0), Line(7, 9, "c", 1),
             Line(30, 31, "d", 1)]
    out = merge_turns(lines, gap=4)
    assert [(l.text, l.speaker) for l in out] == [("a b", 0), ("c", 1), ("d", 1)]


def test_talk_time_and_transcript_text():
    assert talk_time(turns()) == {0: 20.0, 1: 10.0}
    text = transcript_text([Line(0, 3, "hello", 0), Line(4, 5, "plain")])
    assert text.splitlines() == ["[00:00] Speaker 1: hello", "[00:04] plain"]


def test_md_to_html_basics_and_escaping():
    html = md_to_html("## Decisions\n- ship **v5**\n- [ ] Bob: test <it>\n")
    assert "<h3>Decisions</h3>" in html and "<ul>" in html  # ## -> h3 (h1/h2 are the page title)
    assert "<strong>v5</strong>" in html
    assert "&lt;it&gt;" in html


def test_render_html_and_markdown():
    lines = [Line(0, 3, "hello", 0), Line(4, 6, "hi back", 1)]
    talk = {0: 3.0, 1: 2.0}
    html = render_html("Meeting", "Nov 7 · 6m", "## Summary\nok", lines, talk)
    assert html.lower().startswith("<!doctype html")
    assert "Speaker 1" in html and "Speaker 2" in html and "hi back" in html
    no_tx = render_html("Meeting", "m", "## Summary\nok", lines, talk,
                        include_transcript=False)
    assert "hi back" not in no_tx
    md = render_markdown("Meeting", "m", "## Summary\nok", lines, talk)
    assert "Speaker 2: hi back" in md


def test_clean_summary_collapses_speaker_rambling_and_none_items():
    from apps.meeting.report import clean_summary
    md = ("## Summary\n- did things\n\n## Action Items\n"
          "- [ ] No specific individual tasks with deadlines were assigned.\n- [ ] None\n\n"
          "## Speakers\n- Speaker 1: Laura, PM.\n- Speaker 3: Andrew (note: also says Greg...\n"
          "    - Let's re-evaluate the intro.\n    - Speaker 4 appears at [01:46].\n\n"
          "- Speaker 1: Laura, Project Manager\n- Speaker 3: Andrew, Marketing\n"
          "- Speaker 4: Greg, User Interface\n")
    out = clean_summary(md)
    assert "re-evaluate" not in out
    assert out.count("Speaker 1:") == 1 and "Laura, Project Manager" in out
    assert "- Speaker 4: Greg, User Interface" in out
    assert "## Action Items\n- None" in out
    assert "- did things" in out


def test_clean_summary_keeps_real_action_items():
    from apps.meeting.report import clean_summary
    md = "## Action Items\n- [ ] Sarah: send the deck by Friday\n"
    assert "Sarah: send the deck" in clean_summary(md)


def test_extract_title_and_slug():
    from apps.meeting.report import extract_title, slugify
    t, rest = extract_title("# Remote Control Design Kickoff\n\n## Summary\n- a\n")
    assert t == "Remote Control Design Kickoff"
    assert rest.startswith("## Summary")
    assert extract_title("## Summary\n- a") == ("", "## Summary\n- a")
    assert slugify("Remote Control: Design Kickoff!") == "remote-control-design-kickoff"


def test_clean_summary_drops_stray_none_among_real_items():
    from apps.meeting.report import clean_summary
    md = "## Action Items\n*   [ ] Laura: send notes\n*   [ ] None\n"
    out = clean_summary(md)
    assert "Laura: send notes" in out and "None" not in out
