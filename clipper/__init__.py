"""
clipper — two-character comedic explainer reels from finished Chipper modules.

This package lives outside the MAROS app but reuses a few helpers from
chipper.py directly (the LLM router, the JSON fence stripper, the timestamp
converters). MAROS is read-only from here: nothing in this package writes to
the MAROS tree.

MAROS_ROOT points at that checkout so `from chipper import ...` resolves.
Override with the MAROS_ROOT environment variable if the checkout moves.
"""

import os
import sys
from pathlib import Path

MAROS_ROOT = Path(os.getenv("MAROS_ROOT", Path.home() / "Desktop" / "MAROS"))

if not MAROS_ROOT.exists():
    raise RuntimeError(
        f"MAROS checkout not found at {MAROS_ROOT}. "
        "Set MAROS_ROOT to point at it — clipper imports chipper.py from there."
    )

# Prepend so MAROS's config.py/models.py win over any same-named local module.
if str(MAROS_ROOT) not in sys.path:
    sys.path.insert(0, str(MAROS_ROOT))
