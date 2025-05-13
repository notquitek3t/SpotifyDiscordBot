# SpotifyDiscordBot
Spotify bot, for Discord. May be stuttery, or otherwise not a good experience, but there was a lack of Spotify bots, and that needed to change.

## Preparation
Run the bot once locally outside of Docker, and then authenticate python with the url, AND librespot by going to the devices tab in your Spotify client, and selecting "Discord Bot"

Then just stop the bot and build it with Docker.

Example compose file:
`mkdir src && git clone https://github.com/notquitek3t/SpotifyDiscordBot src/spotify`
```yaml
services:
  spotifybot:
    build: ./src/spotify
    restart: unless-stopped
```

made with love by notquitek3t, enjoy :3
