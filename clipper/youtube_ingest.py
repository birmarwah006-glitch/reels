"""
YouTube -> module dicts, in the exact shape run_clipper.py already reads
out of a Chipper manifest ({module_id, concept, transcript}).

Two new deps beyond what's already in requirements.txt:
- yt-dlp (audio download; runs as a subprocess, no Python import needed)
- faster-whisper is already in requirements.txt (local transcription)

Nothing here touches MAROS. This only prepares the `modules` list that
run_module() (in run_clipper.py) already knows how to turn into a reel.
"""

import subprocess
import tempfile
from pathlib import Path


def download_audio(youtube_url: str, out_dir: Path) -> Path:
    out_template = str(out_dir / "audio.%(ext)s")
    print(f"[youtube_ingest] downloading audio: {youtube_url}")
    subprocess.run(
        ["yt-dlp", "-x", "--audio-format", "mp3", "-o", out_template, youtube_url],
        check=True,
    )
    mp3s = list(out_dir.glob("audio.mp3"))
    if not mp3s:
        raise RuntimeError("yt-dlp finished but no audio.mp3 was produced")
    return mp3s[0]


def transcribe(audio_path: Path) -> list[dict]:
    """Local transcription via faster-whisper. Returns
    [{"start": float, "end": float, "text": str}, ...]."""
    from faster_whisper import WhisperModel

    print(f"[youtube_ingest] transcribing {audio_path.name} (faster-whisper, base model)")
    model = WhisperModel("base", compute_type="int8")
    segments, info = model.transcribe(str(audio_path))
    out = [{"start": s.start, "end": s.end, "text": s.text.strip()} for s in segments]
    print(f"[youtube_ingest] transcribed {len(out)} segments, "
          f"~{info.duration:.0f}s audio, language={info.language}")
    return out


def chunk_into_modules(segments: list[dict], chunk_minutes: float, title: str) -> list[dict]:
    """Group whisper segments into ~chunk_minutes-long modules."""
    chunk_seconds = chunk_minutes * 60
    modules: list[dict] = []
    current: list[dict] = []
    current_start = 0.0
    module_id = 1

    def flush():
        nonlocal module_id
        text = " ".join(s["text"] for s in current).strip()
        modules.append({
            "module_id": module_id,
            "concept": f"{title} - part {module_id}",
            "transcript": text,
        })
        module_id += 1

    for seg in segments:
        if current and seg["start"] - current_start >= chunk_seconds:
            flush()
            current = []
        if not current:
            current_start = seg["start"]
        current.append(seg)

    if current:
        flush()

    return modules


def build_modules_from_youtube(youtube_url: str, chunk_minutes: float = 4.0,
                                title: str | None = None) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        audio_path = download_audio(youtube_url, tmp_path)
        segments = transcribe(audio_path)
        modules = chunk_into_modules(segments, chunk_minutes, title or youtube_url)
        print(f"[youtube_ingest] grouped into {len(modules)} module(s), "
              f"~{chunk_minutes:.1f} min each")
        return modules
