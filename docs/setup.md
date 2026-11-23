# Setting up and testing with a live bot

This page is the long version of the README's quick start, plus a checklist for the first test with a real MAX
bot. It is written so that a person — or a Hermes agent asked to "install and check the MAX plugin" — can follow
it step by step.

## 1. Before you start

* Hermes Agent **0.22.6 or newer** with the gateway running (`hermes gateway status`).
* A MAX bot made for this purpose (business.max.ru → *Chat bots*), and its token. Do not reuse a bot another
  program already reads: MAX hands each update to one reader only, and two readers steal messages from each other.
* Optional: `ffmpeg` on the server for long voice notes; Pillow and a monospace font (`fonts-dejavu-core`) for
  tables as pictures.

## 2. Install

```bash
hermes plugins install urock88/hermes-max-messenger --enable
```

This clones the repository under `~/.hermes/plugins/`, installs `httpx`, `aiohttp` and `certifi`, and lists the
plugin under `plugins.enabled`. Hermes Desktop picks up the "MAX sessions" page from the plugin's
`desktop/` folder on its next start.

Check it:

```bash
hermes plugins list            # max-messenger … enabled
hermes plugins doctor max-messenger
```

If `max-platform` (another MAX plugin) is installed, disable it first: both claim the `max` platform.

## 3. Configure

```bash
hermes gateway setup           # choose MAX
```

The wizard asks for the token and checks it with `GET /me`, then asks who may use the bot (empty = pairing
codes), whether to use long polling or a webhook (it generates the webhook secret), and the home chat. Everything
lands in `~/.hermes/.env`. The same can be done by hand — see the settings table in the README.

```bash
hermes gateway restart
```

The gateway log should show `MAX: connected as @<bot name> (<id>), long polling` and, with long polling, no warning about webhook
subscriptions. If it says the bot has webhook subscriptions, either remove them or set `MAX_POLLING_TAKEOVER=true`.

## 4. Test checklist

Use a MAX account that is **not** in `MAX_ALLOWED_USERS` for step 1, then your own account.

| # | Do | Expect |
|---|---|---|
| 1 | Open the bot from a new account and press *Start*. | A pairing code. On the server `hermes pairing approve max <code>`; the next message gets an answer. Nothing sent before approval is downloaded. |
| 2 | Ask something that takes the agent a while ("summarise the latest Hermes release notes"). | "печатает…" / "typing…" under the bot's name the whole time the agent works, not only for the first seconds. |
| 3 | Ask for a long answer (a 1 500-word essay). | The message appears early and grows by edits; past ~3 500 characters it continues in a second message; no text is lost. |
| 4 | Ask for code and a small table. | Code in a monospaced block, the table in aligned columns (or a picture with `MAX_TABLE_AS_IMAGE=true`). |
| 5 | Send a voice note (with an STT provider configured). | The agent answers what you said. With `/voice` on, the answer comes back as audio. |
| 6 | Send a photo with a caption, then a PDF. | The agent sees the picture and reads the document. |
| 7 | Ask the agent to make a file and send it to you. | The file arrives; "sending a file" shows while it uploads. |
| 8 | Ask for something that needs a dangerous command (e.g. delete a temp folder). | A message with buttons. Pressing *Allow once* runs it; the buttons disappear. A press from another account is refused. |
| 9 | Send `/model`. | A list of providers, then models as buttons; paging works; the choice is applied. |
| 10 | Send `/sethome`, then `hermes cron add "every 2m" "say hi" --deliver max` (or wait for an existing job). | The cron message arrives in this chat. Remove the job afterwards. |
| 11 | Restart the gateway (`hermes gateway restart`) while nothing is being written. | No old messages are answered again after the restart. |
| 12 | Add the bot to a group as an administrator and write without mentioning it, then mention it. | Silence, then an answer to the mention only. |

Things worth writing down for the author while testing (they are not documented by MAX and were never seen on a
live bot): how long the typing indicator lasts after one call; anything MAX refuses to render; how a mention of the
bot looks in a group message; whether voice notes arrive with a ready `transcription` field.

## 5. Webhook instead of long polling

1. A domain with a valid certificate on port 443, and a reverse proxy to `127.0.0.1:8646` (nginx example in the README).
2. `MAX_WEBHOOK_URL=https://<domain>/max/webhook` and `MAX_WEBHOOK_SECRET=<5–256 chars of A-Z a-z 0-9 _ ->`.
3. `hermes gateway restart`. The adapter subscribes the URL with the secret instead of polling.
4. On the server: `curl http://127.0.0.1:8646/health` → `{"ok": true, "received": …}`. From outside, a POST to
   the public URL without the secret header → 401.

The subscription stays across restarts on purpose (MAX keeps delivering to it). Going back to polling: remove
`MAX_WEBHOOK_URL` and start once with `MAX_POLLING_TAKEOVER=true`, which removes the subscription; then turn the
flag off again.

## 6. Removing it

```bash
hermes plugins disable max-messenger
hermes plugins remove max-messenger
```

The adapter's state (polling position, known dialogs) stays in `~/.hermes/plugin-data/max-messenger/` until you
delete the folder.
