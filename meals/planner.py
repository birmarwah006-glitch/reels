"""
Meal planner — a real lecture in, a connected Meal series out.

    30-minute Python project  ->  12-15 Meals  ->  by the last one you can
                                                    build the project yourself

This is the piece the product rests on, and it is deliberately NOT chipper.
Chipper segments on a 20-minute sliding window with MAX_MODULES=8; that is
duration-driven, and the product rule is that the SOURCE DURATION MUST NOT
DETERMINE THE MEAL COUNT — learning objectives do. Chipper still contributes
what it is good at: the transcript and the timestamps that let a Meal cite its
source.

Three passes, deliberately separable so a bad one can be re-run alone.

  PASS 1  COMPREHEND
      Read the whole lecture at once. What is being built? What concepts does
      it actually teach? What ANALOGIES does the teacher use, verbatim? What
      code gets written? What is filler?

      The analogies matter more than they look. A lecturer who says "a
      dictionary is like a phone book" has already done the hard work of
      making the idea land for this audience. Inventing a fresh analogy throws
      that away and risks one that is subtly wrong. So they are extracted and
      carried through to the Meals that need them.

  PASS 2  CURRICULUM
      Turn comprehension into an ORDERED list of learning objectives, one per
      Meal. Constraints that are checked, not just requested: strictly
      increasing dependencies, no duplicate objectives, coverage of the whole
      build, and a count that follows the material rather than a target.

  PASS 3  AUTHOR
      Write each Meal against the canonical schema, in series order, with the
      preceding objectives in context so Meal 7 can build on Meal 6 instead of
      re-teaching it.

Accuracy is enforced by execution, not by trust: every Meal's code is run for
real by verify.py afterwards, and a Meal whose code does not run is not
publishable.

    python3 planner.py --job <chipper_job_id>
    python3 planner.py --transcript path/to/transcript.txt --title "..."
    python3 planner.py --job <id> --pass1-only
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

# This project owns its own .env and does not read MAROS's. env.bootstrap()
# must run BEFORE any MAROS module is imported: podcastengine reads
# GROQ_API_KEY at import time and never loads a .env itself.
import env as project_env

HERE = Path(__file__).parent
MAROS_ROOT = project_env.bootstrap()

from podcastengine import llm_chat  # noqa: E402  Cerebras primary, Groq fallback

BUILD_DIR = project_env.BUILD_DIR
CATALOGUE_DIR = project_env.CATALOGUE_DIR

import taxonomy

BUILD_DIR = HERE / "build"
CATALOGUE_DIR = HERE / "catalogue"

# A Meal is 30-90 spoken seconds. At the ~150wpm edge-tts actually delivers,
# that is roughly 75-225 words. The cap is a guard against a runaway script,
# not a target to hit.
SCRIPT_WORD_CAP = 200

# There is no Meal quota, and the count is NOT derived from the length of the
# source. Duration says nothing about how much a lecture teaches: a rambling
# hour can hold five ideas and a dense ten minutes can hold twelve.
#
# The count comes from the CONCEPTS the lecture actually teaches, which Pass 1
# reports. The governing rule is that the learner must end up understanding the
# material — brevity never wins over that. So this is a sanity bound to catch
# runaway output, not a budget to spend down.
MIN_MEALS = 3

# Room above the concept count for the Meals a project needs that are not
# language features: what we are building, how the pieces fit, common mistakes.
MEAL_HEADROOM = 8

# Only a model that has lost the plot produces more than this.
ABSURD = 120


def max_meals_for(analysis: dict) -> int:
    """The most Meals this lecture could reasonably justify.

    Bounded by what Pass 1 found, not by how long the video is. A course that
    genuinely teaches forty things is allowed forty Meals; clamping it would
    silently drop what the learner needs.
    """
    concepts = len(analysis.get("concepts_taught") or [])
    code_bits = len(analysis.get("code_written") or [])
    substantive = max(concepts, code_bits)
    return min(max(substantive + MEAL_HEADROOM, MIN_MEALS + MEAL_HEADROOM), ABSURD)

# The curriculum reply must fit under the per-minute ceiling alongside its own
# prompt. Roughly 200 tokens per Meal entry, so this comfortably covers a
# 20-Meal series while leaving room for the digest and the catalogue.
CURRICULUM_MAX_TOKENS = 4600

# An authored Meal is ~700 tokens of JSON. Reserving 4000 meant each call
# claimed half the per-minute ceiling and the account rate-limited anyway,
# which cost seven Meals in a single run.
AUTHOR_MAX_TOKENS = 2200


# ─────────────────────────────────────────────────────────────────────────
# LLM plumbing
#
# Two things shape this layer, both learned the hard way against the live API.
#
# 1. explainengine._safe_llm never raises — it logs and returns "". That is
#    right for a chat route, where a dead LLM should degrade to "no answer",
#    but wrong here: a rate-limit rejection came back indistinguishable from a
#    model that produced nothing, and three retries were burned on a request
#    that could never succeed. podcastengine.llm_chat is called directly
#    instead so the real error survives.
#
# 2. Groq's free tier allows 8000 tokens PER MINUTE. A 45-minute lecture is
#    ~13k tokens, so a single whole-lecture call is not merely slow, it is
#    impossible. Everything below is budgeted around that ceiling.
# ─────────────────────────────────────────────────────────────────────────

# Measured against the live endpoint: Groq reported 13,271 tokens for a
# transcript of 8,863 words, i.e. ~1.5 tokens per word for speech transcripts.
TOKENS_PER_WORD = 1.8

# The account ceiling. Override if the key is upgraded.
TPM_LIMIT = int(os.getenv("MAROS_LLM_TPM", "8000"))

# Leave headroom: the reported figure includes the system prompt and the
# reservation for max_tokens, and running right at the edge just trades a
# rejection for a retry.
TPM_SAFETY = 0.75


def estimate_tokens(text: str) -> int:
    return int(len(text.split()) * TOKENS_PER_WORD)


class _RateBudget:
    """Paces calls against the account's ACTUAL remaining budget.

    The first version estimated cost as `input + max_tokens` and slept when
    its own running total looked too high. That over-reserved badly: a real
    authoring call costs ~1,100 tokens, but reserving max_tokens=4000 against
    it made the estimate 4.6x the truth, which throttled the run to ONE call a
    minute when the account could comfortably do six. A 16-Meal series took
    sixteen minutes for no reason.

    Groq reports the real state on every response:

        x-ratelimit-remaining-tokens   what is actually left
        x-ratelimit-reset-tokens       when it refills

    So the server is the source of truth and the estimate is only used before
    the first call, when nothing has been observed yet.
    """

    def __init__(self, limit: int = TPM_LIMIT):
        self.limit = limit
        self.remaining: int | None = None
        self.reset_seconds: float = 0.0
        self._observed_at = 0.0

    @staticmethod
    def _parse_duration(value: str | None) -> float:
        """Groq formats these as '8.7s', '1m26.4s', '615ms'."""
        if not value:
            return 0.0
        total, number = 0.0, ""
        for ch in value:
            if ch.isdigit() or ch == ".":
                number += ch
                continue
            if not number:
                continue
            amount = float(number)
            total += {"m": amount * 60, "s": amount}.get(ch, 0.0)
            number = ""
        if value.endswith("ms"):
            return total / 1000 if total else 0.0
        return total

    def observe(self, headers) -> None:
        """Record what the server just told us."""
        try:
            remaining = headers.get("x-ratelimit-remaining-tokens")
            if remaining is not None:
                self.remaining = int(remaining)
                self._observed_at = time.time()
            self.reset_seconds = self._parse_duration(
                headers.get("x-ratelimit-reset-tokens"))
        except Exception:
            pass

    def wait_for(self, needed: int) -> None:
        """Sleep only when the server says there is genuinely not enough left."""
        if self.remaining is None:
            return  # nothing observed yet; the first call reveals the state

        # The bucket refills continuously, so anything observed a while ago is
        # stale in our favour — assume it has recovered.
        age = time.time() - self._observed_at
        if age > 60:
            self.remaining = None
            return

        if self.remaining >= needed:
            return

        wait = max(self.reset_seconds, 60 - age) + 1.0
        print(f"      rate budget: {self.remaining} tokens left, need ~{needed} "
              f"— waiting {wait:.0f}s", flush=True)
        time.sleep(wait)
        self.remaining = None


BUDGET = _RateBudget()

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


class _KeyRing:
    """Groq keys from separate accounts, rotated on DAILY quota exhaustion.

    The free tier allows 200,000 tokens per day per ACCOUNT, and a full-length
    course costs most of that — so a single key supports roughly one course a
    day, which is not enough to iterate on. Keys from different accounts have
    independent allowances, so listing several multiplies the ceiling.

    Rotation happens only on the daily limit. The per-minute limit is shared
    behaviour worth waiting out, and switching keys to dodge it would just
    exhaust every account's daily budget faster.
    """

    def __init__(self) -> None:
        primary = os.getenv("GROQ_API_KEY", "").strip()
        extra = [
            k.strip() for k in os.getenv("GROQ_API_KEYS", "").split(",") if k.strip()
        ]
        # Preserve order, drop duplicates.
        self.keys: list[str] = []
        for key in [primary, *extra]:
            if key and key not in self.keys:
                self.keys.append(key)
        self.index = 0
        self.exhausted: set[int] = set()

    @property
    def current(self) -> str:
        return self.keys[self.index] if self.keys else ""

    def label(self) -> str:
        return f"key {self.index + 1}/{len(self.keys)}"

    def retire_current(self) -> bool:
        """Mark this account as out for the day. True if another is available."""
        self.exhausted.add(self.index)
        for i in range(len(self.keys)):
            if i not in self.exhausted:
                self.index = i
                print(f"      daily quota gone on the previous account — "
                      f"switching to {self.label()}", flush=True)
                # The new account has its own per-minute budget too.
                BUDGET.remaining = None
                return True
        return False


KEYRING = _KeyRing()


class QuotaExhausted(Exception):
    """The account's DAILY allowance is gone.

    Distinct from RateLimited, which is worth waiting out. This one is hours
    away, so every remaining call in the run will fail the same way. It must
    abort the run rather than be retried or swallowed — an earlier version let
    it fall into the generic handler, which turned it into an empty string and
    reported it as "empty reply". Nine Meals were recorded as authoring
    failures when the real cause was that the account had simply run out.
    """


class RateLimited(Exception):
    """Throttled, not answered.

    This distinction cost twelve of fourteen windows of a two-hour course. A
    429 used to be returned as an empty string, which the retry loop could not
    tell apart from a model that genuinely produced nothing — so it burned all
    three attempts on a request that was never going to be answered until the
    bucket refilled, then skipped the window and moved on. The course lost most
    of its content and nothing reported an error.
    """

    def __init__(self, wait: float):
        super().__init__(f"rate limited, retry in {wait:.0f}s")
        self.wait = wait


def _groq_chat(messages: list[dict], temperature: float,
               max_tokens: int) -> tuple[str, object]:
    """One Groq call, returning the content AND the response headers.

    podcastengine.llm_chat is still the fallback, but it discards the
    response object, and the rate-limit headers on it are the only reliable
    way to pace a long run. Anything other than a Groq key falls through to
    the shared router.
    """
    import requests

    key = KEYRING.current
    if not key:
        return llm_chat(messages, temperature=temperature,
                        max_tokens=max_tokens) or "", {}

    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    # gpt-oss is a reasoning model: without this it spends the whole budget on
    # hidden reasoning and returns empty content.
    if "gpt-oss" in model:
        payload["reasoning_effort"] = "low"

    response = requests.post(
        GROQ_URL,
        headers={"Authorization": f"Bearer {key}",
                 "Content-Type": "application/json"},
        json=payload,
        timeout=180,
    )
    BUDGET.observe(response.headers)

    # A key that is rejected outright — revoked, mistyped, out of credit — is
    # no more usable than one that is out of quota. Retire it and let another
    # account finish the work rather than failing the whole run on one bad key.
    if response.status_code in (401, 403):
        print(f"      {KEYRING.label()} rejected ({response.status_code})", flush=True)
        if KEYRING.retire_current():
            raise RateLimited(0.0)
        raise QuotaExhausted(
            f"every configured Groq key was rejected (last: {response.status_code})")

    if response.status_code == 429:
        detail = ""
        try:
            detail = response.json().get("error", {}).get("message", "")
        except Exception:
            pass

        # Two very different 429s wear the same status code. The per-minute one
        # is worth waiting out. The per-DAY one is not: it is hours away, and
        # the per-minute headers still read as healthy while it is in force,
        # so a waiting loop looks like normal throttling and never ends.
        if "per day" in detail.lower() or "(TPD)" in detail:
            # Try another account before giving up on the run.
            if KEYRING.retire_current():
                raise RateLimited(0.0)
            raise QuotaExhausted(
                "Groq daily token quota is exhausted, not the per-minute one.\n"
                f"        {detail.strip()}\n"
                "        Nothing will succeed until it resets. A full-length "
                "course costs most of the free tier's daily allowance, so this "
                "is the real ceiling on how much can be processed per day."
            )

        wait = BUDGET._parse_duration(
            response.headers.get("retry-after")
            or response.headers.get("x-ratelimit-reset-tokens")) or 20
        raise RateLimited(wait)

    response.raise_for_status()
    data = response.json()
    choice = data["choices"][0]
    content = choice["message"].get("content") or ""

    # gpt-oss is a reasoning model: it spends tokens thinking before it writes.
    # If max_tokens is too small for prompt + reasoning + answer, it returns
    # finish_reason "length" with EMPTY content — which reads as "the model had
    # nothing to say" when it actually means "the budget was too small".
    if not content and choice.get("finish_reason") == "length":
        used = data.get("usage", {}).get("completion_tokens", "?")
        raise RuntimeError(
            f"the model exhausted max_tokens on reasoning and returned nothing "
            f"(finish_reason=length, completion_tokens={used}). Raise max_tokens "
            f"or shorten the prompt."
        )

    return content, response.headers


def _extract_json(raw: str):
    """Pull the first JSON object or array out of a model reply."""
    if not raw:
        raise ValueError("empty model reply")
    cleaned = re.sub(r"```(?:json)?", "", raw).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = cleaned.find(opener), cleaned.rfind(closer)
        if start != -1 and end > start:
            candidate = cleaned[start:end + 1]
            candidate = re.sub(r",\s*([\]}])", r"\1", candidate)  # trailing commas
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no parsable JSON in reply: {cleaned[:200]!r}")


def _llm_raw(system: str, user: str, label: str, max_tokens: int,
             temperature: float) -> str:
    """One call, paced against the server's reported budget, errors preserved."""
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": user}]

    # Groq counts the FULL max_tokens reservation against the per-minute
    # budget, not the tokens actually produced. Under-reserving here is what
    # generated the 429s in the first place: two comprehension calls whose
    # real cost was ~5,200 each were let through against an 8,000 ceiling.
    needed = estimate_tokens(system + user) + max_tokens
    BUDGET.wait_for(needed)

    try:
        content, _headers = _groq_chat(messages, temperature, max_tokens)
        return content
    except QuotaExhausted:
        raise
    except RateLimited as limited:
        # Not a failed attempt — the request was never answered. Wait for the
        # bucket and let the caller try again without spending its budget.
        print(f"      {label}: rate limited, waiting {limited.wait:.0f}s", flush=True)
        time.sleep(limited.wait + 1)
        BUDGET.remaining = None
        raise
    except Exception as e:
        message = str(e)
        # A request too large will never succeed by retrying; say so rather
        # than letting the caller spend its attempts discovering it.
        if "Request too large" in message or "reduce your message size" in message \
                or "413" in message:
            raise RuntimeError(
                f"{label}: this request exceeds the account's per-minute token "
                f"limit ({TPM_LIMIT} TPM). Shrink the window, not the retries. "
                f"Original: {message[:200]}"
            ) from e
        print(f"      {label}: LLM call failed — {message[:200]}", flush=True)
        return ""


