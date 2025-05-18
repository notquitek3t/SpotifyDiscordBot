import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import subprocess
import os
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
import numpy as np
from scipy import signal
import yaml

# Load environment variables
load_dotenv()

TOKEN = os.getenv("TOKEN")

# Get admin list from environment variable
admins = [int(admin_id.strip()) for admin_id in os.getenv('BOT_ADMINS', '').split(',') if admin_id.strip()]

# Initialize counter variables
personpausecounter = 0
personskipcounter = 0
personshutdowncounter = 0
lastperson = None

# Spotify credentials
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')
SPOTIFY_REDIRECT_URI = os.getenv('SPOTIFY_REDIRECT_URI', 'http://localhost:8888/callback')


# Initialize Spotify client with OAuth
sp = None
librespot_process = None
def get_queue():
    """grabs the now playing song, and the next 10 tracks if possible, returns None if there's nothing playing."""
    try:
        queue = sp.queue()
        tracks = []
        index = 0
        nowplaying = queue['currently_playing']
        tracks.append(f"{nowplaying['artists'][0]['name']} - {nowplaying['name']}")
        for i in queue['queue']:
          if f"{i['artists'][0]['name']} - {i['name']}" == tracks[-1]:
              break
          tracks.append(f"{i['artists'][0]['name']} - {i['name']}")
          index += 1
          if index > 9:
            break
        return tracks
    except:
        return None

def setup_spotify():
    global sp
    try:
        sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope='user-modify-playback-state user-read-playback-state user-read-currently-playing user-library-modify',
            cache_path='.spotify_cache',
            open_browser=False
        ))
        print("Spotify authentication successful!")
        return True
    except Exception as e:
        print(f"Error setting up Spotify: {e}")
        return False

# Try to authenticate with Spotify if not already authenticated
print(setup_spotify())

