"""Use the workspace-local Rerun runtime, without changing global packages."""
import sys
import site
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / '.runtime/rerun'
if RUNTIME.exists():
    sys.path.insert(0, str(RUNTIME))
    site.addsitedir(str(RUNTIME))