def _llm_json(system: str, user: str, label: str, max_tokens: int = 3000,
              temperature: float = 0.3, attempts: int = 3):
    """Ask for JSON and keep asking until it parses.

    gpt-oss-120b is a reasoning model and returns empty content often enough
    that one attempt is not a plan. Each retry lowers the temperature, because
    a model that just emitted unparsable output is usually being too creative.
    """
    last_error = None
    attempt = 0
    throttled = 0
    while attempt < attempts:
        try:
            raw = _llm_raw(system, user, f"{label} (attempt {attempt + 1})",
                           max_tokens, max(temperature - attempt * 0.1, 0.0))
        except QuotaExhausted:
            raise
        except RateLimited:
            # Does not count: nothing was asked and nothing was answered.
            throttled += 1
            if throttled > 6:
                raise RuntimeError(
                    f"{label}: still rate limited after {throttled} waits — "
                    f"the account's per-minute budget is too small for this "
                    f"request size")
            continue

        attempt += 1
        if not raw.strip():
            last_error = "empty reply"
            continue
        try:
            return _extract_json(raw)
        except ValueError as e:
            last_error = str(e)
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last_error}")


def window_transcript(text: str, words_per_window: int) -> list[str]:
    """Split a transcript into windows that fit the per-minute token ceiling.

    Prefers paragraph boundaries so a window never starts mid-sentence. But a
    boundary-only split is not sufficient: a chipper manifest with a single
    module is ONE paragraph, so a 6,800-word lecture came through as one
    15,686-token window and was rejected with a 413. Any block that is itself
    over budget is therefore hard-split on sentence ends, and failing that on
    words, so no window can ever exceed the cap.
    """
    def split_block(block: str) -> list[str]:
        words = block.split()
        if len(words) <= words_per_window:
            return [block]

        # Sentence ends first, so the seams land somewhere readable.
        pieces, chunk, count = [], [], 0
        for sentence in re.split(r"(?<=[.!?])\s+", block):
            n = len(sentence.split())
            if chunk and count + n > words_per_window:
                pieces.append(" ".join(chunk))
                chunk, count = [], 0
            # A single sentence longer than the window (unpunctuated
            # auto-captions do this) still has to be cut somewhere.
            if n > words_per_window:
                if chunk:
                    pieces.append(" ".join(chunk))
                    chunk, count = [], 0
                sentence_words = sentence.split()
                for i in range(0, len(sentence_words), words_per_window):
                    pieces.append(" ".join(sentence_words[i:i + words_per_window]))
                continue
            chunk.append(sentence)
            count += n
        if chunk:
            pieces.append(" ".join(chunk))
        return pieces

    windows, current, count = [], [], 0
    for block in (b for b in text.split("\n\n") if b.strip()):
        for piece in split_block(block):
            n = len(piece.split())
            if current and count + n > words_per_window:
                windows.append("\n\n".join(current))
                current, count = [], 0
            current.append(piece)
            count += n
    if current:
        windows.append("\n\n".join(current))

    return windows


