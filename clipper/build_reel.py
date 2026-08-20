"""
Step 3 — stems + character PNGs to a finished 9:16 mp4.

One character is on screen at a time: whoever is speaking. Captions are
colour-coded per speaker so the swap reads instantly even on mute.
"""

import os

from moviepy import (
    AudioFileClip,
    CompositeVideoClip,
    ImageClip,
    TextClip,
    concatenate_audioclips,
)

W, H = 1080, 1920

CAPTION_COLOR = {
    "A": "#7EE8FA",
    "B": "#FFD166",
}

# MoviePy 2.x renders text through Pillow, so this must be a font FILE, not a
# family name. Override per machine with CLIPPER_FONT.
FONT = os.getenv("CLIPPER_FONT", "/System/Library/Fonts/Supplemental/Arial Bold.ttf")

CAPTION_FONT_SIZE = 56
CAPTION_BOTTOM_OFFSET = 300
CHARACTER_HEIGHT = 900


def _caption_clip(text: str, speaker: str, duration: float, start: float):
    """One caption line.

    The vertical margin is load-bearing: MoviePy 2.x fits the text box to a
    tight glyph bbox, which slices the descenders off every line without it.
    """
    return (
        TextClip(
            font=FONT,
            text=text,
            font_size=CAPTION_FONT_SIZE,
            color=CAPTION_COLOR[speaker],
            stroke_color="black",
            stroke_width=3,
            size=(W - 100, None),
            method="caption",
            text_align="center",
            margin=(0, 20),
        )
        .with_duration(duration)
        .with_start(start)
        .with_position(("center", H - CAPTION_BOTTOM_OFFSET))
    )


def _character_clip(png_path: str, speaker: str, duration: float, start: float):
    """The speaking character, anchored bottom-left for A, bottom-right for B."""
    return (
        ImageClip(png_path)
        .with_duration(duration)
        .with_start(start)
        .resized(height=CHARACTER_HEIGHT)
        .with_position(("left" if speaker == "A" else "right", "bottom"))
    )


def build_reel(stems: list, bg_path: str, char_a_png: str, char_b_png: str, out_path: str):
    """Composite the reel and write it to out_path."""
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    audio_clips = [AudioFileClip(s["path"]) for s in stems]
    full_audio = concatenate_audioclips(audio_clips)
    total_dur = full_audio.duration

    bg = ImageClip(bg_path).with_duration(total_dur).resized((W, H))
    layers = [bg]

    t_cursor = 0.0
    for s in stems:
        png = char_a_png if s["speaker"] == "A" else char_b_png
        layers.append(_character_clip(png, s["speaker"], s["duration"], t_cursor))
        layers.append(_caption_clip(s["text"], s["speaker"], s["duration"], t_cursor))
        t_cursor += s["duration"]

    final = CompositeVideoClip(layers, size=(W, H)).with_duration(total_dur)
    final = final.with_audio(full_audio)
    final.write_videofile(out_path, fps=24, codec="libx264", audio_codec="aac", logger=None)

    final.close()
    for c in audio_clips:
        c.close()
    return out_path


if __name__ == "__main__":
    # Test step 2: one line per speaker against the real background, to
    # confirm caption sync, caption colour swap, and character side swap
    # before any of it is driven by a real script.
    from clipper.generate_audio import generate_stems, get_durations

    demo = [
        {"speaker": "A", "text": "A timer interrupt lets the OS take the CPU back by force."},
        {"speaker": "B", "text": "So the computer has a tiny angry alarm clock. Got it."},
    ]
    stems = get_durations(generate_stems(demo, out_dir="clipper/audio_stems/_buildtest"))
    out = build_reel(
        stems,
        bg_path="clipper/assets/backgrounds/classroom.png",
        char_a_png="clipper/assets/characters/character_a.png",
        char_b_png="clipper/assets/characters/character_b.png",
        out_path="clipper/output/_buildtest.mp4",
    )
    print(f"wrote {out}")
