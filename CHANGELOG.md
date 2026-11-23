# Changelog

## 0.2.0 — first contact

* A stranger's access code now comes in the language of their MAX app (Russian or English, otherwise `MAX_LANGUAGE`)
  instead of Hermes core's global language, and says plainly to forward the message to the bot's owner. The plugin rewords
  core's pairing reply as it goes out; codes are still minted, rate-limited and approved by core, and other platforms keep
  core's text.
* The owner's home chat is told about every new access request at once: name, MAX id, code, approve command.
* A stranger who writes again while core stays silent (one code per 10 minutes) is reminded of the same code, once.
* The guest is told "You're approved" within about 15 seconds of `hermes pairing approve`; `/start` from an approved person
  says they may just write.
* `hermes gateway setup` asks for the bot's language and ends with a numbered checklist and the one-reader warning.
* Start-up errors (no token, token refused, API unreachable, webhook in the way) are plain sentences in `MAX_LANGUAGE`
  with the next step to take.
* An info log line for every code issued (user id, name, masked code).
* README: "Letting a new person in" — both routes, step by step for the guest and the owner.

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