# ─────────────────────────────────────────────────────────────────────────
# Source loading
# ─────────────────────────────────────────────────────────────────────────

def load_from_job(job_id: str) -> dict:
    """Read a chipper manifest. Its module boundaries are NOT used as Meal
    boundaries — only the transcript and the timestamps, which let a Meal cite
    where in the source it came from."""
    manifest_path = MAROS_ROOT / "outputs" / job_id / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"No manifest for job {job_id}")
    data = json.loads(manifest_path.read_text())

    segments = [
        {
            "start": m["start"],
            "end": m["end"],
            "chipper_concept": m.get("concept", ""),
            "text": m.get("transcript", ""),
        }
        for m in data["modules"]
    ]

    title = data["modules"][0]["concept"] if data.get("modules") else "Lecture"
    yt_path = MAROS_ROOT / "outputs" / job_id / "youtube.json"
    if yt_path.exists():
        try:
            title = json.loads(yt_path.read_text()).get("title") or title
        except Exception:
            pass

    return {"job_id": job_id, "title": title, "segments": segments}


def load_from_transcript(path: Path, title: str) -> dict:
    text = Path(path).read_text()
    # The caption fetcher writes "<title>\n\n<transcript>"; drop the title line
    # so it is not analysed as if it were speech.
    if text.startswith(title + "\n\n"):
        text = text.split("\n\n", 1)[1]
    return {
        "job_id": None,
        "title": title,
        "segments": [{"start": "00:00", "end": "", "chipper_concept": "", "text": text}],
    }


def full_text(source: dict) -> str:
    """The whole lecture, with timestamps kept inline so the model can cite
    where something was taught."""
    return "\n\n".join(
        f"[{s['start']}-{s['end']}] {s['text']}".strip()
        for s in source["segments"]
        if s["text"].strip()
    )


# ─────────────────────────────────────────────────────────────────────────
# PASS 1 — COMPREHEND
# ─────────────────────────────────────────────────────────────────────────

COMPREHEND_SYSTEM = """You are a curriculum analyst for MAROS, a Python-first
microlearning platform. You are given the transcript of a real programming
lecture or project walkthrough.

Your job is to UNDERSTAND it, not to summarise it. Someone will use your
analysis to rebuild this lecture as a series of short lessons, and they will
not have the transcript.

Return ONLY valid JSON. No markdown, no prose, no commentary.

Pay particular attention to ANALOGIES, and read that word BROADLY. An analogy
is any moment where the teacher explains a new thing by comparing it to
something the learner already knows. Two kinds matter, and the second is the
one most often missed:

  EVERYDAY   "a variable is like a labelled box", "a function is a recipe"
  TECHNICAL  "you access it like a dictionary", "this is basically a list",
             "it is very similar to XML", "think of it as a for loop"

In a programming lecture the TECHNICAL comparisons are usually the more
valuable of the two, because they connect an unfamiliar API to something the
learner has already learned. Capture phrases of the form "like a X", "similar
to X", "basically a X", "the same idea as X", "think of it as X".

Capture them VERBATIM. The teacher has already found a comparison that works
for this audience; that is hard-won and easy to throw away. Do not paraphrase,
and do not invent new ones. If the lecture genuinely uses none, return an
empty list — a fabricated analogy is worse than none.

Be honest about what is NOT taught. If the lecture waves at a topic without
explaining it, that is not a concept it teaches."""

COMPREHEND_USER = """LECTURE TITLE: {title}

TRANSCRIPT (timestamps in square brackets):
{transcript}

Return this exact JSON shape:

{{
  "is_programming": true,
  "language": "python",
  "builds_something": true,
  "artifact": "one sentence: what is built by the end, or null if nothing is built",
  "summary": "3-4 sentences: what this lecture actually teaches, in order",
  "arc": ["the sequence of things the lecture does, 5-12 short phrases"],
  "concepts_taught": [
    {{
      "name": "short name as the lecture treats it",
      "what_is_taught": "one sentence: the specific claim or skill",
      "evidence": "a short VERBATIM quote from the transcript",
      "timestamp": "MM:SS where this happens",
      "depth": "mentioned | explained | worked_through"
    }}
  ],
  "analogies": [
    {{
      "analogy": "VERBATIM quote of the comparison the teacher used",
      "explains": "which concept it is explaining",
      "timestamp": "MM:SS"
    }}
  ],
  "code_written": [
    {{
      "purpose": "what this code does",
      "code": "the code as best you can reconstruct it from the transcript",
      "timestamp": "MM:SS"
    }}
  ],
  "decisions": [
    {{"decision": "a choice the teacher made", "why": "the reason given"}}
  ],
  "filler": ["things a short lesson should drop: admin, tangents, repetition"]
}}

If this is NOT a programming lecture, set "is_programming" to false, fill
"summary", and leave the other lists empty."""


# Sized so one window plus its prompt plus the reply fits inside the
# per-minute ceiling. At ~1.5 tokens/word this is ~2600 tokens of transcript,
# ~700 of prompt and up to 2600 of reply — under 6000, with headroom.
WINDOW_WORDS = int(os.getenv("MAROS_WINDOW_WORDS", "1750"))


SYNTHESIS_SYSTEM = """You are consolidating per-section analyses of one
programming lecture into a single coherent picture.

The sections were analysed independently, so they overlap and repeat. Merge
them: one entry per distinct idea, in the order the lecture teaches it.

Return ONLY valid JSON. No markdown, no prose.

Do NOT invent anything that is not present in the sections. If the sections
disagree, prefer the one with the more specific evidence."""

SYNTHESIS_USER = """LECTURE TITLE: {title}

SECTION ANALYSES:
{sections}

Return:

{{
  "is_programming": true,
  "language": "python",
  "builds_something": true,
  "artifact": "one sentence: what is built by the end, or null",
  "summary": "3-4 sentences: what this lecture actually teaches, in order",
  "arc": ["the sequence of what the lecture does, 5-12 short phrases"],
  "concepts_taught": [
    {{"name": "...", "what_is_taught": "...", "evidence": "...",
      "timestamp": "MM:SS", "depth": "mentioned|explained|worked_through"}}
  ],
  "analogies": [{{"analogy": "VERBATIM", "explains": "...", "timestamp": "MM:SS"}}],
  "code_written": [{{"purpose": "...", "code": "...", "timestamp": "MM:SS"}}],
  "decisions": [{{"decision": "...", "why": "..."}}],
  "filler": ["..."]
}}"""


def analysis_cache_path(source: dict) -> Path:
    return BUILD_DIR / f"analysis_{_slug(source['title'])}.json"


