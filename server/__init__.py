"""MaxQ Phase 2 - mission API, session streaming and replay recording (WP2-06).

    from server import create_app
    app = create_app()        # or: uvicorn server.app:app

The live stream and the replay serve the same bytes: the optimizer writes a `session.jsonl`, the
server tails it for live viewers and serves it for replay. See server/session.py.

Owner: Yash (lane: server/).
"""

from server.app import create_app
from server.store import MissionStore

__all__ = ["MissionStore", "create_app"]
