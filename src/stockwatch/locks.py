"""Folyamatok kozotti fajlzar (a monitor, a webes felulet es a strategia-labor kulon folyamat).

Egy olvas-modosit-ir ciklus (pl. broker.json, lab/experiments.json) zar nelkul elveszithetne
egy kozben tortent modositast. A zarfajl ures, a merete nem no.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path


class LockTimeout(Exception):
    """A zar nem szabadult fel idoben."""


@contextmanager
def file_lock(target: Path, timeout: float = 120.0):
    """Kizarolagos zar a `target` melletti `<nev>.lock` fajlon."""
    path = target.with_name(target.name + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with open(path, "a+b") as fh:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise LockTimeout(str(target)) from None
                    time.sleep(0.2)
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise LockTimeout(str(target)) from None
                    time.sleep(0.2)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


def write_atomic(path: Path, text: str) -> None:
    """Atomi csere: az olvaso soha nem lat felig irt fajlt. Windowson a csere atmenetileg elutasithato, amig mas
    (masik folyamat, virusirto, indexelo) eppen olvassa a celfajlt - ilyenkor rovid ujraprobalas."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(10 if os.name == "nt" else 1):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if os.name != "nt" or attempt == 9:
                raise
            time.sleep(0.05 * (attempt + 1))