class LibrespotAudio(discord.AudioSource):
    def __init__(self):
        self.process = None
        self._started = False
        self._input_rate = 44100
        self._output_rate = 48000
        self._channels = 2
        self._resample_ratio = self._output_rate / self._input_rate

    async def start(self):
        if self._started:
            return

        try:
            self.process = subprocess.Popen(
                ['librespot',
                '--name', 'Discord Bot',
                '--backend', 'pipe',
                '--format', 'S16',
                '--bitrate', '320',
                '--cache', '/tmp/spotifycache',
                '--system-cache', './cache'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )
            self._started = True
            print("Librespot started successfully")
        except Exception as e:
            print(f"Error starting librespot: {e}")
            raise

    def _resample_audio(self, data):
        # Convert bytes to numpy array of int16
        audio_data = np.frombuffer(data, dtype=np.int16)
        
        # Ensure we have an even number of samples (for stereo)
        if len(audio_data) % 2 != 0:
            audio_data = audio_data[:-1]
        
        # Reshape to separate channels
        audio_data = audio_data.reshape(-1, self._channels)
        
        # Resample each channel
        resampled_channels = []
        for channel in range(self._channels):
            resampled = signal.resample_poly(
                audio_data[:, channel],
                up=self._output_rate,
                down=self._input_rate
            )
            resampled_channels.append(resampled)
        
        # Combine channels back
        resampled_audio = np.column_stack(resampled_channels)
        
        # Convert back to bytes
        return resampled_audio.astype(np.int16).tobytes()

    def read(self, blocksize):
        if not self.process or self.process.stdout is None:
            return b'\x00' * (blocksize // 2 )
        
        try:
            # Read enough data to get the desired output size after resampling
            # Ensure we read a multiple of 4 bytes (2 bytes per sample * 2 channels)
            input_size = int(blocksize * self._input_rate / self._output_rate)
            input_size = (input_size // 4) * 4
            
            data = self.process.stdout.read(input_size)
            if not data:
                return b'\x00\x00' * blocksize
            
            # Ensure data length is a multiple of 4
            if len(data) % 4 != 0:
                data = data[:(len(data) // 4) * 4]
            
            # Resample the audio
            resampled_data = self._resample_audio(data)
            
            # Ensure we return exactly blocksize bytes
            if len(resampled_data) > blocksize:
                return resampled_data[:blocksize]
            elif len(resampled_data) < blocksize:
                return resampled_data + (b'\x00\x00' * (blocksize - len(resampled_data)))
            return resampled_data
            
        except Exception as e:
            print(f"Error reading audio: {e}")
            return b'' * blocksize

    def cleanup(self):
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=1)
            except:
                self.process.kill()
            self.process = None
            self._started = False

    def is_opus(self):
        return False

    def __del__(self):
        self.cleanup()


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

async def is_bot_in_any_voice_channel(bot: commands.Bot) -> bool:
    return any(vc.is_connected() for vc in bot.voice_clients)


async def monitor_playback_and_disconnect(vc: discord.VoiceClient, check_interval: int = 10):
    """
    Periodically checks if Spotify is playing. If not, disconnects the bot from the voice channel.
    """
    while vc.is_connected():
        await asyncio.sleep(check_interval)
        try:
            playback = sp.current_playback()
            if not playback or not playback.get('is_playing'):
                print("Spotify is not playing. Disconnecting from voice channel.")
                if vc.source:
                    vc.source.cleanup()
                await vc.disconnect()
                await shutdown_bot()
                break
        except Exception as e:
            print(f"Error in playback monitor: {e}")
            await shutdown_bot()
            break


@tree.command(name="leave", description="Leave the voice channel", )
async def leave(interaction: discord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("you're not in a voice channel", ephemeral=True)
        return
    if interaction.guild.voice_client:
        # Clean up librespot process
        if interaction.guild.voice_client.source:
            interaction.guild.voice_client.source.cleanup()
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("Disconnected.", ephemeral=True)
        await shutdown_bot()
    else:
        await interaction.response.send_message("I'm not in a voice channel.", ephemeral=True)
        await shutdown_bot()

async def is_spotify_playing():
    """
    Returns True if Spotify is currently playing a track, False otherwise.
    """
    try:
        playback = sp.current_playback()
        if playback and playback.get('is_playing'):
            return True
        return False
    except Exception as e:
        print(f"Error checking Spotify playback status: {e}")
        return False


@tree.command(name="play", description="Play a song or album on Spotify", )
@app_commands.describe(
    query="Name of the track or album to play",
    play_type="Type of content to play"
)
async def play(interaction: discord.Interaction, query: str, play_type: str = "track"):
    if not interaction.guild.voice_client:
        if not interaction.user.voice:
            await interaction.response.send_message("you're not in a voice channel", ephemeral=True)
            return
    if not interaction.guild.voice_client:
        if await is_bot_in_any_voice_channel(bot):
            await interaction.response.send_message("I'm already playing music in another server. I can't join multiple VCs.", ephemeral=True)
            return

        channel = interaction.user.voice.channel
        vc = await channel.connect()

        librespot = LibrespotAudio()
        await librespot.start()
        source = discord.PCMAudio(
            librespot
        )
        vc.play(source)
        asyncio.create_task(monitor_playback_and_disconnect(vc))

        await interaction.response.send_message("firing up librespot...", ephemeral=True)
        loopthingy = 0
        while loopthingy == 0:
            devices = sp.devices()
            for d in devices['devices']:
                print(f"{d['name']}: {d['id']}")
                if d['name'] == "Discord Bot":
                    try:
                        sp.transfer_playback(device_id=d['id'], force_play=False)
                        loopthingy = 1
                        await asyncio.sleep(2)
                    except Exception as e:
                        print(e)
                        pass
    elif interaction.guild.voice_client.channel.id != interaction.user.voice.channel.id:
        await interaction.response.send_message("you're in the wrong vc, or the bot is playing in another server.", ephemeral=True)
        return
    else:
        await interaction.response.send_message("searching for the requested content...", ephemeral=True)
    try:
        if play_type.lower() == "album":
            # Search for album
            results = sp.search(query, limit=1, type='album')
            if not results['albums']['items']:
                await interaction.edit_original_response(content="no albums found :(")
                return

            album = results['albums']['items'][0]
            album_uri = album['uri']
            
            # Get all tracks from the album
            album_tracks = sp.album_tracks(album['id'])
            track_uris = [track['uri'] for track in album_tracks['items']]
            track_ids = [track['id'] for track in album_tracks['items']]
            
            if not track_uris:
                await interaction.edit_original_response(content="No tracks found in album!")
                return

            # Add all tracks to liked songs
            try:
                # Add tracks in chunks of 50 (Spotify API limit)
                chunk_size = 50
                for i in range(0, len(track_ids), chunk_size):
                    chunk = track_ids[i:i + chunk_size]
                    sp.current_user_saved_tracks_add(tracks=chunk)
            except Exception as e:
                print(f"Error adding album tracks to liked songs: {e}")

            if await is_spotify_playing():
                # Add all tracks to queue
                for uri in track_uris:
                    sp.add_to_queue(uri)
                await interaction.edit_original_response(content=f"Added album to queue: {album['name']} by {album['artists'][0]['name']}\n({len(track_uris)} tracks)")
            else:
                # Start playback with first track and queue the rest
                sp.start_playback(uris=[track_uris[0]])
                for uri in track_uris[1:]:
                    sp.add_to_queue(uri)
                await interaction.edit_original_response(content=f"Now playing album: {album['name']} by {album['artists'][0]['name']}\n({len(track_uris)} tracks)")

        else:  # Default to track
            # Search for the track
            results = sp.search(query, limit=1, type='track')
            if not results['tracks']['items']:
                await interaction.edit_original_response(content="no tracks found :(")
                return

            track = results['tracks']['items'][0]
            track_uri = track['uri']
            track_name = track['name']
            artist_name = track['artists'][0]['name']

            # Add track to liked songs
            try:
                sp.current_user_saved_tracks_add(tracks=[track['id']])
            except Exception as e:
                print(f"Error adding track to liked songs: {e}")

            if await is_spotify_playing():
                # Add to queue
                sp.add_to_queue(track_uri)
                await interaction.edit_original_response(content=f"added {track_name} by {artist_name} to the queue.")
            else:
                # Start playback
                sp.start_playback(uris=[track_uri])
                await interaction.edit_original_response(content=f"started playing: {track_name} by {artist_name}")
                sp.previous_track()

    except Exception as e:
        print(e)
        await interaction.edit_original_response(content=f"Error playing or queuing: {str(e)}")


@tree.command(name="pause", description="Pause Spotify playback", )
async def pause(interaction: discord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("brotha join a vc first", ephemeral=True)
        return
    if interaction.guild.voice_client.channel.id != interaction.user.voice.channel.id:
        await interaction.response.send_message("you're in the wrong vc, or the bot is playing in another server.", ephemeral=True)
        return
    if interaction.guild.voice_client:

        match personpausecounter:
            case _ if interaction.user.id in admins:
                personpausecounter = 0
                await interaction.response.send_message("[admin override applied] pausing the bot, use /resume to keep playing the queue")
            case _ if personpausecounter == 0:
                personpausecounter += 1
                lastperson = interaction.user.id
                await interaction.response.send_message("voted to pause, but 1 more person needs to also run /pause.")
                return
            case _ if personshutdowncounter == 1 and lastperson != interaction.user.id:
                personshutdowncounter = 0
                lastperson = None
                await interaction.response.send_message("pausing the bot, use /resume to keep playing the queue")
            case _ if personshutdowncounter == 1 and lastperson == interaction.user.id:
                await interaction.response.send_message("the 2nd /pause needs to be ran by someone else :V")
                return

        # Clean up librespot process
        if interaction.guild.voice_client.source:
            interaction.guild.voice_client.source.cleanup()
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("paused spotify, use /resume to start from where ya left off.", ephemeral=True)
        await shutdown_bot()
    else:
        await interaction.response.send_message("brotha i'm not in a vc.", ephemeral=True)

@tree.command(name="queue", description="Sends what's up next and what's playing right now in the chat", )
async def queue(interaction: discord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("brotha join a vc first", ephemeral=True)
        return
    if interaction.guild.voice_client.channel.id != interaction.user.voice.channel.id:
        await interaction.response.send_message("you're in the wrong vc, or the bot is playing in another server.", ephemeral=True)
        return
    if interaction.guild.voice_client:
        queue = get_queue()
        if queue != None:
            await interaction.response.send_message(f"""Queue (first song is currently playing):
{yaml.dump(queue)}""", ephemeral=True)
        else:
            await interaction.response.send_message(f"seems like the queue is empty, or the spotify library had an issue.", ephemeral=True)
    else:
        await interaction.response.send_message("brotha i'm not in a vc.", ephemeral=True)

@tree.command(name="resume", description="Resume Spotify playback", )
async def resume(interaction: discord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("brotha join a vc first", ephemeral=True)
        return
    if interaction.guild.voice_client:
        await interaction.response.send_message("brotha i'm in the vc already", ephemeral=True)
        return
    if not interaction.guild.voice_client:
        if await is_bot_in_any_voice_channel(bot):
            await interaction.response.send_message("I'm already playing music in another server. I can't join multiple VCs.", ephemeral=True)
            return

    channel = interaction.user.voice.channel
    vc = await channel.connect()

    librespot = LibrespotAudio()
    await librespot.start()
    # the other audio handler, might work better? idfk tho
    #source = discord.FFmpegPCMAudio(
    #    librespot,
    #    pipe=True,
    #    before_options='-rtbufsize 150000 -f s16le -ar 48000 -ac 2',  # 176400 = 44100 * 2 * 2 (1 second of s16 stereo at 44.1kHz)
    #    options='-vn -vbr 0 -bufsize 150000'
    #) q
    source = discord.PCMAudio(
        librespot
    )
    vc.play(source)
    asyncio.create_task(monitor_playback_and_disconnect(vc))
    
    await interaction.response.send_message("firing up librespot and requesting spotify to start playback..", ephemeral=True)
    loopthingy = 0
    while loopthingy == 0:
        devices = sp.devices()
        for d in devices['devices']:
            print(f"{d['name']}: {d['id']}")
            if d['name'] == "Discord Bot":
                try:
                    sp.transfer_playback(device_id=d['id'], force_play=True)
                    loopthingy = 1
                    await asyncio.sleep(3)
                    await interaction.edit_original_response(content="spotify seems to be playing now, if not, use /shutdown and try again.")
                except Exception as e:
                    print(e)
                    await interaction.edit_original_response(content=f"an error occurred{e}, try using /shutdown and then using /play again after 10 seconds.")
                    pass

@tree.command(name="search", description="Search for a song on Spotify", )
async def search(interaction: discord.Interaction, query: str):
    if not sp:
        await interaction.response.send_message("the bot owner fucked up, please dm notquitek3t and tell em to reconnect Spotify.", ephemeral=True)
        return

    try:
        results = sp.search(query, limit=5, type='track')
        if not results['tracks']['items']:
            await interaction.response.send_message("no tracks found :(", ephemeral=True)
            return

        # Create a formatted list of results
        tracks = []
        for idx, track in enumerate(results['tracks']['items'], 1):
            artists = ", ".join(artist['name'] for artist in track['artists'])
            tracks.append(f"{idx}. {track['name']} by {artists}")

        response = "**Search Results:**\n" + "\n".join(tracks)
        await interaction.response.send_message(response)
    except Exception as e:
        await interaction.response.send_message(f"Error searching: {str(e)}", ephemeral=True)

@tree.command(name="skip", description="Skip to the next song on Spotify", )
async def skip(interaction: discord.Interaction):
    # Basic verification, prevents shenanigans as is
    if not sp:
        await interaction.response.send_message("the bot owner fucked up, please dm notquitek3t and tell em to reconnect Spotify.", ephemeral=True)
        return
    if not interaction.user.voice:
        await interaction.response.send_message("brotha join a vc first", ephemeral=True)
        return
    if not interaction.guild.voice_client:
        await interaction.response.send_message("brotha i'm not even in the vc, use /resume or /play first.", ephemeral=True)
        return
    if interaction.guild.voice_client.channel.id != interaction.user.voice.channel.id:
        await interaction.response.send_message("you're in the wrong vc, or the bot is playing in another server.", ephemeral=True)
        return

    # The two-party verification, to avoid trolls and other assorted people doing evil things
    match personskipcounter:
        case _ if interaction.user.id in admins:
            personskipcounter = 0
            sp.next_track()
            await interaction.response.send_message("[admin override applied] skipped to the next track :3")
            return
        case _ if personskipcounter == 0:
            personskipcounter += 1
            lastperson = interaction.user.id
            await interaction.response.send_message("voted to skip, but 1 more person needs to also run /skip.")
            return
        case _ if personskipcounter == 1 and lastperson != interaction.user.id:
            personskipcounter = 0
            lastperson = None
            sp.next_track()
            await interaction.response.send_message("skipped to the next track :3")
            return
        case _ if personskipcounter == 1 and lastperson == interaction.user.id:
            await interaction.response.send_message("the 2nd /skip needs to be ran by someone else :V")
            return


@tree.command(name="radio", description="Start a Spotify radio based on a track or artist", )
async def radio(interaction: discord.Interaction, query: str):
    if not sp:
        await interaction.response.send_message("the bot owner fucked up, please dm notquitek3t and tell em to reconnect Spotify.", ephemeral=True)
        return
    if not interaction.user.voice:
        await interaction.response.send_message("brotha join a vc first", ephemeral=True)
        return
    if not interaction.guild.voice_client:
        await interaction.response.send_message("brotha i'm not even in the vc, use /resume or /play first.", ephemeral=True)
        return
    elif interaction.guild.voice_client.channel.id != interaction.user.voice.channel.id:
        await interaction.response.send_message("you're in the wrong vc, or the bot is playing in another server.", ephemeral=True)
        return
    try:
        # Try to find a track or artist
        results = sp.search(query, limit=1, type='track,artist')
        seed_tracks = []
        seed_artists = []
        if results['tracks']['items']:
            seed_tracks.append(results['tracks']['items'][0]['id'])
        elif results['artists']['items']:
            seed_artists.append(results['artists']['items'][0]['id'])
        else:
            await interaction.response.send_message("no matching track or artist found for radio :(", ephemeral=True)
            return
        # Get recommendations
        recs = sp.recommendations(seed_tracks=seed_tracks, seed_artists=seed_artists, limit=10)
        if not recs['tracks']:
            await interaction.response.send_message("No recommendations found.", ephemeral=True)
            return
        # Start playback with the first recommendation
        first_uri = recs['tracks'][0]['uri']
        sp.start_playback(uris=[first_uri])
        # Queue the rest
        for track in recs['tracks'][1:]:
            sp.add_to_queue(track['uri'])
        track_names = [f"{t['name']} by {t['artists'][0]['name']}" for t in recs['tracks']]
        await interaction.response.send_message(f"Started radio!\nQueued:\n" + "\n".join(track_names), ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Error starting radio: {str(e)}", ephemeral=True)

@tree.command(name="url", description="Play content directly from a Spotify URL (track, album, or playlist)", )
@app_commands.describe(
    url="Spotify URL to play (track, album, or playlist)"
)
async def url(interaction: discord.Interaction, url: str):
    if not sp:
        await interaction.response.send_message("the bot owner fucked up, please dm notquitek3t and tell em to reconnect Spotify.", ephemeral=True)
        return
    if not interaction.user.voice:
        await interaction.response.send_message("brotha join a vc first", ephemeral=True)
        return
    if not interaction.guild.voice_client:
        if await is_bot_in_any_voice_channel(bot):
            await interaction.response.send_message("I'm already playing music in another server. I can't join multiple VCs.", ephemeral=True)
            return

        channel = interaction.user.voice.channel
        vc = await channel.connect()

        librespot = LibrespotAudio()
        await librespot.start()
        source = discord.PCMAudio(
            librespot
        )
        vc.play(source)
        asyncio.create_task(monitor_playback_and_disconnect(vc))

        await interaction.response.send_message("firing up librespot...", ephemeral=True)
        loopthingy = 0
        while loopthingy == 0:
            devices = sp.devices()
            for d in devices['devices']:
                print(f"{d['name']}: {d['id']}")
                if d['name'] == "Discord Bot":
                    try:
                        sp.transfer_playback(device_id=d['id'], force_play=False)
                        loopthingy = 1
                        await asyncio.sleep(2)
                    except Exception as e:
                        print(e)
                        pass
    elif interaction.guild.voice_client.channel.id != interaction.user.voice.channel.id:
        await interaction.response.send_message("you're in the wrong vc, or the bot is playing in another server.", ephemeral=True)
        return
    else:
        await interaction.response.send_message("processing the url...", ephemeral=True)

    try:
        # Extract the type and ID from the URL
        # Example URLs:
        # https://open.spotify.com/track/1234567890
        # https://open.spotify.com/album/1234567890
        # https://open.spotify.com/playlist/1234567890
        parts = url.split('/')
        
        if parts[2] != "open.spotify.com":
            await interaction.edit_original_response(content="not a spotify url")
            return            
        
        if len(parts) < 5:
            await interaction.edit_original_response(content="invalid url passed")
            return

        content_type = parts[3]  # track, album, or playlist
        content_id = parts[4].split('?')[0]  # Remove any query parameters

        if content_type == 'track':
            # Get track info
            track = sp.track(content_id)
            track_uri = track['uri']
            
            # Add track to liked songs
            try:
                sp.current_user_saved_tracks_add(tracks=[track['id']])
            except Exception as e:
                print(f"Error adding track to liked songs: {e}")

            if await is_spotify_playing():
                # Add to queue
                sp.add_to_queue(track_uri)
                await interaction.edit_original_response(content=f"added {track['name']} by {track['artists'][0]['name']} to the queue.")
            else:
                # Start playback
                sp.start_playback(uris=[track_uri])
                await interaction.edit_original_response(content=f"now playing: {track['name']} by {track['artists'][0]['name']}")

        elif content_type == 'album':
            # Get album tracks and play them
            album = sp.album(content_id)
            album_tracks = sp.album_tracks(content_id)
            track_uris = [track['uri'] for track in album_tracks['items']]
            track_ids = [track['id'] for track in album_tracks['items']]
            
            if not track_uris:
                await interaction.edit_original_response(content="no tracks found in the album :(")
                return

            # Add all tracks to liked songs
            try:
                # Add tracks in chunks of 50 (Spotify API limit)
                chunk_size = 50
                for i in range(0, len(track_ids), chunk_size):
                    chunk = track_ids[i:i + chunk_size]
                    sp.current_user_saved_tracks_add(tracks=chunk)
            except Exception as e:
                print(f"Error adding album tracks to liked songs: {e}")

            if await is_spotify_playing():
                # Add all tracks to queue
                for uri in track_uris:
                    sp.add_to_queue(uri)
                await interaction.edit_original_response(content=f"added album to queue: {album['name']} by {album['artists'][0]['name']}\n({len(track_uris)} tracks)")
            else:
                # Start playback with first track and queue the rest
                sp.start_playback(uris=[track_uris[0]])
                for uri in track_uris[1:]:
                    sp.add_to_queue(uri)
                await interaction.edit_original_response(content=f"now playing album: {album['name']} by {album['artists'][0]['name']}\n({len(track_uris)} tracks)")

        elif content_type == 'playlist':
            # Get playlist tracks and play them
            playlist = sp.playlist(content_id)
            playlist_tracks = []
            
            # Get all tracks from playlist (handling pagination)
            results = sp.playlist_tracks(content_id)
            playlist_tracks.extend(results['items'])
            while results['next']:
                results = sp.next(results)
                playlist_tracks.extend(results['items'])
            
            track_uris = [item['track']['uri'] for item in playlist_tracks if item['track'] is not None]
            track_ids = [item['track']['id'] for item in playlist_tracks if item['track'] is not None]
            
            if not track_uris:
                await interaction.edit_original_response(content="no tracks found in playlist!")
                return

            # Add all tracks to liked songs
            try:
                # Add tracks in chunks of 50 (Spotify API limit)
                chunk_size = 50
                for i in range(0, len(track_ids), chunk_size):
                    chunk = track_ids[i:i + chunk_size]
                    sp.current_user_saved_tracks_add(tracks=chunk)
            except Exception as e:
                print(f"Error adding playlist tracks to liked songs: {e}")

            if await is_spotify_playing():
                # Add all tracks to queue
                for uri in track_uris:
                    sp.add_to_queue(uri)
                await interaction.edit_original_response(content=f"added playlist to queue: {playlist['name']}\n({len(track_uris)} tracks)")
            else:
                # Start playback with first track and queue the rest
                sp.start_playback(uris=[track_uris[0]])
                for uri in track_uris[1:]:
                    sp.add_to_queue(uri)
                await interaction.edit_original_response(content=f"now playing playlist: {playlist['name']}\n({len(track_uris)} tracks)")

        else:
            await interaction.edit_original_response(content="unsupported content type, please only play albums, tracks, or playlists.")

    except Exception as e:
        print(f"Error playing from URL: {e}")
        await interaction.edit_original_response(content=f"something went wrong :/ - {str(e)}")

@tree.command(name="stop", description="Stop playback and clear the queue", )
async def stop(interaction: discord.Interaction):
    if not sp:
        await interaction.response.send_message("the bot owner fucked up, please dm notquitek3t and tell em to reconnect Spotify.", ephemeral=True)
        return
    if not interaction.user.voice:
        await interaction.response.send_message("brotha join a vc first", ephemeral=True)
        return
    if not interaction.guild.voice_client:
        await interaction.response.send_message("brotha i'm not even in the vc, use /resume or /play first.", ephemeral=True)
        return
    elif interaction.guild.voice_client.channel.id != interaction.user.voice.channel.id:
        await interaction.response.send_message("you're in the wrong vc, or the bot is playing in another server.", ephemeral=True)
        return
    try:
        # Stop playback
        sp.pause_playback()
        
        # Clear queue by starting a new empty queue
        # Note: Spotify API doesn't have a direct "clear queue" endpoint,
        # so we start a new empty queue to effectively clear it
        sp.start_playback(uris=[])
        
        await interaction.response.send_message("cleared the queue")
    except Exception as e:
        await interaction.response.send_message(f"Error stopping playback: {str(e)}", ephemeral=True)

async def shutdown_bot():
    print("Shutting down bot...")

    # Terminate any librespot processes from active voice clients
    for vc in bot.voice_clients:
        if vc.is_connected():
            try:
                if vc.source:
                    vc.source.cleanup()
                await vc.disconnect()
            except Exception as e:
                print(f"Error disconnecting voice client: {e}")

    # Optionally, stop playback on Spotify (just in case)
    try:
        if sp:
            sp.pause_playback()
    except Exception as e:
        print(f"Failed to pause playback: {e}")

    # Stop the bot gracefully
    try:
        os.system("pkill librespot")
    except Exception as e:
        print(f"Failed to kill librespot processes: {e}")

    try:
        await bot.close()
    except Exception as e:
        print(f"Failed to close bot: {e}")


@tree.command(name="shutdown", description="Force the bot to restart (to fix a bug), please don't abuse this command.")
async def shutdown(interaction: discord.Interaction):
    match personshutdowncounter:
        case _ if interaction.user.id in admins:
            personshutdowncounter = 0
            await interaction.response.send_message("[admin override applied] shutting the bot down")
        case _ if personshutdowncounter == 0:
            personshutdowncounter += 1
            lastperson = interaction.user.id
            await interaction.response.send_message("voted to shutdown, but 1 more person needs to also run /shutdown.")
            return
        case _ if personshutdowncounter == 1 and lastperson != interaction.user.id:
            personshutdowncounter = 0
            lastperson = None
            await interaction.response.send_message("shutting the bot down")
        case _ if personshutdowncounter == 1 and lastperson == interaction.user.id:
            await interaction.response.send_message("the 2nd /shutdown needs to be ran by someone else :V")
            return
    await interaction.response.send_message("Shutting down...", ephemeral=True)
    await shutdown_bot()



@bot.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {bot.user}")

bot.run(TOKEN)

