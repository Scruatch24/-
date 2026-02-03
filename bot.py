import discord
from discord.ext import commands, tasks
from discord import app_commands
import yt_dlp
import os
from dotenv import load_dotenv
import asyncio
import imageio_ffmpeg
import time
import collections
import json

load_dotenv()

import pymongo
# Use MongoDB for persistence on Render
MONGO_URI = os.getenv('MONGO_URI')
if MONGO_URI:
    mongo_client = pymongo.MongoClient(MONGO_URI)
    db = mongo_client['music_bot']
    playlists_col = db['playlists']
else:
    playlists_col = None

from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "Bot is running!"

def run_flask():
    # Render specifies the port in the PORT environment variable
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

TOKEN = os.getenv('DISCORD_TOKEN')
PLAYLIST_FILE = 'playlists.json'

def load_playlists():
    if playlists_col is not None:
        try:
            doc = playlists_col.find_one({"_id": "global_playlists"})
            if doc:
                return doc.get('data', {})
        except Exception as e:
            print(f"MongoDB Load Error: {e}")
            
    # Fallback to local file for development or if DB fails
    if os.path.exists(PLAYLIST_FILE):
        try:
            with open(PLAYLIST_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_playlists(data):
    if playlists_col is not None:
        try:
            playlists_col.update_one(
                {"_id": "global_playlists"},
                {"$set": {"data": data}},
                upsert=True
            )
            return
        except Exception as e:
            print(f"MongoDB Save Error: {e}")

    # Fallback/Local
    with open(PLAYLIST_FILE, 'w') as f:
        json.dump(data, f, indent=4)

# Suppress noise about console usage from errors
yt_dlp.utils.bug_reports_message = lambda *args, **kwargs: ''

ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'extract_flat': False, # Ensure we get full info for audio quality selection
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
}

# Base ffmpeg options
ffmpeg_options = {
    'options': '-vn',
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -reconnect_at_eof 1'
}

ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

import re
import aiohttp

# ... (Previous imports)

# Helper to resolve Spotify tracks to YouTube search queries
async def resolve_spotify_track(query):
    if "open.spotify.com/track" in query:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(query) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        # Extract title from meta tags or title tag
                        # <meta property="og:title" content="Song Name" />
                        # <meta property="og:description" content="Artist ... " />
                        # Simple Title tag fallback: <title>Song - Artist | Spotify</title>
                        
                        m = re.search(r'<title>(.*?) \| Spotify</title>', text) or re.search(r'<title>(.*?)</title>', text)
                        if m:
                            title = m.group(1)
                            # Clean up common spotify title suffixes
                            title = title.replace(" - song by", "")
                            title = title.replace(" | Spotify", "")
                            return f"ytsearch:{title}"
        except Exception as e:
            print(f"Failed to resolve Spotify link: {e}")
    return query

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.duration = data.get('duration')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False, start_time=0):
        loop = loop or asyncio.get_event_loop()
        
        # Resolve Spotify links first
        url = await resolve_spotify_track(url)
        
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        
        ffmpeg_executable = imageio_ffmpeg.get_ffmpeg_exe()
        
        # Add seeking if needed
        options = ffmpeg_options.copy()
        if start_time > 0:
            options['before_options'] += f' -ss {start_time}'

        return cls(discord.FFmpegPCMAudio(filename, executable=ffmpeg_executable, **options), data=data)

# Helper for progress bar
def create_progress_bar(current, total, length=20):
    if not total:
        return ""
    
    # Snap to end if within last 3 seconds
    if current >= total - 3:
        progress = 1.0
    else:
        progress = min(1, max(0, current / total))

    filled_length = int(length * progress)
    empty_length = length - filled_length
    
    bar = "▬" * filled_length + "🔘" + "▬" * empty_length
    return f"[{bar}] {format_time(current)} / {format_time(total)}"

def format_time(seconds):
    if not seconds:
        return "00:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

