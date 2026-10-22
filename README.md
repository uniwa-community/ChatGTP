# E-Pavlidis

A joke Discord bot backed by an OpenAI-compatible endpoint.

## Behaviour

- Only talks in one configured guild, and only in configured channels.
- Stays silent until pinged (`@E-Pavlidis ...`). Once pinged, it keeps chiming
  in when addressed directly, and goes quiet again after 2 minutes of silence
  in the channel (stays muted until pinged again).
- Context: the last 5 messages of the channel plus its system prompt.
- Slash commands: `/epav stop` (silence the bot in that channel) and
  `/epav status` (reports status ok).

## Setup

```sh
cp .env.example .env
# fill in DISCORD_TOKEN, DISCORD_GUILD_ID, OPENAI_API_KEY, EPav_CHANNELS
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

Env vars:

| Var | Purpose |
| --- | --- |
| `DISCORD_TOKEN` | Bot token |
| `DISCORD_GUILD_ID` | The single server the bot talks in |
| `OPENAI_API_KEY` | API key for the endpoint |
| `OPENAI_BASE_URL` | `https://some.url.gr/v1` |
| `EPav_MODEL` | Model name sent to the endpoint |
| `EPav_CHANNELS` | Comma-separated channel IDs the bot may talk in |
| `EPAV_SYSTEM_PROMPT` | The role the bot plays |

The bot needs the `message_content` intent enabled in the Discord developer portal.
