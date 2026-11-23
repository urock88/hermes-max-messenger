# Changelog

## 0.1.0 — first public version

* MAX as a Hermes gateway platform: direct chats and groups (mention gating, allowed groups, free-response chats).
* Long polling with a saved position and a single reader per token; webhook with a required secret. Refuses to
  start polling while a webhook subscription exists, unless `MAX_POLLING_TAKEOVER=true`.
* Redelivered updates are dropped; updates of one chat are handled in order, chats in parallel.
* Typing indicator for the whole agent turn, "sending a file" during uploads.
* Streamed replies by editing; long answers split without losing text; Markdown turned into MAX HTML with a plain
  text fallback; tables as aligned text or PNG pictures.
* Photos, video, audio, voice notes and documents in both directions; `attachment.not.ready` retried.
* Buttons for command approvals, clarifying questions and the model picker, bound to the person who raised them.
* Pairing, allowlists, home chat, cron delivery and `hermes send` outside the gateway.
* Command menu (13 commands, ru/en), `hermes gateway setup` wizard.
* Speech-to-text providers `yandex_speechkit` and `sber_salutespeech` for every Hermes platform.
* Hermes Desktop page "MAX sessions".
