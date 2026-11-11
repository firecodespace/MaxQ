"""Session files: the recording and the live stream are the same bytes (WP2-06).

The optimizer writes one JSON event per line to a `session.jsonl` as it runs (HANDOFF_PHASE2 6.3).
Rather than build a second, parallel path for live streaming, the server TAILS that file and
publishes each line as it appears. The consequence is the property WP2-06 is asked to deliver:

    a live session and its replay produce the same UI

not because two code paths were kept in step, but because there is only one artifact. A replay reads
the file the live viewer was watching being written. `tests/server/test_api.py` asserts it.

Owner: Yash (lane: server/).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

from contracts.validate import ContractError, validate

# A session line is small; this only bounds pathological input.
MAX_LINE_BYTES = 1 << 20


def read_events(path: Path | str, *, validate_events: bool = False) -> list[dict[str, Any]]:
    """Read a whole session file. Incomplete trailing lines are skipped, not guessed at."""
    out: list[dict[str, Any]] = []
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue  # a half-written final line while the run is still going
        if validate_events:
            validate("session", ev)
        out.append(ev)
    return out


def iter_events(path: Path | str) -> Iterator[dict[str, Any]]:
    """Yield the events already in a session file."""
    yield from read_events(path)


def is_terminal(event: dict[str, Any]) -> bool:
    return event.get("type") == "mission_finished"


class SessionTail:
    """Follow a session file, yielding each event once, until it ends or the run stops.

    Holds a byte offset rather than re-reading, and keeps any partial trailing line until the writer
    completes it: the optimizer flushes per line, but a reader can still arrive mid-write.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._offset = 0
        self._partial = b""
        self.finished = False

    def poll(self) -> list[dict[str, Any]]:
        """Every event that has appeared since the last call."""
        if not self.path.exists():
            return []
        events: list[dict[str, Any]] = []
        with open(self.path, "rb") as f:
            f.seek(self._offset)
            chunk = f.read()
            self._offset = f.tell()
        if not chunk:
            return []
        buf = self._partial + chunk
        *lines, self._partial = buf.split(b"\n")
        if len(self._partial) > MAX_LINE_BYTES:  # not a session file; stop trusting it
            self._partial = b""
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                ev = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            events.append(ev)
            if is_terminal(ev):
                self.finished = True
        return events

    async def follow(self, *, poll_s: float = 0.05, is_running=None) -> AsyncIterator[dict[str, Any]]:
        """Yield events as they are written, then stop when the run ends.

        `is_running` is the server's view of the worker. Without it a reader would hang forever on a
        run that died before writing `mission_finished`, which is exactly when a UI most needs to be
        told something went wrong.
        """
        while True:
            for ev in self.poll():
                yield ev
                if is_terminal(ev):
                    return
            if self.finished:
                return
            if is_running is not None and not is_running():
                # The worker is gone. Drain whatever it managed to write, then stop.
                for ev in self.poll():
                    yield ev
                    if is_terminal(ev):
                        return
                return
            await asyncio.sleep(poll_s)


def validate_session_events(path: Path | str) -> tuple[int, list[str]]:
    """Validate every event in a file against the session contract. Returns (count, errors)."""
    errors: list[str] = []
    n = 0
    for i, ev in enumerate(read_events(path), 1):
        n += 1
        try:
            validate("session", ev)
        except ContractError as e:
            errors += [f"line {i}: {m}" for m in e.errors]
    return n, errors
