"""Slack notifier: secrets stay secret, failures stay quiet, messages stay honest.

No test here touches the network. Every notifier is built with an injected transport or in dry
run, and `no_real_webhook` clears the environment variable for the whole module, so a developer
with a live webhook exported in their shell cannot accidentally post into a real channel by
running pytest.

Owner: Yash (lane: notify/, tests/).
"""

from __future__ import annotations

import json

import pytest

from notify import ENV_VAR, NotifyError, SlackNotifier, messages, redact, webhook_from_env

# Assembled at runtime rather than written as a literal. It is entirely synthetic, but a literal
# here matches GitHub's secret scanner and blocks the push -- and the scanner is right to be
# strict, so the fix is to stop looking like a credential, not to add an exception for this file.
GOOD = "https://hooks.slack.com/" + "services/" + "T01ABCDEF/" + "B02GHIJKL/" + ("Zz9Yy" * 5)


@pytest.fixture(autouse=True)
def no_real_webhook(monkeypatch):
    """Nothing in this file may reach a real workspace, whatever the developer has exported."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    monkeypatch.delenv("MAXQ_SLACK_DRY_RUN", raising=False)


class FakeTransport:
    """Records calls and replays a scripted sequence of (status, body)."""

    def __init__(self, *responses):
        self.responses = list(responses) or [(200, "ok")]
        self.calls: list[tuple[str, dict, float]] = []

    def __call__(self, url, body, timeout):
        self.calls.append((url, json.loads(body.decode()), timeout))
        return self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


def _notifier(*responses, **kw):
    kw.setdefault("sleep", lambda _s: None)  # no real backoff in tests
    return SlackNotifier(GOOD, transport=FakeTransport(*responses), **kw)


# ------------------------------------------------------------------ configuration


def test_a_missing_webhook_explains_how_to_set_one():
    with pytest.raises(NotifyError, match=ENV_VAR):
        webhook_from_env({})


def test_a_malformed_webhook_is_rejected_without_echoing_it():
    secret = "https://evil.example.com/not-slack?token=SUPERSECRET"
    with pytest.raises(NotifyError) as e:
        webhook_from_env({ENV_VAR: secret})
    assert "SUPERSECRET" not in str(e.value), "the rejection echoed the value back"


def test_is_configured_does_not_raise():
    assert SlackNotifier.is_configured({}) is False
    assert SlackNotifier.is_configured({ENV_VAR: GOOD}) is True


def test_dry_run_needs_no_webhook_at_all():
    """The first thing anyone tries must work before they have created a Slack app."""
    out = []
    n = SlackNotifier.from_env({}, dry_run=True, printer=out.append)
    assert n.post({"text": "hello"}).dry_run is True
    assert out, "dry run printed nothing"


def test_dry_run_env_var_forces_dry_run():
    n = SlackNotifier.from_env({ENV_VAR: GOOD, "MAXQ_SLACK_DRY_RUN": "1"}, printer=lambda _: None)
    assert n.dry_run is True


# ------------------------------------------------------------------ the secret


def test_redact_removes_a_webhook_from_any_string():
    assert GOOD not in redact(f"POST failed for {GOOD} after 3 tries")
    assert "<redacted>" in redact(GOOD)


def test_a_transport_exception_carrying_the_url_is_redacted_before_it_is_reported():
    """A library that puts the URL in its exception message is the realistic leak path."""

    def boom(url, body, timeout):
        raise OSError(f"connection refused: {url}")

    out = []
    n = SlackNotifier(GOOD, transport=boom, sleep=lambda _s: None, printer=out.append)
    d = n.post_or_log({"text": "x"})
    assert d.ok is False
    assert GOOD not in d.detail
    assert GOOD not in " ".join(out)


def test_an_http_error_body_containing_the_url_is_redacted():
    n = _notifier((404, f"no_service for {GOOD}"))
    d = n.post({"text": "x"})
    assert d.ok is False
    assert GOOD not in d.detail


# ------------------------------------------------------------------ delivery policy


def test_a_successful_post_sends_once():
    t = FakeTransport((200, "ok"))
    n = SlackNotifier(GOOD, transport=t, sleep=lambda _s: None)
    assert n.post({"text": "x"}).ok is True
    assert len(t.calls) == 1


def test_a_server_error_is_retried_then_reported():
    t = FakeTransport((500, "boom"), (500, "boom"), (500, "boom"))
    n = SlackNotifier(GOOD, transport=t, sleep=lambda _s: None, retries=3)
    d = n.post({"text": "x"})
    assert d.ok is False
    assert len(t.calls) == 3


def test_a_transient_failure_then_success_delivers():
    t = FakeTransport((503, "later"), (200, "ok"))
    n = SlackNotifier(GOOD, transport=t, sleep=lambda _s: None, retries=3)
    assert n.post({"text": "x"}).ok is True
    assert len(t.calls) == 2


def test_a_revoked_webhook_is_not_retried():
    """404 on a webhook means revoked. Retrying posts the same failure three times."""
    t = FakeTransport((404, "no_service"))
    n = SlackNotifier(GOOD, transport=t, sleep=lambda _s: None, retries=3)
    assert n.post({"text": "x"}).ok is False
    assert len(t.calls) == 1


def test_rate_limiting_is_retried():
    """429 is the one 4xx that does improve on a second try."""
    t = FakeTransport((429, "slow down"), (200, "ok"))
    n = SlackNotifier(GOOD, transport=t, sleep=lambda _s: None, retries=3)
    assert n.post({"text": "x"}).ok is True
    assert len(t.calls) == 2


def test_post_or_log_never_raises():
    def boom(url, body, timeout):
        raise RuntimeError("anything at all")

    n = SlackNotifier(GOOD, transport=boom, sleep=lambda _s: None, printer=lambda _: None)
    assert n.post_or_log({"text": "x"}).ok is False


def test_a_message_without_a_text_fallback_is_refused():
    """`text` is what Slack shows in the sidebar and on mobile push."""
    with pytest.raises(NotifyError, match="text"):
        _notifier().post({"blocks": []})


# ------------------------------------------------------------------ honesty (charter 7, 11, 12)


def test_a_run_message_never_reports_a_zero_gap_when_there_is_no_benchmark():
    m = messages.run_finished(
        mission_id="msn_x",
        run={"status": "finished", "stop_reason": "budget_exhausted", "elapsed_s": 11.0},
        attempts=100,
        feasible=34,
        best_cost=1.082,
        best_known_gap_pct=None,
        search_label="CMA-ES (cma==4.5.0)",
    )
    blob = json.dumps(m)
    assert "n/a (no benchmark yet)" in blob
    assert "0.00%" not in blob


def test_a_run_message_carries_the_sim_label_and_the_docking_caveat():
    """These get screenshotted and forwarded; the caveat has to travel with the number."""
    m = messages.run_finished(
        mission_id="msn_x",
        run={"status": "finished", "stop_reason": "budget_exhausted", "elapsed_s": 1.0},
        attempts=10,
        feasible=1,
        best_cost=1.107,
        search_label="CMA-ES",
    )
    blob = json.dumps(m)
    assert messages.SIM_LABEL in blob
    assert messages.SIM_LABEL in m["text"], "the sidebar preview drops the caveat"
    assert "no approach or docking phase" in blob


def test_a_run_message_names_the_search_the_run_declared():
    m = messages.run_finished(
        mission_id="msn_x",
        run={"status": "finished", "elapsed_s": 1.0},
        attempts=3,
        feasible=0,
        best_cost=None,
        search_label="fixed-sigma evolution strategy (non-adaptive)",
    )
    assert "fixed-sigma evolution strategy (non-adaptive)" in json.dumps(m)


def test_no_feasible_attempt_says_so_rather_than_showing_a_cost():
    m = messages.run_finished(
        mission_id="msn_x",
        run={"status": "finished", "elapsed_s": 1.0},
        attempts=24,
        feasible=0,
        best_cost=None,
    )
    assert "none (no feasible attempt)" in json.dumps(m)


# ------------------------------------------------------------------ message shape


@pytest.mark.parametrize(
    "msg",
    [
        messages.run_finished(
            mission_id="m", run={"status": "finished", "elapsed_s": 1.0}, attempts=1,
            feasible=1, best_cost=1.0,
        ),
        messages.status_update(owner="shiva", title="t", body="b", needs="Yash: verifier bump"),
        messages.commit_pushed(branch="main", sha="abc12345", subject="s", author="a", files=2),
        messages.alert(kind="ContractError", summary="s", detail="d", where="w"),
    ],
)
def test_every_message_has_a_fallback_and_valid_blocks(msg):
    assert msg["text"] and isinstance(msg["text"], str)
    for b in msg["blocks"]:
        assert b["type"] in {"header", "section", "context", "divider"}
        if b["type"] == "header":
            assert len(b["text"]["text"]) <= 150, "Slack hard-limits header text"
        if b["type"] == "section":
            assert len(b["text"]["text"]) <= 3000, "Slack truncates section text"
    # It must survive the round trip the transport will do.
    assert json.loads(json.dumps(msg)) == msg


def test_long_text_is_truncated_not_dropped():
    m = messages.alert(kind="k", summary="s", detail="x" * 10_000)
    body = next(b for b in m["blocks"] if b["type"] == "section" and "```" in b["text"]["text"])
    assert len(body["text"]["text"]) <= 3000
    assert "…" in body["text"]["text"]
