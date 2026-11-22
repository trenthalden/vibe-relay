"""Plugin-local persistent state under Hermes plugin-data."""
from __future__ import annotations

import json
import time
import threading
from pathlib import Path
from typing import Any, Optional

PLUGIN_ID = "vibe-relay"
_LOCK = threading.RLock()


def data_dir() -> Path:
    from hermes_compat import plugin_data_dir

    return plugin_data_dir(PLUGIN_ID)


def state_path() -> Path:
    return data_dir() / "state.json"


def load_state() -> dict[str, Any]:
    path = state_path()
    with _LOCK:
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            return raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}


def save_state(state: dict[str, Any]) -> None:
    path = state_path()
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
        # cost: at most 5 attempts, 4 sleeps totaling 0.5s per write; no background loop.
        for attempt in range(1, 6):
            try:
                tmp.replace(path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.05 * attempt)


def update_state(**kwargs: Any) -> dict[str, Any]:
    with _LOCK:
        state = load_state()
        state.update(kwargs)
        save_state(state)
        return state


def get(key: str, default: Any = None) -> Any:
    return load_state().get(key, default)
