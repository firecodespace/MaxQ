# Slack notifications

MaxQ posts updates into a Slack channel through an Incoming Webhook.

**One-way, deliberately.** This sends; it never reads. Nothing said in Slack can instruct MaxQ or
any agent working on it. That keeps the trust boundary where it belongs: a message in a channel is
not the same authority as the lead typing in a terminal, and a bot that acted on channel messages
would make anyone who can post into the channel able to drive the project. A two-way bot is a
separate decision, not a flag on this one.

Nothing here is load-bearing. With no webhook configured, the notifier is `None`, the server
behaves exactly as it did before, and every CLI command still works in `--dry-run`.

---

## Try it before setting anything up

```bash
python scripts/notify_slack.py commit --dry-run
```

That prints the exact JSON that would be posted and sends nothing. It works with no Slack app, no
webhook and no network.

## Setting it up

1. Create a Slack app at <https://api.slack.com/apps> → **Create New App** → **From scratch**.
   Pick the workspace and name it (`MaxQ` is fine).
2. **Incoming Webhooks** → toggle **On** → **Add New Webhook to Workspace** → choose the channel.
   Slack gives you a URL of the form `https://hooks.slack.com/services/T.../B.../...`.
3. Export it. **Never commit it, never paste it into a file in this repository, never paste it
   into a chat.** The URL *is* the credential: anyone holding it can post as MaxQ.

   ```bash
   export MAXQ_SLACK_WEBHOOK='https://hooks.slack.com/services/T.../B.../...'
   ```

   On Windows PowerShell: `$env:MAXQ_SLACK_WEBHOOK = 'https://...'`

   To keep it across shells, put it in `.env` (already in `.gitignore`) or your shell profile.
4. Check it:

   ```bash
   python scripts/notify_slack.py commit
   ```

The scope needed is only `incoming-webhook`. No bot token, no OAuth, no public endpoint, no
long-running process.

### Environment variables

| Variable | Meaning |
|---|---|
| `MAXQ_SLACK_WEBHOOK` | The Incoming Webhook URL. Read from the environment only. |
| `MAXQ_SLACK_DRY_RUN` | `1` forces dry run everywhere, even with a webhook set. |

---

## What posts, and from where

| Event | How it fires |
|---|---|
| **Mission run finished** | Automatic. `MissionStore` posts once per run, from `poll_run` and `abort`. |
| **Status / findings** | `python scripts/notify_slack.py status --owner … --title … --body … [--needs …]` |
| **Commits** | `python scripts/notify_slack.py commit`, or the `post-commit` hook below. |
| **Contract / verifier alerts** | `python scripts/notify_slack.py alert --kind … --summary … [--detail …]` |

Run `python scripts/notify_slack.py --help`, or `<subcommand> --help`, for the full flags.

### The commit hook

```bash
cp scripts/hooks/post-commit .git/hooks/post-commit && chmod +x .git/hooks/post-commit
```

In a worktree, `.git` is a file, so install it in the real hooks directory instead:

```bash
cp scripts/hooks/post-commit "$(git rev-parse --git-common-dir)/hooks/post-commit"
```

The hook never blocks a commit: it exits 0 whether or not Slack was reachable, and does nothing at
all when `MAXQ_SLACK_WEBHOOK` is unset.

---

## Honesty rules apply to Slack too

Charter items 7, 11 and 12 (`AGENTS.md`) are enforced in `notify/messages.py`, and there are tests
for each. A number in a channel outlives the conversation that qualified it — it gets screenshotted
and forwarded — so the caveats travel with it:

- every run message carries **`SIM - reduced fidelity`**, in the blocks *and* in the sidebar
  fallback text;
- the benchmark gap is **`n/a (no benchmark yet)`**, never `0%`, because no benchmark exists
  (WP2-12), and `0%` reads as "already matches best known";
- the search is named as whatever the **run itself declared**, not as what we would like it to be;
- a run with no feasible attempt says **"none (no feasible attempt)"** rather than showing a cost;
- runs recorded before the 2026-11-12 labels fix stored `best_known_gap_pct: 0.0`. Those files are
  evidence and are never rewritten, but the notifier reports their gap as `n/a` and says why in the
  message footer.

## Security notes

- The webhook is read from the environment only. It is never written to disk, never logged, and
  `notify.redact()` scrubs it from every error, traceback and printed line — there are tests for
  the realistic leak paths, including a transport exception that embeds the URL in its message.
- A malformed `MAXQ_SLACK_WEBHOOK` is rejected **without echoing the value**, so a wrong secret
  pasted into the wrong variable does not end up in a terminal scrollback.
- Delivery failures are never raised at the caller. A finished mission run cannot be reported as
  failed because Slack was down.
- `404` is not retried: a revoked webhook stays revoked. `429` and `5xx` are retried with
  exponential backoff.
- Tests clear `MAXQ_SLACK_WEBHOOK` for the whole module and inject a fake transport, so running
  `pytest` with a live webhook exported cannot post into a real channel.

## Rotating or revoking

Delete the webhook in the Slack app's **Incoming Webhooks** page and create a new one, then update
`MAXQ_SLACK_WEBHOOK`. Nothing in the repository needs changing.

---

Owner: Yash (lane: `notify/`, `scripts/`, `server/`, `docs/`).
