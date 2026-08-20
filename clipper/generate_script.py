"""
Step 1 — transcript to a two-character dialogue.

CHARACTER_A explains the real concept, grounded in the transcript.
CHARACTER_B is wrong in a funny way first, then accidentally lands on the
right intuition.

The LLM call is chipper's _chipper_llm (Cerebras-primary / Groq-fallback,
retry-on-transient, semaphore-guarded). Nothing new is written here.
"""

import json
import re

import clipper  # noqa: F401  — puts MAROS on sys.path
from chipper import _chipper_llm, _strip_json_fences

# LLMs emit typographic unicode (smart quotes, non-breaking hyphens, em
# dashes). gTTS mispronounces some of it and it renders as tofu boxes in the
# captions, so every line is flattened to ASCII punctuation before it is
# either spoken or drawn.
_TYPOGRAPHY = {
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2011": "-", "\u2212": "-",
    "\u2026": "...", "\u00a0": " ", "\u200b": "",
    # Dashes get spaces around them: a bare "-" glues words together
    # ("Got it-an implication"), which both reads wrong and speaks wrong.
    "\u2013": " - ", "\u2014": " - ",
    # Symbols gTTS either skips silently or mangles. These are read ALOUD,
    # so they have to become words, not punctuation.
    "\u2192": " implies ", "->": " implies ", "\u21d2": " implies ",
    "\u2194": " if and only if ", "\u2261": " is equivalent to ",
    "\u00ac": " not ", "\u2227": " and ", "\u2228": " or ",
    "\u2264": " at most ", "\u2265": " at least ", "\u2260": " not equal to ",
}


def _llm_json(prompt: str, temperature: float, label: str) -> str:
    """chipper's router, with a guard for the reasoning-model failure mode.

    GROQ_CHAT_MODEL is openai/gpt-oss-120b, a REASONING model, and
    _chipper_llm's Groq branch sends no max_tokens and no reasoning_effort.
    On a long prompt the model spends its whole budget on hidden reasoning
    and returns empty `content` — which, under json_mode, Groq rejects
    outright as json_validate_failed with an empty failed_generation.

    So: try the router first (it handles Cerebras-primary routing and the
    transient retries). If it comes back empty, re-issue the same prompt to
    Groq with reasoning_effort low and an explicit token budget, reusing
    config's own credentials and model. This is a workaround for a model
    quirk, not a second router — MAROS is not modified.
    """
    try:
        out = _chipper_llm(prompt, temperature=temperature, json_mode=True)
        if out and out.strip():
            return out
        print(f"  [clipper] {label}: router returned empty — retrying with reasoning_effort=low")
    except Exception as e:
        print(f"  [clipper] {label}: router failed ({e}) — retrying with reasoning_effort=low")

    import requests
    from config import GROQ_HEADERS, GROQ_BASE_URL, GROQ_CHAT_MODEL

    body = {
        "model": GROQ_CHAT_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": 4000,
        "response_format": {"type": "json_object"},
    }
    if "gpt-oss" in GROQ_CHAT_MODEL:
        body["reasoning_effort"] = "low"

    res = requests.post(f"{GROQ_BASE_URL}/chat/completions",
                        headers=GROQ_HEADERS, json=body, timeout=120)
    res.raise_for_status()
    return res.json()["choices"][0]["message"]["content"]


def _normalize(text: str) -> str:
    for bad, good in _TYPOGRAPHY.items():
        text = text.replace(bad, good)
    return re.sub(r"\s+", " ", text).strip()

# gTTS delivers ~115-120 words/minute, so 140 words lands at ~70s — over the
# 50-60s target. 105 is the budget that actually hits the window.
MAX_WORDS = 105
MIN_LINES = 4

