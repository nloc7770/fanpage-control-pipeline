"""ASS subtitle builder + libass burn-in helper.

Two public renderers:

* :func:`build_ass` -- legacy "condensed lines" path. Each item is
  ``{"start", "end", "text", "emphasis_words"}``. Kept for back-compat with
  the older condensed-subtitle flow.
* :func:`build_ass_words` -- new dynamic karaoke path. Takes a flat list of
  word dicts (``{"word", "start", "end", "speaker"?}``) and packs them into
  3-5-word lines with per-word ``\\k`` karaoke timing and per-word color
  override for emphasized words.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ASS_HEADER_TEMPLATE = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes
Kerning: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{primary},&H00000000,{outline},&H80000000,1,0,0,0,100,100,0,0,1,{outline_w},2,{alignment},60,60,{margin_v},1
Style: Emphasis,{font},{size},{emphasis},&H00000000,{outline},&H80000000,1,0,0,0,100,100,0,0,1,{outline_w},3,{alignment},60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

# Emotion / emphasis keywords (lowercase). Words matching these get the
# emphasis color regardless of length.
_EMOTION_WORDS = frozenset(
    {
        # English
        "never", "always", "shocking", "shocked", "incredible", "amazing",
        "unbelievable", "crazy", "wow", "huge", "massive", "killed",
        "destroyed", "secret", "stop", "wait", "look", "watch", "warning",
        "fired", "rich", "free", "viral", "exposed", "boom", "explosion",
        "feeding", "frenzy", "hunting", "violent", "wild",
        # Vietnamese (matching the narrative_script_vi tone)
        "không", "rất", "tuyệt", "kinh", "khủng", "sốc", "đỉnh", "đỉnh nhất",
        "viral", "lừa", "bí mật", "khám phá", "bất ngờ", "vĩ đại",
    }
)

# Speaker color rotation -- ASS color literals in &HAABBGGRR.
_SPEAKER_COLORS = [
    "&H00FFFFFF",  # white
    "&H00FFFF00",  # cyan
    "&H0000FFFF",  # yellow
]


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _hex_to_ass_color(hex_color: str | None, fallback: str = "&H00FFFFFF") -> str:
    """Convert ``"#RRGGBB"`` or ``"#AARRGGBB"`` to ASS ``&HAABBGGRR``."""
    if not hex_color or not hex_color.startswith("#"):
        return fallback
    h = hex_color.lstrip("#")
    if len(h) == 6:
        r, g, b = h[0:2], h[2:4], h[4:6]
        return f"&H00{b.upper()}{g.upper()}{r.upper()}"
    if len(h) == 8:
        a, r, g, b = h[0:2], h[2:4], h[4:6], h[6:8]
        return f"&H{a.upper()}{b.upper()}{g.upper()}{r.upper()}"
    return fallback


def _fmt_ts(seconds: float) -> str:
    """ASS timestamp ``h:mm:ss.cs`` (centiseconds)."""
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds - (h * 3600) - (m * 60)
    return f"{h}:{m:02d}:{s:05.2f}"


def _ass_header(style: dict[str, Any] | None) -> tuple[str, int, int]:
    style = style or {}
    position = (style.get("position") or "bottom").lower()
    if position == "top":
        alignment, margin_v = 8, 200
    elif position == "middle":
        alignment, margin_v = 5, 0
    else:
        alignment, margin_v = 2, 320  # bottom-third (raised a bit from 280)

    header = ASS_HEADER_TEMPLATE.format(
        font=style.get("font") or "Inter",
        size=int(style.get("size") or 84),
        primary=_hex_to_ass_color(style.get("primary_color"), "&H00FFFFFF"),
        outline=_hex_to_ass_color(style.get("outline_color"), "&H00000000"),
        outline_w=int(style.get("outline_width") or 4),
        emphasis=_hex_to_ass_color(style.get("emphasis_color"), "&H00FF00FF"),  # violet
        alignment=alignment,
        margin_v=margin_v,
    )
    return header, alignment, margin_v


# ---------------------------------------------------------------------------
# Legacy: condensed lines (kept so older callers still work).
# ---------------------------------------------------------------------------


def build_ass(
    subtitle_chunks: Sequence[dict[str, Any]],
    style: dict[str, Any] | None = None,
) -> str:
    """Render an ASS document from condensed subtitle lines.

    Each chunk: ``{"start": float, "end": float, "text": str,
    "emphasis_words": [str, ...]}``. ``style`` mirrors
    :class:`shared_py.llm_contracts.SubtitleStyle`.
    """
    header, _, _ = _ass_header(style)
    style = style or {}

    lines: list[str] = []
    for chunk in subtitle_chunks:
        start = _fmt_ts(chunk.get("start", 0.0))
        end = _fmt_ts(chunk.get("end", 0.0))
        text = str(chunk.get("text", "")).replace("\n", "\\N")
        emphasis = {w.lower() for w in (chunk.get("emphasis_words") or [])}
        if emphasis and style.get("word_highlight", True):
            words = text.split()
            rendered: list[str] = []
            for w in words:
                if w.lower().strip(".,!?:;\"'") in emphasis:
                    color = _hex_to_ass_color(
                        style.get("emphasis_color"), "&H0000FFFF"
                    )
                    rendered.append(f"{{\\c{color}}}{w}{{\\r}}")
                else:
                    rendered.append(w)
            text = " ".join(rendered)
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    return header + "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# New: dynamic word-level karaoke subs.
# ---------------------------------------------------------------------------


def _is_emphasis(word: str) -> bool:
    """Return True if a word deserves the emphasis color."""
    stripped = word.lower().strip(".,!?:;\"'-")
    if not stripped:
        return False
    if stripped in _EMOTION_WORDS:
        return True
    # Long words tend to be the "loaded" ones in a spoken sentence.
    if len(stripped) >= 7:
        return True
    return False


def _speaker_color(speaker: Any) -> str:
    """Pick a stable color per speaker label. Falls back to white."""
    if speaker is None or speaker == "":
        return _SPEAKER_COLORS[0]
    key = str(speaker)
    idx = abs(hash(key)) % len(_SPEAKER_COLORS)
    return _SPEAKER_COLORS[idx]


def _group_words_into_lines(
    words: Sequence[dict[str, Any]],
    *,
    max_words: int = 4,
    max_gap_s: float = 0.7,
    max_line_dur_s: float = 2.5,
) -> list[list[dict[str, Any]]]:
    """Pack a flat word stream into short lines (3-5 words, 1-3s each).

    A new line starts when:

    * the current line already has ``max_words`` words, OR
    * the time gap between the previous word's ``end`` and this word's
      ``start`` exceeds ``max_gap_s`` (sentence boundary), OR
    * the line would exceed ``max_line_dur_s``, OR
    * the speaker label changes mid-line.
    """
    out: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    line_start = 0.0
    prev_end = 0.0
    prev_speaker: Any = None

    for w in words:
        ws = float(w.get("start", 0.0))
        we = float(w.get("end", ws + 0.1))
        if we <= ws:
            we = ws + 0.08
        spk = w.get("speaker")

        if not current:
            current = [dict(w, start=ws, end=we)]
            line_start = ws
            prev_end = we
            prev_speaker = spk
            continue

        boundary = (
            len(current) >= max_words
            or (ws - prev_end) > max_gap_s
            or (we - line_start) > max_line_dur_s
            or (prev_speaker is not None and spk is not None and spk != prev_speaker)
        )
        if boundary:
            out.append(current)
            current = [dict(w, start=ws, end=we)]
            line_start = ws
            prev_end = we
            prev_speaker = spk
        else:
            current.append(dict(w, start=ws, end=we))
            prev_end = we
            if prev_speaker is None:
                prev_speaker = spk

    if current:
        out.append(current)
    return out


def build_ass_words(
    words: Sequence[dict[str, Any]],
    style: dict[str, Any] | None = None,
    *,
    max_words_per_line: int = 4,
) -> str:
    """Build an ASS file with word-level karaoke timing and emphasis.

    ``words`` is a flat sequence of ``{"word", "start", "end", "speaker"?}``
    items (each timestamp in *the rendered clip's* timeline, not the source).
    The function:

    * Packs words into 3-5 word lines via :func:`_group_words_into_lines`.
    * Emits one ``Dialogue`` per line.
    * Each word inside a line is wrapped in ``{\\kNN}`` for karaoke timing,
      plus a per-word ``{\\c&HAABBGGRR&}`` color override for emphasis or
      speaker color rotation.
    * Lines use a bold, large white font with 4px black outline, bottom-third
      placement.

    The function is robust to empty input -- it returns just the header so the
    burn-in step can still execute (libass is fine with an empty document).
    """
    if not words:
        return _ass_header(style)[0] + "\n"

    header, _, _ = _ass_header(style)
    style = style or {}
    emphasis_color = _hex_to_ass_color(
        style.get("emphasis_color"), "&H00FF00FF"  # violet
    )
    primary_color = _hex_to_ass_color(style.get("primary_color"), "&H00FFFFFF")

    lines = _group_words_into_lines(words, max_words=max_words_per_line)

    out: list[str] = []
    for line in lines:
        if not line:
            continue
        line_start = float(line[0]["start"])
        line_end = float(line[-1]["end"])
        if line_end <= line_start:
            line_end = line_start + 0.5

        spk_color = _speaker_color(line[0].get("speaker"))
        # Per-line color reset to the speaker color (white by default).
        prefix = f"{{\\c{spk_color}}}" if spk_color != primary_color else ""

        # Build the karaoke text. \k is in centiseconds; we use \kf for a
        # smooth fill effect.
        parts: list[str] = []
        # Anchor the line by inserting a leading karaoke "delay" if there's
        # any gap between line_start and the first word -- in practice they
        # match, but be defensive.
        for i, w in enumerate(line):
            wt = max(0.0, float(w["end"]) - float(w["start"]))
            k_cs = max(1, int(round(wt * 100)))
            word_txt = str(w.get("word", "")).strip()
            # Escape ASS special chars.
            word_txt = (
                word_txt.replace("\\", "\\\\")
                .replace("{", "\\{")
                .replace("}", "\\}")
            )
            if _is_emphasis(word_txt):
                # Per-word emphasis: switch color, draw karaoke, then reset
                # to the line color so subsequent words don't inherit.
                token = (
                    f"{{\\kf{k_cs}\\c{emphasis_color}}}{word_txt}"
                    f"{{\\c{spk_color if prefix else primary_color}}}"
                )
            else:
                token = f"{{\\kf{k_cs}}}{word_txt}"
            parts.append(token)

        text = prefix + " ".join(parts)

        start_ts = _fmt_ts(line_start)
        end_ts = _fmt_ts(line_end)
        out.append(f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{text}")

    return header + "\n".join(out) + "\n"


def _split_narrative_into_chunks(text: str, target_words: int = 4) -> list[str]:
    """Split a narrative paragraph into 3-5 word chunks at punctuation + word boundaries.

    Strategy: split on sentence-ending punctuation first (so each chunk is at
    most one sentence), then pack consecutive tokens up to ``target_words``.
    Trailing punctuation is preserved on the last token of each chunk.
    """
    text = (text or "").strip()
    if not text:
        return []

    # First, split into sentence-ish fragments on . ! ? ; … (keep delimiters).
    # We don't use a heavyweight NLP tokenizer: regex is fine for short
    # Vietnamese narrative paragraphs.
    import re as _re

    fragments = [
        f.strip() for f in _re.split(r"(?<=[.!?…;])\s+", text) if f.strip()
    ]
    chunks: list[str] = []
    for frag in fragments:
        words = frag.split()
        if not words:
            continue
        # Pack into ``target_words``-sized groups. If a sentence is shorter
        # than target_words it becomes its own chunk -- that's fine, short
        # punchy lines read well on social.
        for i in range(0, len(words), target_words):
            chunk = " ".join(words[i : i + target_words]).strip()
            if chunk:
                chunks.append(chunk)
    return chunks


def build_ass_from_narrative(
    text: str,
    total_duration_s: float,
    style: dict[str, Any] | None = None,
    *,
    target_words_per_line: int = 4,
    min_line_duration_s: float = 0.6,
    max_line_duration_s: float = 2.8,
) -> str:
    """Build ASS subtitles from a narrative script, spread evenly across the clip.

    Used when the source audio has been replaced by a TTS voiceover and we
    don't have word-level timestamps for the new audio. The viewer sees
    Vietnamese subtitles roughly loosely synced with the Vietnamese TTS --
    standard practice for re-voiced shorts.

    Each chunk is allocated an equal share of ``total_duration_s``, clamped to
    ``[min_line_duration_s, max_line_duration_s]``.

    Parameters
    ----------
    text:
        The narrative paragraph (typically ``EditPlan.narrative_script_vi``).
    total_duration_s:
        The rendered clip's duration in seconds (after silence-tighten /
        before-or-after audio replace -- pass the *final* duration).
    style:
        Optional :class:`SubtitleStyle` dict.
    target_words_per_line:
        Roughly how many words per displayed line.
    min_line_duration_s, max_line_duration_s:
        Clamp on per-line on-screen time.
    """
    chunks = _split_narrative_into_chunks(text, target_words=target_words_per_line)
    header, _, _ = _ass_header(style)
    if not chunks or total_duration_s <= 0:
        return header + "\n"

    # Even time distribution. Start slightly after t=0 (lead-in) and end
    # slightly before the clip end (so the last word doesn't get cut off by
    # any encoder rounding).
    lead_in = 0.10
    tail_pad = 0.05
    usable = max(0.0, float(total_duration_s) - lead_in - tail_pad)
    per_chunk = usable / len(chunks) if chunks else 0.0
    per_chunk = max(min_line_duration_s, min(max_line_duration_s, per_chunk))

    style_dict = style or {}
    emphasis_color = _hex_to_ass_color(
        style_dict.get("emphasis_color"), "&H00FF00FF"
    )

    out: list[str] = []
    t = lead_in
    for idx, chunk in enumerate(chunks):
        start_s = t
        # The last chunk extends to the end so we cover the full duration even
        # if per_chunk * n < usable (clamped on the upper bound above).
        if idx == len(chunks) - 1:
            end_s = max(start_s + min_line_duration_s, float(total_duration_s) - tail_pad)
        else:
            end_s = start_s + per_chunk
        if end_s <= start_s:
            end_s = start_s + min_line_duration_s

        # Emphasis: per-word color override for "loaded" words (matches the
        # existing dynamic-subs heuristic).
        words = chunk.split()
        rendered: list[str] = []
        for w in words:
            if _is_emphasis(w):
                rendered.append(f"{{\\c{emphasis_color}}}{w}{{\\r}}")
            else:
                rendered.append(w)
        text_line = " ".join(rendered)
        # Escape ASS specials we DIDN'T already insert (the override tags
        # we just added use { } intentionally -- the chunk text itself
        # shouldn't contain { or }).
        # We avoid escaping our own tags by only touching the raw words above.

        start_ts = _fmt_ts(start_s)
        end_ts = _fmt_ts(end_s)
        out.append(f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{text_line}")
        t = end_s

    return header + "\n".join(out) + "\n"


def build_ass_from_segments(
    segments: Sequence[dict[str, Any]],
    total_duration_s: float,
    style: dict[str, Any] | None = None,
    *,
    max_words_per_line: int = 5,
) -> str:
    """Build ASS subtitles whose timing mirrors ``narrative_segments``.

    Each segment is ``{"start", "end", "text_vi"}`` with times in seconds
    relative to the rendered clip. The function emits one or more Dialogue
    lines per segment: if a segment has more than ``max_words_per_line`` words
    it is split into evenly-timed sub-chunks inside the segment's window.

    Style mirrors :func:`build_ass_from_narrative` -- large white text with
    black outline, bottom-third placement (per ``style.position``).
    """
    header, _, _ = _ass_header(style)
    style_dict = style or {}
    emphasis_color = _hex_to_ass_color(
        style_dict.get("emphasis_color"), "&H00FF00FF"
    )

    cleaned: list[dict[str, Any]] = []
    for s in segments or []:
        try:
            s_start = max(0.0, float(s.get("start", 0.0)))
            s_end = float(s.get("end", s_start))
        except (TypeError, ValueError):
            continue
        if total_duration_s > 0:
            s_end = min(s_end, float(total_duration_s))
            s_start = min(s_start, float(total_duration_s))
        if s_end <= s_start:
            continue
        text = (s.get("text_vi") or s.get("text") or "").strip()
        if not text:
            continue
        cleaned.append({"start": s_start, "end": s_end, "text": text})

    if not cleaned:
        return header + "\n"

    out: list[str] = []
    for seg in cleaned:
        words = seg["text"].split()
        seg_start = seg["start"]
        seg_end = seg["end"]
        seg_dur = max(0.05, seg_end - seg_start)

        if len(words) <= max_words_per_line:
            chunks = [seg["text"]]
        else:
            # Split into ~equal-size chunks of <= max_words_per_line words.
            n_chunks = (len(words) + max_words_per_line - 1) // max_words_per_line
            # Re-balance: try to keep chunks roughly equal length.
            chunk_size = (len(words) + n_chunks - 1) // n_chunks
            chunks = [
                " ".join(words[i : i + chunk_size]).strip()
                for i in range(0, len(words), chunk_size)
            ]
            chunks = [c for c in chunks if c]
            if not chunks:
                chunks = [seg["text"]]

        per_chunk_dur = seg_dur / len(chunks)
        t = seg_start
        for ci, chunk in enumerate(chunks):
            line_start = t
            if ci == len(chunks) - 1:
                line_end = seg_end
            else:
                line_end = t + per_chunk_dur
            if line_end <= line_start:
                line_end = line_start + 0.1

            # Per-word emphasis colouring (matches build_ass_from_narrative).
            tokens = chunk.split()
            rendered: list[str] = []
            for w in tokens:
                if _is_emphasis(w):
                    rendered.append(f"{{\\c{emphasis_color}}}{w}{{\\r}}")
                else:
                    rendered.append(w)
            text_line = " ".join(rendered)

            start_ts = _fmt_ts(line_start)
            end_ts = _fmt_ts(line_end)
            out.append(
                f"Dialogue: 0,{start_ts},{end_ts},Default,,0,0,0,,{text_line}"
            )
            t = line_end

    return header + "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Dual-language (EN bottom + VI top) subtitles.
# ---------------------------------------------------------------------------


# ASS color literal &HAABBGGRR.
_TOP_VI_DEFAULT_PRIMARY = "&H0099F0FF"  # warm yellow-white (#FFF099 in RGB)
_BOTTOM_EN_DEFAULT_PRIMARY = "&H00FFFFFF"  # white
_BLACK_OUTLINE = "&H00000000"


_DUAL_HEADER_TEMPLATE = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes
Kerning: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: BottomEN,{en_font},{en_size},{en_primary},&H00000000,{en_outline},&HA0000000,0,0,0,0,100,100,0,0,1,{en_outline_w},2,2,60,60,{en_margin_v},1
Style: TopVI,{vi_font},{vi_size},{vi_primary},&H00000000,{vi_outline},&H80000000,1,0,0,0,100,100,0,0,1,{vi_outline_w},3,2,60,60,{vi_margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _strip_karaoke(text: str) -> str:
    """Remove ASS karaoke override blocks (``{\\k...}`` / ``{\\kf...}``)."""
    if not text:
        return ""
    import re as _re

    # Strip ``\k``, ``\kf``, ``\ko`` tags (with optional value) but leave other
    # override blocks untouched. We only strip the karaoke tags within ``{}``;
    # if a block has nothing left after stripping, drop the empty ``{}``.
    def _scrub_block(m: "_re.Match[str]") -> str:
        inner = m.group(1)
        inner = _re.sub(r"\\k[fo]?\d*", "", inner)
        inner = inner.strip()
        return f"{{{inner}}}" if inner else ""

    return _re.sub(r"\{([^}]*)\}", _scrub_block, text)


def _chunk_text_evenly(
    text: str,
    seg_start: float,
    seg_end: float,
    *,
    max_words: int = 5,
    max_chars: int = 30,
) -> list[tuple[float, float, str]]:
    """Split a long segment into roughly equal sub-chunks across its timespan.

    Returns a list of ``(start, end, text)`` tuples. If the text fits in a
    single chunk under both limits, returns ``[(seg_start, seg_end, text)]``.
    Karaoke tags are stripped from each chunk.
    """
    text = _strip_karaoke((text or "").strip())
    if not text:
        return []
    seg_start = float(seg_start)
    seg_end = float(seg_end)
    if seg_end <= seg_start:
        seg_end = seg_start + 0.1
    words = text.split()
    if len(words) <= max_words and len(text) <= max_chars:
        return [(seg_start, seg_end, text)]

    # Decide chunk count from BOTH word-count and char-count constraints.
    n_by_words = (len(words) + max_words - 1) // max_words
    n_by_chars = (len(text) + max_chars - 1) // max_chars
    n_chunks = max(1, n_by_words, n_by_chars)
    chunk_size = max(1, (len(words) + n_chunks - 1) // n_chunks)
    raw_chunks: list[str] = []
    for i in range(0, len(words), chunk_size):
        c = " ".join(words[i : i + chunk_size]).strip()
        if c:
            raw_chunks.append(c)
    if not raw_chunks:
        return [(seg_start, seg_end, text)]

    seg_dur = seg_end - seg_start
    per = seg_dur / len(raw_chunks)
    out: list[tuple[float, float, str]] = []
    t = seg_start
    for idx, c in enumerate(raw_chunks):
        a = t
        b = seg_end if idx == len(raw_chunks) - 1 else t + per
        if b <= a:
            b = a + 0.1
        out.append((a, b, c))
        t = b
    return out


def build_ass_dual(
    en_segments: Sequence[dict[str, Any]],
    vi_segments: Sequence[dict[str, Any]],
    total_duration_s: float,
    style: dict[str, Any] | None = None,
) -> str:
    """Build an ASS document with bilingual subtitles.

    * English (``en_segments``) appears at the BOTTOM as plain blocks.
    * Vietnamese (``vi_segments``) appears at the TOP as plain blocks.

    Both lists carry items shaped like ``{"start", "end", "text"}`` (or
    ``"text_vi"`` for the VI list) with times in the rendered clip's timeline.
    libass renders the two styles concurrently, giving the viewer a live
    bilingual readout that follows the original speaker's audio.

    ``style`` is an optional dict that may override:

    * ``font`` -- default ``"Inter"``; falls back to Roboto/Arial via libass.
    * ``en_size`` / ``vi_size`` -- pixel sizes (defaults 64 / 52 on 1080x1920).
    * ``en_primary_color`` / ``vi_primary_color`` -- ``#RRGGBB`` hex.
    * ``en_outline_width`` / ``vi_outline_width`` -- pixel outline.
    * ``en_margin_v`` / ``vi_margin_v`` -- vertical margins from edge.

    Long segments (>5 words / >30 chars) are split evenly across their span
    so the on-screen text never crowds. Karaoke ``\\k`` tags are stripped
    from any input text -- dual subs read better as static blocks.
    """
    style_dict = style or {}
    font = style_dict.get("font") or "Inter"
    en_size = int(style_dict.get("en_size") or style_dict.get("size") or 38)
    vi_size = int(style_dict.get("vi_size") or 68)
    en_primary = _hex_to_ass_color(
        style_dict.get("en_primary_color") or style_dict.get("primary_color"),
        _BOTTOM_EN_DEFAULT_PRIMARY,
    )
    vi_primary = _hex_to_ass_color(
        style_dict.get("vi_primary_color"),
        _TOP_VI_DEFAULT_PRIMARY,
    )
    en_outline = _hex_to_ass_color(
        style_dict.get("en_outline_color") or style_dict.get("outline_color"),
        _BLACK_OUTLINE,
    )
    vi_outline = _hex_to_ass_color(
        style_dict.get("vi_outline_color") or style_dict.get("outline_color"),
        _BLACK_OUTLINE,
    )
    en_outline_w = int(style_dict.get("en_outline_width") or 3)
    vi_outline_w = int(style_dict.get("vi_outline_width") or 4)
    # Stack the two lines together at the bottom: EN sits at the very bottom,
    # VI sits directly above EN with enough gap for legibility.
    # EN line height ~38px + padding => VI margin offset by ~60px.
    en_margin_v = int(style_dict.get("en_margin_v") or 120)
    vi_margin_v = int(style_dict.get("vi_margin_v") or 200)

    header = _DUAL_HEADER_TEMPLATE.format(
        en_font=font,
        en_size=en_size,
        en_primary=en_primary,
        en_outline=en_outline,
        en_outline_w=en_outline_w,
        en_margin_v=en_margin_v,
        vi_font=font,
        vi_size=vi_size,
        vi_primary=vi_primary,
        vi_outline=vi_outline,
        vi_outline_w=vi_outline_w,
        vi_margin_v=vi_margin_v,
    )

    out: list[str] = []

    def _emit(segments: Sequence[dict[str, Any]], style_name: str) -> None:
        for s in segments or []:
            try:
                a = max(0.0, float(s.get("start", 0.0)))
                b = float(s.get("end", a))
            except (TypeError, ValueError):
                continue
            if total_duration_s and total_duration_s > 0:
                a = min(a, float(total_duration_s))
                b = min(b, float(total_duration_s))
            if b <= a:
                continue
            raw = (s.get("text") or s.get("text_vi") or "").strip()
            if not raw:
                continue
            for ca, cb, ctxt in _chunk_text_evenly(raw, a, b):
                start_ts = _fmt_ts(ca)
                end_ts = _fmt_ts(cb)
                out.append(
                    f"Dialogue: 0,{start_ts},{end_ts},{style_name},,0,0,0,,{ctxt}"
                )

    _emit(en_segments, "BottomEN")
    _emit(vi_segments, "TopVI")

    return header + "\n".join(out) + ("\n" if out else "")


def build_segments_from_words(
    words: Sequence[dict[str, Any]],
    clip_start: float,
    clip_end: float,
    intervals: Sequence[tuple[float, float]],
    *,
    max_words: int = 4,
    max_gap_s: float = 0.5,
    max_chunk_dur_s: float = 2.5,
) -> list[dict[str, Any]]:
    """Pack source-timeline transcript words into clip-time subtitle segments.

    Filters ``words`` to those starting in ``[clip_start, clip_end)``, re-maps
    each word's ``(start, end)`` through the silence-tighten ``intervals``
    (see :func:`ffmpeg.audio.remap_time`), then groups consecutive words into
    chunks of 3-5 words / <=2.5s, breaking on any internal gap > ``max_gap_s``.

    Returns a list of ``{"start", "end", "text"}`` dicts in CLIP time, ready
    to feed into :func:`build_ass_dual` as the ``en_segments`` argument.
    """
    # Lazy import to avoid a circular dependency at module load.
    from ffmpeg.audio import remap_time as _remap_time

    intervals_list: list[tuple[float, float]] = [
        (float(a), float(b)) for (a, b) in (intervals or [])
    ]

    remapped: list[dict[str, Any]] = []
    for w in words or []:
        try:
            ws = float(w.get("start", 0.0))
            we = float(w.get("end", ws + 0.1))
        except (TypeError, ValueError):
            continue
        if ws < clip_start or ws >= clip_end:
            continue
        we = min(we, float(clip_end))
        if we <= ws:
            we = ws + 0.05
        if intervals_list:
            new_s = _remap_time(ws - float(clip_start), intervals_list)
            new_e = _remap_time(we - float(clip_start), intervals_list)
        else:
            new_s = ws - float(clip_start)
            new_e = we - float(clip_start)
        if new_s is None or new_e is None:
            continue
        if new_e <= new_s:
            new_e = new_s + 0.05
        token = str(w.get("word") or w.get("text") or "").strip()
        if not token:
            continue
        remapped.append({"word": token, "start": float(new_s), "end": float(new_e)})

    return _chunk_remapped_words(
        remapped,
        max_words=max_words,
        max_gap_s=max_gap_s,
        max_chunk_dur_s=max_chunk_dur_s,
    )


def _chunk_remapped_words(
    remapped: Sequence[dict[str, Any]],
    *,
    max_words: int = 4,
    max_gap_s: float = 0.5,
    max_chunk_dur_s: float = 2.5,
) -> list[dict[str, Any]]:
    """Group a list of clip-time words (``{word,start,end}``) into segments.

    A new chunk starts when:

    * the current chunk already has ``max_words`` words,
    * the gap between the previous word's ``end`` and this word's ``start``
      exceeds ``max_gap_s``, or
    * the chunk would exceed ``max_chunk_dur_s``.

    Returns segments of ``{"start", "end", "text"}`` in clip time.
    """
    out: list[dict[str, Any]] = []
    cur_words: list[dict[str, Any]] = []
    cur_start = 0.0
    prev_end = 0.0

    for w in remapped:
        ws = float(w["start"])
        we = float(w["end"])
        if not cur_words:
            cur_words = [w]
            cur_start = ws
            prev_end = we
            continue
        gap = ws - prev_end
        would_dur = we - cur_start
        if (
            len(cur_words) >= max_words
            or gap > max_gap_s
            or would_dur > max_chunk_dur_s
        ):
            out.append(
                {
                    "start": cur_start,
                    "end": prev_end,
                    "text": " ".join(str(x["word"]) for x in cur_words),
                }
            )
            cur_words = [w]
            cur_start = ws
            prev_end = we
        else:
            cur_words.append(w)
            prev_end = we

    if cur_words:
        out.append(
            {
                "start": cur_start,
                "end": prev_end,
                "text": " ".join(str(x["word"]) for x in cur_words),
            }
        )
    return out


def build_segments_from_clip_words(
    clip_words: Sequence[dict[str, Any]],
    *,
    max_words: int = 4,
    max_gap_s: float = 0.5,
    max_chunk_dur_s: float = 2.5,
) -> list[dict[str, Any]]:
    """Group already-remapped clip-time words into ``{start,end,text}`` segments.

    Convenience wrapper for callers (e.g. the render pipeline) that have
    already remapped words through highlights and silence intervals via
    ``_clip_words`` and just need them packed into subtitle chunks.
    """
    normalised: list[dict[str, Any]] = []
    for w in clip_words or []:
        try:
            ws = float(w.get("start", 0.0))
            we = float(w.get("end", ws + 0.05))
        except (TypeError, ValueError):
            continue
        if we <= ws:
            we = ws + 0.05
        token = str(w.get("word") or w.get("text") or "").strip()
        if not token:
            continue
        normalised.append({"word": token, "start": ws, "end": we})
    return _chunk_remapped_words(
        normalised,
        max_words=max_words,
        max_gap_s=max_gap_s,
        max_chunk_dur_s=max_chunk_dur_s,
    )


def burn_ass(input_path: str | Path, ass_path: str | Path, output_path: str | Path) -> Path:
    """Burn the ASS file into ``input_path`` via libass."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # libass needs forward slashes and escaped colons inside the filter graph.
    ass_str = str(ass_path).replace("\\", "/").replace(":", "\\:")
    cmd = [
        _ffmpeg_bin(),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-vf",
        f"ass='{ass_str}'",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "copy",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path