def comprehend(source: dict, use_cache: bool = True) -> dict:
    """Pass 1, windowed.

    A whole-lecture call is not possible on this account: Groq's free tier
    allows 8000 tokens per minute and a 45-minute lecture is ~13k. So the
    lecture is read in windows and the partial analyses are then consolidated.

    This is the same shape as chipper's windowed segmentation, for the same
    underlying reason, and it has a side benefit: a model reading 1,750 words
    notices detail that one reading 9,000 words skims past — which matters
    most for the analogies, the thing most easily lost in a summary.
    """
    # Pass 1 is by far the most expensive stage — six rate-limited calls plus
    # a synthesis — and it depends only on the transcript. Caching it means
    # iterating on Passes 2 and 3 costs seconds instead of eight minutes.
    cache = analysis_cache_path(source)
    if use_cache and cache.exists():
        cached = json.loads(cache.read_text())
        print(f"[planner] Pass 1 — reusing cached analysis ({cache.name}); "
              f"delete it or pass --fresh to re-read the lecture")
        print(f"  concepts: {len(cached.get('concepts_taught', []))}, "
              f"analogies: {len(cached.get('analogies', []))}, "
              f"code: {len(cached.get('code_written', []))}")
        return cached

    transcript = full_text(source)
    windows = window_transcript(transcript, WINDOW_WORDS)
    total_words = len(transcript.split())

    print(f"[planner] Pass 1 — comprehending {total_words} words "
          f"in {len(windows)} window(s)...")

    partials: list[dict] = []
    lost: list[int] = []
    for i, window in enumerate(windows, 1):
        print(f"    window {i}/{len(windows)} ({len(window.split())} words)")
        try:
            partials.append(_llm_json(
                COMPREHEND_SYSTEM,
                COMPREHEND_USER.format(
                    title=f"{source['title']} (section {i} of {len(windows)})",
                    transcript=window,
                ),
                f"comprehend-{i}",
                max_tokens=2600,
            ))
        except RuntimeError as e:
            # One unreadable window should not lose the rest of the lecture —
            # but losing most of them silently is far worse than failing.
            lost.append(i)
            print(f"      window {i} LOST: {e}", flush=True)

    if not partials:
        raise RuntimeError("no window could be analysed — see errors above")

    # A course analysed from two of its fourteen sections is not an analysis of
    # that course, and every stage downstream would treat it as one. Better to
    # stop and say so than to publish a series built on a tenth of the
    # material.
    lost_share = len(lost) / max(len(windows), 1)
    if lost_share > 0.25:
        raise RuntimeError(
            f"{len(lost)} of {len(windows)} sections could not be analysed "
            f"({lost_share:.0%}). The result would misrepresent the lecture. "
            f"Lost sections: {lost}. Re-run to resume — completed sections are "
            f"cached.")
    if lost:
        print(f"  WARNING: {len(lost)} of {len(windows)} sections lost "
              f"({lost_share:.0%}) — the series may have gaps", flush=True)

    # Cheap merge first, so the synthesis call sees compact input rather than
    # the raw partials.
    def gather(key: str) -> list:
        out, seen = [], set()
        for part in partials:
            for item in part.get(key) or []:
                fingerprint = json.dumps(item, sort_keys=True)[:180]
                if fingerprint not in seen:
                    seen.add(fingerprint)
                    out.append(item)
        return out

    merged = {
        "concepts_taught": gather("concepts_taught"),
        "analogies": gather("analogies"),
        "code_written": gather("code_written"),
        "decisions": gather("decisions"),
        "filler": gather("filler"),
        "section_summaries": [p.get("summary", "") for p in partials],
        "section_arcs": [a for p in partials for a in (p.get("arc") or [])],
    }

    print(f"    merging {len(merged['concepts_taught'])} concepts, "
          f"{len(merged['analogies'])} analogies, "
          f"{len(merged['code_written'])} code snippets")

    # The synthesis call is for the NARRATIVE fields only — artifact, summary,
    # arc. The lists are merged deterministically above and are authoritative.
    #
    # Sending the whole merge and truncating it was silently destructive: on a
    # two-hour course eleven window analyses are far larger than any prompt
    # budget, so most of what the course taught would be cut off mid-JSON and
    # simply never reach the curriculum. Only a compact digest is sent, and the
    # full lists are restored afterwards.
    digest = {
        "section_summaries": merged["section_summaries"],
        "concept_names": [
            c.get("name") for c in merged["concepts_taught"] if c.get("name")
        ],
        "code_purposes": [
            c.get("purpose") for c in merged["code_written"] if c.get("purpose")
        ][:40],
        "decisions": merged["decisions"][:15],
    }

    try:
        result = _llm_json(
            SYNTHESIS_SYSTEM,
            SYNTHESIS_USER.format(
                title=source["title"],
                sections=json.dumps(digest, indent=1)[:9000],
            ),
            "synthesis",
            max_tokens=2000,
        )
        # The digest cannot carry evidence or timestamps, so the merged lists
        # win outright rather than being replaced by a summarised version.
        for key in ("concepts_taught", "analogies", "code_written", "decisions"):
            if len(merged[key]) >= len(result.get(key) or []):
                result[key] = merged[key]
    except RuntimeError as e:
        # If consolidation fails the raw merge is still usable — it is missing
        # only the narrative fields, which Pass 2 can live without.
        print(f"    synthesis failed ({e}); continuing with the raw merge")
        result = {
            "is_programming": True,
            "language": "python",
            "builds_something": bool(merged["code_written"]),
            "artifact": None,
            "summary": " ".join(merged["section_summaries"])[:800],
            "arc": merged["section_arcs"][:12],
            **merged,
        }

    # Consolidation is asked to merge duplicates, but it has a habit of
    # dropping whole categories instead — an early run turned 18 extracted
    # code snippets into zero. Anything that is VERBATIM EVIDENCE rather than
    # prose is restored from the windows if synthesis returned fewer, because
    # the windows read the actual transcript and the synthesis call did not.
    for key in ("analogies", "code_written", "decisions"):
        kept = len(result.get(key) or [])
        found = len(merged[key])
        if kept < found:
            print(f"    restoring {found} {key} (synthesis kept {kept})")
            result[key] = merged[key]

    print(f"  artifact: {result.get('artifact')}")
    print(f"  concepts taught: {len(result.get('concepts_taught', []))}")
    print(f"  analogies found: {len(result.get('analogies', []))}")
    print(f"  code snippets: {len(result.get('code_written', []))}")

    cache.write_text(json.dumps(result, indent=2) + "\n")
    return result


# ─────────────────────────────────────────────────────────────────────────
# PASS 2 — CURRICULUM
# ─────────────────────────────────────────────────────────────────────────

CURRICULUM_SYSTEM = """You are designing a MAROS Meal series from an analysed
lecture.

A Meal is one short lesson — 30 to 90 seconds — built around exactly ONE
learning objective. A series of Meals is watched in order, and by the last one
the learner should be able to build the thing the lecture builds.

Return ONLY valid JSON. No markdown, no prose.

THE AUDIENCE is sixteen years old and has never written a line of code. They
do not know what a module is, what a list is, or what a variable does. They
have never seen a terminal. Assume nothing.

That audience decides the ORDER. A lecture is usually taught to people who
already know the basics, and it jumps straight to the interesting part. Your
series may not. Rank the objectives so that NOTHING IS USED BEFORE IT HAS BEEN
EXPLAINED, even when the lecture itself used it early.

If the lecture opens by importing a module, the module import cannot be Meal 1
unless a beginner can follow it — either put a Meal before it that explains
what a module is, or make that Meal explain it as part of its own objective.
Prefer explaining inside the Meal to adding a Meal; the series should stay
tight.

Set "assumes" on every Meal: the terms a viewer must already understand to
follow it. Anything in "assumes" must have been taught by an EARLIER Meal in
this series, or be genuinely everyday knowledge. That list is checked.

THE RULES THAT MATTER:

1. ONE MEAL = ONE LEARNING OBJECTIVE. If an objective needs the word "and" to
   state it, it is two Meals.
2. THE COUNT FOLLOWS THE MATERIAL. There is no target and no quota. Do not pad
   to reach a number and do not compress to be brief. A lecture with twelve
   real ideas gets twelve Meals; one with forty gets forty.

   UNDERSTANDING WINS OVER BREVITY. The point is not to make the course
   shorter — it is to make it learnable. If dropping a Meal would leave the
   viewer unable to follow the next one, or unable to build the thing at the
   end, that Meal stays. Never omit a step because the series is getting long.
3. STRICT ORDER. Meal N may only rely on what Meals 1..N-1 have taught, plus
   general knowledge. Never forward-reference.
4. SUFFICIENCY. Taken together the series must actually get someone to the
   finished artifact. Do not skip the unglamorous steps that the build needs.

4b. COVER WHAT THE LECTURE TEACHES. Every concept in the analysis marked
   "explained" or "worked_through" must appear in some Meal's "covers", or be
   listed in "out_of_scope" with an honest reason. This is checked.

   A concept marked "worked_through" MAY NOT be put in out_of_scope at all —
   the teacher built a working example of it, so the learner needs it. Only
   "explained" concepts may be excused, and only for an honest reason.
   Dropping them silently is how a series about classes ends up never
   mentioning encapsulation. If a concept is small, fold it into a related Meal and list
   it in that Meal's "covers" — do not simply leave it out.
5. NO FILLER. Course admin, greetings, tangents and repetition do not become
   Meals.
6. REUSE THE TEACHER'S ANALOGIES. If the analysis captured an analogy for a
   concept, attach it to the Meal that teaches that concept. Do not invent new
   analogies.
7. BE HONEST ABOUT CODE. Only attach code you can actually justify from the
   lecture. A Meal with no code is fine."""

CURRICULUM_USER = """LECTURE TITLE: {title}

ANALYSIS:
{analysis}

AVAILABLE CONCEPT IDS — use these exact strings, or null if none fits:
{catalogue}

Design the Meal series. Return:

{{
  "series_title": "short title for the whole series",
  "artifact": "what the learner can build after watching all of it",
  "out_of_scope": [
    {{"concept": "name from the analysis", "why": "why it needs no Meal"}}
  ],
  "meals": [
    {{
      "order": 1,
      "title": "short, concrete, max 60 chars",
      "objective": "ONE sentence: what the learner can do after this Meal",
      "concept_id": "an id from the list above, or null",
      "builds_on": [list of earlier "order" numbers, empty for the first],
      "covers": ["EXACT concept names from the analysis this Meal teaches"],
      "assumes": ["terms the viewer must already know, e.g. variable, list"],
      "introduces": ["terms this Meal explains for the first time"],
      "difficulty": "beginner | intermediate | advanced",
      "analogy": "the teacher's VERBATIM analogy if one applies, else null",
      "evidence": "a SHORT verbatim quote, max 15 words",
      "visual_hint": "one of: input_output, loop, memory, flow, decision, terminal, none"
    }}
  ]
}}"""


