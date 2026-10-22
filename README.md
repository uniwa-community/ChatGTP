# E-Pavlidis

Ο πολυαγαπητός Ηλεκτρονικός Παυλίδης, Discord bot βασιζόμενο σε OpenAI-compatible endpoint.

## Behaviour

- Only talks in one configured guild, and only in configured channels.
- Stays silent until pinged (`@ChatΓΤΠ ...`). Once pinged, it keeps chiming
  in when addressed directly, and goes quiet again after 2 minutes of silence
  in the channel (stays muted until pinged again).
- Context: the last 5 messages of the channel plus its system prompt.
- Slash commands: `/epav stop` (silence the bot in that channel) and
  `/epav status` (reports status ok).

## Setup

```sh
cp .env.example .env
# fill in credentials
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

The bot needs the `message_content` intent enabled in the Discord developer portal.
