"""Run stages 3, 5, 6 and evaluation readiness checks, stopping on errors."""
import subprocess
import sys
from pathlib import Path

if __name__ == '__main__':
    here = Path(__file__).resolve().parent
    for stage in ('stage3.py', 'stage5.py', 'stage6.py', 'stage7.py'):
        subprocess.run([sys.executable, str(here / stage)], check=True)