def digest_analysis(analysis: dict) -> str:
    """A compact view of Pass 1 for Pass 2.

    The full analysis is far too large to send: with the catalogue, the system
    prompt and the reply reservation it blows straight past the per-minute
    ceiling. Pass 2 does not need the prose fields — it needs to know what was
    taught, in what order, with which analogy and which code attached. So it
    gets exactly that, one line each.
    """
    lines: list[str] = []
    if analysis.get("artifact"):
        lines.append(f"ARTIFACT: {analysis['artifact']}")
    if analysis.get("summary"):
        lines.append(f"SUMMARY: {analysis['summary'][:320]}")

    arc = analysis.get("arc") or []
    if arc:
        lines.append("ARC: " + " -> ".join(str(a)[:44] for a in arc[:10]))

    lines.append("\nCONCEPTS TAUGHT:")
    # Depth and name are what Pass 2 orders by; the gloss only has to
    # disambiguate. Every character here competes with the reply's token
    # budget inside the same 8,000-token minute.
    for c in (analysis.get("concepts_taught") or [])[:32]:
        lines.append(
            f"- ({str(c.get('depth', '?'))[:4]}) {c.get('name')}: "
            f"{str(c.get('what_is_taught', ''))[:70]}"
        )

    analogies = analysis.get("analogies") or []
    if analogies:
        lines.append("\nTEACHER'S ANALOGIES — reuse these verbatim, do not invent:")
        for a in analogies[:8]:
            lines.append(
                f"- {a.get('explains','?')}: \"{str(a.get('analogy',''))[:110]}\""
            )

    # Only the PURPOSE of each snippet, never the snippet itself: authoring
    # writes its own code and then executes it, so the source here would be
    # replaced anyway — and it was the single largest block in the prompt.
    code = analysis.get("code_written") or []
    if code:
        lines.append("\nCODE DEMONSTRATED: " + "; ".join(
            str(c.get("purpose", ""))[:52] for c in code[:12]))

    # Design rationale belongs to individual Meals, not to the ordering.
    decisions = analysis.get("decisions") or []
    if decisions:
        lines.append("DECISIONS: " + "; ".join(
            str(d.get("decision", ""))[:52] for d in decisions[:6]))

    return "\n".join(lines)


def curriculum_cache_path(source: dict) -> Path:
    return BUILD_DIR / f"curriculum_{_slug(source['title'])}.json"


def build_curriculum(source: dict, analysis: dict, use_cache: bool = True) -> dict:
    # The curriculum is cached for the same reason the analysis is, plus one
    # more that matters: resume matches Meals on an id derived from their
    # order and title, so a re-planned series with different titles would fail
    # to match and re-author everything. Caching the plan makes a resumed run
    # actually resume, and makes the series reproducible.
    cache = curriculum_cache_path(source)
    if use_cache and cache.exists():
        cached = json.loads(cache.read_text())
        print(f"[planner] Pass 2 — reusing cached plan ({cache.name}), "
              f"{len(cached.get('meals', []))} Meals")
        return cached

    print("[planner] Pass 2 — designing the Meal series...")

    result = _llm_json(
        CURRICULUM_SYSTEM,
        CURRICULUM_USER.format(
            title=source["title"],
            analysis=digest_analysis(analysis),
            catalogue=taxonomy.compact_catalogue(),
        ),
        "curriculum",
        max_tokens=CURRICULUM_MAX_TOKENS,
    )

    meals = result.get("meals", [])
    problems = _check_curriculum(meals, analysis, result)

    # One targeted repair attempt. Re-running the whole pass usually produces a
    # different plan rather than a fixed one, so the faults are named instead.
    if problems:
        print(f"  {len(problems)} problem(s) in the plan — asking for a repair")
        for p in problems[:6]:
            print(f"    {p}")
        result = _llm_json(
            CURRICULUM_SYSTEM,
            CURRICULUM_USER.format(
                title=source["title"],
                analysis=digest_analysis(analysis),
                catalogue=taxonomy.compact_catalogue(),
            ) + "\n\nA previous attempt had these faults. Fix them:\n- "
              + "\n- ".join(problems[:12]),
            "curriculum-repair",
            max_tokens=CURRICULUM_MAX_TOKENS,
        )
        meals = result.get("meals", [])
        problems = _check_curriculum(meals, analysis, result)

    # Whatever survives, normalise the concept ids honestly: an id that does
    # not resolve becomes null rather than being forced onto a near-miss.
    for meal in meals:
        resolved = taxonomy.nearest_valid(meal.get("concept_id") or "")
        if meal.get("concept_id") and not resolved:
            print(f"  note: concept id {meal['concept_id']!r} does not map to the "
                  f"Python taxonomy — left unset on Meal {meal.get('order')}")
        meal["concept_id"] = resolved

    result["meals"] = meals
    result["plan_warnings"] = problems

    cache.write_text(json.dumps(result, indent=2) + "\n")

    print(f"  series: {len(meals)} Meals")
    for m in meals:
        tag = m.get("concept_id") or "-"
        mark = " [analogy]" if m.get("analogy") else ""
        print(f"    {m.get('order'):>2}. {m.get('title')}  ({tag}){mark}")
    return result


def _check_curriculum(meals: list, analysis: dict | None = None,
                      plan: dict | None = None) -> list[str]:
    """Checks a prompt cannot enforce. Returns human-readable faults."""
    problems: list[str] = []
    analysis = analysis or {}
    ceiling = max_meals_for(analysis)

    # ── Coverage ───────────────────────────────────────────────────────
    # Matching is on the plan's own declarations, not on keywords in titles:
    # a keyword match called "debit method" covered because the word appeared
    # somewhere, while encapsulation — which the lecture worked through — was
    # dropped entirely and nothing noticed.
    def norm(v: str) -> str:
        return "".join(ch for ch in str(v).lower() if ch.isalnum())

    claimed = {norm(c) for m in meals for c in (m.get("covers") or [])}
    excused = {norm(x.get("concept", "")) for x in ((plan or {}).get("out_of_scope") or [])}

    uncovered, wrongly_excused = [], []
    for concept in (analysis.get("concepts_taught") or []):
        name = (concept.get("name") or "").strip()
        depth = concept.get("depth") or ""
        if not name or depth == "mentioned":
            continue  # a passing mention does not owe a Meal
        key = norm(name)

        # A concept the teacher WORKED THROUGH cannot be waved away. Given the
        # chance, the model excused encapsulation as "demonstrated via class
        # attributes" and the bank-account methods as "not required" — both
        # were worked examples the teacher spent real time on. Allowing an
        # excuse there turns the coverage check into a formality and, on one
        # run, shrank the series from twelve Meals to ten.
        if depth == "worked_through" and key in excused and key not in claimed:
            wrongly_excused.append(name)
            continue

        if key not in claimed and key not in excused:
            uncovered.append(name)

    if wrongly_excused:
        problems.append(
            "the lecture WORKED THROUGH these, so they cannot be out of scope — "
            "each needs a Meal, or must be folded into one and listed in its "
            '"covers": ' + "; ".join(wrongly_excused[:12]))

    if uncovered:
        # One grouped line, not one per concept: forty-five separate messages
        # pushed the repair prompt past the per-minute token limit, so the
        # repair could never be sent at all.
        problems.append(
            "these concepts are taught in the lecture but no Meal lists them in "
            '"covers": ' + "; ".join(uncovered[:30]) +
            ". Give each one a Meal, fold it into a related Meal's \"covers\", "
            'or list it in "out_of_scope" with a reason.')

    if not meals:
        return ["the plan contains no Meals"]
    if len(meals) < MIN_MEALS:
        problems.append(f"only {len(meals)} Meals — a real lecture teaches more than that")
    if len(meals) > ceiling:
        problems.append(
            f"{len(meals)} Meals exceeds the {ceiling} this transcript supports; "
            f"merge the ones that share an objective")

    orders = [m.get("order") for m in meals]
    if orders != sorted(orders) or len(set(orders)) != len(orders):
        problems.append("`order` must be unique and ascending, starting at 1")

    # Everyday words a sixteen-year-old already has. Anything else must be
    # taught by an earlier Meal before it can be assumed.
    COMMON = {
        "number", "numbers", "text", "word", "words", "letter", "letters",
        "message", "computer", "keyboard", "screen", "game", "player",
        "rules", "list of things", "name", "value", "choice",
    }

    introduced: set[str] = set()
    seen_objectives = set()
    for m in meals:
        order = m.get("order")

        # Rule: nothing is used before it is explained.
        for term in (m.get("assumes") or []):
            key = str(term).strip().lower()
            if key and key not in COMMON and key not in introduced:
                problems.append(
                    f"Meal {order} assumes the viewer knows {term!r}, but no "
                    f"earlier Meal introduces it — teach it first, explain it "
                    f"inside this Meal, or move this Meal later")
        introduced.update(
            str(t).strip().lower() for t in (m.get("introduces") or []))

        objective = (m.get("objective") or "").strip().lower()
        if not objective:
            problems.append(f"Meal {order} has no objective")
        elif objective in seen_objectives:
            problems.append(f"Meal {order} repeats an earlier objective")
        seen_objectives.add(objective)

        # Rule 1, mechanically: an objective joining two verbs with "and" is
        # usually two Meals wearing one hat.
        if re.search(r"\band\b", objective) and len(objective.split()) > 12:
            problems.append(
                f"Meal {order} states a compound objective — split it or narrow it")

        # Rule 3: no forward references.
        for dep in m.get("builds_on") or []:
            if not isinstance(dep, int) or dep >= (order or 0):
                problems.append(
                    f"Meal {order} depends on Meal {dep}, which does not come before it")

        if len(m.get("title") or "") > 60:
            problems.append(f"Meal {order} has a title longer than 60 characters")

    return problems


# ─────────────────────────────────────────────────────────────────────────
# PASS 3 — AUTHOR
# ─────────────────────────────────────────────────────────────────────────

