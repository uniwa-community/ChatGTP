#!/usr/bin/env python3
"""E-Pavlidis - ΗΛΕΚΤΡΟΝΙΚΟΣ ΠΑΥΛΙΔΗΣ Discord bot backed by an OpenAI-compatible endpoint."""

import asyncio
import logging
import time
from pathlib import Path

import aiohttp
import discord
from discord import app_commands

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("discord").setLevel(logging.WARNING)
log = logging.getLogger("epavlidis")

# ---- .env loading ----

ENV_FILE = Path(__file__).parent / ".env"


def load_env() -> dict[str, str]:
    """Minimal .env parser: KEY=VALUE lines, # comments, no shell expansion."""
    if not ENV_FILE.exists():
        raise SystemExit(f"Missing {ENV_FILE} — copy .env.example to .env and fill it in.")
    env: dict[str, str] = {}
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip("'\"")
    return env


_env = load_env()


def _env_var(key: str, default: str | None = None, *, required: bool = False) -> str:
    value = _env.get(key) or default or ""
    if required and not value:
        raise SystemExit(f"{ENV_FILE}: missing required key {key}")
    return value


TOKEN = _env_var("DISCORD_TOKEN", required=True)
API_KEY = _env_var("OPENAI_API_KEY", required=True)
API_BASE = _env_var("OPENAI_BASE_URL", "https://llm.beryllium.gr/v1")
MODEL = _env_var("EPAV_MODEL", "Gemma-4-Uncensored")
GUILD_ID = int(_env_var("DISCORD_GUILD_ID", required=True))
ADMIN_ROLE_ID = int(_env_var("EPAV_ADMIN_ROLE_ID", required=True))
MAX_TOKENS = int(_env_var("EPAV_MAX_TOKENS", "1700"))
TEMPERATURE = float(_env_var("EPAV_TEMPERATURE", "0.9"))
SYSTEM_PROMPT_FILE = Path(__file__).parent / "system_prompt.txt"
DECIDER_PROMPT = """You are the decision-making brain of E-Pavlidis, a joke Discord bot playing a Greek professor persona. Given the recent channel history and the latest message, decide whether the bot should reply. Default to YES if the message is addressed at the bot or continues a conversation it was part of, and to NO if the message is clearly not for the bot. Answer NO if the latest message is just a link, an image, a GIF, an emoji, or any other non-textual content — never respond to those. Answer NO if the latest message mentions suicide or self-harm — never engage with those, no matter the context. Reply with exactly one word: YES or NO."""


def load_channels() -> set[int]:
    """All EPAV_CHANNEL_* entries in .env, re-read every call so live
    edits to .env are picked up without a restart."""
    try:
        env = load_env()
    except SystemExit:
        log.error("Could not read %s", ENV_FILE)
        return set()
    channels = set()
    for key, value in sorted(env.items()):
        if not key.startswith("EPAV_CHANNEL"):
            continue
        stripped = value.strip()
        if not stripped.isdigit():
            log.warning("Ignoring %s=%r: not a channel id", key, value)
            continue
        channels.add(int(stripped))
    return channels


def load_system_prompt() -> str:
    try:
        return SYSTEM_PROMPT_FILE.read_text()
    except OSError:
        log.error("Could not read %s", SYSTEM_PROMPT_FILE)
        return "You are E-Pavlidis. Stay in character."

HISTORY_DEPTH = 5

YES_WORDS = {"YES", "ΝΑΙ", "OK", "TRUE"}


