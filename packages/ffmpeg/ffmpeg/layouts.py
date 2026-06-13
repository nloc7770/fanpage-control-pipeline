"""Video layout composers for the shortform rendering pipeline.

Each layout function takes a source video and composes it onto a 1080x1920
canvas with a specific visual treatment. The ``layout`` field in the edit plan
selects which composer to use.

Available layouts:
  - ``fit_blur``: Full source frame centred over a blurred fill (default, in crop.py).
  - ``tweet_card``: Fake tweet/X post card with glitchy camera UI overlay.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from loguru import logger


def _ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def _escape_drawtext(text: str) -> str:
    """Escape special characters for ffmpeg drawtext filter."""
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "’")  # unicode right single quote
    text = text.replace(":", "\\:")
    text = text.replace("%", "%%")
    text = text.replace("\n", " ")
    text = text.replace('"', '\\"')
    text = text.replace("[", "\\[")
    text = text.replace("]", "\\]")
    text = text.replace(";", "\\;")
    return text


def apply_tweet_layout(
    input_path: str | Path,
    output_path: str | Path,
    title: str,
    channel_name: str = "Channel Name",
    handle: str = "@handle",
    *,
    timestamp_text: str = "4\\:00 PM \\u00b7 Jun 1, 2025",
    stats_text: str = "354K Retweets  354K Quote Tweets  354K Likes",
    canvas_w: int = 1080,
    canvas_h: int = 1920,
    card_margin_x: int = 60,
    card_start_y: int = 200,
    blur_strength: int = 20,
) -> Path:
    """Compose source video into a fake tweet card layout on a 1080x1920 canvas.

    Layers (bottom to top):
      1. Blurred background (source scaled to fill + boxblur)
      2. Dark tweet card (#15202B) with profile, title, video, stats
      3. Glitchy camera recording UI overlay (top)
      4. Bottom profile + subscribe bar

    All composed in a single ffmpeg pass using a complex filtergraph.

    Parameters
    ----------
    input_path:
        Source video file (any resolution).
    output_path:
        Destination 1080x1920 mp4.
    title:
        Main hook / title text displayed in the tweet card (bold white caps).
    channel_name:
        Display name shown in the profile row.
    handle:
        @username shown in the profile row and bottom bar.
    timestamp_text:
        Fake tweet timestamp (pre-escaped for drawtext).
    stats_text:
        Fake engagement stats line.
    canvas_w, canvas_h:
        Output dimensions (default 1080x1920).
    card_margin_x:
        Horizontal margin for the card (default 60px each side).
    card_start_y:
        Y position where the card begins (default 200).
    blur_strength:
        Boxblur radius for the background.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    card_w = canvas_w - (card_margin_x * 2)  # 960px
    card_padding = 20
    video_w = card_w - (card_padding * 2)  # 920px

    # Escape text for drawtext
    esc_title = _escape_drawtext(title.upper()[:80])
    esc_channel = _escape_drawtext(channel_name)
    esc_handle = _escape_drawtext(handle)
    esc_timestamp = _escape_drawtext(timestamp_text)
    esc_stats = _escape_drawtext(stats_text)
    esc_subscribe = _escape_drawtext("\\u0110\\u0103ng k\\u00fd")  # "Đăng ký" escaped

    # --- Compute card geometry ---
    # Profile row: y_card + 20, height ~50px
    profile_y = card_start_y + card_padding
    # Title: below profile row
    title_y = profile_y + 60
    # Video area: below title (allow 2 lines of title text at 36px = 80px)
    video_area_y = title_y + 80
    # Stats/timestamp/actions go below the video -- we'll position them
    # relative to the video bottom using overlay positioning.

    # --- Build the complex filtergraph ---
    # The strategy:
    # 1. [0:v] split into background (blur) and foreground (card video)
    # 2. Background: scale to fill canvas, crop, blur
    # 3. Foreground video: scale to 920px wide (fit width)
    # 4. Draw the card background box on the blurred bg
    # 5. Overlay the scaled video inside the card
    # 6. Draw all text elements (profile, title, stats, camera UI, bottom bar)

    # We need to know the video height after scaling to 920w to compute
    # card total height. Since we can't know this ahead of time in a single
    # pass, we use a fixed card height that accommodates most aspect ratios.
    # For 16:9 source -> 920px wide = 518px tall video
    # For 4:3 source -> 920px wide = 690px tall video
    # We'll use overlay expressions that reference the scaled video height.

    # Card elements below video (relative to video bottom):
    # timestamp: video_bottom + 16
    # stats: timestamp + 36
    # actions: stats + 36
    # card bottom padding: actions + 40

    # Glitch camera text positions
    cam_y = 30
    cam_left_x = 20
    cam_right_x = canvas_w - 20  # right-aligned

    # Bottom bar position
    bottom_bar_y = canvas_h - 120

    # Build filtergraph as a list of filter steps
    filters: list[str] = []

    # Step 1: Split input into bg and fg streams
    filters.append("[0:v]split=2[bg_src][fg_src]")

    # Step 2: Background - scale to fill, crop to canvas, blur
    filters.append(
        f"[bg_src]scale={canvas_w}:{canvas_h}"
        f":force_original_aspect_ratio=increase,"
        f"crop={canvas_w}:{canvas_h},"
        f"boxblur={blur_strength}:1,"
        # Darken the background slightly for contrast
        f"eq=brightness=-0.15[bg]"
    )

    # Step 3: Scale foreground video to fit card width (920px)
    filters.append(f"[fg_src]scale={video_w}:-2[vid_scaled]")

    # Step 4: Draw the dark card background on the blurred bg
    # We draw a tall enough box to cover the card area. The card height
    # depends on the video height, so we make it generous (covers most cases).
    # Using drawbox with a fixed large height; the bottom will be clipped by canvas.
    card_bg_color = "15202B"
    card_h_max = canvas_h - card_start_y - 200  # leave room for bottom bar
    filters.append(
        f"[bg]drawbox="
        f"x={card_margin_x}:y={card_start_y}:"
        f"w={card_w}:h={card_h_max}:"
        f"color=#{card_bg_color}@0.95:"
        f"t=fill[bg_card]"
    )

    # Step 5: Overlay the scaled video inside the card
    vid_x = card_margin_x + card_padding  # 60 + 20 = 80
    filters.append(
        f"[bg_card][vid_scaled]overlay="
        f"x={vid_x}:y={video_area_y}[canvas_vid]"
    )

    # Step 6: Draw all text elements
    # We chain drawtext filters on the canvas_vid stream.

    # 6a: Profile row - channel name (bold white)
    profile_text_x = card_margin_x + card_padding + 50  # after avatar space
    filters.append(
        f"[canvas_vid]drawtext="
        f"text='{esc_channel}':"
        f"fontsize=28:"
        f"fontcolor=white:"
        f"x={profile_text_x}:y={profile_y}:"
        f"font='Arial Bold'"
        f"[t1]"
    )

    # 6b: Handle (@username) next to channel name
    handle_x = profile_text_x + 10  # slightly indented below
    handle_y = profile_y + 30
    filters.append(
        f"[t1]drawtext="
        f"text='{esc_handle}':"
        f"fontsize=24:"
        f"fontcolor=#8899A6:"
        f"x={handle_x}:y={handle_y}:"
        f"font='Arial'"
        f"[t2]"
    )

    # 6c: Avatar placeholder (gray circle - approximate with a filled box)
    avatar_x = card_margin_x + card_padding
    avatar_y = profile_y
    filters.append(
        f"[t2]drawbox="
        f"x={avatar_x}:y={avatar_y}:"
        f"w=40:h=40:"
        f"color=#657786@1.0:"
        f"t=fill[t3]"
    )

    # 6d: Title text (BOLD WHITE CAPS, 36px)
    title_text_x = card_margin_x + card_padding
    filters.append(
        f"[t3]drawtext="
        f"text='{esc_title}':"
        f"fontsize=36:"
        f"fontcolor=white:"
        f"x={title_text_x}:y={title_y}:"
        f"font='Arial Bold':"
        # Wrap text within card width
        f"width={video_w}"
        f"[t4]"
    )

    # 6e: Timestamp (below video - use expression referencing overlay height)
    # Since we can't easily get the video height in drawtext expressions,
    # we position relative to a calculated offset.
    # For 16:9 video at 920w: height = 518. video_area_y + 518 + 16 = ~814
    # We use a generous estimate and let it adapt via the 'y' expression.
    # Actually, ffmpeg drawtext y can use 'h' (frame height) but not overlay
    # dimensions. We'll use a fixed position that works for most aspect ratios.
    # video_area_y (340) + estimated video height (~518 for 16:9) + gap
    ts_y_expr = f"{video_area_y}+518+16"
    filters.append(
        f"[t4]drawtext="
        f"text='{esc_timestamp}':"
        f"fontsize=22:"
        f"fontcolor=#8899A6:"
        f"x={title_text_x}:y={ts_y_expr}:"
        f"font='Arial'"
        f"[t5]"
    )

    # 6f: Stats row
    stats_y_expr = f"{video_area_y}+518+52"
    filters.append(
        f"[t5]drawtext="
        f"text='{esc_stats}':"
        f"fontsize=22:"
        f"fontcolor=white:"
        f"x={title_text_x}:y={stats_y_expr}:"
        f"font='Arial'"
        f"[t6]"
    )

    # 6g: Action buttons row (unicode symbols)
    actions_text = _escape_drawtext("\U0001f4ac   \U0001f501   ❤️   ⬆️")
    actions_y_expr = f"{video_area_y}+518+88"
    filters.append(
        f"[t6]drawtext="
        f"text='{actions_text}':"
        f"fontsize=24:"
        f"fontcolor=#8899A6:"
        f"x={title_text_x}:y={actions_y_expr}:"
        f"font='Arial'"
        f"[t7]"
    )

    # Step 7: Glitch camera overlay (top)
    # Three passes for chromatic aberration: red shifted left, blue shifted right, white on top
    cam_text_left = _escape_drawtext("CAMERAI PLAY ▶ 00:00:XX")
    cam_text_right = _escape_drawtext("SOURCE IPHONE")

    # Red pass (shifted 1px left)
    filters.append(
        f"[t7]drawtext="
        f"text='{cam_text_left}':"
        f"fontsize=20:"
        f"fontcolor=#FF0000@0.5:"
        f"x={cam_left_x - 1}:y={cam_y}:"
        f"font='Courier New'"
        f"[g1]"
    )
    # Blue pass (shifted 1px right)
    filters.append(
        f"[g1]drawtext="
        f"text='{cam_text_left}':"
        f"fontsize=20:"
        f"fontcolor=#0000FF@0.5:"
        f"x={cam_left_x + 1}:y={cam_y}:"
        f"font='Courier New'"
        f"[g2]"
    )
    # White pass (center - on top)
    filters.append(
        f"[g2]drawtext="
        f"text='{cam_text_left}':"
        f"fontsize=20:"
        f"fontcolor=white:"
        f"x={cam_left_x}:y={cam_y}:"
        f"font='Courier New'"
        f"[g3]"
    )

    # Right side camera text - same glitch treatment
    # Red pass
    filters.append(
        f"[g3]drawtext="
        f"text='{cam_text_right}':"
        f"fontsize=20:"
        f"fontcolor=#FF0000@0.5:"
        f"x=w-tw-{20 + 1}:y={cam_y}:"
        f"font='Courier New'"
        f"[g4]"
    )
    # Blue pass
    filters.append(
        f"[g4]drawtext="
        f"text='{cam_text_right}':"
        f"fontsize=20:"
        f"fontcolor=#0000FF@0.5:"
        f"x=w-tw-{20 - 1}:y={cam_y}:"
        f"font='Courier New'"
        f"[g5]"
    )
    # White pass
    filters.append(
        f"[g5]drawtext="
        f"text='{cam_text_right}':"
        f"fontsize=20:"
        f"fontcolor=white:"
        f"x=w-tw-20:y={cam_y}:"
        f"font='Courier New'"
        f"[g6]"
    )

    # Step 8: Bottom bar
    # Profile handle + subscribe button area
    bottom_handle_y = bottom_bar_y
    filters.append(
        f"[g6]drawtext="
        f"text='{esc_handle}':"
        f"fontsize=24:"
        f"fontcolor=white:"
        f"x=80:y={bottom_handle_y}:"
        f"font='Arial'"
        f"[b1]"
    )

    # Subscribe button (white pill background + text)
    subscribe_x = canvas_w - 180
    filters.append(
        f"[b1]drawbox="
        f"x={subscribe_x}:y={bottom_handle_y - 4}:"
        f"w=120:h=32:"
        f"color=white@0.95:"
        f"t=fill[b2]"
    )
    filters.append(
        f"[b2]drawtext="
        f"text='{esc_subscribe}':"
        f"fontsize=18:"
        f"fontcolor=black:"
        f"x={subscribe_x + 16}:y={bottom_handle_y + 4}:"
        f"font='Arial Bold'"
        f"[b3]"
    )

    # Bottom title text (repeated, white, below the bar)
    bottom_title_y = bottom_handle_y + 44
    # Truncate for bottom display
    bottom_title = _escape_drawtext(title[:50].upper())
    filters.append(
        f"[b3]drawtext="
        f"text='{bottom_title}':"
        f"fontsize=28:"
        f"fontcolor=white:"
        f"x=80:y={bottom_title_y}:"
        f"font='Arial Bold':"
        f"width={canvas_w - 160}"
        f"[out]"
    )

    # Join all filter steps
    filtergraph = ";\n".join(filters)

    cmd = [
        _ffmpeg_bin(),
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
        "-filter_complex",
        filtergraph,
        "-map",
        "[out]",
        "-map",
        "0:a?",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-c:a",
        "copy",
        str(output_path),
    ]

    logger.info(
        "tweet_layout: composing card (title={!r}, channel={!r}, handle={!r})",
        title[:30],
        channel_name,
        handle,
    )
    subprocess.run(cmd, check=True, capture_output=True)
    return output_path


def apply_layout(
    input_path: str | Path,
    output_path: str | Path,
    layout: str = "fit_blur",
    *,
    focus_track: Any = None,
    title: str = "",
    channel_name: str = "Channel Name",
    handle: str = "@handle",
    **kwargs: Any,
) -> Path:
    """Dispatch to the appropriate layout composer.

    Parameters
    ----------
    input_path:
        Source video.
    output_path:
        Destination 1080x1920 mp4.
    layout:
        Layout identifier. One of: ``fit_blur``, ``tweet_card``.
    focus_track:
        Focus keypoints for fit_blur layout (passed to crop_to_9_16).
    title:
        Title/hook text for tweet_card layout.
    channel_name:
        Display name for tweet_card layout.
    handle:
        @username for tweet_card layout.
    **kwargs:
        Additional keyword arguments forwarded to the layout function.
    """
    from ffmpeg.crop import crop_to_9_16

    layout = (layout or "fit_blur").strip().lower()

    if layout == "tweet_card":
        return apply_tweet_layout(
            input_path,
            output_path,
            title=title,
            channel_name=channel_name,
            handle=handle,
            **kwargs,
        )
    elif layout == "fit_blur":
        return crop_to_9_16(input_path, output_path, focus_track=focus_track)
    else:
        logger.warning(
            "Unknown layout {!r}, falling back to fit_blur", layout
        )
        return crop_to_9_16(input_path, output_path, focus_track=focus_track)
