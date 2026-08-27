"""
Patches planner.py for multi-language support (Python + C).

Run from meals/:
    python3 patch_planner.py

Each patch is tried independently and reports success/failure, so a
mismatch in one doesn't block the rest. Failed patches print the search
string that didn't match — paste that back for a manual fix.
"""
from pathlib import Path

PLANNER = Path("planner.py")
text = PLANNER.read_text()

patches = []

def add(label, old, new):
    patches.append((label, old, new))


# 1. Swap the static Python taxonomy import for the language-aware resolver.
add(
    "import taxonomy -> languages module",
    'BUILD_DIR = project_env.BUILD_DIR\n'
    'CATALOGUE_DIR = project_env.CATALOGUE_DIR\n'
    '\n'
    'import taxonomy\n'
    '\n'
    'BUILD_DIR = HERE / "build"\n'
    'CATALOGUE_DIR = HERE / "catalogue"',

    'BUILD_DIR = project_env.BUILD_DIR\n'
    'CATALOGUE_DIR = project_env.CATALOGUE_DIR\n'
    '\n'
    'import languages as lang_config\n'
    '\n'
    '# Reassigned in plan_series() once the source language is known.\n'
    'taxonomy = lang_config.taxonomy_for(lang_config.DEFAULT_LANGUAGE)\n'
    '\n'
    'BUILD_DIR = HERE / "build"\n'
    'CATALOGUE_DIR = HERE / "catalogue"',
)

# 2. CLI flag.
add(
    "add --language CLI flag",
    'ap.add_argument("--key", help="stable id for caching and the series manifest; "\n'
    '                              "used when the source is a transcript rather than a job")',

    'ap.add_argument("--key", help="stable id for caching and the series manifest; "\n'
    '                              "used when the source is a transcript rather than a job")\n'
    '    ap.add_argument("--language", default=lang_config.DEFAULT_LANGUAGE,\n'
    '                    choices=list(lang_config.LANGUAGES.keys()),\n'
    '                    help="source language (default: python)")',
)

# 3. Thread the language through main() into the source dict.
add(
    "main(): store args.language on source",
    "    source = (load_from_job(args.job) if args.job\n"
    "              else load_from_transcript(Path(args.transcript), args.title))\n"
    "    if args.key:\n"
    "        source[\"job_id\"] = args.key",

    "    source = (load_from_job(args.job) if args.job\n"
    "              else load_from_transcript(Path(args.transcript), args.title))\n"
    "    if args.key:\n"
    "        source[\"job_id\"] = args.key\n"
    "    source[\"language\"] = args.language",
)

# 4. plan_series(): resolve taxonomy for this run's language before Pass 1.
add(
    "plan_series(): reassign global taxonomy for source language",
    "def plan_series(source: dict, write: bool = True, limit: int | None = None,\n"
    "                fresh: bool = False, resume: bool = True) -> dict:\n"
    "    analysis = comprehend(source, use_cache=not fresh)",

    "def plan_series(source: dict, write: bool = True, limit: int | None = None,\n"
    "                fresh: bool = False, resume: bool = True) -> dict:\n"
    "    global taxonomy\n"
    "    taxonomy = lang_config.taxonomy_for(source.get(\"language\"))\n"
    "    analysis = comprehend(source, use_cache=not fresh)",
)

# 5. COMPREHEND_SYSTEM: drop the Python-only framing.
add(
    "COMPREHEND_SYSTEM: remove Python-first framing",
    'COMPREHEND_SYSTEM = """You are a curriculum analyst for MAROS, a Python-first\n'
    'microlearning platform. You are given the transcript of a real programming\n'
    'lecture or project walkthrough.',

    'COMPREHEND_SYSTEM = """You are a curriculum analyst for MAROS, a microlearning\n'
    'platform. You are given the transcript of a real programming lecture or\n'
    'project walkthrough.',
)

# 6. COMPREHEND_USER template: stop hardcoding "python" in the JSON shape.
add(
    'COMPREHEND_USER: "language": "python" -> template var',
    '{{\n'
    '  "is_programming": true,\n'
    '  "language": "python",\n'
    '  "builds_something": true,',

    '{{\n'
    '  "is_programming": true,\n'
    '  "language": "{language}",\n'
    '  "builds_something": true,',
)

# 7. SYNTHESIS_USER template: same fix, second occurrence.
add(
    'SYNTHESIS_USER: "language": "python" -> template var',
    'Return:\n'
    '\n'
    '{{\n'
    '  "is_programming": true,\n'
    '  "language": "python",\n'
    '  "builds_something": true,\n'
    '  "artifact": "one sentence: what is built by the end, or null",',

    'Return:\n'
    '\n'
    '{{\n'
    '  "is_programming": true,\n'
    '  "language": "{language}",\n'
    '  "builds_something": true,\n'
    '  "artifact": "one sentence: what is built by the end, or null",',
)

