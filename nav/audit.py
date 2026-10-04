"""Append-only audit log (JSON lines) so another person can reproduce any result."""
from __future__ import annotations
import json
import time

from . import config


def log(event, **fields):
    config.OUT_DIR.mkdir(parents=True, exist_ok=True)
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event, **fields}
    with open(config.AUDIT_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
