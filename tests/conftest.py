"""
Pytest configuration: make the project root importable so that
`import response_time` and `import telegram_monitor` work from the
`tests/` directory without extra PYTHONPATH setup.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
