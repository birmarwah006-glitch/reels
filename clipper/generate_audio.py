"""
Step 2 — dialogue lines to per-line TTS stems.

edge-tts (the same engine MAROS uses in podcastengine), with the two speakers
cast as genuinely different voices rather than one voice in two accents:

    A — clipped, precise, higher and faster. The smug know-it-all.
    B — slower, lower, louder. The confident idiot.

The contrast comes from voice choice plus prosody (rate / pitch / volume).
These are original synthetic voices, deliberately NOT clones of any existing
performance — see the note in the project brief.

gTTS is kept as an offline fallback: edge-tts needs network access, so if the
call fails the pipeline still produces audio rather than dying mid-run.
"""

import asyncio
import os

import edge_tts

# Prosody is what sells the characters. Rate/pitch/volume are edge-tts's own
# SSML-style knobs; the percentages are relative to the voice's baseline.
VOICE_CONFIG = {
    "A": {
        "voice": os.getenv("CLIPPER_VOICE_A", "en-GB-ThomasNeural"),
        "rate": "+12%",      # talks fast because he thinks you're slow
        "pitch": "+18Hz",    # tighter, more clipped
        "volume": "+0%",
    },
    "B": {
        "voice": os.getenv("CLIPPER_VOICE_B", "en-US-GuyNeural"),
        "rate": "-8%",       # takes his time getting it wrong
        "pitch": "-25Hz",    # heavier, dopier
        "volume": "+15%",    # and louder about it
    },
}

# gTTS fallback keeps the old accent-only split.
GTTS_CONFIG = {
    "A": {"lang": "en", "tld": "co.uk"},
    "B": {"lang": "en", "tld": "com"},
}


async def _edge_save(text: str, cfg: dict, path: str, attempts: int = 3):
    """Render one line, retrying edge-tts transients. Raises if all fail.

    edge-tts occasionally 'succeeds' while writing a zero-byte file, so the
    size is checked rather than trusted — same guard podcastengine uses.
    """
    for attempt in range(attempts):
        try:
            comm = edge_tts.Communicate(
                text,
                cfg["voice"],
                rate=cfg["rate"],
                pitch=cfg["pitch"],
                volume=cfg["volume"],
            )
            await comm.save(path)
            if os.path.exists(path) and os.path.getsize(path) > 100:
                return
            raise RuntimeError("edge-tts wrote an empty file")
        except Exception:
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(2 ** attempt)


def _gtts_save(text: str, speaker: str, path: str):
    from gtts import gTTS

    cfg = GTTS_CONFIG[speaker]
    gTTS(text=text, lang=cfg["lang"], tld=cfg["tld"], slow=False).save(path)


async def _generate_all(lines: list, out_dir: str) -> list:
    stems = []
    for i, line in enumerate(lines):
        speaker = line["speaker"]
        path = f"{out_dir}/line_{i:03d}_{speaker}.mp3"
        try:
            await _edge_save(line["text"], VOICE_CONFIG[speaker], path)
        except Exception as e:
            print(f"  [clipper-tts] line {i} edge-tts failed ({e}) — falling back to gTTS")
            _gtts_save(line["text"], speaker, path)
        stems.append({"path": path, "speaker": speaker, "text": line["text"]})
    return stems


def generate_stems(lines: list, out_dir: str = "clipper/audio_stems") -> list:
    """One mp3 per line. Returns [{path, speaker, text}]."""
    os.makedirs(out_dir, exist_ok=True)
    return asyncio.run(_generate_all(lines, out_dir))


def get_durations(stems: list) -> list:
    """Attach the real rendered duration to each stem — the compositor needs
    it to place characters and captions on the timeline."""
    from moviepy import AudioFileClip

    for s in stems:
        clip = AudioFileClip(s["path"])
        s["duration"] = clip.duration
        clip.close()
    return stems


if __name__ == "__main__":
    # Confirm the two voices read as different characters, not one narrator
    # doing two accents.
    demo = [
        {"speaker": "A", "text": "The timer interrupt is what lets the operating system reclaim the CPU."},
        {"speaker": "B", "text": "Oh, so the computer has a tiny alarm clock that yells at the programs?"},
    ]
    out = get_durations(generate_stems(demo, out_dir="clipper/audio_stems/_voicetest"))
    for s in out:
        cfg = VOICE_CONFIG[s["speaker"]]
        print(f"  {s['speaker']}  {cfg['voice']:<24} rate={cfg['rate']:>5} "
              f"pitch={cfg['pitch']:>7}  {s['duration']:5.2f}s  {s['path']}")
