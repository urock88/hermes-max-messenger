# Security notes

A messenger adapter is the door to an agent that can run commands on your server. This page lists what the
plugin does to keep that door narrow, and what stays your decision.

## Who can talk to the agent

* Access is decided by Hermes core, not by the plugin: `MAX_ALLOWED_USERS`, approved pairing codes,
  `MAX_GROUP_ALLOWED_CHATS`, or `MAX_ALLOW_ALL_USERS`. The plugin never drops a stranger on its own, so the
  stranger reaches Hermes' pairing flow and gets a code — and nothing else.
* **Attachments of an unauthorised sender are never downloaded.** The check runs before any media request.
* In groups the bot reacts only when addressed (mention, reply, command) unless the chat is listed in
  `MAX_FREE_RESPONSE_CHATS`. Being addressed is not being allowed: the sender still has to pass the access check.
* Messages from other bots and from channels are ignored.

## Buttons

* Every prompt (command approval, clarifying question, model picker) remembers the person who raised it, the chat
  and the message it was sent as. A press is accepted only when all three match.
* Prompts expire after 15 minutes; a newer prompt in the same chat replaces the older one.
* A refused press is answered with a short notice in the client and does not use the prompt up.
* Button payloads carry a random prompt id and an index, never the command text or a model name.

## Network

* The bot token travels only in the `Authorization` header to `platform-api2.max.ru` — never in a URL, never to a
  media or upload host.
* Media downloads go through Hermes' SSRF-checked HTTP client: https only, public addresses only, every redirect
  re-checked. Sizes are capped (50 MB per file, 100 MB and 10 files per message).
* Uploads go only to MAX upload hosts (`*.max.ru`, `*.oneme.ru`, `*.okcdn.ru`, `*.cdn-max.ru`); any other host
  returned by the API is refused.
* TLS verification is never disabled. The Russian Trusted Root CA, which signs MAX's and Sber's certificates, is
  added to certifi's bundle for those clients only.

## Webhook

* The listener binds to `127.0.0.1` by default; expose it through a reverse proxy with TLS.
* It refuses to start without a secret. The `X-Max-Bot-Api-Secret` header is compared in constant time before the
  body is read; bodies over 1 MiB are refused.
* The handler answers at once and processes the update in the background, so a slow agent never makes MAX retry.

## Data

* State on disk: the long-polling position, which MAX dialog belongs to which user, which chats are groups, and
  ids of recently seen updates (kept 7 days) — in `<HERMES_HOME>/plugin-data/max-messenger/state.db`. No message
  text.
* Logs carry update types, ids and error codes, not message text.
* Speech-to-text providers send audio to Yandex or Sber only when you select them in `stt.provider`.

## What remains your decision

* `MAX_ALLOW_ALL_USERS=true` lets anyone who finds the bot drive your agent. Keep it off unless the agent has no
  dangerous tools.
* Approve pairing codes only for people you know: an approved user can ask the agent to do anything it can do.
* Keep Hermes' command approvals on; the buttons make them painless.

## Reporting a problem

Please report security issues privately through GitHub's "Report a vulnerability" on the repository page, not in
a public issue.