AUTHOR_SYSTEM = f"""You write a single MAROS Meal: one short lesson, 30-90
seconds, teaching exactly ONE objective.

Return ONLY valid JSON matching the shape given. No markdown, no prose.

THE SCRIPT IS READ ALOUD by a text-to-speech voice.

NEVER PUT CODE IN THE SCRIPT. Not a line, not a fragment, not "here is the
code" followed by the code. The code appears ON SCREEN; the narration EXPLAINS
what it does. A script containing source is read out character by character
and is unlistenable.

  WRONG: "if user_choice == computer_choice: result = 'tie'"
  RIGHT: "If both players picked the same move, it is a tie."

NEVER DICTATE SYNTAX. Say what the code MEANS, not what it looks like.

  WRONG: "double equals"        RIGHT: "checks whether they match"
  WRONG: "f string"             RIGHT: "builds a message with the value inside"
  WRONG: "elif"                 RIGHT: "otherwise, if"
  WRONG: "dot lower open paren" RIGHT: "converts it to lowercase"

NO EMOJI. Not one, anywhere in the script or in any on-screen text. The voice
reads them aloud as their names — a script containing a celebration emoji is
narrated as "party popper".

No markdown, no arrows, no bare symbols. Write "input" not "input()". Spell
out anything a voice cannot pronounce. Under {SCRIPT_WORD_CAP} words total —
this is a hard cap.

VOICE: a smart person explaining something to another student. Direct,
slightly energetic, never lecturing. Bad: "In today's lesson we will discuss
the concept of loops." Good: "You have ten thousand names. How do you make
Python do the same thing to every one?"

THE HOOK is the first one to three seconds and it decides whether anyone
watches. Make it a real question, a surprising fact, or a problem the learner
recognises. It MUST be technically accurate — never clickbait.

IF AN ANALOGY IS SUPPLIED, USE IT. It is the teacher's own words and it
already works for this audience. Do not replace it with your own. If none is
supplied, only add one if it genuinely helps, and never a strained one.

CODE MUST RUN, AND IT MUST RUN ON ITS OWN. It is executed as a complete
`main.py` in an empty directory, with nothing from any other Meal in scope,
and the Meal is REJECTED if it fails.

This is the one place where continuity does NOT apply. Teaching builds on
earlier Meals; code does not. If an earlier Meal created `soup`, this Meal's
snippet still has to create it again — the imports, the HTML, the parse, all
of it — or it dies with a NameError.

Concretely, every snippet must:
  - import everything it uses
  - define every name it references
  - never reach the network, and never need a package outside the standard
    library — the runner has neither
  - if it reads a file, SHIP THAT FILE in `code.files`. A Meal about reading a
    file should really read one; the file travels with the Meal
  - stay short: the few lines that carry the idea, plus the minimum setup

If teaching the idea honestly needs a page of setup, use a small inline string
instead of a real fetch. A three-line HTML literal is a better teacher than a
network call that cannot run.

If the code needs typed input, supply it in `stdin`. Do NOT write the expected
output — it is captured from a real run.

EXPLAIN EVERY TERM YOU INTRODUCE, in plain words, the first time it appears.
The viewer is sixteen and has never programmed. Naming a thing is not
teaching it.

  WRONG: "First we import math and random."
  RIGHT: "Python ships with bundles of ready-made tools called modules.
          Importing one is how you bring its tools into your program.
          Random is the bundle that does anything unpredictable."

  WRONG: "We store it in a list."
  RIGHT: "A list holds several values in order under one name, so you can
          reach for any of them later."

One short clause is usually enough. Do not turn the Meal into a glossary — the
explanation serves THIS Meal's objective, and anything a previous Meal already
taught is assumed, not repeated.

BE CRISP AND BE ACCURATE. Never invent behaviour Python does not have. If you
are unsure a detail is true, leave it out. A shorter correct Meal beats a
longer one with a wrong claim. Do not repeat yourself and do not pad.

CONTINUITY applies to EXPLANATION, not to code. Earlier Meals are listed
below: do not re-explain what they covered, and never forward-reference a
later one. But every code snippet is still standalone and runnable, as above."""

AUTHOR_USER = """SERIES: {series_title}
FINAL ARTIFACT: {artifact}

ALREADY TAUGHT (do not re-teach):
{prior}

THIS MEAL — number {order} of {total}
  title:      {title}
  objective:  {objective}
  assumes:    {assumes}
  introduces: {introduces}   <- explain each of these in plain words
  concept:    {concept_id}
  analogy:    {analogy}
  evidence:   {evidence}
  visual:     {visual_hint}

Return:

{{
  "title": "max 70 chars",
  "objective": "one sentence, the same objective as above, tightened",
  "script": "the FULL narration, in order, spoken language only",
  "hook_text": "the on-screen hook, max 90 chars",
  "question_text": "the problem this Meal answers, max 90 chars, or null",
  "concept_flow": {{
    "nodes": [{{"id": "a", "label": "max 30 chars", "kind": "box|actor|value|decision|output"}}],
    "edges": [{{"from": "a", "to": "b", "label": "optional, max 24 chars"}}]
  }},
  "code": {{
    "source": "runnable Python, or null if this Meal needs no code",
    "stdin": ["lines fed to the program, [] if none"],
    "files": [{{"name": "simple.html", "content": "any file the code opens"}}],
    "highlight": "the exact substring to emphasise, or null"
  }},
  "takeaway_text": "the one thing to remember, max 110 chars",
  "practice_prompt": "a task the learner does next, max 140 chars",
  "practice_hint": "one sentence, or null",
  "anchors": {{
    "hook": "VERBATIM phrase from script where the hook lands",
    "question": "VERBATIM phrase, or null",
    "concept": "VERBATIM phrase",
    "code": "VERBATIM phrase where code starts appearing",
    "execution": "VERBATIM phrase where output is discussed, or null",
    "visual": "VERBATIM phrase where the diagram is discussed, or null",
    "takeaway": "VERBATIM phrase",
    "practice": "VERBATIM phrase"
  }}
}}

Every anchor MUST appear in `script` EXACTLY ONCE, character for character.
The anchors are how the visuals are synchronised to the voice; an anchor that
does not appear breaks the Meal."""


REPAIR_SYSTEM = """You are fixing one Python snippet for a MAROS Meal.

The snippet was executed and it failed. Return ONLY valid JSON, no markdown.

Rules:
- Fix the actual cause shown in the error. Do not rewrite the lesson.
- If the code calls input(), it MUST come with enough stdin lines to run to
  completion. One line per input() call, in order.
- The snippet must run standalone: it cannot rely on variables defined in an
  earlier Meal.
- Keep it to the few lines that carry the idea."""

REPAIR_USER = """This snippet failed:

```python
{code}
```

stdin supplied: {stdin}

Error:
{error}

Return:

{{
  "source": "the corrected Python",
  "stdin": ["lines to feed it, [] if it needs none"]
}}"""


def _run_snippet(code: str, stdin: list[str]) -> tuple[bool, str]:
    """Execute a snippet exactly as verify.py will. Returns (ok, error)."""
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "main.py"
        script.write_text(code)
        try:
            proc = subprocess.run(
                [sys.executable, str(script)],
                input="".join(line + "\n" for line in stdin),
                capture_output=True, text=True, timeout=10, cwd=tmp,
            )
        except subprocess.TimeoutExpired:
            return False, "timed out after 10s"
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr or "non-zero exit").strip()[-600:]


def repair_code(code: str, stdin: list[str], label: str,
                attempts: int = 2) -> tuple[str, list[str], str | None]:
    """Run the snippet; if it fails, ask for a fix and run it again.

    Without this the verifier simply rejects the Meal and the series ends up
    with holes — and the holes land on exactly the steps that matter, because
    the interesting lessons are the ones with real code. Two failure modes
    dominate: code that calls input() with no stdin declared, and ordinary
    syntax errors. Both are trivially fixable when the model is shown the
    actual error.
    """
    ok, error = _run_snippet(code, stdin)
    if ok:
        return code, stdin, None

    for attempt in range(attempts):
        print(f"      {label}: code failed ({error.splitlines()[-1][:70]}) "
              f"— repairing, attempt {attempt + 1}", flush=True)
        try:
            fixed = _llm_json(
                REPAIR_SYSTEM,
                REPAIR_USER.format(code=code, stdin=stdin or "[]", error=error),
                f"{label}-repair", max_tokens=1200, temperature=0.2, attempts=2,
            )
        except RuntimeError as e:
            return code, stdin, f"repair call failed: {e}"

        new_code = (fixed.get("source") or "").strip()
        new_stdin = [str(x) for x in (fixed.get("stdin") or [])]
        if not new_code:
            continue
        if not new_code.endswith("\n"):
            new_code += "\n"

        ok, error = _run_snippet(new_code, new_stdin)
        if ok:
            print(f"      {label}: repaired", flush=True)
            return new_code, new_stdin, None
        code, stdin = new_code, new_stdin

    return code, stdin, error


# Anything in these ranges is read aloud by name, so none of it can survive
# into a script. Ranges rather than a list, because a list is always missing
# the one that actually appeared.
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002190-\U000021FF\U00002300-\U000027BF"
    "\U00002B00-\U00002BFF\U0000FE00-\U0000FE0F\U0001F1E6-\U0001F1FF]"
)