SCRIPT_PROMPT = """
You are writing a 50-60 second two-character comedic explainer script based on this lecture module.

THE WHOLE SCRIPT IS BUILT AROUND ONE CONCRETE WORKED EXAMPLE.
Pick the single clearest example from the transcript — an actual case with
actual values, inputs, or steps. If the transcript contains a worked example,
use THAT one. Do not explain the concept in the abstract; explain it by
walking this one example through, start to finish. By the last line the
listener should have seen the example fully resolved.

CHARACTER_A: hyper-intelligent, precise, slightly condescending. Walks the
example step by step, using the real values and terms from the transcript.
Every claim must be factually accurate and grounded in the transcript below —
do not invent facts, numbers, or steps that are not there.

CHARACTER_B: well-meaning, dim, and CONFIDENTLY WRONG. This is the important
part: B's errors must be the mistakes a REAL STUDENT ACTUALLY MAKES on this
exact topic — the classic misreading, the step everyone skips, the rule
applied backwards. B is funny because he states a genuinely common error with
total certainty, not because he is random. B stays ON TOPIC and on THIS
example the whole time.

Do NOT write B lines that are surreal non-sequiturs (talking cats, exploding
universes, dinosaur costumes). Every B line must be a wrong answer someone
could plausibly write in an exam and lose marks for.

ARC — follow this shape:
1. A introduces the concrete example with its real values.
2. B jumps in with the classic wrong reading of it, stated confidently.
3. A corrects B, showing why that specific error is wrong.
4. B tries again — closer, but still makes a second common slip.
5. A resolves the example completely and states the general rule.
6. B restates the rule in his own dumb words — and this time is ACCIDENTALLY
   CORRECT. It must be genuinely right, just phrased like an idiot.

MODULE CONCEPT: {concept}
LECTURE TRANSCRIPT FOR THIS MODULE:
{transcript}

{pyq_block}

EVERY LINE IS READ ALOUD BY A TEXT-TO-SPEECH VOICE. Write only words a person
would say. No arrows, no symbols, no notation, no markdown. Write "A implies B",
never "A->B". Write "true false case", never "T->F". Spell out anything a
speaker could not pronounce.

Write 8-12 alternating lines (A, B, A, B...). Keep total spoken word count under {max_words} words.
This is a hard cap — going over makes the reel run past its slot.

Output ONLY valid JSON, this exact schema, no markdown fences:
{{
  "lines": [
    {{"speaker": "A", "text": "..."}},
    {{"speaker": "B", "text": "..."}}
  ]
}}
"""


def generate_conversation(module: dict, pyq_matches: list | None = None, max_retries: int = 1):
    """Transcript in, list of {speaker, text} out. Returns None if the LLM
    could not produce a usable script — the caller skips that module rather
    than failing the whole run."""
    pyq_block = ""
    if pyq_matches:
        pyq_lines = "\n".join(f"- {q['text']}" for q in pyq_matches)
        pyq_block = (
            f"PAST EXAM QUESTIONS ON THIS TOPIC:\n{pyq_lines}\n\n"
            "Use these to choose WHICH mistakes CHARACTER_B makes — B should get "
            "wrong exactly the thing these questions test, because that is what "
            "students actually lose marks on. Only use a question if it is "
            "genuinely about the same topic as the transcript; if none of them "
            "are, ignore them entirely rather than forcing a connection. Never "
            "read a question aloud or mention exams — the questions shape B's "
            "errors, they are not part of the dialogue."
        )

    prompt = SCRIPT_PROMPT.format(
        concept=module["concept"],
        transcript=module["transcript"],
        pyq_block=pyq_block,
        max_words=MAX_WORDS,
    )

    for attempt in range(max_retries + 1):
        try:
            raw = _llm_json(prompt, 0.7, "module-script")
            parsed = json.loads(_strip_json_fences(raw))
            lines = parsed["lines"]
            if not isinstance(lines, list) or len(lines) < MIN_LINES:
                raise ValueError("script too short or malformed")
            clean = []
            for ln in lines:
                speaker = str(ln.get("speaker", "")).strip().upper()
                text = _normalize(str(ln.get("text", "")))
                if speaker in ("A", "B") and text:
                    clean.append({"speaker": speaker, "text": text})
            if len(clean) < MIN_LINES:
                raise ValueError("too few usable lines after cleaning")
            return clean
        except Exception as e:
            if attempt < max_retries:
                print(f"[clipper] script gen attempt {attempt + 1} failed ({e}) — retrying")
                continue
            print(f"[clipper] script gen failed for module '{module['concept']}', skipping: {e}")
            return None


def flag_for_review(lines: list, transcript: str) -> bool:
    """Cheap keyword-overlap sanity check — not a hard gate.

    Flags scripts where CHARACTER_A's lines share almost no vocabulary with
    the transcript, which is a sign the LLM invented something instead of
    grounding in the real lecture content. Callers log the flagged module ids
    for a manual spot-check and carry on."""
    a_text = " ".join(l["text"] for l in lines if l["speaker"] == "A").lower()
    transcript_words = set(transcript.lower().split())
    a_words = set(a_text.split())
    overlap = len(a_words & transcript_words) / max(len(a_words), 1)
    return overlap < 0.15


