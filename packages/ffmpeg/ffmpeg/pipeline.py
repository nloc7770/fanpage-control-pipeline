"""Render pipeline: cut -> silence-tighten -> [audio] -> crop -> zoom -> subs -> mux.

The render worker calls :func:`render_clip` with an :class:`~shared_py.llm_contracts.EditPlan`
plus a transcript (segments + words) and gets back the final ``.mp4`` path. All
intermediates live under a temp dir managed by :func:`_scratch_dir`.

The "aggressive social edit" pipeline is roughly:

1. ``cut_segment`` -- pull the clip window out of the source.
2. Detect silences with ``ffmpeg.audio.detect_silence``, invert to speech
   intervals, and run ``cut_concat`` so the resulting mp4 is 15-25% shorter.
3. **Audio**: by default (``KEEP_ORIGINAL_AUDIO=1``) the silence-tightened
   clip carries the original speaker's voice straight through to the output
   -- no TTS, no vocal separation. Set ``KEEP_ORIGINAL_AUDIO=0`` to re-enable
   the legacy Vietnamese TTS / vocal-sep stack (gated by ``TTS_REPLACE_AUDIO``
   and ``KEEP_BACKGROUND_AUDIO`` exactly as before).
4. ``crop_to_9_16`` to 1080x1920.
5. Apply zoom-OUT punches at emotional peaks (re-mapped through the speech
   intervals so the timing matches the tightened timeline). Replaces the
   older zoom-IN treatment.
6. Burn subtitles. When the audio was replaced by TTS, subtitles are built
   from the narrative text spread evenly across the clip; in original-audio
   mode we use the karaoke-style word-level subtitles built from transcript
   words (the dual-language subtitle builder owned by the other agent runs
   over this same pre-mux video, so original audio is preserved end-to-end).
7. Final encode.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from loguru import logger

from ffmpeg.audio import (
    compute_speech_intervals,
    detect_silence,
    mute_audio,
    remap_time,
    replace_audio,
    replace_audio_keep_background,
    total_kept,
)
from ffmpeg.crop import crop_to_9_16
from ffmpeg.cut import cut_concat, cut_segment
from ffmpeg.effects import (
    apply_pattern_interrupts,
    apply_zoom_out,
    apply_zoom_punches,
)
from ffmpeg.layouts import apply_layout
from ffmpeg.overlays import apply_hook_text
from ffmpeg.probe import get_duration_s
from ffmpeg.subtitles import (
    build_ass,
    build_ass_dual,
    build_ass_from_narrative,
    build_ass_from_segments,
    build_ass_words,
    build_segments_from_clip_words,
    burn_ass,
)


@contextmanager
def _scratch_dir() -> Iterator[Path]:
    """Temporary working directory, cleaned on exit unless ``KEEP_SCRATCH=1``."""
    import os

    keep = os.environ.get("KEEP_SCRATCH", "0") == "1"
    dirpath = Path(tempfile.mkdtemp(prefix="sff-render-"))
    try:
        yield dirpath
    finally:
        if not keep:
            shutil.rmtree(dirpath, ignore_errors=True)


def render_clip(
    plan: Any,  # shared_py.llm_contracts.EditPlan -- duck-typed for test ergonomics
    source_path: str | Path,
    output_path: str | Path,
    *,
    progress_cb: Callable[[float], None] | None = None,
    start_time: float | None = None,
    end_time: float | None = None,
    subtitle_lines: list[dict[str, Any]] | None = None,
    transcript_words: Sequence[dict[str, Any]] | None = None,
    highlight_segments: list[dict[str, Any]] | None = None,
    yolo_focal_track: list[dict[str, Any]] | None = None,
) -> Path:
    """Render one clip end-to-end with aggressive social-first editing.

    Parameters
    ----------
    plan:
        ``EditPlan`` instance (or dict with the same shape).
    source_path:
        Source video file.
    output_path:
        Final ``.mp4`` destination.
    progress_cb:
        Called with monotonically-increasing percentages in [0, 100].
    start_time, end_time:
        Clip window in source timeline. Required.
    subtitle_lines:
        Optional pre-computed condensed lines. Used only when ``transcript_words``
        is absent; the dynamic karaoke subs (built from words) take precedence.
    transcript_words:
        Flat list of word dicts (``{"word", "start", "end", "speaker"?}``) in
        the **source** timeline. Words outside ``[start_time, end_time]`` are
        ignored; the rest are re-mapped to the tightened clip timeline.
    highlight_segments:
        Optional list of ``{"start", "end"}`` dicts (in source-video time)
        describing a stitched recap montage. When present (or recoverable
        from ``plan.highlight_segments``) the raw cut is replaced by a
        concatenation of these intervals and word remapping becomes
        piecewise. When absent, the legacy single-cut behaviour is used.
    yolo_focal_track:
        Optional list of ``{"t": float, "cx": float, "cy": float}`` dicts
        from ``YoloAnalysis.focal_track``. When ``crop_plan.mode`` is
        ``track_face`` or ``smart`` this replaces the static LLM keyframes
        with real per-frame subject positions, enabling a time-varying crop.
        Pass ``None`` (default) to rely entirely on the LLM crop_plan.
    """
    if start_time is None or end_time is None:
        raise ValueError("render_clip requires start_time and end_time")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    def _report(pct: float) -> None:
        if progress_cb:
            progress_cb(max(0.0, min(100.0, pct)))

    _report(0.0)

    plan_dict = _coerce_plan(plan)

    # Resolve highlight_segments: prefer the explicit kwarg, then fall back to
    # the value stashed on the plan dict (carried over from qwen.detect_clips).
    highlights = _coerce_highlights(
        highlight_segments
        if highlight_segments is not None
        else plan_dict.get("highlight_segments")
    )

    with _scratch_dir() as scratch:
        # ---- 1. Raw cut from the source ---------------------------------
        raw_path = scratch / "raw_cut.mp4"
        if highlights:
            # Recap montage: concat all highlights into one raw_cut.
            # Apply a 0.3s dissolve crossfade between segments for smoother flow.
            intervals_src = [(h["start"], h["end"]) for h in highlights]
            _xfade = 0.3 if len(intervals_src) > 1 else 0.0
            cut_concat(
                source_path, intervals_src, raw_path, reencode=True,
                crossfade_s=_xfade,
            )
            raw_dur = sum(h["end"] - h["start"] for h in highlights)
            # Account for crossfade overlap reducing total duration.
            if _xfade > 0:
                raw_dur -= _xfade * (len(intervals_src) - 1)
            logger.info(
                "render: recap montage from {} highlights "
                "(stitched={:.2f}s span=[{:.2f},{:.2f}]s)",
                len(highlights),
                raw_dur,
                min(h["start"] for h in highlights),
                max(h["end"] for h in highlights),
            )
        else:
            cut_segment(source_path, start_time, end_time, raw_path, reencode=True)
            raw_dur = max(0.0, float(end_time) - float(start_time))
        _report(10.0)

        # ---- 2. Silence detection + tightening --------------------------
        # Operate on raw_cut.mp4 (smaller / faster than the source). The
        # intervals are in the raw_cut timeline (0 = start of raw_cut).
        tight_path = scratch / "tightened.mp4"
        intervals: list[tuple[float, float]] = []
        tightened_duration = raw_dur
        try:
            # First pass at the spec-mandated -30 dB. If the clip is mostly
            # quiet ambient with low-amplitude breath/room-tone we won't trim
            # anything; in that case try a more aggressive threshold so the
            # output still feels tightened.
            silences = detect_silence(
                raw_path, min_silence_ms=350, threshold_db=-30.0
            )
            intervals = compute_speech_intervals(raw_dur, silences)
            kept = total_kept(intervals)
            if raw_dur > 0 and (raw_dur - kept) / raw_dur < 0.05:
                # Retry at -25 dB for noisier sources.
                silences2 = detect_silence(
                    raw_path, min_silence_ms=350, threshold_db=-25.0
                )
                intervals2 = compute_speech_intervals(raw_dur, silences2)
                kept2 = total_kept(intervals2)
                if (
                    intervals2
                    and (raw_dur - kept2) / raw_dur > (raw_dur - kept) / raw_dur
                ):
                    silences, intervals, kept = silences2, intervals2, kept2

            logger.info(
                "render: silence detect found {} silences, kept {:.2f}s of {:.2f}s "
                "({} intervals)",
                len(silences),
                kept,
                raw_dur,
                len(intervals),
            )
            # Only run cut_concat if it actually shortens the clip by 5%+
            # AND we have multiple intervals to stitch
            # AND the resulting clip stays at or above MIN_OUTPUT_DURATION_S
            # (default 30s) so the final video meets the minimum-length rule.
            min_output_s = float(os.environ.get("MIN_OUTPUT_DURATION_S", "30.0"))
            if (
                intervals
                and len(intervals) > 1
                and kept > 0
                and (raw_dur - kept) / raw_dur >= 0.05
                and kept >= min_output_s
            ):
                cut_concat(raw_path, intervals, tight_path, reencode=True)
                tightened_duration = kept
            else:
                # Skip tightening: copy raw_path forward.
                if (
                    intervals
                    and len(intervals) > 1
                    and kept > 0
                    and kept < min_output_s
                ):
                    logger.info(
                        "render: silence-tighten would yield {:.2f}s "
                        "(< MIN_OUTPUT_DURATION_S={:.0f}s), keeping raw cut",
                        kept,
                        min_output_s,
                    )
                shutil.copy2(raw_path, tight_path)
                intervals = [(0.0, raw_dur)]
        except subprocess.CalledProcessError as exc:
            logger.warning(
                "render: silence-tighten step failed, using raw cut: {}", exc
            )
            shutil.copy2(raw_path, tight_path)
            intervals = [(0.0, raw_dur)]
        _report(28.0)

        # ---- 2b. Audio handling ----------------------------------------
        # Default behaviour (``KEEP_ORIGINAL_AUDIO=1``, the new default): leave
        # the silence-tightened audio alone -- no TTS, no vocal separation.
        # ``cut_concat`` above re-encoded both video and audio together, so
        # the source speaker's voice (now silence-tightened) flows through
        # unchanged to the crop / zoom / subs / mux stages.
        #
        # Legacy behaviour (``KEEP_ORIGINAL_AUDIO=0``): honour the older
        # ``TTS_REPLACE_AUDIO`` switch and the vocal-separation stack. We
        # keep the legacy code path intact so power users can opt back in,
        # but it never fires by default.
        keep_original_audio = (
            os.environ.get("KEEP_ORIGINAL_AUDIO", "1") == "1"
        )
        # Legacy switch -- the TTS branch only runs if the operator opts out of
        # KEEP_ORIGINAL_AUDIO *and* the legacy ``TTS_REPLACE_AUDIO`` toggle is
        # on. With the default (``KEEP_ORIGINAL_AUDIO=1``) the source speaker's
        # audio flows through unchanged and the dual EN+VI subtitle path is
        # used below.
        tts_enabled = (
            (not keep_original_audio)
            and os.environ.get("TTS_REPLACE_AUDIO", "1") == "1"
        )
        narrative = (plan_dict.get("narrative_script_vi") or "").strip()
        narrative_segments_raw = plan_dict.get("narrative_segments") or []
        # Normalise to plain dicts so downstream callers don't have to care
        # about pydantic vs dict.
        narrative_segments: list[dict[str, Any]] = []
        for ns in narrative_segments_raw:
            if hasattr(ns, "model_dump"):
                ns = ns.model_dump()
            if not isinstance(ns, dict):
                continue
            txt = (ns.get("text_vi") or ns.get("text") or "").strip()
            if not txt:
                continue
            narrative_segments.append(
                {
                    "start": float(ns.get("start", 0.0)),
                    "end": float(ns.get("end", 0.0)),
                    "text_vi": txt,
                }
            )
        audio_replaced = False
        used_segment_tts = False
        if keep_original_audio:
            # New default: preserve the source speaker's voice. Skip TTS,
            # skip vocal separation, skip muting. The silence-tightened
            # ``tight_path`` already carries the original audio.
            logger.info(
                "render: audio mode = original (TTS disabled, vocal-sep disabled)"
            )
        else:
            # Legacy stack: only consulted when KEEP_ORIGINAL_AUDIO=0.
            tts_enabled = os.environ.get("TTS_REPLACE_AUDIO", "1") == "1"
            if tts_enabled and (narrative or narrative_segments):
                try:
                    # Lazy import: the TTS service module may not exist yet at
                    # import time. Failing loudly at call time is fine -- the
                    # except below catches it and falls back to muting source
                    # audio so the rest of the pipeline keeps working.
                    try:
                        from services.tts.runner import (  # type: ignore
                            synthesize_vi,
                            synthesize_vi_segments,
                        )
                    except ImportError as imp_exc:
                        raise RuntimeError(
                            "services.tts.runner.synthesize_vi(_segments) not available yet"
                        ) from imp_exc

                    tts_wav = scratch / "tts.wav"
                    # Probe the (post-tighten) clip duration so segment timings
                    # align with what the viewer actually sees. ``tightened_duration``
                    # tracks this; fall back to a probe if it's missing.
                    try:
                        clip_audio_dur = float(get_duration_s(tight_path))
                    except Exception:
                        clip_audio_dur = max(0.1, tightened_duration)

                    if narrative_segments:
                        # Segment-aware path: each NarrativeSegment becomes its
                        # own TTS render, placed onto a silent track of length
                        # ``clip_audio_dur``. Vietnamese pauses now mirror the
                        # original speaker's pacing.
                        synthesize_vi_segments(
                            narrative_segments,
                            clip_audio_dur,
                            tts_wav,
                        )
                        used_segment_tts = True
                    else:
                        # Legacy fallback: one big continuous narration.
                        synthesize_vi(narrative, tts_wav)
                    if not tts_wav.exists() or tts_wav.stat().st_size == 0:
                        raise RuntimeError("synthesize_vi(_segments) produced no output")

                    replaced_path = scratch / "audio_replaced.mp4"
                    # Decide between background-preserving splice (default) and
                    # the older full-replacement behaviour.
                    keep_bg = os.environ.get("KEEP_BACKGROUND_AUDIO", "1") == "1"
                    vocal_sep_mode = (
                        os.environ.get("VOCAL_SEPARATION", "demucs") or "demucs"
                    ).strip().lower()
                    try:
                        bg_gain_db = float(os.environ.get("BG_GAIN_DB", "-8.0"))
                    except ValueError:
                        bg_gain_db = -8.0

                    if keep_bg and vocal_sep_mode != "off":
                        use_demucs = vocal_sep_mode == "demucs"
                        logger.info(
                            "render: splicing TTS over preserved background "
                            "(vocal_sep={}, bg_gain={:.1f}dB)",
                            vocal_sep_mode,
                            bg_gain_db,
                        )
                        replace_audio_keep_background(
                            tight_path,
                            tts_wav,
                            replaced_path,
                            fit_to="video",
                            bg_gain_db=bg_gain_db,
                            use_demucs=use_demucs,
                        )
                    else:
                        # Legacy behaviour: full audio replacement (kills bg).
                        replace_audio(
                            tight_path, tts_wav, replaced_path, fit_to="video"
                        )
                    tight_path = replaced_path
                    audio_replaced = True
                    logger.info(
                        "render: TTS voiceover spliced in ({}, segment_aware={}, "
                        "keep_bg={}, vocal_sep={})",
                        tts_wav,
                        used_segment_tts,
                        keep_bg,
                        vocal_sep_mode,
                    )
                except Exception as exc:  # broad on purpose: keep the pipeline alive
                    logger.warning(
                        "render: TTS synth failed ({}); muting source audio instead",
                        exc,
                    )
                    try:
                        muted_path = scratch / "muted.mp4"
                        mute_audio(tight_path, muted_path)
                        tight_path = muted_path
                        audio_replaced = True  # source is silent, so subs from narrative still apply
                    except Exception as mute_exc:  # pragma: no cover - defensive
                        logger.warning(
                            "render: mute_audio fallback also failed ({}); "
                            "keeping original audio",
                            mute_exc,
                        )
            elif tts_enabled and not narrative and not narrative_segments:
                logger.info(
                    "render: TTS enabled but plan has no narrative content; "
                    "leaving source audio untouched"
                )
            else:
                logger.info(
                    "render: KEEP_ORIGINAL_AUDIO=0 and TTS_REPLACE_AUDIO=0; "
                    "leaving source audio untouched"
                )
        _report(40.0)

        # Resolve hook text early -- used by both layout (step 3) and overlay (step 5b).
        hook_text = (
            plan_dict.get("main_hook")
            or plan_dict.get("title")
            or ""
        ).strip()

        # ---- 3. Crop / Layout to 9:16 ------------------------------------
        crop_path = scratch / "crop.mp4"
        layout_name = (plan_dict.get("layout") or "tweet_card").strip().lower()
        focus = _focus_from_plan(
            plan_dict,
            source_start=float(start_time),
            yolo_focal_track=yolo_focal_track,
        )
        apply_layout(
            tight_path,
            crop_path,
            layout=layout_name,
            focus_track=focus,
            title=hook_text or plan_dict.get("title") or "",
            channel_name=plan_dict.get("channel_name") or "Channel Name",
            handle=plan_dict.get("handle") or "@handle",
        )
        logger.info("render: layout={!r} applied", layout_name)
        _report(55.0)

        # ---- 4. Zoom-OUT punches at emotional peaks ---------------------
        # The new treatment scales DOWN (e.g. 1.0 -> 0.93 -> 1.0) so the
        # framing breathes outward at emotional peaks. Beat timestamps come
        # from pattern_interrupts (preferred) or visual_effects, re-mapped
        # from the source timeline through the silence intervals onto the
        # tightened-clip timeline.
        beats_source = _zoom_out_beats_from_plan(plan_dict)
        beats = _remap_beats(
            beats_source, float(start_time), intervals, highlights=highlights
        )
        # Moderate zoom amplitude: clamp scale_to to 0.95-0.97 range (3-5% zoom)
        # so the effect is subtle and not distracting.
        _ZOOM_MODERATE_SCALE = 0.96  # 4% zoom-out at peaks
        for b in beats:
            b["scale_to"] = _ZOOM_MODERATE_SCALE

        effects_in = crop_path
        if beats:
            zoom_path = scratch / "zoom.mp4"
            apply_zoom_out(effects_in, zoom_path, beats, default_scale_to=_ZOOM_MODERATE_SCALE)
            effects_in = zoom_path
        _report(68.0)

        # ---- 5. Pattern interrupts (flash cuts etc.) --------------------
        interrupts_source = list(plan_dict.get("pattern_interrupts") or [])
        interrupts = _remap_interrupts(
            interrupts_source, float(start_time), intervals, highlights=highlights
        )
        if interrupts:
            interrupt_path = scratch / "interrupt.mp4"
            apply_pattern_interrupts(effects_in, interrupt_path, interrupts)
            effects_in = interrupt_path
        _report(78.0)

        # ---- 5b. Hook text overlay -----------------------------------------
        # Burn the clip's main_hook or title as a text banner in the first 3s.
        # (hook_text resolved earlier, before step 3)
        if hook_text:
            hook_path = scratch / "hook_overlay.mp4"
            apply_hook_text(effects_in, hook_path, hook_text)
            effects_in = hook_path
        _report(80.0)

        # ---- 6. Burn subtitles -----------------------------------------
        # When the source audio was replaced by a TTS voiceover the original
        # word-level timestamps no longer match what the viewer hears. In
        # that case we generate subtitles directly from the narrative text,
        # spread evenly across the clip's tightened duration. Otherwise we
        # keep the existing dynamic karaoke subs built from transcript words.
        clip_words = _clip_words(
            transcript_words or [],
            float(start_time),
            float(end_time),
            intervals,
            highlights=highlights,
        )
        used_dynamic = False
        used_narrative = False
        used_segment_subs = False
        used_dual = False
        # Decide whether we're in the new "original-audio + dual EN/VI subs"
        # mode. We use dual subs when the source audio is still the original
        # English speaker (KEEP_ORIGINAL_AUDIO=1 OR TTS didn't run) AND we have
        # both English word timings AND Vietnamese narrative segments to pair
        # with them.
        use_dual_subs = (
            (keep_original_audio or not audio_replaced)
            and bool(clip_words)
            and bool(narrative_segments or narrative)
        )
        if use_dual_subs:
            try:
                clip_dur = get_duration_s(effects_in)
            except Exception:
                clip_dur = max(0.1, tightened_duration)
            # VI segments: prefer narrative_segments (already in clip time),
            # fall back to spreading narrative_script_vi evenly when only the
            # legacy single-string field is available.
            if narrative_segments:
                vi_segments = [
                    {"start": s["start"], "end": s["end"], "text": s["text_vi"]}
                    for s in narrative_segments
                ]
            else:
                vi_segments = []
            # Build EN segments. If we have VI segment boundaries, group the EN
            # words into one EN dialogue per VI segment so the two lines flip
            # together (no flicker mismatch between top and bottom).
            if vi_segments:
                en_segments = []
                for vi in vi_segments:
                    vs, ve = float(vi["start"]), float(vi["end"])
                    in_window = [
                        w
                        for w in (clip_words or [])
                        if float(w.get("end", 0.0)) > vs
                        and float(w.get("start", 0.0)) < ve
                    ]
                    if not in_window:
                        continue
                    text = " ".join(
                        (w.get("word") or w.get("text") or "").strip()
                        for w in in_window
                    ).strip()
                    if not text:
                        continue
                    en_segments.append({"start": vs, "end": ve, "text": text})
                if not en_segments:
                    en_segments = build_segments_from_clip_words(clip_words)
            else:
                en_segments = build_segments_from_clip_words(clip_words)
            if not vi_segments:
                # Even spread of the single narrative string across the EN
                # span. This is only the legacy fallback; the new prompt
                # always emits narrative_segments.
                if en_segments:
                    span_a = en_segments[0]["start"]
                    span_b = en_segments[-1]["end"]
                else:
                    span_a, span_b = 0.0, max(0.1, float(clip_dur))
                words_vi = narrative.split()
                if words_vi:
                    n = max(1, (len(words_vi) + 4) // 5)
                    chunk_size = max(1, (len(words_vi) + n - 1) // n)
                    per = (span_b - span_a) / max(1, n)
                    vi_segments = []
                    t = span_a
                    for i in range(0, len(words_vi), chunk_size):
                        c = " ".join(words_vi[i : i + chunk_size]).strip()
                        if not c:
                            continue
                        a = t
                        b = min(span_b, t + per)
                        if b <= a:
                            b = a + 0.2
                        vi_segments.append({"start": a, "end": b, "text": c})
                        t = b
                else:
                    vi_segments = []
            ass_text = build_ass_dual(
                en_segments,
                vi_segments,
                total_duration_s=clip_dur,
                style=plan_dict.get("subtitle_style"),
            )
            ass_path = scratch / "subs.ass"
            ass_path.write_text(ass_text, encoding="utf-8")
            burn_ass(effects_in, ass_path, output_path)
            used_dual = True
            logger.info(
                "render: burned dual EN+VI subs (en={}, vi={}, dur={:.2f}s)",
                len(en_segments),
                len(vi_segments),
                clip_dur,
            )
        elif audio_replaced and (narrative or narrative_segments):
            # Probe the duration of the most recent intermediate so the
            # narrative chunks line up with what's actually in the output.
            try:
                clip_dur = get_duration_s(effects_in)
            except Exception:
                clip_dur = max(0.1, tightened_duration)
            if narrative_segments:
                # Preferred: subtitles inherit the segment timing for free.
                ass_text = build_ass_from_segments(
                    narrative_segments,
                    total_duration_s=clip_dur,
                    style=plan_dict.get("subtitle_style"),
                )
                used_segment_subs = True
            else:
                ass_text = build_ass_from_narrative(
                    narrative,
                    total_duration_s=clip_dur,
                    style=plan_dict.get("subtitle_style"),
                )
                used_narrative = True
            ass_path = scratch / "subs.ass"
            ass_path.write_text(ass_text, encoding="utf-8")
            burn_ass(effects_in, ass_path, output_path)
            logger.info(
                "render: burned VI subs (segment_aware={}, narrative_len={}, "
                "segments={}, dur={:.2f}s)",
                used_segment_subs,
                len(narrative),
                len(narrative_segments),
                clip_dur,
            )
        elif clip_words:
            ass_path = scratch / "subs.ass"
            ass_path.write_text(
                build_ass_words(clip_words, plan_dict.get("subtitle_style")),
                encoding="utf-8",
            )
            burn_ass(effects_in, ass_path, output_path)
            used_dynamic = True
            logger.info(
                "render: burned dynamic karaoke subs for {} words", len(clip_words)
            )
        elif subtitle_lines:
            ass_path = scratch / "subs.ass"
            ass_path.write_text(
                build_ass(subtitle_lines, plan_dict.get("subtitle_style")),
                encoding="utf-8",
            )
            burn_ass(effects_in, ass_path, output_path)
        else:
            shutil.copy2(effects_in, output_path)
        _report(95.0)

        # ---- 7. Verify output --------------------------------------------
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError(f"render produced empty output: {output_path}")

        # Best-effort sanity check on duration.
        try:
            out_dur = get_duration_s(output_path)
            logger.info(
                "render: clip done -- raw={:.2f}s tightened={:.2f}s output={:.2f}s "
                "zoom_out_beats={} dual_subs={} dynamic_subs={} narrative_subs={} "
                "segment_subs={} segment_tts={} audio_replaced={}",
                raw_dur,
                tightened_duration,
                out_dur,
                len(beats),
                used_dual,
                used_dynamic,
                used_narrative,
                used_segment_subs,
                used_segment_tts,
                audio_replaced,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("probe of rendered clip failed: {}", exc)

        _report(100.0)
        return output_path


def write_blank_mp4(output_path: str | Path, duration_s: float = 5.0) -> Path:
    """Generate a black 9:16 mp4 -- used by MOCK_RENDER and integration tests."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        shutil.which("ffmpeg") or "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s=1080x1920:d={duration_s:.2f}",
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=channel_layout=stereo:sample_rate=44100:d={duration_s:.2f}",
        "-shortest",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-tune",
        "stillimage",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _coerce_plan(plan: Any) -> dict[str, Any]:
    """Accept either a pydantic ``EditPlan`` or a plain dict."""
    if hasattr(plan, "model_dump"):
        return plan.model_dump()
    if isinstance(plan, dict):
        return plan
    raise TypeError(f"unsupported plan type: {type(plan)!r}")