# Lines that are source code rather than speech. The model is told not to put
# code in the script and mostly complies, but "mostly" produces a Meal that
# reads a whole program aloud, so this is checked rather than trusted.
_CODE_LINE = re.compile(
    r"""^\s*(?:
          (?:import|from|def|class|return|elif|else\s*:|if\s.*:|for\s.*:|while\s.*:|print\()
        | [A-Za-z_][A-Za-z0-9_]*\s*=[^=]
        | [A-Za-z_][A-Za-z0-9_]*\s*\(.*\)\s*$
    )""",
    re.X,
)

# Operators a voice should never pronounce literally.
_SPOKEN = [
    (re.compile(r"\s*==\s*"), " is equal to "),
    (re.compile(r"\s*!=\s*"), " is not equal to "),
    (re.compile(r"\s*>=\s*"), " is at least "),
    (re.compile(r"\s*<=\s*"), " is at most "),
    (re.compile(r"\s*->\s*"), " gives "),
    (re.compile(r"\s*\+=\s*"), " increases by "),
]


def sanitise_script(script: str, label: str) -> tuple[str, list[str]]:
    """Strip anything the voice must not read aloud.

    Three things get through the prompt often enough to need enforcing:
    emoji (narrated by name), pasted source code (narrated character by
    character), and bare operators. Fixing them here is deterministic and
    costs nothing, which beats another round trip to the model.
    """
    notes: list[str] = []

    if _EMOJI.search(script):
        count = len(_EMOJI.findall(script))
        script = _EMOJI.sub("", script)
        notes.append(f"removed {count} emoji")

    # "Here is the code: <code>" puts source INLINE after the introduction, so
    # a line-start match alone leaves the first statement behind. Cut from the
    # marker to the end of that line first.
    marker = re.compile(
        r"(?:Here(?:'s| is)|This is|Take a look at|Look at)\s+the\s+"
        r"(?:code|snippet|script)\s*[:.]?[^\n]*", re.I)
    if marker.search(script):
        script = marker.sub("", script)
        notes.append("removed an inline code introduction")

    # Then drop whole lines that are source rather than speech.
    kept, dropped = [], 0
    for line in script.split("\n"):
        if _CODE_LINE.match(line) and len(line.strip()) > 3:
            dropped += 1
            continue
        kept.append(line)
    if dropped:
        script = "\n".join(kept)
        notes.append(f"removed {dropped} line(s) of pasted code")

    # Finally any code statement still sitting inside a sentence.
    inline = re.compile(
        r"(?:^|(?<=[.;:!?]))\s*(?:import\s+\w+|from\s+\w+\s+import\s+\w+"
        r"|\w+\s*=\s*[^=\s][^.\n]*)(?=\s|$)")
    before = script
    script = inline.sub(" ", script)
    if script != before:
        notes.append("removed inline code")

    for pattern, spoken in _SPOKEN:
        if pattern.search(script):
            script = pattern.sub(spoken, script)
            notes.append(f"spoke out {spoken.strip()!r}")

    script = re.sub(r"[ \t]+", " ", script)
    script = re.sub(r"\n{2,}", " ", script).strip()

    if notes:
        print(f"      {label}: script cleaned — {'; '.join(notes)}", flush=True)
    return script, notes


