"""Hatterfeladat a webes feluletnek: egyszerre egy adatgyujtes vagy futtatas, idokorlattal jelezve."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

log = logging.getLogger(__name__)


class Jobs:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state = {"state": "idle", "kind": None, "message": "", "started": None, "finished": None,
                      "run_id": None, "symbol": None}

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.state)

    def _set(self, **values) -> None:
        with self.lock:
            self.state.update(values)
            if values.get("state") in ("done", "error"):
                self.state["finished"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def start(self, kind: str, symbol: str, fn) -> bool:
        """`fn(progress)` a hatterben fut; visszateresi erteke (ha dict es van 'run_id') a feladatba kerul."""
        with self.lock:
            if self.state["state"] == "running":
                return False
            self.state = {"state": "running", "kind": kind, "message": "indul...", "symbol": symbol,
                          "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          "finished": None, "run_id": None}
        threading.Thread(target=self._run, args=(fn,), daemon=True, name=f"backtest-{kind}").start()
        return True

    def _run(self, fn) -> None:
        try:
            out = fn(lambda msg: self._set(message=str(msg)[:200]))
            out = out if isinstance(out, dict) else {}
            self._set(state="done", message=out.get("message", "Kesz."), run_id=out.get("run_id"))
        except ValueError as exc:
            self._set(state="error", message=str(exc)[:300])
        except Exception as exc:
            log.exception("A backteszt hatterfeladat hibara futott")
            self._set(state="error", message=f"Hiba: {type(exc).__name__}: {exc}"[:300])
