"""
C concept taxonomy.

Mirrors taxonomy_python.py's shape exactly so the planner/curriculum code can
treat every language's taxonomy the same way. See that file's docstring for
the design rules — they apply here unchanged: not a full knowledge graph,
ids are stable and additive-only, grows when real content needs a slot that
doesn't exist yet.
"""

from __future__ import annotations

CONCEPTS: dict[str, tuple[str, str, list[str]]] = {
    # ── Basics ──────────────────────────────────────────────────────────
    "c.basics.hello_world": (
        "Compiling and running",
        "A minimal program, main(), and the compile-then-run cycle.",
        [],
    ),
    "c.basics.printf": (
        "printf",
        "Formatted output; %d, %f, %s, %c placeholders.",
        ["c.basics.hello_world"],
    ),
    "c.basics.scanf": (
        "scanf",
        "Reading input into a variable; why the address-of operator is required.",
        ["c.basics.printf"],
    ),
    "c.basics.variables": (
        "Variables and types",
        "int, float, char, double; declaration versus assignment.",
        ["c.basics.hello_world"],
    ),
    "c.basics.operators": (
        "Operators",
        "Arithmetic, comparison and logical operators; integer division.",
        ["c.basics.variables"],
    ),
    "c.basics.constants": (
        "Constants",
        "#define and const; values that do not change.",
        ["c.basics.variables"],
    ),

    # ── Control flow ────────────────────────────────────────────────────
    "c.control.if": (
        "if / else if / else",
        "Branching on a condition; there is no truthiness beyond zero/nonzero.",
        ["c.basics.operators"],
    ),
    "c.control.for": (
        "for loops",
        "Init; condition; increment — and why each part is optional.",
        ["c.basics.variables"],
    ),
    "c.control.while": (
        "while and do-while",
        "Looping until a condition changes; the difference between the two forms.",
        ["c.control.if"],
    ),
    "c.control.switch": (
        "switch / case",
        "Multi-way branching on an integer or char; why break matters.",
        ["c.control.if"],
    ),
    "c.control.break_continue": (
        "break and continue",
        "Leaving a loop early, and skipping the rest of one pass.",
        ["c.control.for"],
    ),

    # ── Arrays and strings ──────────────────────────────────────────────
    "c.data.arrays": (
        "Arrays",
        "Fixed-size, contiguous, zero-indexed; declared with a size.",
        ["c.basics.variables"],
    ),
    "c.data.strings": (
        "Strings as char arrays",
        "A string is a char array ending in a null terminator; no built-in string type.",
        ["c.data.arrays"],
    ),
    "c.data.multidim_arrays": (
        "Multi-dimensional arrays",
        "Rows and columns; how they're actually laid out in memory.",
        ["c.data.arrays"],
    ),
    "c.data.string_functions": (
        "string.h functions",
        "strlen, strcpy, strcmp, strcat and their pitfalls.",
        ["c.data.strings"],
    ),

    # ── Pointers and memory ─────────────────────────────────────────────
    "c.pointers.basics": (
        "Pointers",
        "A variable that holds an address; & and * as inverse operations.",
        ["c.basics.variables"],
    ),
    "c.pointers.arithmetic": (
        "Pointer arithmetic",
        "Incrementing a pointer moves it by one element's size, not one byte.",
        ["c.pointers.basics", "c.data.arrays"],
    ),
    "c.pointers.arrays_relation": (
        "Pointers and arrays",
        "An array name decays to a pointer to its first element.",
        ["c.pointers.basics", "c.data.arrays"],
    ),
    "c.pointers.functions": (
        "Pointers as function parameters",
        "Passing an address so a function can modify the caller's variable.",
        ["c.pointers.basics", "c.functions.define"],
    ),
    "c.memory.malloc": (
        "malloc and free",
        "Requesting memory at runtime, and the obligation to release it.",
        ["c.pointers.basics"],
    ),
    "c.memory.leaks": (
        "Memory leaks and dangling pointers",
        "What happens when free is skipped, or a pointer is used after it.",
        ["c.memory.malloc"],
    ),

    # ── Functions ───────────────────────────────────────────────────────
    "c.functions.define": (
        "Defining functions",
        "Return type, parameters, the prototype, and why declaration order matters.",
        ["c.basics.variables"],
    ),
    "c.functions.return": (
        "return values",
        "Handing a value back to the caller; void functions.",
        ["c.functions.define"],
    ),
    "c.functions.recursion": (
        "Recursion",
        "A function calling itself; base case; stack frames.",
        ["c.functions.return"],
    ),
    "c.functions.scope": (
        "Scope and storage",
        "Local versus global variables; static's effect on lifetime.",
        ["c.functions.define"],
    ),

    # ── Structs and custom types ────────────────────────────────────────
    "c.structs.define": (
        "struct",
        "Bundling related values under one name; declaring and accessing members.",
        ["c.basics.variables"],
    ),
    "c.structs.pointers": (
        "Pointers to structs",
        "The -> operator; passing a struct by address instead of copying it.",
        ["c.structs.define", "c.pointers.basics"],
    ),
    "c.structs.typedef": (
        "typedef",
        "Giving a type a shorter, custom name.",
        ["c.structs.define"],
    ),

    # ── Errors and debugging ────────────────────────────────────────────
    "c.errors.compile_errors": (
        "Compile errors",
        "Reading a compiler error: file, line, what the compiler expected.",
        ["c.basics.hello_world"],
    ),
    "c.errors.segfault": (
        "Segmentation faults",
        "Accessing memory the program doesn't own; common causes.",
        ["c.pointers.basics"],
    ),
    "c.errors.debugging": (
        "Debugging",
        "Locating a fault: printf tracing, narrowing down, reading the exit code.",
        ["c.errors.compile_errors"],
    ),

    # ── Files and I/O ────────────────────────────────────────────────────
    "c.io.files": (
        "File I/O",
        "fopen, fread/fwrite or fprintf/fscanf, and always closing what you open.",
        ["c.functions.define"],
    ),

    # ── Project-shaped concepts ─────────────────────────────────────────
    "c.project.overview": (
        "What we are building",
        "The goal of the project, its shape, and why it is worth building.",
        [],
    ),
    "c.project.setup": (
        "Project setup",
        "The compiler, the compile command, and the file layout a project starts from.",
        ["c.basics.hello_world"],
    ),
    "c.project.design": (
        "Design decisions",
        "Choosing an approach and the trade-off behind it.",
        [],
    ),
    "c.project.pipeline": (
        "Putting it together",
        "How the pieces built so far combine into a working whole.",
        [],
    ),
    "c.project.pitfalls": (
        "Common mistakes",
        "The errors people actually hit here, and how to avoid them.",
        [],
    ),
}