# View for Music Controls
class MusicControls(discord.ui.View):
    def __init__(self, bot, guild_id, looping=False):
        super().__init__(timeout=None)
        self.bot = bot
        self.guild_id = guild_id
        
        # Set initial state of loop button
        self.loop_button.style = discord.ButtonStyle.success if looping else discord.ButtonStyle.secondary

    @discord.ui.button(label="⏪ -10s", style=discord.ButtonStyle.secondary)
    async def rewind(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.bot.get_cog("Music").seek(interaction, -10)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.bot.get_cog("Music").stop_music(interaction)

    @discord.ui.button(label="+10s ⏩", style=discord.ButtonStyle.secondary)
    async def forward(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.bot.get_cog("Music").seek(interaction, 10)
        
    @discord.ui.button(label="Skip ⏭️", style=discord.ButtonStyle.primary)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.bot.get_cog("Music").skip_song(interaction)

    # Assign button to variable so we can modify it in __init__
    @discord.ui.button(label="🔁 Loop", style=discord.ButtonStyle.secondary)
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
         await self.bot.get_cog("Music").toggle_loop(interaction, self, button)

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.queues = collections.defaultdict(list) # guild_id -> list of song queries/urls
        self.current_song = {} # guild_id -> {url, title, start_timestamp, current_position, duration, message}
        self.voice_states = {} # guild_id -> bool (is_playing)
        self.looping = {} # guild_id -> bool
        self.update_progress.start()

    def cog_unload(self):
        self.update_progress.cancel()

    @tasks.loop(seconds=1.0)
    async def update_progress(self):
        # We use a copy of items to avoid runtime errors if dictionary changes during iteration
        for guild_id, info in list(self.current_song.items()):
            message = info.get('message')
            if not message:
                continue
            
            # Don't overwrite if we are not in Playing state (e.g. Skipped/Stopped/Finished)
            # The cleanup_song method handles the final update.
            if info.get('status') != 'Playing':
                continue
                
            try:
                # Calculate current position
                now = time.time()
                elapsed = now - info['start_timestamp']
                current_position = info['seek_position'] + elapsed
                
                # Check if song is basically over
                if current_position > info.get('duration', 0) + 2:
                    continue

                msg_content = f"**Now playing:** {info['title']}\n{create_progress_bar(current_position, info.get('duration'))}"
                
                # Only edit if content changed significantly (usually every second it does)
                # We put a try/except specifically for the edit to catch rate limits
                await message.edit(content=msg_content)
            except discord.NotFound:
                # Message deleted, remove it from tracking so we don't spam errors
                info['message'] = None
            except Exception as e:
                # Keep quiet largely to avoid console spam on rate limits
                pass

    def get_queue(self, guild_id):
        return self.queues[guild_id]

    # Helper to clean up the message of the ending song
    async def cleanup_song(self, guild_id, status="Finished"):
        if guild_id in self.current_song:
            info = self.current_song[guild_id]
            message = info.get('message')
            if message:
                try:
                    # Final update
                    now = time.time()
                    elapsed = now - info['start_timestamp']
                    # Use accurate elapsed or duration if finished
                    
                    final_pos = info['seek_position'] + elapsed
                    duration = info.get('duration')
                    
                    if status == "Finished" and duration:
                         final_pos = duration # Snap to end
                    
                    bar_str = create_progress_bar(final_pos, duration)
                    
                    msg_content = f"**{info['title']}**\n{bar_str} ({status})"
                    await message.edit(content=msg_content, view=None)
                except:
                    pass

    async def play_next(self, interaction):
        guild_id = interaction.guild_id
        
        # Decide what to play next
        query = None
        
        # Check loop first
        if self.looping.get(guild_id) and self.current_song.get(guild_id):
             query = self.current_song[guild_id]['query']
        elif self.queues[guild_id]:
            query = self.queues[guild_id].pop(0)
            
        if query:
            try:
                # We need to recreate the player logic here without the interaction response context
                # because this is a callback
                voice_client = interaction.guild.voice_client
                if not voice_client:
                    return

                # Prepare player
                player = await YTDLSource.from_url(query, loop=self.bot.loop, stream=True)
                
                # Send a message to the channel
                view = MusicControls(self.bot, guild_id, looping=self.looping.get(guild_id, False))
                msg_content = f'**Now playing:** {player.title}\n{create_progress_bar(0, player.duration)}'
                message = await interaction.channel.send(msg_content, view=view)

                # Update current song info
                self.current_song[guild_id] = {
                    'query': query,
                    'title': player.title,
                    'start_timestamp': time.time(),
                    'seek_position': 0,
                    'duration': player.duration,
                    'message': message,
                    'status': 'Playing'
                }

                # Define the after callback recursively
                def after_playing(error):
                    if error:
                        print(f"Player error: {error}")
                    
                    # Cleanup old song first
                    # We check if status was manually set (e.g. Skipped/Stopped)
                    current_status = self.current_song[guild_id].get('status', 'Finished')
                    if current_status == 'Playing':
                        current_status = 'Finished'
                    
                    coro_cleanup = self.cleanup_song(guild_id, current_status)
                    asyncio.run_coroutine_threadsafe(coro_cleanup, self.bot.loop)

                    # If stopped, don't play next
                    if current_status == "Stopped":
                        return

                    # Schedule play_next on the loop
                    coro = self.play_next(interaction)
                    fut = asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
                    try:
                        fut.result()
                    except:
                        pass

                voice_client.play(player, after=after_playing)
                
            except Exception as e:
                print(f"Error in play_next: {e}")
                # If we fail to play a song, we should try the next one
                # But if we are looping, we might get stuck in an infinite loop of failures
                # So we should probably disable loop if it fails
                if self.looping.get(guild_id):
                     self.looping[guild_id] = False
                     await interaction.channel.send(f"Error playing **{query}**, loop disabled. Skipping...")
                else:
                     await interaction.channel.send(f"Error playing **{query}**: {e}. Skipping...")
                
                # Recursive call to skip to next
                await self.play_next(interaction)
        else:
            self.current_song.pop(guild_id, None)
            # await interaction.channel.send("Queue finished.") # Reduced spam

    def check_channel(self, interaction: discord.Interaction) -> bool:
        return interaction.channel.name == "ჭაჭing"

    @app_commands.command(name="join", description="Joins your voice channel")
    async def join(self, interaction: discord.Interaction):
        if not self.check_channel(interaction):
            return await interaction.response.send_message(f"🚫 I can only be used in the #ჭაჭing channel!", ephemeral=True)

        if interaction.user.voice:
            channel = interaction.user.voice.channel
            if interaction.guild.voice_client is not None:
                await interaction.guild.voice_client.move_to(channel)
                await interaction.response.send_message(f"Moved to {channel.name}")
            else:
                await channel.connect()
                await interaction.response.send_message(f"Joined {channel.name}")
        else:
            await interaction.response.send_message("You are not connected to a voice channel.", ephemeral=True)

    @app_commands.command(name="play", description="Plays a song or adds to queue")
    @app_commands.describe(query="The song/url you want to play")
    async def play(self, interaction: discord.Interaction, query: str):
        if not self.check_channel(interaction):
            return await interaction.response.send_message(f"🚫 I can only be used in the #ჭაჭing channel!", ephemeral=True)

        if not interaction.user.voice:
             return await interaction.response.send_message("You are not connected to a voice channel.", ephemeral=True)
             
        if not interaction.guild.voice_client:
             await interaction.user.voice.channel.connect()
        
        await interaction.response.defer()

        # Resolve query (Playlist vs Single)
        songs_to_add = []
        is_playlist = False
        
        # Pre-resolve if it's a spotify track to avoid yt-dlp DRM error on initial check
        query = await resolve_spotify_track(query)
        
        try:
            # Quick extraction to resolve types/entries
            with yt_dlp.YoutubeDL({'extract_flat': True, 'quiet': True, 'noplaylist': False, 'default_search': 'auto'}) as ydl:
                 info = await self.bot.loop.run_in_executor(None, lambda: ydl.extract_info(query, download=False))
            
            if 'entries' in info:
                # It's a playlist or a search result with multiple items
                # Usually standard search "auto" returns 1 entry if we don't specify number, but let's be safe.
                # If it's a search query like 'despacito', _type might be 'playlist' (search results)
                # If it's a playlist URL, _type is 'playlist'
                
                is_playlist = info.get('_type') == 'playlist' and 'http' in query # Simple heuristic
                
                for entry in info['entries']:
                    if entry.get('url'):
                        songs_to_add.append(entry['url'])
                    elif entry.get('id'):
                         songs_to_add.append(f"https://www.youtube.com/watch?v={entry['id']}")
            else:
                # Single item
                songs_to_add.append(info.get('webpage_url') or info.get('url') or query)
        except Exception:
            # Fallback
            songs_to_add = [query]

        await self.process_songs(interaction, songs_to_add)

    # Helper to process a list of songs (queue/play)
    async def process_songs(self, interaction, songs_to_add):
        guild_id = interaction.guild_id
        voice_client = interaction.guild.voice_client
        
        if not songs_to_add:
            return await interaction.followup.send("Could not find any songs.")

        # Lock to ensure we don't race on 'is_playing' check
        # We need a per-guild lock really, but global is fine for small bot
        # Or just checking our own state flag
        
        is_playing = voice_client.is_playing() or voice_client.is_paused()
        
        # Check our internal flag too in case ffmpeg hasn't started yet
        if self.voice_states.get(guild_id):
             is_playing = True
             
        play_now = None
        queued_count = 0
        
        if not is_playing and not self.queues[guild_id]:
             play_now = songs_to_add.pop(0)

        # Queue the rest
        self.queues[guild_id].extend(songs_to_add)
        queued_count = len(songs_to_add)

        # Responses
        if play_now:
            self.voice_states[guild_id] = True # Mark as "busty starting"
            try:
                player = await YTDLSource.from_url(play_now, loop=self.bot.loop, stream=True)
                
                view = MusicControls(self.bot, guild_id, looping=self.looping.get(guild_id, False))
                msg_content = f'**Now playing:** {player.title}\n{create_progress_bar(0, player.duration)}'
                if queued_count > 0:
                     msg_content += f"\n*(+ {queued_count} more songs added to queue)*"
                
                message = await interaction.followup.send(msg_content, view=view)

                self.current_song[guild_id] = {
                    'query': play_now,
                    'title': player.title,
                    'start_timestamp': time.time(),
                    'seek_position': 0,
                    'duration': player.duration,
                    'message': message,
                    'status': 'Playing'
                }

                def after_playing(error):
                    self.voice_states[guild_id] = False # Reset busy flag (will be set again in play_next if playing)
                    if error:
                        print(f"Player error: {error}")
                    
                    # Cleanup old song first
                    current_status = self.current_song[guild_id].get('status', 'Finished')
                    if current_status == 'Playing':
                        current_status = 'Finished'
                    
                    coro_cleanup = self.cleanup_song(guild_id, current_status)
                    asyncio.run_coroutine_threadsafe(coro_cleanup, self.bot.loop)

                    if current_status == "Stopped":
                        return

                    coro = self.play_next(interaction)
                    fut = asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
                    try:
                        fut.result()
                    except:
                        pass

                voice_client.play(player, after=after_playing)
            except Exception as e:
                self.voice_states[guild_id] = False
                import traceback
                traceback.print_exc()
                await interaction.followup.send(f"An error occurred: {e}")
                # Try to play next if this failed but we added stuff to queue
                if queued_count > 0:
                     await self.play_next(interaction)
        else:
            # We just queued everything
            msg = f"Added **{queued_count}** songs to queue."
            if queued_count == 1:
                 msg = f"Added to queue: **{songs_to_add[0]}**" # Might be raw url
            await interaction.followup.send(msg)

    # Playlist Group
    playlist_group = app_commands.Group(name="playlist", description="Manage your playlists")

    @playlist_group.command(name="create", description="Create a new playlist")
    async def playlist_create(self, interaction: discord.Interaction, name: str):
        if not self.check_channel(interaction):
            return await interaction.response.send_message(f"🚫 I can only be used in the #ჭაჭing channel!", ephemeral=True)
            
        data = load_playlists()
        user_id = str(interaction.user.id)
        
        if user_id not in data:
            data[user_id] = {}
            
        if name in data[user_id]:
             return await interaction.response.send_message(f"Playlist **{name}** already exists!", ephemeral=True)
             
        data[user_id][name] = []
        save_playlists(data)
        await interaction.response.send_message(f"Created playlist **{name}**.")

    @playlist_group.command(name="delete", description="Delete a playlist")
    async def playlist_delete(self, interaction: discord.Interaction, name: str):
        if not self.check_channel(interaction):
            return await interaction.response.send_message(f"🚫 I can only be used in the #ჭაჭing channel!", ephemeral=True)

        data = load_playlists()
        user_id = str(interaction.user.id)
        
        if user_id in data and name in data[user_id]:
            del data[user_id][name]
            save_playlists(data)
            await interaction.response.send_message(f"Deleted playlist **{name}**.")
        else:
            await interaction.response.send_message(f"Playlist **{name}** not found.", ephemeral=True)

    @playlist_group.command(name="add_to", description="Add a song to a playlist")
    async def playlist_add_to(self, interaction: discord.Interaction, name: str, query: str):
        if not self.check_channel(interaction):
            return await interaction.response.send_message(f"🚫 I can only be used in the #ჭაჭing channel!", ephemeral=True)

        data = load_playlists()
        user_id = str(interaction.user.id)
        
        if user_id not in data or name not in data[user_id]:
             return await interaction.response.send_message(f"Playlist **{name}** not found. Create it first with /playlist create", ephemeral=True)
        
        # We store the raw query. Resolving at play time is better to avoid stale URLs.
        data[user_id][name].append(query)
        save_playlists(data)
        await interaction.response.send_message(f"Added **{query}** to playlist **{name}**.")

    @playlist_group.command(name="list", description="List your playlists")
    async def playlist_list(self, interaction: discord.Interaction):
        if not self.check_channel(interaction):
            return await interaction.response.send_message(f"🚫 I can only be used in the #ჭაჭing channel!", ephemeral=True)

        data = load_playlists()
        user_id = str(interaction.user.id)
        
        if user_id in data and data[user_id]:
            msg = "**Your Playlists:**\n"
            for name, songs in data[user_id].items():
                msg += f"- **{name}**: {len(songs)} songs\n"
            await interaction.response.send_message(msg)
        else:
            await interaction.response.send_message("You don't have any playlists yet.", ephemeral=True)

    @playlist_group.command(name="play", description="Play a saved playlist")
    async def playlist_play(self, interaction: discord.Interaction, name: str):
        if not self.check_channel(interaction):
            return await interaction.response.send_message(f"🚫 I can only be used in the #ჭაჭing channel!", ephemeral=True)

        data = load_playlists()
        user_id = str(interaction.user.id)
        
        if user_id not in data or name not in data[user_id]:
             return await interaction.response.send_message(f"Playlist **{name}** not found.", ephemeral=True)
             
        songs = data[user_id][name]
        if not songs:
             return await interaction.response.send_message(f"Playlist **{name}** is empty!", ephemeral=True)

        if not interaction.user.voice:
             return await interaction.response.send_message("You are not connected to a voice channel.", ephemeral=True)
             
        if not interaction.guild.voice_client:
             await interaction.user.voice.channel.connect()
        
        await interaction.response.defer()
        
        # Process the list of songs
        # Note: These are queries, so we pass them as is. 
        # process_songs is expecting a list of things to play
        await self.process_songs(interaction, list(songs)) # Pass a copy

    async def update_status(self, interaction):
        # Manual refresh still useful if automatic one lags
        await interaction.response.defer() 
        # We don't need to do anything because the background task is running
        # But we can force an update immediately
        pass

    async def toggle_loop(self, interaction, view, button):
        guild_id = interaction.guild_id
        is_looping = not self.looping.get(guild_id, False)
        self.looping[guild_id] = is_looping
        
        button.style = discord.ButtonStyle.success if is_looping else discord.ButtonStyle.secondary
        await interaction.response.edit_message(view=view)

    async def seek(self, interaction, seconds):
        if not interaction.guild.voice_client or not interaction.guild.voice_client.is_playing():
            return await interaction.response.send_message("Nothing is playing.", ephemeral=True)
        
        # Defer immediately because processing takes time
        await interaction.response.defer()
        
        guild_id = interaction.guild_id
        current_info = self.current_song.get(guild_id)
        
        if not current_info:
            return await interaction.followup.send("Cannot determine current song.", ephemeral=True)

        # Calculate current position
        now = time.time()
        elapsed = now - current_info['start_timestamp']
        current_position = current_info['seek_position'] + elapsed
        
        new_position = max(0, current_position + seconds)
        
        # Trick: Temporarily disable the 'after' callback by removing it or setting a flag
        # But we can't easily remove it. Just pausing is usually safe.
        interaction.guild.voice_client.pause() 
        
        query = current_info['query']
        
        try:
             # Create new player at offset
             player = await YTDLSource.from_url(query, loop=self.bot.loop, stream=True, start_time=new_position)
             
             # Update state
             current_info['start_timestamp'] = time.time()
             current_info['seek_position'] = new_position
             
             # Swap source
             interaction.guild.voice_client.source = player
             interaction.guild.voice_client.resume()
             
             # Force update the message with new progress bar immediately
             message = current_info.get('message')
             if message:
                 msg_content = f"**Now playing:** {current_info['title']}\n{create_progress_bar(new_position, current_info.get('duration'))}"
                 await message.edit(content=msg_content)
             
        except Exception as e:
            interaction.guild.voice_client.resume() # Try to resume old one if fail
            await interaction.followup.send(f"Failed to seek: {e}", ephemeral=True)

    async def stop_music(self, interaction):
        if interaction.guild.voice_client:
            self.queues[interaction.guild_id].clear() # Clear queue
            if interaction.guild_id in self.current_song:
                 self.current_song[interaction.guild_id]['status'] = 'Stopped'
            interaction.guild.voice_client.stop()
            await interaction.guild.voice_client.disconnect()
            # Disable buttons
            await interaction.response.defer()
        else:
            await interaction.response.send_message("Not playing.", ephemeral=True)

    async def skip_song(self, interaction):
        if interaction.guild.voice_client and interaction.guild.voice_client.is_playing():
            if interaction.guild_id in self.current_song:
                 self.current_song[interaction.guild_id]['status'] = 'Skipped'
            interaction.guild.voice_client.stop() # This triggers 'after' which calls play_next
            # We can update the message to say "Skipped" but play_next will send a NEW message for the next song.
            # So here we can just delete the old controls or say skipped.
            await interaction.response.defer()
        else:
            await interaction.response.send_message("Not playing.", ephemeral=True)

class MusicBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix='!', intents=intents)
    
    async def setup_hook(self):
        await self.add_cog(Music(self))
        await self.tree.sync()
        print("Commands synced globally.")

bot = MusicBot()

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')
    print('------')

if __name__ == "__main__":
    if not TOKEN:
        print("Error: DISCORD_TOKEN not found in .env file.")
    else:
        keep_alive()
        bot.run(TOKEN)
