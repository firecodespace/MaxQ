"""Post MaxQ updates to Slack through an Incoming Webhook.

One-way by design: this sends, it never reads. Nothing in Slack can instruct MaxQ, which keeps the
trust boundary where it belongs -- a message in a channel is not the same authority as the lead
typing in a terminal. A two-way bot is a separate decision, not an extension of this file.

Secrets. The webhook URL comes from the environment only, never a file in the repository, never an
argument, never a log line. `redact()` is applied to everything this module prints or raises,
because a webhook URL *is* the credential: anyone holding it can post as MaxQ.

Failure policy. Notification is never load-bearing. Every public entry point here is safe to call
from a code path that must not fail -- a dead network, a revoked webhook, or Slack being down
returns a `Delivery` saying so instead of raising. The caller decides whether to care; the mission
run does not.

Honesty. Charter items 7, 11 and 12 apply to a Slack message exactly as they apply to the UI. A run
summary posted here carries the same "SIM - reduced fidelity" label, the same null benchmark gap,
and the same search label the run itself declared. `notify/messages.py` builds them.

Owner: Yash (lane: notify/, scripts/, server/, tests/).
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

#: The only place the webhook may come from. Mirrors the SPACETRACK_* convention.
ENV_VAR = "MAXQ_SLACK_WEBHOOK"

#: Slack's own documented shape. Anything else is a typo or a pasted wrong value, and failing here
#: beats discovering it as a silent 404 in production.
_WEBHOOK_RE = re.compile(r"^https://hooks\.slack\.com/services/T[A-Za-z0-9]+/B[A-Za-z0-9]+/[A-Za-z0-9]+$")

#: Matches any Slack webhook URL anywhere in a string, so it can be scrubbed from errors and logs.
_LEAK_RE = re.compile(r"https://hooks\.slack\.com/services/\S+")

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_RETRIES = 3


class NotifyError(RuntimeError):
    """Configuration is wrong in a way the caller must fix. Never raised for transport failures."""


@dataclass(frozen=True)
class Delivery:
    """What happened. `ok` False is normal and not an exception."""

    ok: bool
    status: int | None = None
    detail: str = ""
    dry_run: bool = False

    def __str__(self) -> str:  # pragma: no cover - trivial
        if self.dry_run:
            return "dry run: nothing sent"
        return f"delivered ({self.status})" if self.ok else f"not delivered: {self.detail}"


def redact(text: str) -> str:
    """Remove any Slack webhook URL from a string.

    Applied to every message this module logs or raises. A webhook URL in a traceback, a CI log or
    a pasted error report is a live credential that anyone reading it can use.
    """
    return _LEAK_RE.sub("https://hooks.slack.com/services/<redacted>", text)


def webhook_from_env(env: dict[str, str] | None = None) -> str:
    """The webhook URL, or a NotifyError explaining how to set it. The value is never echoed."""
    env = os.environ if env is None else env
    url = (env.get(ENV_VAR) or "").strip()
    if not url:
        raise NotifyError(
            f"set {ENV_VAR} in your shell first. Create an Incoming Webhook at "
            "https://api.slack.com/apps (Incoming Webhooks -> Add New Webhook to Workspace), "
            "then export the URL. It is read from the environment only, never stored in the "
            "repository, and never printed. See docs/slack.md."
        )
    if not _WEBHOOK_RE.match(url):
        # Deliberately does not include the value, not even truncated.
        raise NotifyError(
            f"{ENV_VAR} is set but is not a Slack Incoming Webhook URL "
            "(expected https://hooks.slack.com/services/T.../B.../...)"
        )
    return url


#: A transport takes (url, payload_bytes, timeout) and returns (status_code, body). Injectable so
#: tests exercise the retry, redaction and failure policy without touching the network.
Transport = Callable[[str, bytes, float], "tuple[int, str]"]


def _urllib_transport(url: str, body: bytes, timeout: float) -> tuple[int, str]:
    req = urllib.request.Request(  # noqa: S310 - scheme is pinned by _WEBHOOK_RE
        url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "MaxQ-notifier/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read().decode("utf-8", "replace")[:500]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:500]


class SlackNotifier:
    """Posts Block Kit messages. Construct once and reuse; it holds no connection.

    `dry_run=True` formats and validates everything, then prints instead of sending. That is the
    mode to develop in, and the mode tests use, so a test run can never post into a real channel.
    """

    def __init__(
        self,
        webhook: str | None = None,
        *,
        dry_run: bool = False,
        transport: Transport | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        retries: int = DEFAULT_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
        printer: Callable[[str], None] = print,
    ) -> None:
        self.dry_run = dry_run
        # In dry-run the webhook is optional, so the whole pipeline can be exercised with no secret
        # configured at all -- which is how someone should first try this.
        self._webhook = webhook if webhook is not None else (None if dry_run else webhook_from_env())
        self._transport = transport or _urllib_transport
        self._timeout_s = timeout_s
        self._retries = max(1, retries)
        self._sleep = sleep
        self._print = printer

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, **kw: Any) -> SlackNotifier:
        """Build from the environment. `MAXQ_SLACK_DRY_RUN=1` forces dry run."""
        env = os.environ if env is None else env
        if (env.get("MAXQ_SLACK_DRY_RUN") or "").strip() in {"1", "true", "yes"}:
            kw.setdefault("dry_run", True)
        if kw.get("dry_run") and not (env.get(ENV_VAR) or "").strip():
            return cls(webhook=None, **kw)
        return cls(webhook=webhook_from_env(env), **kw)

    @staticmethod
    def is_configured(env: dict[str, str] | None = None) -> bool:
        """True when a usable webhook is set. Lets callers skip silently rather than crash."""
        try:
            webhook_from_env(env)
        except NotifyError:
            return False
        return True

    def post(self, message: dict[str, Any]) -> Delivery:
        """Send one Block Kit message. Returns a Delivery; raises only for a malformed message."""
        if not isinstance(message, dict) or "text" not in message:
            # `text` is the notification fallback Slack shows in the sidebar and on mobile. A
            # message without one is delivered but unreadable where it matters most.
            raise NotifyError("message must be a dict with a 'text' fallback field")

        body = json.dumps(message).encode("utf-8")

        if self.dry_run:
            self._print(redact(json.dumps(message, indent=2)))
            return Delivery(ok=True, dry_run=True, detail="dry run")

        assert self._webhook is not None  # guaranteed by __init__ outside dry run
        last = ""
        for attempt in range(self._retries):
            try:
                status, text = self._transport(self._webhook, body, self._timeout_s)
            except Exception as e:  # noqa: BLE001 - notification must never raise at the caller
                last = redact(f"{type(e).__name__}: {e}")
            else:
                if 200 <= status < 300:
                    return Delivery(ok=True, status=status)
                last = redact(f"HTTP {status}: {text}")
                # 4xx other than 429 will not improve by trying again -- a revoked webhook stays
                # revoked, and retrying a bad payload just posts the same error three times.
                if 400 <= status < 500 and status != 429:
                    return Delivery(ok=False, status=status, detail=last)
            if attempt < self._retries - 1:
                self._sleep(2.0**attempt)
        return Delivery(ok=False, detail=last or "no response")

    def post_or_log(self, message: dict[str, Any]) -> Delivery:
        """`post`, but never raises at all. For code paths that must not fail, like a finished run."""
        try:
            d = self.post(message)
        except Exception as e:  # noqa: BLE001
            d = Delivery(ok=False, detail=redact(f"{type(e).__name__}: {e}"))
        if not d.ok:
            self._print(f"slack: {d}")
        return d
