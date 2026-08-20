"""
Render a reel in which the two characters SOLVE one real past-year question.

    python -m clipper.run_pyq 2017-ct1-2
    python -m clipper.run_pyq --featured          # every featured question
    python -m clipper.run_pyq --concept file-systems --limit 3

Output lands in clipper/output/pyq_{q_id}.mp4 alongside the module reels.
Nothing in the MAROS tree is written to.
"""

import argparse
import json
import sys
from pathlib import Path

from clipper.build_reel import build_reel
from clipper.generate_audio import generate_stems, get_durations
from clipper.generate_script import generate_pyq_solution
from clipper.pyq_matcher import PYQ_POOL

PKG_DIR = Path(__file__).parent

BG_PATH = str(PKG_DIR / "assets" / "backgrounds" / "classroom.png")
CHAR_A = str(PKG_DIR / "assets" / "characters" / "character_a.png")
CHAR_B = str(PKG_DIR / "assets" / "characters" / "character_b.png")


def find_pyq(q_id: str) -> dict:
    for q in PYQ_POOL:
        if q["q_id"] == q_id:
            return q
    raise KeyError(f"No question with q_id {q_id!r} in the pool")


def run_pyq(pyq: dict, module: dict | None = None) -> dict | None:
    """Script, narrate and render one PYQ-solving reel."""
    marks = f", {pyq['marks']:g}m" if pyq.get("marks") else ""
    print(f"\n[clipper] {pyq['q_id']}  ({pyq['year']} {pyq['exam_type']}{marks})")
    print(f"  topic: {pyq['text'][:70]}")

    lines = generate_pyq_solution(pyq, module=module)
    if lines is None:
        return None

    words = sum(len(l["text"].split()) for l in lines)
    print(f"  script: {len(lines)} lines, {words} words")

    stems = get_durations(generate_stems(
        lines, out_dir=str(PKG_DIR / "audio_stems" / f"pyq_{pyq['q_id']}")
    ))

    out_path = str(PKG_DIR / "output" / f"pyq_{pyq['q_id']}.mp4")
    build_reel(stems, BG_PATH, CHAR_A, CHAR_B, out_path)

    duration = sum(s["duration"] for s in stems)
    print(f"  rendered: {out_path}  ({duration:.1f}s)")

    return {
        "q_id": pyq["q_id"],
        "year": pyq["year"],
        "exam_type": pyq["exam_type"],
        "marks": pyq.get("marks"),
        "concepts": pyq["concepts"],
        "question": pyq["text"],
        "lines": lines,
        "video_path": out_path,
        "duration_sec": round(duration, 1),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("q_id", nargs="?", help="question id, e.g. 2017-ct1-2")
    ap.add_argument("--featured", action="store_true", help="render every featured question")
    ap.add_argument("--concept", help="render questions tagged with this concept")
    ap.add_argument("--limit", type=int, default=1)
    args = ap.parse_args()

    if args.q_id:
        targets = [find_pyq(args.q_id)]
    elif args.featured:
        targets = [q for q in PYQ_POOL if q.get("featured")]
    elif args.concept:
        targets = [q for q in PYQ_POOL if args.concept in q["concepts"]][: args.limit]
    else:
        ap.error("give a q_id, or --featured, or --concept")

    results = []
    for pyq in targets:
        try:
            r = run_pyq(pyq)
        except Exception as e:
            print(f"[clipper] {pyq['q_id']} FAILED: {e}")
            continue
        if r:
            results.append(r)

    if results:
        sidecar = PKG_DIR / "output" / "pyq_reels.json"
        existing = {}
        if sidecar.exists():
            try:
                existing = {r["q_id"]: r for r in json.loads(sidecar.read_text())["reels"]}
            except Exception:
                pass
        for r in results:
            existing[r["q_id"]] = r
        sidecar.write_text(json.dumps({"reels": list(existing.values())}, indent=2))
        print(f"\n[clipper] {len(results)} PYQ reel(s) written, index at {sidecar}")

    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
