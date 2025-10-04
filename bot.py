# bot.py
import os
import asyncio

import itertools
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from music import PlayerPool, Track

# --- Last inn .env ---
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# --- Intents ---
INTENTS = discord.Intents.default()
INTENTS.guilds = True
INTENTS.voice_states = True

bot = commands.Bot(command_prefix="!", intents=INTENTS)
tree = bot.tree
players = PlayerPool()


# --- Hjelpefunksjoner ---
def fmt_duration(seconds: Optional[int]) -> str:
    if not seconds:
        return "live"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


async def get_user_voice_channel(interaction: discord.Interaction) -> Optional[discord.VoiceChannel]:
    """Returner brukerens voice-kanal, eller None uten å svare i interaksjonen."""
    assert interaction.guild
    user = interaction.user
    if isinstance(user, discord.Member) and user.voice and user.voice.channel:
        return user.voice.channel
    return None


# --- Kommandoer ---
@tree.command(name="ping", description="Test at boten svarer raskt.")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("🏓 Pong!", ephemeral=True)


@tree.command(name="join", description="Bli med i talekanalen din (uten å starte avspilling).")
async def join(interaction: discord.Interaction):
    if not interaction.guild:
        return

    await interaction.response.defer(ephemeral=True, thinking=False)

    channel = await get_user_voice_channel(interaction)
    if not channel:
        await interaction.followup.send("❌ Du må være i en talekanal for å bruke denne kommandoen.", ephemeral=True)
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
async def play(interaction: discord.Interaction, query: str):
    if not interaction.guild:
        return

    await interaction.response.defer(thinking=True)

    channel = await get_user_voice_channel(interaction)
    if not channel:
        await interaction.followup.send("❌ Du må være i en talekanal for å bruke denne kommandoen.", ephemeral=True)
        return

    player = players.get_player(interaction.guild)
    await player.connect(channel)

    try:
        track = await Track.create(query, requester=interaction.user if isinstance(interaction.user, discord.Member) else None)
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
    items = list(player.queue._queue)

    desc = ""
    if current:
        desc += f"**▶️ Spiller nå:** [{current.title}]({current.url}) — `{fmt_duration(current.duration)}`\n\n"
    else:
        desc += "_Ingen sang spiller nå._\n\n"

    if items:
        lines = []
        for i, t in enumerate(itertools.islice(items, 10), start=1):
            lines.append(f"`{i:02d}.` [{t.title}]({t.url}) — `{fmt_duration(t.duration)}`")
        more = len(items) - 10
        if more > 0:
            lines.append(f"... og **{more}** til")
        desc += "📜 **Kø:**\n" + "\n".join(lines)
    else:
        desc += "📜 _Køen er tom._"

    await interaction.response.send_message(embed=discord.Embed(description=desc, color=discord.Color.dark_blue()))


@tree.command(name="skip", description="Hopp over nåværende sang.")
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
    embed.add_field(name="⏭️ /skip", value="Hopp over sangen", inline=False)
    embed.add_field(name="⏹️ /stop", value="Stopp musikken og koble fra voice.", inline=False)
    embed.add_field(name="⏸️ /pause", value="Pause avspillingen.", inline=False)
    embed.add_field(name="▶️ /resume", value="Fortsett etter pause.", inline=False)
    embed.add_field(name="🔊 /volume <0.0–1.5>", value="Juster volumet (gjelder fra neste sang).", inline=False)
    embed.add_field(name="🔗 /join", value="Koble boten til voicechannel", inline=False)
    embed.add_field(name="🏓 /ping", value="🏓 PONG!", inline=False)
    embed.add_field(name="ℹ️ /help", value="Viser detette", inline=False)

    if bot.user and bot.user.avatar:
        embed.set_thumbnail(url=bot.user.avatar.url)
        embed.set_footer(
            text="@anthonyleinebo om du har features eller fant en feil 😎",
            icon_url="https://cdn.discordapp.com/avatars/163711118357823488/94e589444fb113b5bf5180a18f4d72b0.webp?size=128"
        )
    else:
        embed.set_footer(text="@anthonyleinebo om du har features eller fant en feil 😎")

    await interaction.response.send_message(embed=embed, ephemeral=True)





# --- Event hooks ---
@bot.event
async def on_ready():
    try:
        await tree.sync()
        print(f"Synced {len(tree.get_commands())} app commands.")
    except Exception as e:
        print("Kunne ikke sync'e slash-commands:", e)
    print(f"Logget inn som {bot.user} (ID: {bot.user.id})")


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: Exception):
    try:
        if interaction.response.is_done():
            await interaction.followup.send(f"⚠️ Feil: {error}", ephemeral=True)
        else:
            await interaction.response.send_message(f"⚠️ Feil: {error}", ephemeral=True)
    except Exception:
        pass
    import traceback
    traceback.print_exception(error)

# --- Main ---
if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("❌ Manglende DISCORD_TOKEN i .env")
    bot.run(TOKEN, log_handler=None)
