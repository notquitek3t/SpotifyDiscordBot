# SpotifyDiscordBot
Spotify bot, for Discord. May be stuttery, or otherwise not a good experience, but there was a lack of Spotify bots, and that needed to change.

## Preparation
Make a new Spotify account and make sure it has Premium, whether it's individual or family, or something else. It just has to have premium. It will hijack your account while it plays music.
Run the bot once locally outside of Docker, and then authenticate python with the url, AND librespot by going to the devices tab in your Spotify client, and selecting "Discord Bot"

Then just stop the bot and build it with Docker.
If you're building for a server, make sure you copy .cache, and .spotify_cache files, and the cache folder after librespot has been authenticated at least once.

Example compose file:
`mkdir src && git clone https://github.com/notquitek3t/SpotifyDiscordBot src/spotify`
```yaml
services:
  spotifybot:
    build: ./src/spotify
    restart: unless-stopped
    environment:
      - TOKEN=
      - SPOTIFY_CLIENT_ID=
      - SPOTIFY_CLIENT_SECRET=
      - SPOTIFY_REDIRECT_URI=
      - BOT_ADMINS=00000,00001 # Comma separated list of user ids of who can override /skip, /pause, and /shutdown
```

made with love by notquitek3t, enjoy :3

## Donations (since people have asked)
I'd greatly appreciate donations, but I don't want people to feel forced into it.

If you're still interested, contact me on discord at `notquitek3t` to discuss options.
