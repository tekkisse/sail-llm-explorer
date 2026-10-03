"""Append-only audit log (JSON lines). Ship to your SIEM from stdout or the file."""
from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timezone
from typing import Any

_lock = threading.Lock()


class AuditLog:
    def __init__(self, target: str = "-"):
        self.target = target

    def write(self, event: str, **fields: Any) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
        line = json.dumps(record, default=str)
        with _lock:
            if self.target == "-":
                print(line, file=sys.stdout, flush=True)
            else:
                with open(self.target, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
