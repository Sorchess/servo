# tests/conftest.py
# Гарантирует, что корень репозитория (где лежат пакеты core/, tools/, ui/)
# находится в sys.path при запуске pytest из любого места.

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