def _slug(value: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return re.sub(r"_+", "_", out)[:48] or "meal"


def _fix_anchors(script: str, anchors: dict) -> tuple[dict, list[str]]:
    """Anchors must appear verbatim exactly once. Repair what can be repaired
    and drop what cannot, rather than emitting a Meal that fails validation."""
    fixed, notes = {}, []
    for beat, phrase in (anchors or {}).items():
        if not phrase or not isinstance(phrase, str):
            continue
        phrase = phrase.strip()
        count = script.count(phrase)
        if count == 1:
            fixed[beat] = phrase
            continue
        if count > 1:
            notes.append(f"anchor for {beat} is not unique — dropped")
            continue
        # Not found verbatim. Try a whitespace-tolerant match against the
        # script, which is where most misses come from.
        loose = re.escape(phrase).replace(r"\ ", r"\s+")
        m = re.search(loose, script)
        if m and script.count(m.group(0)) == 1:
            fixed[beat] = m.group(0)
            notes.append(f"anchor for {beat} recovered by whitespace match")
        else:
            notes.append(f"anchor for {beat} not found in script — dropped")
    return fixed, notes


def author_meal(plan: dict, spec: dict, prior: list[dict], total: int) -> dict:
    order = spec.get("order")
    print(f"  [{order}/{total}] {spec.get('title')}")

    # Only the recent run of Meals, plus a count of what came before. Carrying
    # every prior Meal made the prompt grow with the series: by Meal 12 it was
    # several times the size it was at Meal 2, which is why failures clustered
    # at the end. Continuity only needs what was just taught.
    RECENT = 6
    earlier = len(prior) - RECENT
    lines = [
        f"  {p['order']}. {p['title']} — {p['objective']}"
        for p in prior[-RECENT:]
    ]
    if earlier > 0:
        lines.insert(0, f"  (plus {earlier} earlier Meal(s) already taught)")
    prior_text = "\n".join(lines) or "  (nothing yet — this is the first Meal)"

    written = _llm_json(
        AUTHOR_SYSTEM,
        AUTHOR_USER.format(
            series_title=plan.get("series_title", ""),
            artifact=plan.get("artifact", ""),
            prior=prior_text,
            order=order, total=total,
            title=spec.get("title"),
            objective=spec.get("objective"),
            assumes=", ".join(spec.get("assumes") or []) or "(nothing — assume zero knowledge)",
            introduces=", ".join(spec.get("introduces") or []) or "(nothing new)",
            concept_id=spec.get("concept_id") or "(unmapped)",
            analogy=spec.get("analogy") or "(none supplied — do not invent one)",
            evidence=spec.get("evidence", ""),
            visual_hint=spec.get("visual_hint", "none"),
        ),
        f"author-{order}",
        max_tokens=AUTHOR_MAX_TOKENS,
        temperature=0.5,
    )

    script = (written.get("script") or "").strip()
    if not script:
        raise RuntimeError(f"Meal {order}: empty script")

    # Clean BEFORE anchoring: anchors are matched against the final script,
    # so cleaning afterwards would invalidate them.
    script, _cleanup = sanitise_script(script, f"meal-{order}")

    words = len(script.split())
    if words > SCRIPT_WORD_CAP * 1.25:
        print(f"      script is {words} words, over the {SCRIPT_WORD_CAP} cap")

    anchors, anchor_notes = _fix_anchors(script, written.get("anchors", {}))
    for note in anchor_notes:
        print(f"      {note}")

    return _assemble(plan, spec, written, script, anchors, order)


def _assemble(plan: dict, spec: dict, written: dict, script: str,
              anchors: dict, order: int) -> dict:
    """Turn the authored content into a schema-valid Meal document.

    Scene ASSEMBLY is deterministic and lives here, not in the model. The
    model decides what to teach and say; the shape of a Meal is not its
    decision to make.
    """
    meal_id = f"meal_{order:02d}_{_slug(spec.get('title') or '')}"
    scenes: list[dict] = []

    def add(beat: str, visual: dict, min_duration: float | None = None):
        scene: dict = {"beat": beat, "visual": visual}
        if beat in anchors:
            scene["narration_anchor"] = anchors[beat]
        if min_duration:
            scene["min_duration"] = min_duration
        scenes.append(scene)

    def clean(value) -> str:
        return _EMOJI.sub("", str(value or "")).strip()

    hook = clean(written.get("hook_text") or spec.get("title"))
    add("hook", {"type": "text", "tone": "hook", "text": hook[:160]}, 2.0)

    if written.get("question_text"):
        add("question", {"type": "text", "tone": "question",
                         "text": clean(written["question_text"])[:160]})

    flow = written.get("concept_flow") or {}
    nodes = [
        {"id": str(n.get("id") or f"n{i}"),
         "label": str(n.get("label") or "")[:40],
         "kind": n.get("kind") if n.get("kind") in
                 {"box", "actor", "value", "decision", "output"} else "box"}
        for i, n in enumerate(flow.get("nodes") or [])
        if n.get("label")
    ]
    if nodes:
        valid_ids = {n["id"] for n in nodes}
        edges = [
            {k: v for k, v in
             (("from", str(e.get("from"))), ("to", str(e.get("to"))),
              ("label", str(e.get("label"))[:32] if e.get("label") else None))
             if v is not None}
            for e in (flow.get("edges") or [])
            if str(e.get("from")) in valid_ids and str(e.get("to")) in valid_ids
        ]
        add("concept", {"type": "flow", "layout": "vertical",
                        "nodes": nodes, "edges": edges})

    code_block = written.get("code") or {}
    source_code = (code_block.get("source") or "").strip()
    stdin_lines = [str(x) for x in (code_block.get("stdin") or [])]
    if source_code:
        if not source_code.endswith("\n"):
            source_code += "\n"
        # Run it here, while the model that wrote it is still in reach. A
        # failure caught now is repairable; one caught by verify.py at the end
        # of the run is just a missing Meal.
        source_code, stdin_lines, unfixed = repair_code(
            source_code, stdin_lines, f"meal-{order}")
        if unfixed:
            print(f"      meal-{order}: code still failing — "
                  f"this Meal will be rejected by validation", flush=True)
        lines = source_code.rstrip("\n").split("\n")
        actions: list[dict] = [{
            "action": "type",
            "text": source_code.rstrip("\n"),
            "lines": list(range(1, len(lines) + 1)),
            "speed_cps": 26,
        }]
        highlight = code_block.get("highlight")
        if highlight and highlight in source_code:
            actions.append({"action": "highlight", "text": highlight})

        add("code", {"type": "code_editor", "language": "python",
                     "filename": "main.py", "code": source_code,
                     "show_line_numbers": True, "actions": actions})

        # Execution is left UNVERIFIED on purpose. verify.py runs the code for
        # real and writes back what it actually printed; validate.py refuses
        # the Meal until then. The model never supplies output.
        add("execution", {
            "type": "terminal",
            "command": "python main.py",
            "stdin": stdin_lines,
            **({"files": [
                {"name": str(f.get("name")), "content": str(f.get("content", ""))}
                for f in code_block["files"]
                if isinstance(f, dict) and f.get("name")
            ]} if code_block.get("files") else {}),
            "execution": {"verified": False, "source": "unverified",
                          "stdout": "", "stderr": "", "exit_code": -1},
        }, 3.0)

    if written.get("takeaway_text"):
        add("takeaway", {"type": "text", "tone": "takeaway",
                         "text": clean(written["takeaway_text"])[:160]}, 2.5)

    practice_prompt = clean(written.get("practice_prompt"))
    if practice_prompt:
        add("practice", {"type": "practice", "prompt": practice_prompt[:200]}, 2.5)

    concept_id = spec.get("concept_id")
    document = {
        "schema_version": "1.0",
        "id": meal_id,
        "title": str(written.get("title") or spec.get("title") or "")[:80],
        "concept": concept_id or "python.project.overview",
        "objective": str(written.get("objective") or spec.get("objective") or "")[:200],
        "difficulty": spec.get("difficulty") if spec.get("difficulty") in
                      {"beginner", "intermediate", "advanced"} else "beginner",
        "prerequisites": taxonomy.prerequisites(concept_id) if concept_id else [],
        "next_concepts": [],
        "source": {"kind": "lecture"},
        "teaches": [str(t) for t in (spec.get("introduces") or [])],
        "assumes": [str(t) for t in (spec.get("assumes") or [])],
        "voice": {"script": script, "voice_id": "en-US-BrianNeural", "rate": "+6%"},
        "captions": {"enabled": True, "words_per_line": 5,
                     "highlight_active_word": True},
        "scenes": scenes,
        "render": {"width": 1080, "height": 1920, "fps": 30},
    }

    if practice_prompt:
        document["practice"] = {
            "kind": "write_code",
            "prompt": practice_prompt,
            **({"hint": str(written["practice_hint"])}
               if written.get("practice_hint") else {}),
        }

    return document


# ─────────────────────────────────────────────────────────────────────────
# ORCHESTRATION
# ─────────────────────────────────────────────────────────────────────────

def link_series(documents: list[dict], plan: dict) -> None:
    """Wire the Meals to each other.

    A Meal series is not a playlist of unrelated clips: `next_concepts` is what
    lets the feed say what comes next, and it is filled from the SERIES order
    rather than from the taxonomy, because the lecture's own ordering is the
    better teacher here.
    """
    for i, doc in enumerate(documents):
        following = documents[i + 1:i + 3]
        doc["next_concepts"] = [d["concept"] for d in following if d.get("concept")]
        doc["series"] = {
            "title": plan.get("series_title"),
            "artifact": plan.get("artifact"),
            "order": i + 1,
            "total": len(documents),
            "previous_id": documents[i - 1]["id"] if i > 0 else None,
            "next_id": documents[i + 1]["id"] if i + 1 < len(documents) else None,
        }


def _existing_document(spec: dict) -> dict | None:
    """A previously authored Meal for this plan entry, if one is on disk.

    Matched on the deterministic id, which is derived from order and title, so
    a re-planned series with different titles correctly does not match."""
    meal_id = f"meal_{spec.get('order'):02d}_{_slug(spec.get('title') or '')}"
    path = CATALOGUE_DIR / f"{meal_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())["meal"]
    except Exception:
        return None


def plan_series(source: dict, write: bool = True, limit: int | None = None,
                fresh: bool = False, resume: bool = True) -> dict:
    analysis = comprehend(source, use_cache=not fresh)

    if not analysis.get("is_programming", True):
        raise SystemExit(
            "[planner] This does not look like a programming lecture.\n"
            f"  {analysis.get('summary', '')[:300]}\n"
            "  MAROS is Python-first; Meals are not generated for other subjects yet."
        )

    plan = build_curriculum(source, analysis, use_cache=not fresh)
    specs = plan.get("meals", [])
    if limit:
        specs = specs[:limit]

    print(f"[planner] Pass 3 — authoring {len(specs)} Meals...")
    documents, prior, failures = [], [], []
    for spec in specs:
        # Resume. Authoring is rate-limited and a long series can lose Meals
        # to the per-minute ceiling; re-running should fill the gaps, not
        # re-pay for the Meals that already succeeded.
        existing = _existing_document(spec)
        if resume and existing is not None:
            print(f"  [{spec.get('order')}/{len(specs)}] {spec.get('title')} "
                  f"— already written, skipping")
            documents.append(existing)
            prior.append({"order": spec.get("order"), "title": spec.get("title"),
                          "objective": spec.get("objective")})
            continue
        try:
            document = author_meal(plan, spec, prior, len(specs))
            # Written immediately, not at the end of the run. Authoring is
            # rate-limited and a long series routinely loses calls to the
            # per-minute ceiling; batching the writes meant a run that died on
            # Meal 15 threw away the fourteen that had succeeded.
            if write:
                (CATALOGUE_DIR / f"{document['id']}.json").write_text(
                    json.dumps({"meal": document}, indent=2) + "\n")
            documents.append(document)
            prior.append({
                "order": spec.get("order"),
                "title": spec.get("title"),
                "objective": spec.get("objective"),
            })
        except QuotaExhausted as e:
            print(f"\n[planner] STOPPING: {e}", flush=True)
            print(f"[planner] {len(documents)} Meal(s) written before the quota "
                  f"ran out. Re-running resumes from here.", flush=True)
            break
        except Exception as e:
            # One bad Meal must not lose the other fourteen.
            print(f"      FAILED: {e}")
            failures.append({"order": spec.get("order"),
                             "title": spec.get("title"), "error": str(e)})

    link_series(documents, plan)

    if write:
        # Rewrite everything once more: link_series has now filled in each
        # Meal's position and neighbours, which is not known while authoring.
        for doc in documents:
            path = CATALOGUE_DIR / f"{doc['id']}.json"
            path.write_text(json.dumps({"meal": doc}, indent=2) + "\n")
        print(f"[planner] wrote {len(documents)} Meals to {CATALOGUE_DIR}")

    result = {
        "source_title": source["title"],
        "job_id": source.get("job_id"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "analysis": analysis,
        "plan": plan,
        "meal_ids": [d["id"] for d in documents],
        "failures": failures,
    }
    if write:
        sidecar = BUILD_DIR / f"series_{_slug(source['title'])}.json"
        sidecar.write_text(json.dumps(result, indent=2) + "\n")
        print(f"[planner] plan sidecar -> {sidecar.name}")

        # Also key the series by job id. Callers that drive the planner (the
        # pipeline, the UI) know the job, not the title slug, and detecting
        # the output by diffing the catalogue directory breaks on a resumed
        # run where every Meal already exists.
        if source.get("job_id"):
            pointer = BUILD_DIR / f"series_job_{source['job_id']}.json"
            pointer.write_text(json.dumps(result, indent=2) + "\n")

    print()
    print(f"[planner] {len(documents)} Meals authored"
          + (f", {len(failures)} failed" if failures else ""))
    print("[planner] NEXT — nothing is publishable until the code actually runs:")
    print("    python3 verify.py catalogue/meal_*.json")
    print("    python3 validate.py catalogue/meal_*.json")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Turn a lecture into a Meal series.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--job", help="chipper job id from outputs/")
    src.add_argument("--transcript", help="path to a plain-text transcript")
    ap.add_argument("--title", default="Lecture", help="title, with --transcript")
    ap.add_argument("--key", help="stable id for caching and the series manifest; "
                                  "used when the source is a transcript rather than a job")
    ap.add_argument("--pass1-only", action="store_true",
                    help="comprehend and print the analysis, author nothing")
    ap.add_argument("--plan-only", action="store_true",
                    help="comprehend and design the series, author nothing")
    ap.add_argument("--limit", type=int, help="author only the first N Meals")
    ap.add_argument("--no-resume", action="store_true",
                    help="re-author Meals that already exist on disk")
    ap.add_argument("--fresh", action="store_true",
                    help="re-read the lecture instead of reusing the cached analysis")
    args = ap.parse_args()

    source = (load_from_job(args.job) if args.job
              else load_from_transcript(Path(args.transcript), args.title))
    if args.key:
        source["job_id"] = args.key

    print(f"[planner] source: {source['title']}")
    print(f"[planner] {len(source['segments'])} segment(s), "
          f"{len(full_text(source).split())} words")

    if args.pass1_only:
        print(json.dumps(comprehend(source, use_cache=not args.fresh), indent=2))
        return 0

    if args.plan_only:
        analysis = comprehend(source, use_cache=not args.fresh)
        print(json.dumps(build_curriculum(source, analysis,
                                        use_cache=not args.fresh), indent=2))
        return 0

    plan_series(source, limit=args.limit, fresh=args.fresh,
                resume=not args.no_resume)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
