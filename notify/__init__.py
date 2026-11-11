"""MaxQ -> Slack notifications. One-way: this package sends, it never reads.

    from notify import SlackNotifier, messages
    n = SlackNotifier.from_env()
    n.post_or_log(messages.run_finished(...))

Nothing here is load-bearing. If Slack is down, misconfigured, or simply not set up, every entry
point degrades to a printed line and the caller carries on.

Owner: Yash (lane: notify/).
"""

from notify import messages
from notify.slack import ENV_VAR, Delivery, NotifyError, SlackNotifier, redact, webhook_from_env

__all__ = [
    "ENV_VAR",
    "Delivery",
    "NotifyError",
    "SlackNotifier",
    "messages",
    "redact",
    "webhook_from_env",
]