# ─────────────────────────────────────────────
# PYQ-SOLVING MODE
# ─────────────────────────────────────────────

# The pool stores terse topic labels ("hard vs soft links properties"), not
# full question text, so CHARACTER_A has to state the question properly
# before solving it. That reconstruction is part of the job.
PYQ_PROMPT = """
You are writing a 50-60 second two-character comedic script in which the pair
SOLVE A REAL PAST EXAM QUESTION together, on camera.

THE QUESTION (from the {year} {exam_type} paper{marks_phrase}):
{question}

The line above is a terse topic label from a question bank, not the full
wording. CHARACTER_A must first state what the question is actually ASKING in
proper exam terms, then solve it completely. By the final line the listener
must have the full mark-scoring answer.

CHARACTER_A: hyper-intelligent, precise, slightly condescending. Opens by
naming the paper out loud — for example "Let's do a {year} {exam_type} question{marks_phrase}"
— then states the question and works the answer step by step. Everything A
says must be factually correct standard Operating Systems material. Do not
invent properties, numbers, or behaviour.

CHARACTER_B: well-meaning, dim, and CONFIDENTLY WRONG. B answers the exam
question badly — with the specific wrong answers students actually write and
lose marks for on THIS question. The classic mix-up, the property attributed
to the wrong side, the condition everyone forgets. B is funny because the
error is real and stated with total certainty, never because it is random.

Do NOT write B lines that are surreal non-sequiturs. Every B line must be
something a real student could have written in the exam.

ARC — follow this shape:
1. A names the paper and states what the question asks.
2. B fires off the classic wrong answer, confidently.
3. A corrects it and gives the first correct point.
4. B gets it half right but flips or forgets a key condition.
5. A delivers the complete answer — the points that actually score marks.
6. B restates the answer in his own dumb words, and this time is CORRECT.

{grounding_block}

EVERY LINE IS READ ALOUD BY A TEXT-TO-SPEECH VOICE. Write only words a person
would say. No arrows, no symbols, no notation, no markdown. Spell out anything
a speaker could not pronounce.

Write 8-12 alternating lines (A, B, A, B...). Keep total spoken word count under {max_words} words.
This is a hard cap — going over makes the reel run past its slot.

Output ONLY valid JSON, this exact schema, no markdown fences:
{{
  "lines": [
    {{"speaker": "A", "text": "..."}},
    {{"speaker": "B", "text": "..."}}
  ]
}}
"""

EXAM_LABEL = {"midsem": "midsem", "endsem": "endsem"}


def generate_pyq_solution(pyq: dict, module: dict | None = None, max_retries: int = 1):
    """Write a script in which A and B solve one real past-year question.

    `module` is optional — when the reel is being made alongside a lecture
    module, its transcript is passed in so A's answer stays consistent with
    how the course actually taught it.
    """
    marks = pyq.get("marks")
    marks_phrase = f", worth {marks:g} marks" if marks else ""

    grounding_block = ""
    if module and module.get("transcript"):
        grounding_block = (
            "COURSE TRANSCRIPT COVERING THIS TOPIC — prefer this phrasing and "
            "these definitions so the answer matches how the course taught it:\n"
            f"{module['transcript'][:4000]}"
        )

    prompt = PYQ_PROMPT.format(
        year=pyq["year"],
        exam_type=EXAM_LABEL.get(pyq.get("exam_type", ""), "exam"),
        marks_phrase=marks_phrase,
        question=pyq["text"],
        grounding_block=grounding_block,
        max_words=MAX_WORDS,
    )

    for attempt in range(max_retries + 1):
        try:
            raw = _llm_json(prompt, 0.7, "pyq-script")
            parsed = json.loads(_strip_json_fences(raw))
            lines = parsed["lines"]
            if not isinstance(lines, list) or len(lines) < MIN_LINES:
                raise ValueError("script too short or malformed")
            clean = []
            for ln in lines:
                speaker = str(ln.get("speaker", "")).strip().upper()
                text = _normalize(str(ln.get("text", "")))
                if speaker in ("A", "B") and text:
                    clean.append({"speaker": speaker, "text": text})
            if len(clean) < MIN_LINES:
                raise ValueError("too few usable lines after cleaning")
            return clean
        except Exception as e:
            if attempt < max_retries:
                print(f"[clipper] pyq script attempt {attempt + 1} failed ({e}) — retrying")
                continue
            print(f"[clipper] pyq script failed for {pyq['q_id']}, skipping: {e}")
            return None
