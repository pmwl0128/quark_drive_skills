"""`python -m pcui`, or `python <bridge>/pcui` from any directory: the form agents are given."""
import os
import sys
from pathlib import Path

if not __package__:
    # Run as a folder, the package itself is sys.path[0] and its modules (config, security, ...)
    # would shadow top-level ones. Put the bridge folder there instead.
    _here = Path(__file__).resolve().parent
    sys.path[:] = [entry for entry in sys.path if Path(entry or ".").resolve() != _here]
    sys.path.insert(0, str(_here.parent))
    # A release bundle keeps its skills beside the bridge folder; start-local exports the same root.
    _skills = _here.parent.parent / "skills"
    if "PCUI_SKILLS_ROOT" not in os.environ and _skills.is_dir():
        os.environ["PCUI_SKILLS_ROOT"] = str(_skills)

from pcui.cli import main  # noqa: E402

raise SystemExit(main())
