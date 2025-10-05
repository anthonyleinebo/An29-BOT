# bot.py
"""
Discord music bot application entrypoint.

- Keeps user-facing strings in Norwegian (server audience),
  while comments and docstrings are in English.
- Adds light input validation, permission checks, and clearer error messages.
- Introduces small cooldowns to avoid accidental spam.
"""

from __future__ import annotations

import os
import asyncio
import itertools
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from music import PlayerPool, Track

# --- Load .env and token ---
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# --- Intents ---
INTENTS = discord.Intents.default()
INTENTS.guilds = True
INTENTS.voice_states = True  # needed to see/join voice channels

# --- Bot / Command tree ---
bot = commands.Bot(command_prefix="!", intents=INTENTS)
tree = bot.tree
players = PlayerPool()


# -------------------------- Helpers --------------------------
def fmt_duration(seconds: Optional[int]) -> str:
    """Format a duration in seconds to h:mm:ss or m:ss. Returns 'live' on None/0."""
    if not seconds:
        return "live"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


async def get_user_voice_channel(interaction: discord.Interaction) -> Optional[discord.VoiceChannel]:
    """Return the user's current voice channel in this guild, or None."""
    assert interaction.guild
    user = interaction.user
    if isinstance(user, discord.Member) and user.voice and user.voice.channel:
        return user.voice.channel
    return None


def bot_has_connect_speak(interaction: discord.Interaction, channel: discord.VoiceChannel) -> bool:
    """Check that the bot has Connect and Speak permissions in a given channel."""
    guild = interaction.guild
    me = guild.get_member(bot.user.id) if (guild and bot.user) else None
    if not me:
        return False
    perms = channel.permissions_for(me)
    return perms.connect and perms.speak


# -------------------------- Commands --------------------------
@tree.command(name="ping", description="Test at boten svarer raskt.")
async def ping(interaction: discord.Interaction):
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong! `{latency_ms} ms`", ephemeral=True)


@tree.command(name="join", description="Bli med i talekanalen din (uten å starte avspilling).")
async def join(interaction: discord.Interaction):
    if not interaction.guild:
        return

    await interaction.response.defer(ephemeral=True)

    channel = await get_user_voice_channel(interaction)
    if not channel:
        await interaction.followup.send("❌ Du må være i en talekanal for å bruke denne kommandoen.", ephemeral=True)
        return

    if not bot_has_connect_speak(interaction, channel):
        await interaction.followup.send("🚫 Jeg mangler **Connect**/**Speak** i denne talekanalen.", ephemeral=True)
        return

    player = players.get_player(interaction.guild)
    try:
        vc = await player.connect(channel)
        await interaction.followup.send(f"🔊 Koblet til **{vc.channel.name}**.", ephemeral=True)
    except discord.errors.Forbidden:
        await interaction.followup.send("🚫 Mangler tillatelser i denne talekanalen (trenger **Connect** og **Speak**).", ephemeral=True)
    except asyncio.TimeoutError:
        await interaction.followup.send("⏳ Tidsavbrudd ved tilkobling. Sjekk nettverk eller prøv igjen.", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"⚠️ Kunne ikke koble til: `{e}`", ephemeral=True)


@tree.command(name="play", description="Spill av en sang fra YouTube (lenke eller søk).")
@app_commands.describe(query="YouTube-lenke eller søk (f.eks. 'lofi hip hop')")
@app_commands.checks.cooldown(2, 5.0)  # 2 uses per 5s per-user (mild anti-spam)
async def play(interaction: discord.Interaction, query: str):
    if not interaction.guild:
        return

    await interaction.response.defer(thinking=True)

    channel = await get_user_voice_channel(interaction)
    if not channel:
        await interaction.followup.send("❌ Du må være i en talekanall for å bruke denne kommandoen.", ephemeral=True)
        return

    if not bot_has_connect_speak(interaction, channel):
        await interaction.followup.send("🚫 Jeg mangler **Connect**/**Speak** i denne talekanalen.", ephemeral=True)
        return

    player = players.get_player(interaction.guild)
    await player.connect(channel)

    try:
        req_member = interaction.user if isinstance(interaction.user, discord.Member) else None
        track = await Track.create(query, requester=req_member)
    except Exception as e:
        await interaction.followup.send(f"❌ Fikk ikke hentet lydkilde: `{e}`")
        return

    await player.enqueue(track)

    embed = discord.Embed(
        title="➕ Lagt til i kø",
        description=f"[{track.title}]({track.url})",
        color=discord.Color.blurple(),
    )
    if track.duration:
        embed.add_field(name="Lengde", value=fmt_duration(track.duration), inline=True)
    if isinstance(track.requester, discord.Member):
        embed.set_footer(text=f"Ønsket av {track.requester.display_name}")

    await interaction.followup.send(embed=embed)