# 8. comprehend(): pass language into both format() calls.
add(
    "comprehend(): pass language= into COMPREHEND_USER.format()",
    '            partials.append(_llm_json(\n'
    '                COMPREHEND_SYSTEM,\n'
    '                COMPREHEND_USER.format(\n'
    '                    title=f"{source[\'title\']} (section {i} of {len(windows)})",\n'
    '                    transcript=window,\n'
    '                ),',

    '            partials.append(_llm_json(\n'
    '                COMPREHEND_SYSTEM,\n'
    '                COMPREHEND_USER.format(\n'
    '                    title=f"{source[\'title\']} (section {i} of {len(windows)})",\n'
    '                    transcript=window,\n'
    '                    language=lang_config.get(source.get("language"))["display"],\n'
    '                ),',
)

add(
    "comprehend(): pass language= into SYNTHESIS_USER.format()",
    '            SYNTHESIS_USER.format(\n'
    '                title=source["title"],\n'
    '                sections=json.dumps(digest, indent=1)[:9000],\n'
    '            ),',

    '            SYNTHESIS_USER.format(\n'
    '                title=source["title"],\n'
    '                sections=json.dumps(digest, indent=1)[:9000],\n'
    '                language=lang_config.get(source.get("language"))["display"],\n'
    '            ),',
)

# 9. AUTHOR_SYSTEM: swap the hardcoded Python syntax-narration block for the
#    language's own rules, and stop assuming main.py everywhere.
add(
    "AUTHOR_SYSTEM: parameterize as a function of language config",
    'AUTHOR_SYSTEM = f"""You write a single MAROS Meal: one short lesson, 30-90\n'
    'seconds, teaching exactly ONE objective.',

    'def author_system(lang: dict) -> str:\n'
    '    return f"""You write a single MAROS Meal: one short lesson, 30-90\n'
    'seconds, teaching exactly ONE objective. The source material is {lang["display"]}.',
)

add(
    "AUTHOR_SYSTEM: swap hardcoded NEVER DICTATE SYNTAX block for lang.syntax_rules",
    'NEVER DICTATE SYNTAX. Say what the code MEANS, not what it looks like.\n'
    '\n'
    '  WRONG: "double equals"        RIGHT: "checks whether they match"\n'
    '  WRONG: "f string"             RIGHT: "builds a message with the value inside"\n'
    '  WRONG: "elif"                 RIGHT: "otherwise, if"\n'
    '  WRONG: "dot lower open paren" RIGHT: "converts it to lowercase"',

    '{lang["syntax_rules"]}',
)

add(
    'AUTHOR_SYSTEM: "runs as main.py" -> dynamic filename',
    'CODE MUST RUN, AND IT MUST RUN ON ITS OWN. It is executed as a complete\n'
    '`main.py` in an empty directory, with nothing from any other Meal in scope,\n'
    'and the Meal is REJECTED if it fails.',

    'CODE MUST RUN, AND IT MUST RUN ON ITS OWN. It is executed as a complete\n'
    '`{lang["file_name"]}` in an empty directory, with nothing from any other Meal in scope,\n'
    'and the Meal is REJECTED if it fails.',
)

# NOTE: no closing-line edit needed — patch 9 above already turned the
# assignment into `return f"""...`, and the constant's own trailing `"""`
# already closes that same string literal correctly as-is.

# 10. author_meal(): call author_system(lang) instead of the old constant.
add(
    "author_meal(): resolve language config and call author_system(lang)",
    '    written = _llm_json(\n'
    '        AUTHOR_SYSTEM,\n'
    '        AUTHOR_USER.format(',

    '    lang = lang_config.get(spec.get("language") or getattr(plan, "get", lambda *_: None)("language"))\n'
    '    written = _llm_json(\n'
    '        author_system(lang),\n'
    '        AUTHOR_USER.format(',
)

# 12. _assemble(): dynamic filename/command instead of hardcoded main.py.
add(
    "_assemble(): dynamic filename in code_editor scene",
    'add("code", {"type": "code_editor", "language": "python",\n'
    '             "filename": "main.py", "code": source_code,\n'
    '             "show_line_numbers": True, "actions": actions})',

    'lang = lang_config.get(spec.get("language"))\n'
    '        add("code", {"type": "code_editor", "language": lang["id"],\n'
    '                     "filename": lang["file_name"], "code": source_code,\n'
    '                     "show_line_numbers": True, "actions": actions})',
)

add(
    "_assemble(): dynamic run command in terminal scene",
    'add("execution", {\n'
    '            "type": "terminal",\n'
    '            "command": "python main.py",',

    'run_display = " ".join(lang["run_cmd"](Path(lang["file_name"]), Path("a.out")))\n'
    '        add("execution", {\n'
    '            "type": "terminal",\n'
    '            "command": run_display,',
)

