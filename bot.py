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

# Load environment variables
load_dotenv()

TOKEN = os.getenv("TOKEN")

# Spotify credentials
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')
SPOTIFY_REDIRECT_URI = os.getenv('SPOTIFY_REDIRECT_URI', 'http://localhost:8888/callback')
SPOTIFY_USERNAME = os.getenv('SPOTIFY_USERNAME')
SPOTIFY_PASSWORD = os.getenv('SPOTIFY_PASSWORD')

# Initialize Spotify client with OAuth
sp = None
librespot_process = None

def setup_spotify():
    global sp
    try:
        sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope='user-modify-playback-state user-read-playback-state user-read-currently-playing',
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
                break
        except Exception as e:
            print(f"Error in playback monitor: {e}")
            break


@tree.command(name="join", description="Join your voice channel and stream Spotify audio", )
async def join(interaction: discord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("You're not in a voice channel!", ephemeral=True)
        return

    channel = interaction.user.voice.channel
    vc = await channel.connect()

    librespot = LibrespotAudio()
    await librespot.start()
    # the other audio handler, might work better? idfk tho
    source = discord.FFmpegPCMAudio(
        librespot,
        pipe=True,
        before_options='-rtbufsize 150000 -f s16le -ar 48000 -ac 2',  # 176400 = 44100 * 2 * 2 (1 second of s16 stereo at 44.1kHz)
        options='-vn -vbr 0 -bufsize 150000'
    )
    #source = discord.PCMAudio(
    #    librespot
    #)
    vc.play(source)
    asyncio.create_task(monitor_playback_and_disconnect(vc))

    
    await interaction.response.send_message("Joined the channel and started Spotify streaming. Use /play to start playing music!")
    loopthingy = 0
    while loopthingy == 0:
        devices = sp.devices()
        for d in devices['devices']:
            print(f"{d['name']}: {d['id']}")
            if d['name'] == "Discord Bot":
                try:
                    sp.transfer_playback(device_id=d['id'], force_play=False)
                    loopthingy = 1
                    await asyncio.sleep(3)
                except Exception as e:
                    print(e)
                    pass




@tree.command(name="leave", description="Leave the voice channel", )
async def leave(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        # Clean up librespot process
        if interaction.guild.voice_client.source:
            interaction.guild.voice_client.source.cleanup()
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("Disconnected.", ephemeral=True)
    else:
        await interaction.response.send_message("I'm not in a voice channel.", ephemeral=True)

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
            await interaction.response.send_message("You're not in a voice channel!", ephemeral=True)
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
        #)
        source = discord.PCMAudio(
            librespot
        )
        vc.play(source)
        asyncio.create_task(monitor_playback_and_disconnect(vc))

        await interaction.response.send_message("Joined the channel, trying to start playback...")
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

    try:
        if play_type.lower() == "album":
            # Search for album
            results = sp.search(query, limit=1, type='album')
            if not results['albums']['items']:
                await interaction.response.edit_message("No albums found!")
                return

            album = results['albums']['items'][0]
            album_uri = album['uri']
            
            # Get all tracks from the album
            album_tracks = sp.album_tracks(album['id'])
            track_uris = [track['uri'] for track in album_tracks['items']]
            
            if not track_uris:
                await interaction.response.edit_message("No tracks found in album!")
                return

            if await is_spotify_playing():
                # Add all tracks to queue
                for uri in track_uris:
                    sp.add_to_queue(uri)
                await interaction.response.edit_message(
                    f"Added album to queue: {album['name']} by {album['artists'][0]['name']}\n"
                    f"({len(track_uris)} tracks)"
                )
            else:
                # Start playback with first track and queue the rest
                sp.start_playback(uris=[track_uris[0]])
                for uri in track_uris[1:]:
                    sp.add_to_queue(uri)
                await interaction.response.edit_message(
                    f"Now playing album: {album['name']} by {album['artists'][0]['name']}\n"
                    f"({len(track_uris)} tracks)"
                )

        else:  # Default to track
            # Search for the track
            results = sp.search(query, limit=1, type='track')
            if not results['tracks']['items']:
                await interaction.response.edit_message("No tracks found!")
                return

            track = results['tracks']['items'][0]
            track_uri = track['uri']
            track_name = track['name']
            artist_name = track['artists'][0]['name']

            if await is_spotify_playing():
                # Add to queue
                sp.add_to_queue(track_uri)
                await interaction.response.edit_message(f"Added to queue: {track_name} by {artist_name}")
            else:
                # Start playback
                sp.start_playback(uris=[track_uri])
                await interaction.response.edit_message(f"Now playing: {track_name} by {artist_name}")

    except Exception as e:
        print(e)
        await interaction.response.edit_message(f"Error playing or queuing: {str(e)}")


@tree.command(name="pause", description="Pause Spotify playback", )
async def pause(interaction: discord.Interaction):
    if interaction.guild.voice_client:
        # Clean up librespot process
        if interaction.guild.voice_client.source:
            interaction.guild.voice_client.source.cleanup()
        await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("Paused, use /resume to start again.", ephemeral=True)
    else:
        await interaction.response.send_message("I'm not in a voice channel.", ephemeral=True)


@tree.command(name="resume", description="Resume Spotify playback", )
async def resume(interaction: discord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("You're not in a voice channel!", ephemeral=True)
        return

    channel = interaction.user.voice.channel
    vc = await channel.connect()

    librespot = LibrespotAudio()
    await librespot.start()
    # the other audio handler, might work better? idfk tho
    source = discord.FFmpegPCMAudio(
        librespot,
        pipe=True,
        before_options='-rtbufsize 150000 -f s16le -ar 48000 -ac 2',  # 176400 = 44100 * 2 * 2 (1 second of s16 stereo at 44.1kHz)
        options='-vn -vbr 0 -bufsize 150000'
    )
    #source = discord.PCMAudio(
    #    librespot
    #)
    vc.play(source)
    asyncio.create_task(monitor_playback_and_disconnect(vc))
    
    await interaction.response.send_message("Joined the channel, trying to resume playback...")
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
                except Exception as e:
                    print(e)
                    pass

@tree.command(name="search", description="Search for a song on Spotify", )
async def search(interaction: discord.Interaction, query: str):
    if not sp:
        await interaction.response.send_message("Spotify is not authenticated. Please check the console for setup instructions.", ephemeral=True)
        return

    try:
        results = sp.search(query, limit=5, type='track')
        if not results['tracks']['items']:
            await interaction.response.send_message("No tracks found!", ephemeral=True)
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
    if not sp:
        await interaction.response.send_message("Spotify is not authenticated. Please check the console for setup instructions.", ephemeral=True)
        return
    try:
        sp.next_track()
        await interaction.response.send_message("Skipped to the next track.")
    except Exception as e:
        await interaction.response.send_message(f"Error skipping track: {str(e)}", ephemeral=True)


@tree.command(name="previous", description="Go to the previous song on Spotify", )
async def previous(interaction: discord.Interaction):
    if not sp:
        await interaction.response.send_message("Spotify is not authenticated. Please check the console for setup instructions.", ephemeral=True)
        return
    try:
        sp.previous_track()
        await interaction.response.send_message("Went to the previous track.")
    except Exception as e:
        await interaction.response.send_message(f"Error going to previous track: {str(e)}", ephemeral=True)


@tree.command(name="radio", description="Start a Spotify radio based on a track or artist", )
async def radio(interaction: discord.Interaction, query: str):
    if not sp:
        await interaction.response.send_message("Spotify is not authenticated. Please check the console for setup instructions.", ephemeral=True)
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
            await interaction.response.send_message("No matching track or artist found for radio.", ephemeral=True)
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
        await interaction.response.send_message(f"Started radio!\nQueued:\n" + "\n".join(track_names))
    except Exception as e:
        await interaction.response.send_message(f"Error starting radio: {str(e)}", ephemeral=True)


@tree.command(name="stop", description="Stop playback and clear the queue", )
async def stop(interaction: discord.Interaction):
    if not sp:
        await interaction.response.send_message("Spotify is not authenticated. Please check the console for setup instructions.", ephemeral=True)
        return

    try:
        # Stop playback
        sp.pause_playback()
        
        # Clear queue by starting a new empty queue
        # Note: Spotify API doesn't have a direct "clear queue" endpoint,
        # so we start a new empty queue to effectively clear it
        sp.start_playback(uris=[])
        
        await interaction.response.send_message("Stopped playback and cleared the queue.")
    except Exception as e:
        await interaction.response.send_message(f"Error stopping playback: {str(e)}", ephemeral=True)


@bot.event
async def on_ready():
    await tree.sync()
    print(f"Logged in as {bot.user}")

bot.run(TOKEN)

