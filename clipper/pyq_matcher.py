"""
PYQ pool loading and concept matching for clipper.

The pool is pre-tagged against the same 12-concept graph MAROS's prep mode
uses (cpu-virtualization, file-systems, data-integrity, persistence,
memory-virtualization, paging, computer-architecture, locks, threads,
concurrency, processes, scheduling), so matching here is a tag lookup, not
a second tagging pass.
"""

import json
from pathlib import Path

import clipper  # noqa: F401  — puts MAROS on sys.path
from chipper import _chipper_llm
from prep_mode import _tag_prompt, _parse_concepts, heuristic_concept_tagger

PYQ_POOL = json.loads((Path(__file__).parent / "data" / "pyq_pool.json").read_text())

# The 12-concept graph, derived from the pool itself so it can never drift
# out of sync with the data.
VALID_CONCEPTS = sorted({c for q in PYQ_POOL for c in q["concepts"]})


def tag_module_concepts(module: dict, transcript_chars: int = 1200) -> list[str]:
    """Map a module's free-text concept name onto the 12-concept graph.

    This reuses prep mode's own tagger (_tag_prompt / _parse_concepts) rather
    than introducing a second one, so a module and a PYQ are tagged by the
    same rules. The LLM call is chipper's router; if it fails or returns
    nothing usable, prep mode's keyword tagger covers it offline.

    A module may already carry `concept_tags` (e.g. written by an earlier
    pass) — that is trusted and returned as-is.
    """
    existing = [c for c in module.get("concept_tags", []) if c in VALID_CONCEPTS]
    if existing:
        return existing

    text = f"{module.get('concept', '')}\n\n{(module.get('transcript') or '')[:transcript_chars]}"
    prompt = _tag_prompt(text)

    try:
        tags = _parse_concepts(_chipper_llm(prompt, temperature=0, json_mode=True))
        if tags:
            return tags
    except Exception as e:
        print(f"[clipper] LLM concept tagging failed ({e}) — falling back to keywords")

    return _parse_concepts(heuristic_concept_tagger(prompt))


def get_relevant_pyqs(module_concept_tags: list[str], max_q: int = 2) -> list[dict]:
    """Top PYQs matching any of the given concept tags.

    Featured demo questions sort first, then by marks weight. Questions with
    marks: null sort last within their group rather than crashing the sort.
    """
    if not module_concept_tags:
        return []

    matches = [q for q in PYQ_POOL if any(t in q["concepts"] for t in module_concept_tags)]
    matches.sort(key=lambda q: (q.get("featured", False), q.get("marks") or 0), reverse=True)
    return matches[:max_q]
