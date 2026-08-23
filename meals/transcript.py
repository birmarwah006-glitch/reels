"""
Transcript sourcing for the Meal pipeline.

Transcription is both the slowest stage and the one that decides how good
everything downstream can be — the planner cannot find a concept the
transcript garbled.

MAROS's chipper runs faster-whisper at the `tiny` size, which is ~35x realtime
but produces text like:

    "The project that I have, how to get it. So here we have to move the
     main thing. That I will use this user."

against a video whose own captions read:

    "Welcome back to our Python programming project series where we are going
     to build beginner to advanced projects one by one in Python programming."

Measured on the same 60 seconds: tiny 35x realtime and incoherent, base 30x
and still poor, small 11x and usable. YouTube's own captions are better than
all three and cost a single request.

So: prefer the publisher's captions, fall back to Whisper at a size that is
actually accurate. Chipper is left alone — this is a preference expressed by
the Meal pipeline, not a change to the backend.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

# `tiny` is chipper's default and is not good enough for technical material.
# `small` is roughly 11x realtime on CPU, so a two-hour course is ~12 minutes.
FALLBACK_WHISPER_SIZE = "small"

_TIMESTAMP = re.compile(r"(\d{2}:\d{2}):\d{2}\.\d+ -->")
_TAGS = re.compile(r"<[^>]+>")


def _vtt_to_text(vtt: str, words_per_block: int = 160) -> str:
    """Flatten a WebVTT file into timestamped paragraphs.

    YouTube's auto-captions repeat each line as a rolling window, so a naive
    read triples the text and wrecks the word counts everything downstream is
    budgeted against. Lines are de-duplicated in order.
    """
    stamp, seen, cues = "00:00", set(), []
    for line in vtt.splitlines():
        if match := _TIMESTAMP.match(line):
            stamp = match.group(1)
            continue
        if not line.strip() or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")):
            continue
        # Captions arrive HTML-escaped: &nbsp; and &amp; would otherwise be
        # read aloud by the TTS voice as literal text.
        text = html.unescape(_TAGS.sub("", line)).replace("\xa0", " ").strip()
        text = re.sub(r"\s+", " ", text)
        if text and text not in seen:
            seen.add(text)
            cues.append((stamp, text))

    blocks, current, current_stamp = [], [], cues[0][0] if cues else "00:00"
    for stamp, text in cues:
        if len(" ".join(current).split()) >= words_per_block:
            blocks.append(f"[{current_stamp}] " + " ".join(current))
            current, current_stamp = [], stamp
        current.append(text)
    if current:
        blocks.append(f"[{current_stamp}] " + " ".join(current))

    return "\n\n".join(blocks)


def fetch_youtube_captions(url: str, cookies: Path | None = None) -> tuple[str, str] | None:
    """The publisher's own captions, or None if the video has none.

    Returns (title, transcript). Never raises — a missing caption track is an
    ordinary outcome, not an error, and the caller falls back to Whisper.
    """
    ytdlp = shutil.which("yt-dlp") or str(Path.home() / ".local/bin/yt-dlp")
    if not Path(ytdlp).exists():
        return None

    with tempfile.TemporaryDirectory() as tmp:
        # --print suppresses the subtitle write, so the title is taken from a
        # separate --print-to-file rather than stdout.
        command = [
            ytdlp, "--skip-download",
            "--write-auto-sub", "--write-sub",
            "--sub-lang", "en.*", "--sub-format", "vtt",
            "--print-to-file", "%(title)s", f"{tmp}/title.txt",
            "-o", f"{tmp}/cap.%(ext)s", url,
        ]
        if cookies and Path(cookies).exists():
            command[1:1] = ["--cookies", str(cookies)]

        try:
            subprocess.run(command, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return None

        files = sorted(Path(tmp).glob("*.vtt"))
        if not files:
            return None

        title_file = Path(tmp) / "title.txt"
        title = (title_file.read_text().strip().splitlines() or ["Lecture"])[0] \
            if title_file.exists() else "Lecture"
        text = _vtt_to_text(files[0].read_text(encoding="utf-8", errors="replace"))

    # A handful of cues is a music video or a title card, not a lecture.
    return (title, text) if len(text.split()) > 300 else None


if __name__ == "__main__":
    import sys
    result = fetch_youtube_captions(
        sys.argv[1], Path("/Users/biradatiya/Desktop/MAROS/config/cookies.txt"))
    if not result:
        print("no captions available — the pipeline would fall back to Whisper")
        raise SystemExit(1)
    title, text = result
    print(f"title : {title}")
    print(f"words : {len(text.split()):,}")
    print(f"head  : {text[:200]}")
