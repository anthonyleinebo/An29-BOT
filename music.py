# music.py
"""
Music subsystem: queue, track extraction (yt-dlp), and voice playback.

- Extracts best Opus/PCM streams via yt-dlp.
- Prefers Opus with a bitrate cap based on channel bitrate; falls back to PCM.
- Provides a per-guild MusicPlayer with an asyncio queue and an idle disconnect.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Optional

import discord
from discord import VoiceClient
from yt_dlp import YoutubeDL

# yt-dlp configuration: prioritize Opus in WebM, fallback to best
YTDL_OPTS = {
    "format": "bestaudio[ext=webm][acodec=opus]/bestaudio/best",
    "quiet": True,
    "default_search": "ytsearch",
    "noplaylist": True,
    "source_address": "0.0.0.0",
    "extract_flat": False,
    "nocheckcertificate": True,  # reduces SSL fuss on some hosts/networks
}
_ytdl = YoutubeDL(YTDL_OPTS)


def ffmpeg_volume_filter(vol: float) -> str:
    """Return an FFmpeg volume filter for 0.0–1.5 (empty if ~1.0)."""
    v = max(0.0, min(1.5, vol))
    return f'-filter:a "volume={v}"' if abs(v - 1.0) > 1e-3 else ""


@dataclass
class Track:
    """Represents a playable audio track."""
    title: str
    url: str            # YouTube watch URL (or original query if not resolved)
    stream_url: str     # Direct media URL for FFmpeg
    duration: Optional[int] = None
    requester: Optional[discord.Member] = None

    @classmethod
    async def create(cls, query: str, requester: Optional[discord.Member] = None) -> "Track":
        """
        Resolve a query (URL or search) to a playable stream with yt-dlp.
        Falls back to alternate YouTube clients if SABR/PO tokens get in the way.
        """
        loop = asyncio.get_running_loop()

        def _extract(q, opts=None):
            if opts is None:
                return _ytdl.extract_info(q, download=False)
            with YoutubeDL({**YTDL_OPTS, **opts}) as alt:
                return alt.extract_info(q, download=False)

        data = await loop.run_in_executor(None, lambda: _extract(query))

        if "entries" in data:
            # search result or playlist entry: pick the first valid hit
            data = next((e for e in data["entries"] if e), None)
            if data is None:
                raise RuntimeError("Fant ingen treff.")

        stream = data.get("url")

        # Fallback: try alternate player clients (ios/tv) in case of token issues
        if not stream:
            alt_opts = {"extractor_args": {"youtube": {"player_client": ["ios", "tv"]}}}
            data = await loop.run_in_executor(None, lambda: _extract(query, alt_opts))
            if "entries" in data:
                data = next((e for e in data["entries"] if e), None)
                if data is None:
                    raise RuntimeError("Fant ingen treff (fallback).")
            stream = data.get("url")
            if not stream:
                raise RuntimeError("Kunne ikke hente direkte lyd-URL (SABR/PO-token).")

        return cls(
            title=data.get("title", "Ukjent tittel"),
            url=data.get("webpage_url", query),
            stream_url=stream,
            duration=int(data["duration"]) if data.get("duration") else None,
            requester=requester,
        )


class MusicPlayer:
    """
    Per-guild music player with:
    - asyncio.Queue for tracks
    - single playback task
    - idle disconnect timer
    """
    def __init__(self, guild: discord.Guild):
        self.guild = guild
        self.queue: asyncio.Queue[Track] = asyncio.Queue()
        self.next_event = asyncio.Event()
        self.current: Optional[Track] = None
        self.player_task: Optional[asyncio.Task] = None

        # defaults
        self.volume = 0.35
        self.idle_disconnect_after = 900  # seconds

    # ---- Public API used by bot.py ----
    async def enqueue(self, track: Track):
        await self.queue.put(track)
        print(f"🎵 Enqueued: {track.title}")
        self.ensure_task()

    async def skip(self):
        vc = self._voice
        if vc and vc.is_playing():
            vc.stop()
        self.next_event.set()

    async def stop(self, disconnect: bool = True):
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.next_event.set()

        vc = self._voice
        if vc and vc.is_connected():
            vc.stop()
            if disconnect:
                await vc.disconnect(force=True)

    async def pause(self):
        vc = self._voice
        if vc and vc.is_playing():
            vc.pause()

    async def resume(self):
        vc = self._voice
        if vc and vc.is_paused():
            vc.resume()

    async def set_volume(self, vol: float):
        """Set logical volume in [0.0, 1.5]. Opus path uses FFmpeg filter, PCM path uses transformer."""
        self.volume = max(0.0, min(1.5, vol))

    def ensure_task(self):
        if self.player_task is None or self.player_task.done():
            self.player_task = asyncio.create_task(self._player_loop(), name=f"player-{self.guild.id}")

    # ---- Voice helpers ----
    @property
    def _voice(self) -> Optional[VoiceClient]:
        return self.guild.voice_client

    async def connect(self, channel: discord.VoiceChannel) -> VoiceClient:
        """
        Connect to/move to the given voice channel.
        Returns the VoiceClient once connected and ready.
        """
        vc = self._voice
        if vc and vc.channel and vc.channel.id == channel.id and vc.is_connected():
            return vc

        if vc and vc.is_connected():
            await vc.move_to(channel, timeout=10)
        else:
            vc = await channel.connect(self_deaf=True, timeout=10, reconnect=False)

        # wait briefly for the state to settle (voice handshake)
        for _ in range(15):  # ~3 seconds total
            await asyncio.sleep(0.2)
            if vc.is_connected():
                break

        if not vc or not vc.is_connected():
            raise RuntimeError("Voice handshake failed (mulig brannmur/UDP blokkerer).")

        return vc

    # ---- Core playback loop ----
    async def _player_loop(self):
        # ensure we are self-deaf to reduce echo/noise
        if self._voice:
            try:
                await self.guild.change_voice_state(
                    channel=self._voice.channel,
                    self_mute=False,
                    self_deaf=True
                )
            except Exception:
                pass

        idle_timer: Optional[asyncio.Task] = None

        def start_idle_timer():
            nonlocal idle_timer
            if idle_timer and not idle_timer.done():
                return
            idle_timer = asyncio.create_task(self._idle_disconnect_task())

        def cancel_idle_timer():
            nonlocal idle_timer
            if idle_timer and not idle_timer.done():
                idle_timer.cancel()
                idle_timer = None

        start_idle_timer()

        while True:
            self.next_event.clear()
            self.current = await self.queue.get()
            cancel_idle_timer()

            vc = self._voice
            if not vc or not vc.is_connected():
                self.current = None
                start_idle_timer()
                continue

            done_event = asyncio.Event()

            def after_play(err: Optional[Exception]):
                if err:
                    print(f"[player] FFmpeg error: {err}")
                done_event.set()

            # Choose a target Opus bitrate capped by channel bitrate
            channel_bps = getattr(vc.channel, "bitrate", 128_000) or 128_000
            cap_kbps = max(64, min(256, channel_bps // 1000))
            target_kbps = 192 if cap_kbps >= 192 else cap_kbps

            async def start_opus() -> bool:
                try:
                    vol_filter = ffmpeg_volume_filter(self.volume)
                    src = discord.FFmpegOpusAudio(
                        self.current.stream_url,
                        bitrate=target_kbps,
                        before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                        options=f"-vn -ac 2 -ar 48000 -loglevel warning {vol_filter}".strip()
                    )
                    print(f"▶️ Spiller nå (Opus {target_kbps}k): {self.current.title}")
                    vc.play(src, after=after_play)
                    return True
                except Exception as e:
                    print(f"[player] Opus-start feilet: {e}")
                    return False

            async def start_pcm() -> bool:
                try:
                    pcm = discord.FFmpegPCMAudio(
                        self.current.stream_url,
                        before_options="-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                        options="-vn -ac 2 -ar 48000 -loglevel warning"
                    )
                    src = discord.PCMVolumeTransformer(pcm, volume=float(min(self.volume, 1.0)))
                    print(f"▶️ Spiller nå (PCM fallback): {self.current.title}")
                    vc.play(src, after=after_play)
                    return True
                except Exception as e:
                    print(f"[player] PCM-start feilet: {e}")
                    return False

            if not await start_opus():
                if not await start_pcm():
                    print("[player] Ingen avspilling lyktes.")
                    self.current = None
                    start_idle_timer()
                    continue

            # Give FFmpeg/voice pipeline a moment to start
            await asyncio.sleep(2.0)
            if not vc.is_playing():
                print("[player] Opus stoppet for tidlig / ikke i gang → bytter til PCM.")
                if vc.is_playing() or vc.is_paused():
                    vc.stop()
                if not await start_pcm():
                    print("[player] PCM fallback feilet.")
                    self.current = None
                    start_idle_timer()
                    continue

            # Wait for either playback end or a skip request
            done_waiter = asyncio.create_task(done_event.wait())
            skip_waiter = asyncio.create_task(self.next_event.wait())
            done, pending = await asyncio.wait({done_waiter, skip_waiter}, return_when=asyncio.FIRST_COMPLETED)
            for p in pending:
                p.cancel()

            if skip_waiter in done:
                if vc.is_playing() or vc.is_paused():
                    vc.stop()
                print("⏭️ Skippet sangen.")
            else:
                print("✅ Ferdig med sangen.")

            self.current = None
            start_idle_timer()

    async def _idle_disconnect_task(self):
        """Disconnect from voice after a long idle period."""
        try:
            await asyncio.sleep(self.idle_disconnect_after)
            if not self.current and self.queue.empty():
                vc = self._voice
                if vc and vc.is_connected():
                    print("💤 Idle lenge – kobler fra VC.")
                    await vc.disconnect(force=True)
        except asyncio.CancelledError:
            pass


class PlayerPool(dict[int, MusicPlayer]):
    """Simple registry for per-guild MusicPlayer instances."""
    def get_player(self, guild: discord.Guild) -> MusicPlayer:
        if guild.id not in self:
            self[guild.id] = MusicPlayer(guild)
        return self[guild.id]
