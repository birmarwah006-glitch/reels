"""
Orchestrator — a finished Chipper manifest in, one reel per module out.

Reads MAROS's outputs/{job_id}/manifest.json read-only. Nothing in the MAROS
tree is written to; every artifact this produces lands under clipper/.
"""

import argparse
import json
import sys
from pathlib import Path

import clipper
from clipper.build_reel import build_reel
from clipper.generate_audio import generate_stems, get_durations
from clipper.generate_script import flag_for_review, generate_conversation
from clipper.pyq_matcher import get_relevant_pyqs, tag_module_concepts

PKG_DIR = Path(__file__).parent
DEFAULT_OUTPUTS_DIR = clipper.MAROS_ROOT / "outputs"

BG_PATH = str(PKG_DIR / "assets" / "backgrounds" / "classroom.png")
CHAR_A = str(PKG_DIR / "assets" / "characters" / "character_a.png")
CHAR_B = str(PKG_DIR / "assets" / "characters" / "character_b.png")


def run_module(job_id: str, module: dict) -> dict | None:
    """Plan, narrate and render one module. Returns a result dict, or None if
    the module was skipped."""
    module_id = module["module_id"]
    print(f"\n[clipper] module {module_id}: {module['concept'][:70]}")

    tags = tag_module_concepts(module)
    print(f"  tags: {tags or 'none'}")

    pyqs = get_relevant_pyqs(tags)
    if pyqs:
        print(f"  pyqs: {', '.join(q['q_id'] for q in pyqs)}")

    lines = generate_conversation(module, pyq_matches=pyqs)
    if lines is None:
        return None

    words = sum(len(l["text"].split()) for l in lines)
    print(f"  script: {len(lines)} lines, {words} words")

    flagged = flag_for_review(lines, module["transcript"])
    if flagged:
        print("  FLAGGED: CHARACTER_A's lines barely overlap the transcript")

    stems = get_durations(generate_stems(
        lines, out_dir=str(PKG_DIR / "audio_stems" / f"{job_id}_{module_id}")
    ))

    out_path = str(PKG_DIR / "output" / f"{job_id}_reel_{module_id}.mp4")
    build_reel(stems, BG_PATH, CHAR_A, CHAR_B, out_path)

    duration = sum(s["duration"] for s in stems)
    print(f"  rendered: {out_path}  ({duration:.1f}s)")

    return {
        "job_id": job_id,
        "module_id": module_id,
        "concept": module["concept"],
        "concept_tags": tags,
        "pyq_ids": [q["q_id"] for q in pyqs],
        "lines": lines,
        "flagged_for_review": flagged,
        "video_path": out_path,
        "duration_sec": round(duration, 1),
    }


def run_clipper(job_id: str, outputs_dir: Path = DEFAULT_OUTPUTS_DIR,
                only_module: int | None = None):
    manifest_path = outputs_dir / job_id / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest at {manifest_path}")

    manifest = json.loads(manifest_path.read_text())
    modules = manifest["modules"]
    if only_module is not None:
        modules = [m for m in modules if m["module_id"] == only_module]
        if not modules:
            raise KeyError(f"Module {only_module} not in job {job_id}")

    results, review_flags = [], []
    for m in modules:
        try:
            result = run_module(job_id, m)
        except Exception as e:
            print(f"[clipper] module {m['module_id']} FAILED: {e}")
            continue
        if result is None:
            continue
        results.append(result)
        if result["flagged_for_review"]:
            review_flags.append(result["module_id"])

    if results:
        sidecar = PKG_DIR / "output" / f"{job_id}_reels.json"
        sidecar.write_text(json.dumps({"job_id": job_id, "reels": results}, indent=2))
        print(f"\n[clipper] {len(results)} reel(s) written, index at {sidecar}")

    if review_flags:
        flag_file = PKG_DIR / "output" / f"{job_id}_review_flags.txt"
        flag_file.write_text("\n".join(str(m) for m in review_flags))
        print(f"[clipper] modules flagged for manual review: {review_flags}")

    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("job_id")
    ap.add_argument("--outputs-dir", default=str(DEFAULT_OUTPUTS_DIR))
    ap.add_argument("--module", type=int, default=None,
                    help="render just this module id")
    args = ap.parse_args()

    run_clipper(args.job_id, Path(args.outputs_dir), only_module=args.module)


if __name__ == "__main__":
    sys.exit(main())