class Epavlidis(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.session: aiohttp.ClientSession | None = None
        # channel_id -> timestamp of last bot-participating message
        self.last_activity: dict[int, float] = {}
        # per-channel in-flight worker tasks; new messages cancel+replace them
        self.workers: dict[int, asyncio.Task] = {}
        # global on/off switch; disabled = idle, only /epav commands work
        self.enabled = True
        # channels we've gone silent in; stay silent until pinged again
        self.muted_channels: set[int] = set()
        # channels temporarily activated by a ping, outside channels.txt
        self.temp_channels: set[int] = set()
        # set when the last LLM request failed with an HTTP error
        self.api_down = False

    async def setup_hook(self):
        guild = discord.Object(id=GUILD_ID)
        self.register_commands()
        await self.tree.sync(guild=guild)
        self.session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {API_KEY}"}
        )
        log.info("setup_hook done: %d commands synced, LLM session open (base=%s model=%s)",
                 len(self.tree.get_commands(guild=guild)), API_BASE, MODEL)

    async def close(self):
        if self.session:
            await self.session.close()
        await super().close()

    async def on_ready(self):
        log.info("E-Pavlidis online as %s (guild %s, channels %s)", self.user, GUILD_ID, load_channels())

    # ---- LLM ----

    async def chat(self, messages: list[dict], max_tokens: int | None = None) -> str | None:
        if max_tokens is None:
            max_tokens = MAX_TOKENS
        payload = {
            "model": MODEL,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": TEMPERATURE,
            # force the end of reasoning after 500 thinking tokens (llama.cpp
            # per-request field on /v1/chat/completions; the CLI flag is
            # --reasoning-budget, but the JSON field is thinking_budget_tokens)
            "thinking_budget_tokens": 1200,
        }
        assert self.session
        self.api_down = False
        log.info("LLM request: %d messages, max_tokens=%d, last=%r", len(messages), max_tokens, messages[-1]["content"][:120])
        try:
            async with self.session.post(
                f"{API_BASE}/chat/completions", json=payload, timeout=aiohttp.ClientTimeout(total=120)
            ) as resp:
                if resp.status != 200:
                    log.error("LLM API %s: %s", resp.status, (await resp.text())[:300])
                    self.api_down = True
                    return None
                data = await resp.json()
                log.debug("LLM full JSON: %s", str(data)[:1500])
                msg = data["choices"][0]["message"]
                content = msg.get("content") or ""
                # some backends leave think tags inline; keep only what follows them
                think_close = "<" + "/" + "think" + ">"
                if think_close in content:
                    content = content.split(think_close)[-1]
                content = content.strip()
                reasoning = (msg.get("reasoning_content") or msg.get("reasoning") or "").strip()
                finish = data["choices"][0].get("finish_reason")
                if finish == "length":
                    log.warning("LLM hit token limit (max_tokens=%d) while %s", max_tokens, "thinking" if not content else "writing")
                if not content:
                    # never post raw reasoning to Discord; empty means no usable answer
                    log.warning("LLM: no answer content (reasoning had %d chars, finish_reason=%s)", len(reasoning), finish)
                    return None
                log.info("LLM response: %r", content[:300])
                self.api_down = False
                return content
        except Exception:
            log.exception("LLM request failed")
            self.api_down = True
            return None

    async def decide(self, history: list[dict]) -> bool:
        log.info("Decider: asking (%d history messages)", len(history))
        reply = await self.chat(
            [
                {"role": "system", "content": DECIDER_PROMPT},
                {"role": "user", "content": "\n".join(m["content"] for m in history)},
            ]
        )
        decision = bool(reply) and any(w in (reply or "").upper() for w in YES_WORDS)
        log.info("Decider: raw=%r -> %s", reply, decision)
        return decision

    # ---- Discord events ----

    async def on_message(self, message: discord.Message):
        author = message.author
        log.info("MSG in: guild=%s channel=%s author=%s content=%r mentions=%s",
                 message.guild and message.guild.id, message.channel.id,
                 author, message.content[:200], [str(m) for m in message.mentions])
        if author.bot:
            log.debug("MSG skip: bot author")
            return
        if not self.enabled:
            log.info("MSG skip: disabled")
            return
        if message.guild is None or message.guild.id != GUILD_ID:
            log.debug("MSG skip: wrong guild")
            return
        ch_id = message.channel.id
        resolved = message.reference.resolved if message.reference else None
        ref_author = getattr(resolved, "author", resolved)
        pinged = self.user in message.mentions or ref_author is self.user

        if ch_id not in load_channels():
            if not pinged:
                log.debug("MSG skip: channel not allowed (allowed=%s temp=%s)", load_channels(), self.temp_channels)
                return
            self.temp_channels.add(ch_id)
            log.info("MSG pinged: temporary channel join")

        log.info("MSG gated in: pinged=%s muted=%s", pinged, ch_id in self.muted_channels)

        if pinged:
            self.muted_channels.discard(ch_id)
            log.info("MSG pinged: unmuted + will reply")
        elif ch_id in self.muted_channels:
            log.info("MSG skip: channel muted")
            return

        # one worker per channel: a new message cancels any in-flight
        # decision/reply and the worker starts over with the latest state
        old = self.workers.get(ch_id)
        if old and not old.done():
            log.info("WORKER: cancelling in-flight task for channel %s (superseded)", ch_id)
            old.cancel()
            try:
                await old
            except asyncio.CancelledError:
                pass
        self.workers[ch_id] = asyncio.create_task(self.handle_message(message))

    async def handle_message(self, message: discord.Message):
        ch_id = message.channel.id
        resolved = message.reference.resolved if message.reference else None
        pinged = self.user in message.mentions or (
            resolved is not None and getattr(resolved, "author", resolved) is self.user
        )
        try:
            if not pinged:
                # free to interject; ask the decider first (no typing indicator yet)
                history = await self.fetch_history(message.channel, limit=HISTORY_DEPTH)
                if not await self.decide(history):
                    log.info("MSG skip: decider said no")
                    return
                log.info("MSG decider said yes: will reply")
            await self.reply_in_thread(message, reply_to=resolved is not None)
        except asyncio.CancelledError:
            log.info("WORKER: task for channel %s cancelled", ch_id)
            raise

    async def reply_in_thread(self, message: discord.Message, reply_to: bool = False):
        ch_id = message.channel.id
        resolved = message.reference.resolved if message.reference else None
        pinged = self.user in message.mentions or (
            resolved is not None and getattr(resolved, "author", resolved) is self.user
        )
        log.info("REPLY: fetching history + system prompt")
        reply = None
        try:
            async with message.channel.typing():
                history = await self.fetch_history(message.channel, limit=HISTORY_DEPTH)
                reply = await self.chat(
                    [{"role": "system", "content": load_system_prompt()}] + history
                )
        except Exception:
            # never let the worker die silently before the fallback below runs
            log.exception("REPLY: history/chat blew up")
            self.api_down = True
        if reply:
            self.last_activity[ch_id] = time.monotonic()
            try:
                if reply_to:
                    log.info("REPLY: sending reply to message %s", message.id)
                    await message.reply(reply, mention_author=False)
                else:
                    log.info("REPLY: sending message to channel %s", ch_id)
                    await message.channel.send(reply)
            except Exception:
                log.exception("REPLY: sending the reply failed")
        elif pinged:
            # any failure of the chat call counts as the API being down;
            # when directly pinged, say so in the channel instead of silence
            log.error("REPLY: LLM unavailable (api_down=%s), telling the channel (pinged)", self.api_down)
            try:
                await message.reply(
                    "Είμαι απασχολημένος με το ιγκλας. Ξαναρώτα με σε λίγο.",
                    mention_author=False,
                )
            except Exception:
                log.exception("REPLY: sending the fallback failed")
        else:
            log.error("REPLY: LLM returned nothing, not sending")

    async def fetch_history(self, channel, limit: int) -> list[dict]:
        msgs = [m async for m in channel.history(limit=limit)]
        msgs.reverse()
        out = []
        for m in msgs:
            if m.author.bot:
                role = "assistant"
            else:
                role = "user"
            content = f"{m.author.display_name}: {m.content}" if role == "user" else m.content
            out.append({"role": role, "content": content})
        log.debug("HISTORY: %d messages fetched", len(out))
        return out

    async def on_error(self, event_method: str, *args, **kwargs):
        log.exception("Unhandled error in %s", event_method)

    # ---- slash commands ----

    def register_commands(self):
        @self.tree.command(name="epav", description="E-Pavlidis controls", guild=discord.Object(id=GUILD_ID))
        @app_commands.describe(action="What to do")
        @app_commands.choices(
            action=[
                app_commands.Choice(name="stop", value="stop"),
                app_commands.Choice(name="status", value="status"),
                app_commands.Choice(name="disable", value="disable"),
                app_commands.Choice(name="enable", value="enable"),
            ]
        )
        async def epav(interaction: discord.Interaction, action: str):
            log.info("CMD /epav %s from %s in channel %s", action, interaction.user, interaction.channel_id)
            if action in ("disable", "enable"):
                ADMIN_ROLE = ADMIN_ROLE_ID
                if ADMIN_ROLE not in [r.id for r in getattr(interaction.user, "roles", [])]:
                    await interaction.response.send_message("Not allowed.", ephemeral=True)
                    return
                self.enabled = action == "enable"
                if not self.enabled:
                    self.muted_channels.clear()
                    self.temp_channels.clear()
                    self.last_activity.clear()
                await interaction.response.send_message(
                    "\U0001F910 Παω για ύπνο. Βάρα τα μικρομαλακά παράθυρα άμα με χρειαστείς."
                    if not self.enabled else "Ξύπνησα."
                )
                return
            if action == "stop":
                self.muted_channels.add(interaction.channel_id)
                self.temp_channels.discard(interaction.channel_id)
                self.last_activity.pop(interaction.channel_id, None)
                await interaction.response.send_message(
                    "\U0001F910 Παω για ύπνο. Βάρα τα μικρομαλακά παράθυρα άμα με χρειαστείς."
                )
            elif action == "status":
                if not self.enabled:
                    state = "disabled"
                elif interaction.channel_id in self.muted_channels:
                    state = "silenced (ping me to wake me up)"
                elif interaction.channel_id in self.last_activity:
                    elapsed = int(time.monotonic() - self.last_activity[interaction.channel_id])
                    state = f"ΑΑΑΑ (last activity {elapsed}s ago)"
                else:
                    state = "Νανι νάνι"
                await interaction.response.send_message(f"status ok - {state}")


def main():
    client = Epavlidis()
    client.run(TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