def _focus_from_plan(
    plan: dict[str, Any],
    *,
    source_start: float = 0.0,
    yolo_focal_track: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Translate ``crop_plan`` + optional YOLO data into focus points for ``crop_to_9_16``.

    Priority logic by ``crop_plan.mode``:

    * ``track_face`` — use the YOLO focal_track directly (time-varying, real
      subject positions). Falls back to LLM keyframes if the track is empty.
    * ``smart`` — blend: start with YOLO track if non-empty, otherwise fall
      back to LLM keyframes.
    * ``center`` — return an empty list so ``_build_crop_expr`` uses the
      rule-of-thirds bias (better than hard center for landscape subjects).
    * ``static`` / anything else — use LLM keyframes as before.

    Crop-plan keyframes use source-timeline ``t``; we subtract ``source_start``
    to convert to clip-relative time. YOLO focal_track items already carry clip-
    relative ``t`` values (the runner stores ``t = frame_idx / src_fps`` from
    the beginning of the source; callers should pass them pre-offset if needed,
    but for now we pass through as-is since the crop filter treats ``t`` as
    time-since-start-of-the-input-file it receives, which is the tightened clip).
    """
    cp = plan.get("crop_plan") or {}
    mode = (cp.get("mode") or "center").lower()

    # Build LLM keyframe list (always available as fallback).
    llm_kf: list[dict[str, Any]] = []
    for kf in cp.get("keyframes") or []:
        cx = float(kf.get("x", 0.5)) + float(kf.get("w", 0.0)) / 2.0
        cy = float(kf.get("y", 0.5)) + float(kf.get("h", 0.0)) / 2.0
        llm_kf.append({"t": float(kf.get("t", 0.0)) - source_start, "cx": cx, "cy": cy})

    yolo: list[dict[str, Any]] = yolo_focal_track or []

    if mode == "track_face":
        # YOLO real detections preferred; graceful fallback to LLM keyframes.
        if yolo:
            logger.debug("focus: track_face mode — using {} YOLO keypoints", len(yolo))
            return yolo
        logger.info(
            "focus: track_face requested but YOLO focal_track is empty; "
            "falling back to LLM keyframes (re-download as h264 to fix)"
        )
        return llm_kf

    if mode == "smart":
        if yolo:
            logger.debug("focus: smart mode — YOLO track ({} pts)", len(yolo))
            return yolo
        return llm_kf

    if mode == "center":
        # Return empty → rule-of-thirds bias in _build_crop_expr.
        return []

    # mode == "static" or any future value: honour LLM keyframes.
    return llm_kf


def _zoom_beats_from_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull zoom beats from ``visual_effects``. Timestamps are in source time."""
    beats: list[dict[str, Any]] = []
    for ve in plan.get("visual_effects") or []:
        if ve.get("type") in ("zoom_punch", "zoom_in", "punch", "shake"):
            params = ve.get("params") or {}
            start = float(ve.get("start", 0.0))
            end = ve.get("end")
            if end:
                dur = max(0.15, float(end) - start)
            else:
                dur = float(params.get("duration", 0.4))
            beats.append(
                {
                    "_source_at": start,
                    "duration": min(0.8, dur),
                    "scale": float(params.get("scale", 1.12)),
                }
            )
    return beats


def _remap_beats(
    beats: list[dict[str, Any]],
    clip_start: float,
    intervals: list[tuple[float, float]],
    *,
    highlights: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Convert source-time beats to tightened-clip-time beats.

    ``beats[i]["_source_at"]`` is in source time. When ``highlights`` is given
    we map source-time through the stitched-recap timeline first, otherwise
    we fall back to ``raw_t = src - clip_start``. The result then passes
    through :func:`remap_time` for silence-tighten. Beats in a removed gap
    (highlight gap or silenced region) are dropped.

    Passes ``scale`` and/or ``scale_to`` through unchanged so both zoom-IN
    and zoom-OUT beat lists work.
    """
    out: list[dict[str, Any]] = []
    for b in beats:
        src = float(b.get("_source_at", b.get("at", 0.0)))
        if highlights:
            raw_t = remap_word_time_through_highlights(src, highlights)
            if raw_t is None:
                continue
        else:
            raw_t = src - clip_start
            if raw_t < 0:
                continue
        new_t = remap_time(raw_t, intervals)
        if new_t is None:
            continue
        remapped: dict[str, Any] = {
            "at": float(new_t),
            "duration": b.get("duration", 0.4),
        }
        if "scale" in b:
            remapped["scale"] = b["scale"]
        if "scale_to" in b:
            remapped["scale_to"] = b["scale_to"]
        out.append(remapped)
    return out


def _remap_interrupts(
    interrupts: list[dict[str, Any]],
    clip_start: float,
    intervals: list[tuple[float, float]],
    *,
    highlights: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Same idea as ``_remap_beats`` but for pattern_interrupts."""
    out: list[dict[str, Any]] = []
    for it in interrupts:
        src = float(it.get("at", 0.0))
        if highlights:
            raw_t = remap_word_time_through_highlights(src, highlights)
            if raw_t is None:
                continue
        else:
            raw_t = src - clip_start
            if raw_t < 0:
                continue
        new_t = remap_time(raw_t, intervals)
        if new_t is None:
            continue
        new_it = dict(it)
        new_it["at"] = float(new_t)
        out.append(new_it)
    return out


def _auto_beats_from_words(
    words: Sequence[dict[str, Any]],
    clip_start: float,
    intervals: list[tuple[float, float]],
    tightened_dur: float,
) -> list[dict[str, Any]]:
    """Fallback: zoom-punch every ~7s on the longest nearby word.

    When the EditPlan has no visual_effects (or none with zoom-able types)
    we still want some zoom variation. Pick a "long word" (>=7 chars) every
    7 seconds of tightened time.
    """
    if not words or tightened_dur <= 0:
        return []
    beats: list[dict[str, Any]] = []
    next_punch_at = 2.0  # first one ~2s in
    for w in words:
        src_start = float(w.get("start", 0.0))
        raw_t = src_start - clip_start
        if raw_t < 0:
            continue
        new_t = remap_time(raw_t, intervals)
        if new_t is None:
            continue
        if new_t < next_punch_at:
            continue
        token = str(w.get("word", "")).strip(".,!?:;\"'-")
        if len(token) < 7:
            continue
        beats.append({"at": float(new_t), "duration": 0.45, "scale": 1.12})
        next_punch_at = float(new_t) + 7.0
        if len(beats) >= 8:
            break
    return beats


def _zoom_out_beats_from_plan(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull zoom-OUT beats from the plan.

    Source timestamps come from (in priority order):

    * ``pattern_interrupts`` -- treated as emotional-peak markers, regardless
      of ``kind`` (the original meaning of "flash cut" is irrelevant here --
      we just want the time markers).
    * ``visual_effects`` of type ``zoom_punch``/``zoom_in``/``punch``/``shake``
      -- reused as zoom-OUT locations since they already mark emotional peaks
      in the Qwen output.

    Each beat carries ``_source_at`` (source timeline), ``duration``, and
    ``scale_to`` (default 0.85).
    """
    beats: list[dict[str, Any]] = []
    seen_at: set[float] = set()

    for itr in plan.get("pattern_interrupts") or []:
        at = float(itr.get("at", 0.0))
        params = itr.get("params") or {}
        dur = float(params.get("duration", 0.4))
        scale_to = float(params.get("scale_to", 0.50))
        beats.append(
            {
                "_source_at": at,
                "duration": max(0.2, min(0.8, dur)),
                "scale_to": max(0.50, min(0.99, scale_to)),
            }
        )
        seen_at.add(round(at, 2))

    for ve in plan.get("visual_effects") or []:
        if ve.get("type") not in ("zoom_punch", "zoom_in", "punch", "shake"):
            continue
        start = float(ve.get("start", 0.0))
        if round(start, 2) in seen_at:
            continue  # already accounted for via pattern_interrupts
        params = ve.get("params") or {}
        end = ve.get("end")
        if end:
            dur = max(0.2, float(end) - start)
        else:
            dur = float(params.get("duration", 0.4))
        scale_to = float(params.get("scale_to", 0.50))
        beats.append(
            {
                "_source_at": start,
                "duration": max(0.2, min(0.8, dur)),
                "scale_to": max(0.50, min(0.99, scale_to)),
            }
        )
        seen_at.add(round(start, 2))

    return beats


def _auto_zoom_out_beats(
    words: Sequence[dict[str, Any]],
    clip_start: float,
    intervals: list[tuple[float, float]],
    tightened_dur: float,
) -> list[dict[str, Any]]:
    """Fallback when the plan has no peak markers: schedule a zoom-out every 6-8s.

    Prefers a "long word" (>=7 chars) inside each 7-second window so the
    breath aligns with a content beat; falls back to plain time spacing if
    no qualifying word is found (or no transcript is available -- which is
    common once the TTS replaces audio and the source words become
    meaningless).
    """
    if tightened_dur <= 0:
        return []

    # If we have transcript words, anchor on long words first.
    beats: list[dict[str, Any]] = []
    next_at = 2.0
    if words:
        for w in words:
            src_start = float(w.get("start", 0.0))
            raw_t = src_start - clip_start
            if raw_t < 0:
                continue
            new_t = remap_time(raw_t, intervals)
            if new_t is None:
                continue
            if new_t < next_at:
                continue
            token = str(w.get("word", "")).strip(".,!?:;\"'-")
            if len(token) < 7:
                continue
            beats.append({"at": float(new_t), "duration": 0.45, "scale_to": 0.50})
            next_at = float(new_t) + 7.0
            if len(beats) >= 6:
                break

    # If we still don't have enough beats (or no words at all), spread evenly
    # across the clip at ~7s intervals starting at 2s.
    if not beats:
        t = 2.0
        while t < max(2.0, tightened_dur - 1.0):
            beats.append({"at": float(t), "duration": 0.45, "scale_to": 0.50})
            t += 7.0
            if len(beats) >= 6:
                break

    return beats


def _clip_words(
    words: Sequence[dict[str, Any]],
    clip_start: float,
    clip_end: float,
    intervals: list[tuple[float, float]],
    *,
    highlights: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Filter + re-map word timestamps onto the (possibly stitched) clip timeline.

    Each output word: ``{"word", "start", "end", "speaker"?}`` with times in
    [0, tightened_duration]. Words that fall inside removed silences are
    dropped. When ``highlights`` is supplied, the mapping from source time to
    clip time is the piecewise stitch through the highlights (then through
    the silence-tighten intervals); words outside every highlight are dropped.
    """
    out: list[dict[str, Any]] = []
    for w in words:
        try:
            ws = float(w.get("start", 0.0))
            we = float(w.get("end", ws + 0.1))
        except (TypeError, ValueError):
            continue
        if highlights:
            # Stitched timeline path. Words anchored to a single highlight
            # only -- a word that straddles a highlight boundary is dropped
            # because its mapped span would be discontinuous.
            new_s = remap_word_time_through_highlights(ws, highlights)
            new_e = remap_word_time_through_highlights(we, highlights)
            if new_s is None or new_e is None:
                continue
            # Push the stitched-clip-time through the silence-tighten intervals.
            new_s2 = remap_time(new_s, intervals)
            new_e2 = remap_time(new_e, intervals)
            if new_s2 is None or new_e2 is None:
                continue
            new_s, new_e = new_s2, new_e2
        else:
            if we <= clip_start or ws >= clip_end:
                continue
            ws = max(ws, clip_start)
            we = min(we, clip_end)
            new_s = remap_time(ws - clip_start, intervals)
            new_e = remap_time(we - clip_start, intervals)
            if new_s is None or new_e is None:
                continue
        if new_e <= new_s:
            new_e = new_s + 0.08
        text = str(w.get("word") or w.get("text") or "").strip()
        if not text:
            continue
        out.append(
            {
                "word": text,
                "start": new_s,
                "end": new_e,
                "speaker": w.get("speaker"),
            }
        )
    return out


def _coerce_highlights(raw: Any) -> list[dict[str, float]]:
    """Normalise highlight inputs into a sorted list of ``{start, end}`` dicts.

    Accepts None / pydantic models / dicts. Drops degenerate entries.
    Returns ``[]`` when no usable highlights are available so callers can
    fall back to the legacy single-cut path.
    """
    if not raw:
        return []
    out: list[dict[str, float]] = []
    for h in raw:
        if hasattr(h, "model_dump"):
            h = h.model_dump()
        if not isinstance(h, dict):
            continue
        try:
            s = float(h.get("start", 0.0))
            e = float(h.get("end", 0.0))
        except (TypeError, ValueError):
            continue
        if e > s:
            out.append({"start": s, "end": e})
    out.sort(key=lambda h: h["start"])
    return out


def remap_word_time_through_highlights(
    word_t_source: float,
    highlights: list[dict[str, Any]],
) -> float | None:
    """Map a source-video timestamp into the stitched-clip timeline.

    The stitched clip is the concatenation, in order, of every highlight in
    ``highlights``. For an input source time ``t`` we find the unique
    highlight ``[hl.start, hl.end]`` containing ``t``. The stitched time is::

        offset_to_highlight + (t - hl.start)

    where ``offset_to_highlight`` is the sum of durations of all earlier
    highlights. Returns ``None`` if ``t`` falls in a gap between highlights
    (or outside the highlight range entirely).
    """
    offset = 0.0
    for hl in highlights:
        try:
            hs = float(hl["start"])
            he = float(hl["end"])
        except (KeyError, TypeError, ValueError):
            continue
        # Inclusive on the start, inclusive on the end (so boundary words map).
        if hs <= word_t_source <= he:
            return offset + (word_t_source - hs)
        offset += max(0.0, he - hs)
    return None