@tree.command(name="queue", description="Se hva som spiller og køen videre.")
async def queue_cmd(interaction: discord.Interaction):
    if not interaction.guild:
        return

    player = players.get_player(interaction.guild)
    current = player.current
    items = list(player.queue._queue)  # safe view; asyncio.Queue uses a deque internally

    desc = ""
    if current:
        dur = fmt_duration(current.duration) if current.duration else "live"
        desc += f"**▶️ Spiller nå:** [{current.title}]({current.url}) — `{dur}`\n\n"
    else:
        desc += "_Ingen sang spiller nå._\n\n"

    if items:
        lines = []
        for i, t in enumerate(itertools.islice(items, 10), start=1):
            d = fmt_duration(t.duration) if t.duration else "live"
            lines.append(f"`{i:02d}.` [{t.title}]({t.url}) — `{d}`")
        more = len(items) - 10
        if more > 0:
            lines.append(f"... og **{more}** til")
        desc += "📜 **Kø:**\n" + "\n".join(lines)
    else:
        desc += "📜 _Køen er tom._"

    await interaction.response.send_message(embed=discord.Embed(description=desc, color=discord.Color.dark_blue()))


@tree.command(name="skip", description="Hopp over nåværende sang.")
@app_commands.checks.cooldown(2, 5.0)
async def skip(interaction: discord.Interaction):
    if not interaction.guild:
        return
    player = players.get_player(interaction.guild)
    if not player.current:
        await interaction.response.send_message("⚠️ Det spilles ingenting akkurat nå.", ephemeral=True)
        return
    await player.skip()
    await interaction.response.send_message("⏭️ Skipper sangen.")


@tree.command(name="stop", description="Stopp musikken og forlat talekanalen.")
async def stop(interaction: discord.Interaction):
    if not interaction.guild:
        return
    player = players.get_player(interaction.guild)
    await player.stop(disconnect=True)
    await interaction.response.send_message("⏹️ Stoppet og forlot talekanalen.")


@tree.command(name="pause", description="Pause musikken.")
async def pause(interaction: discord.Interaction):
    if not interaction.guild:
        return
    player = players.get_player(interaction.guild)
    await player.pause()
    await interaction.response.send_message("⏸️ Pauset.")


@tree.command(name="resume", description="Fortsett musikken etter pause.")
async def resume(interaction: discord.Interaction):
    if not interaction.guild:
        return
    player = players.get_player(interaction.guild)
    await player.resume()
    await interaction.response.send_message("▶️ Fortsetter.")


@tree.command(name="volume", description="Sett volum (0.0 til 1.5).")
async def volume(interaction: discord.Interaction, value: float):
    if not interaction.guild:
        return
    if value < 0 or value > 1.5:
        await interaction.response.send_message("⚠️ Volum må være mellom 0.0 og 1.5.", ephemeral=True)
        return
    player = players.get_player(interaction.guild)
    await player.set_volume(value)
    await interaction.response.send_message(f"🔊 Volum satt til {value:.2f} (gjelder fra neste sang).")


@tree.command(name="help", description="Vis en oversikt over alle kommandoene.")
async def help_cmd(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 An29 BOT – Hjelp",
        description="Her er en oversikt over kommandoene:",
        color=discord.Color.blurple()
    )
    embed.add_field(name="▶️ /play <søk eller lenke>", value="Spill en sang fra YouTube.", inline=False)
    embed.add_field(name="📜 /queue", value="Se hva som spilles nå og resten av køen.", inline=False)
    embed.add_field(name="⏭️ /skip", value="Hopp over sangen.", inline=False)
    embed.add_field(name="⏹️ /stop", value="Stopp musikken og koble fra voice.", inline=False)
    embed.add_field(name="⏸️ /pause", value="Pause avspillingen.", inline=False)
    embed.add_field(name="▶️ /resume", value="Fortsett etter pause.", inline=False)
    embed.add_field(name="🔊 /volume <0.0–1.5>", value="Juster volumet (gjelder fra neste sang).", inline=False)
    embed.add_field(name="🔗 /join", value="Koble boten til voicechannel.", inline=False)
    embed.add_field(name="🏓 /ping", value="Test responstid (Pong!).", inline=False)
    embed.add_field(name="ℹ️ /help", value="Viser denne oversikten.", inline=False)

    if bot.user and bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)
        embed.set_footer(text="@anthonyleinebo – meld features/bugs 😎")
    else:
        embed.set_footer(text="@anthonyleinebo – meld features/bugs 😎")

    await interaction.response.send_message(embed=embed, ephemeral=True)


# -------------------------- Event hooks --------------------------
@bot.event
async def on_ready():
    try:
        await tree.sync()
        print(f"Synced {len(tree.get_commands())} app commands.")
    except Exception as e:
        print("Kunne ikke sync'e slash-commands:", e)
    if bot.user:
        print(f"Logget inn som {bot.user} (ID: {bot.user.id})")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: Exception):
    """
    Global handler for app command errors (e.g., cooldowns).
    Tries to reply ephemerally to avoid cluttering channels.
    """
    try:
        msg = None
        if isinstance(error, app_commands.CommandOnCooldown):
            msg = f"⌛ Litt kjapp der! Prøv igjen om `{error.retry_after:.1f}s`."
        elif isinstance(error, app_commands.CheckFailure):
            msg = "🚫 Du har ikke tilgang til å bruke denne kommandoen."
        else:
            msg = f"⚠️ Feil: {error}"

        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass  # swallow to avoid secondary exceptions in error path
    import traceback
    traceback.print_exception(error)


# -------------------------- Main --------------------------
if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("❌ Manglende DISCORD_TOKEN i .env")
    # Discord.py handles loop lifecycle internally
    bot.run(TOKEN, log_handler=None)