VALID_IDS = set(CONCEPTS)


def display_name(concept_id: str) -> str:
    entry = CONCEPTS.get(concept_id)
    return entry[0] if entry else concept_id


def prerequisites(concept_id: str) -> list[str]:
    entry = CONCEPTS.get(concept_id)
    return list(entry[2]) if entry else []


def catalogue_for_prompt() -> str:
    return "\n".join(
        f"- {cid}: {gloss}" for cid, (_name, gloss, _pre) in CONCEPTS.items()
    )


def compact_catalogue() -> str:
    return " | ".join(f"{cid}={name}" for cid, (name, _g, _p) in CONCEPTS.items())


def nearest_valid(concept_id: str) -> str | None:
    if concept_id in VALID_IDS:
        return concept_id
    if not concept_id:
        return None

    def squash(v: str) -> str:
        return "".join(ch for ch in v.lower() if ch.isalnum())

    needle = squash(concept_id)
    if not needle:
        return None

    for cid, (name, _gloss, _pre) in CONCEPTS.items():
        candidates = {squash(cid), squash(cid.split(".")[-1]), squash(name)}
        if needle in candidates:
            return cid

    for cid, (name, _gloss, _pre) in CONCEPTS.items():
        target = squash(name)
        if len(target) >= 4 and (target in needle or needle in target):
            return cid

    return None