# 13. _run_snippet(): compile-then-run for compiled languages.
add(
    "_run_snippet(): language-aware compile+run instead of hardcoded python",
    'def _run_snippet(code: str, stdin: list[str]) -> tuple[bool, str]:\n'
    '    """Execute a snippet exactly as verify.py will. Returns (ok, error)."""\n'
    '    with tempfile.TemporaryDirectory() as tmp:\n'
    '        script = Path(tmp) / "main.py"\n'
    '        script.write_text(code)\n'
    '        try:\n'
    '            proc = subprocess.run(\n'
    '                [sys.executable, str(script)],\n'
    '                input="".join(line + "\\n" for line in stdin),\n'
    '                capture_output=True, text=True, timeout=10, cwd=tmp,\n'
    '            )\n'
    '        except subprocess.TimeoutExpired:\n'
    '            return False, "timed out after 10s"\n'
    '    if proc.returncode == 0:\n'
    '        return True, ""\n'
    '    return False, (proc.stderr or "non-zero exit").strip()[-600:]',

    'def _run_snippet(code: str, stdin: list[str], language: str = "python") -> tuple[bool, str]:\n'
    '    """Execute a snippet exactly as verify.py will. Returns (ok, error).\n'
    '\n'
    '    Compiled languages get a compile step first; a compile failure is\n'
    '    reported the same way a runtime failure is, so repair_code can fix\n'
    '    either kind of error through the same path."""\n'
    '    lang = lang_config.get(language)\n'
    '    with tempfile.TemporaryDirectory() as tmp:\n'
    '        script = Path(tmp) / lang["file_name"]\n'
    '        script.write_text(code)\n'
    '        binpath = Path(tmp) / "a.out"\n'
    '        if lang["compile_cmd"]:\n'
    '            try:\n'
    '                compile_proc = subprocess.run(\n'
    '                    lang["compile_cmd"](script, binpath),\n'
    '                    capture_output=True, text=True, timeout=15, cwd=tmp,\n'
    '                )\n'
    '            except subprocess.TimeoutExpired:\n'
    '                return False, "compile timed out after 15s"\n'
    '            if compile_proc.returncode != 0:\n'
    '                return False, (compile_proc.stderr or "compile failed").strip()[-600:]\n'
    '        try:\n'
    '            proc = subprocess.run(\n'
    '                lang["run_cmd"](script, binpath),\n'
    '                input="".join(line + "\\n" for line in stdin),\n'
    '                capture_output=True, text=True, timeout=10, cwd=tmp,\n'
    '            )\n'
    '        except subprocess.TimeoutExpired:\n'
    '            return False, "timed out after 10s"\n'
    '    if proc.returncode == 0:\n'
    '        return True, ""\n'
    '    return False, (proc.stderr or "non-zero exit").strip()[-600:]',
)

# 14. repair_code() and its caller: thread language through.
add(
    "repair_code(): accept and pass through language",
    'def repair_code(code: str, stdin: list[str], label: str,\n'
    '                attempts: int = 2) -> tuple[str, list[str], str | None]:',

    'def repair_code(code: str, stdin: list[str], label: str,\n'
    '                attempts: int = 2, language: str = "python") -> tuple[str, list[str], str | None]:',
)

add(
    "repair_code(): use language in first _run_snippet call",
    '    ok, error = _run_snippet(code, stdin)\n'
    '    if ok:\n'
    '        return code, stdin, None',

    '    ok, error = _run_snippet(code, stdin, language)\n'
    '    if ok:\n'
    '        return code, stdin, None',
)

add(
    "repair_code(): use language in retry-loop _run_snippet call",
    '        ok, error = _run_snippet(new_code, new_stdin)\n'
    '        if ok:',

    '        ok, error = _run_snippet(new_code, new_stdin, language)\n'
    '        if ok:',
)

add(
    "_assemble(): pass language into repair_code() call",
    'source_code, stdin_lines, unfixed = repair_code(\n'
    '            source_code, stdin_lines, f"meal-{order}")',

    'source_code, stdin_lines, unfixed = repair_code(\n'
    '            source_code, stdin_lines, f"meal-{order}", language=spec.get("language") or "python")',
)


# ── apply ──────────────────────────────────────────────────────────────
ok_count = 0
for label, old, new in patches:
    if old not in text:
        print(f"[SKIP] {label}\n       anchor not found — needs a manual look")
        continue
    if text.count(old) > 1:
        print(f"[SKIP] {label}\n       anchor appears {text.count(old)} times — not unique, skipped for safety")
        continue
    text = text.replace(old, new, 1)
    ok_count += 1
    print(f"[OK]   {label}")

PLANNER.write_text(text)
print(f"\n{ok_count}/{len(patches)} patches applied. planner.py written.")
print("Run: python3 -c \"import ast; ast.parse(open('planner.py').read())\" to sanity-check it still parses.")
