"""
Per-language configuration for the Meal pipeline.

Everything that used to be hardcoded to Python — the file name, how a
snippet is run, the syntax-narration rules in AUTHOR_SYSTEM, which taxonomy
module supplies concept ids — lives here instead, keyed by language id.

Adding a new language means adding one entry here plus a matching
taxonomy_<lang>.py; nothing else in planner.py, verify.py or the prompts
should need to know a new language exists.

"compile" is None for interpreted languages. For compiled ones it returns
the subprocess command that produces a binary at `out_path` from `src_path`;
run_cmd then returns the command that executes the compiled artifact (for
interpreted languages, run_cmd executes the source file directly).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

PYTHON_SYNTAX_RULES = """NEVER DICTATE SYNTAX. Say what the code MEANS, not what it looks like.

  WRONG: "double equals"        RIGHT: "checks whether they match"
  WRONG: "f string"             RIGHT: "builds a message with the value inside"
  WRONG: "elif"                 RIGHT: "otherwise, if"
  WRONG: "dot lower open paren" RIGHT: "converts it to lowercase\""""

C_SYNTAX_RULES = """NEVER DICTATE SYNTAX. Say what the code MEANS, not what it looks like.

  WRONG: "star p"                    RIGHT: "the value p points to"
  WRONG: "ampersand x"                RIGHT: "the address of x"
  WRONG: "percent d"                  RIGHT: "a placeholder for a whole number"
  WRONG: "semicolon"                  RIGHT: (never spoken — punctuation isn't narrated)
  WRONG: "struct keyword"             RIGHT: "a custom bundle of related values\""""


def _python_run_cmd(src_path: Path, _bin_path: Path) -> list[str]:
    return [sys.executable, str(src_path)]


def _c_compile_cmd(src_path: Path, bin_path: Path) -> list[str]:
    return ["gcc", "-std=c11", "-O0", "-Wall", "-o", str(bin_path), str(src_path)]


def _c_run_cmd(_src_path: Path, bin_path: Path) -> list[str]:
    return [str(bin_path)]


LANGUAGES: dict[str, dict] = {
    "python": {
        "id": "python",
        "display": "Python",
        "file_name": "main.py",
        "compile_cmd": None,  # type: Callable | None
        "run_cmd": _python_run_cmd,
        "taxonomy_module": "taxonomy_python",
        "syntax_rules": PYTHON_SYNTAX_RULES,
    },
    "c": {
        "id": "c",
        "display": "C",
        "file_name": "main.c",
        "compile_cmd": _c_compile_cmd,
        "run_cmd": _c_run_cmd,
        "taxonomy_module": "taxonomy_c",
        "syntax_rules": C_SYNTAX_RULES,
    },
}

DEFAULT_LANGUAGE = "python"


def get(language: str | None) -> dict:
    """The config for a language id, falling back to Python if unset/unknown
    rather than raising — an unrecognised language should degrade to the
    known-good default, not crash the pipeline."""
    return LANGUAGES.get((language or DEFAULT_LANGUAGE).lower(), LANGUAGES[DEFAULT_LANGUAGE])


def taxonomy_for(language: str | None):
    """Import and return the taxonomy module for a language id."""
    import importlib
    config = get(language)
    return importlib.import_module(config["taxonomy_module"])
