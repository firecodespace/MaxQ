"""The mission API: create, launch, watch, replay (WP2-06).

Two run modes, both required by HANDOFF_PHASE2 section 4:

* **live** -- the server runs the agent and streams session events over a WebSocket as they happen.
* **replay** -- the UI plays a recorded `session.jsonl`. This keeps a static public deploy working
  with no backend at all, which is why the replay endpoints serve exactly the bytes the live stream
  carried rather than a re-rendering of them.

The library endpoints exist because the wizard has to offer real vehicles and pads, and has to show
each one's measured `sim_validation` rather than only its published figure.

Run it: `make server` (or `uvicorn server.app:app --reload`).

Owner: Yash (lane: server/).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from contracts.validate import ContractError
from server.session import SessionTail, read_events
from server.store import MissionStore, Run

REPO_ROOT = Path(__file__).resolve().parents[1]
API = "/api"


def _versions() -> dict[str, str]:
    from env import SIM_VERSION
    from verifier import VERIFIER_VERSION

    return {"sim_version": SIM_VERSION, "verifier_version": VERIFIER_VERSION}


def _library(folder: str) -> list[dict[str, Any]]:
    out = []
    for p in sorted((REPO_ROOT / "data" / folder).glob("*.json")):
        out.append(json.loads(p.read_text(encoding="utf-8")))
    return out


def _notifier_from_env():
    """A SlackNotifier when a webhook is configured, otherwise None. Never raises."""
    try:
        from notify import SlackNotifier

        return SlackNotifier.from_env() if SlackNotifier.is_configured() else None
    except Exception:  # noqa: BLE001 - the API must start with or without Slack
        return None


def create_app(root: Path | str | None = None, *, store: MissionStore | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(a: FastAPI):
        yield
        # A run is a child process; leaving one alive after the server stops would keep writing to a
        # session nobody is watching and keep a CPU busy.
        a.state.store.shutdown()

    app = FastAPI(
        lifespan=lifespan,
        title="MaxQ mission API",
        version="0.1.0",
        description=(
            "Create a mission, launch it, watch the agent work, or replay a recorded session. "
            "Everything it returns is shaped by contracts/*.schema.json."
        ),
    )
    # The UI is a static site that may be served from a different origin (or from file://) while the
    # API runs locally. This is a local development tool; it is not an authenticated public service,
    # and nothing here should be exposed to the internet as it stands.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # Slack notifications are opt-in by configuration alone: with MAXQ_SLACK_WEBHOOK unset the
    # notifier is None and the server behaves exactly as before. Tests pass their own store, so
    # this never constructs a notifier under pytest.
    app.state.store = store or MissionStore(root, notifier=_notifier_from_env())

    def S() -> MissionStore:
        return app.state.store

    def _run_or_404(mission_id: str) -> Run:
        if mission_id not in S().missions:
            raise HTTPException(404, f"no mission {mission_id!r}")
        run = S().latest_run(mission_id)
        if run is None:
            raise HTTPException(409, f"mission {mission_id!r} has not been launched")
        return run

    # ---------------------------------------------------------------- meta

    @app.get(f"{API}/health")
    def health() -> dict[str, Any]:
        s = S()
        return {
            "status": "ok",
            **_versions(),
            "missions": len(s.missions),
            "runs_in_progress": sum(1 for r in s.runs.values() if r.is_running()),
        }

    # ---------------------------------------------------------------- library (for the wizard)

    @app.get(f"{API}/library/rockets")
    def rockets() -> list[dict[str, Any]]:
        """Vehicles with their cited sources and their MEASURED sim-vs-published error.

        The wizard shows both: a card that quotes a published payload without saying how far the
        simulator is from it would be claiming an accuracy nobody measured.
        """
        return _library("rockets")

    @app.get(f"{API}/library/pads")
    def pads() -> list[dict[str, Any]]:
        return _library("pads")

    @app.get(f"{API}/library/windows")
    def windows(profile: str = Query(..., description="crew_iss | cargo_iss | meo_gps | geo_comm")) -> dict[str, Any]:
        results = REPO_ROOT / "data" / "results"
        hits = sorted(results.glob(f"windows_{profile}_*.json"))
        if not hits:
            raise HTTPException(404, f"no windows file for profile {profile!r}")
        return json.loads(hits[0].read_text(encoding="utf-8"))

    # ---------------------------------------------------------------- missions

    @app.post(f"{API}/missions", status_code=201)
    def create_mission(request: dict[str, Any]) -> dict[str, Any]:
        try:
            m = S().create_mission(request)
        except ContractError as e:
            # Name the offending fields; a wizard needs to show which input was wrong.
            raise HTTPException(422, {"error": "mission_request is not contract-valid", "fields": e.errors}) from None
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        return m.to_dict()

    @app.get(f"{API}/missions")
    def list_missions() -> list[dict[str, Any]]:
        return [m.to_dict() for m in S().list_missions()]

    @app.get(f"{API}/missions/{{mission_id}}")
    def get_mission(mission_id: str) -> dict[str, Any]:
        m = S().get_mission(mission_id)
        if not m:
            raise HTTPException(404, f"no mission {mission_id!r}")
        run = S().latest_run(mission_id)
        return {**m.to_dict(), "latest_run": run.to_dict() if run else None}

    @app.post(f"{API}/missions/{{mission_id}}/launch", status_code=202)
    def launch(mission_id: str, max_attempts: int | None = None) -> dict[str, Any]:
        if mission_id not in S().missions:
            raise HTTPException(404, f"no mission {mission_id!r}")
        try:
            run = S().launch(mission_id, max_attempts=max_attempts)
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        return run.to_dict()

    @app.post(f"{API}/missions/{{mission_id}}/abort")
    def abort(mission_id: str) -> dict[str, Any]:
        return S().abort(_run_or_404(mission_id), reason="aborted by request").to_dict()

    @app.get(f"{API}/missions/{{mission_id}}/run")
    def run_status(mission_id: str) -> dict[str, Any]:
        return _run_or_404(mission_id).to_dict()

    # ---------------------------------------------------------------- attempts

    @app.get(f"{API}/missions/{{mission_id}}/attempts")
    def list_attempts(mission_id: str) -> list[dict[str, Any]]:
        return S().list_attempts(_run_or_404(mission_id))

    @app.get(f"{API}/missions/{{mission_id}}/attempts/{{attempt_id}}")
    def get_attempt(mission_id: str, attempt_id: str) -> dict[str, Any]:
        a = S().read_attempt(_run_or_404(mission_id), attempt_id)
        if a is None:
            raise HTTPException(404, f"no attempt {attempt_id!r}")
        return a

    @app.get(f"{API}/missions/{{mission_id}}/attempts/{{attempt_id}}/trajectory")
    def get_trajectory(mission_id: str, attempt_id: str) -> dict[str, Any]:
        t = S().read_trajectory(_run_or_404(mission_id), attempt_id)
        if t is None:
            raise HTTPException(404, f"no trajectory for attempt {attempt_id!r}")
        return t

    # ---------------------------------------------------------------- live stream

    @app.websocket(f"{API}/missions/{{mission_id}}/stream")
    async def stream(ws: WebSocket, mission_id: str) -> None:
        """Every event of the current run, from the beginning, then live as they are written.

        Replaying the backlog first matters: a UI that connects a second after launch would
        otherwise miss `mission_started` and the first attempts, and could never show them.
        """
        await ws.accept()
        store = S()
        run = store.latest_run(mission_id) if mission_id in store.missions else None
        if run is None:
            await ws.send_json({"type": "error", "error": f"mission {mission_id!r} has no run"})
            await ws.close()
            return

        tail = SessionTail(run.session_path)
        try:
            async for ev in tail.follow(is_running=lambda: store.poll_run(run).is_running()):
                await ws.send_json(ev)
            await ws.send_json({"type": "stream_closed", "run": store.poll_run(run).to_dict()})
        except WebSocketDisconnect:
            return
        except RuntimeError:
            return  # socket closed from the other side mid-send
        with contextlib.suppress(RuntimeError):
            await ws.close()

    @app.get(f"{API}/missions/{{mission_id}}/events")
    def mission_events(mission_id: str) -> list[dict[str, Any]]:
        """The run's events so far, as a plain list. The polling fallback for a WebSocket."""
        return read_events(_run_or_404(mission_id).session_path)

    # ---------------------------------------------------------------- replay

    @app.get(f"{API}/sessions")
    def list_sessions() -> list[dict[str, Any]]:
        return S().list_sessions()

    @app.get(f"{API}/sessions/{{session_id}}/events")
    def session_events(session_id: str) -> list[dict[str, Any]]:
        p = S().session_path(session_id)
        if p is None:
            raise HTTPException(404, f"no recorded session {session_id!r}")
        return read_events(p)

    @app.get(f"{API}/sessions/{{session_id}}/stream")
    def session_stream(session_id: str, speed: float = Query(1.0, gt=0, le=1000)) -> StreamingResponse:
        """Replay a recording as server-sent events, paced by each event's own `t_wall`.

        `speed` scales that pacing; the default replays at the speed the run actually happened, which
        is what makes a replay and a live session look the same to a viewer.
        """
        p = S().session_path(session_id)
        if p is None:
            raise HTTPException(404, f"no recorded session {session_id!r}")
        events = read_events(p)

        async def gen():
            started = time.perf_counter()
            for ev in events:
                target = float(ev.get("t_wall", 0.0)) / speed
                delay = target - (time.perf_counter() - started)
                if delay > 0:
                    await asyncio.sleep(min(delay, 30.0))
                yield f"data: {json.dumps(ev)}\n\n"
            yield 'data: {"type": "stream_closed"}\n\n'

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ---------------------------------------------------------------- errors

    @app.exception_handler(ContractError)
    async def _contract_error(_request, exc: ContractError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": str(exc), "fields": exc.errors})

    return app


app = create_app()
