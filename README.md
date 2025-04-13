# Spotify Playlist Sync

A Flask web application that syncs and manages your Spotify playlists.

## Features

- OAuth2 authentication with Spotify
- View all your playlists
- Sync playlist data
- Dark theme matching Spotify's design

## Setup

1. Clone this repository:
```bash
git clone https://github.com/yourusername/spotify-playlist-sync.git
cd spotify-playlist-sync
```

2. Create a virtual environment and install dependencies:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

3. Create a `.env` file with your Spotify API credentials:
```ini
SPOTIFY_CLIENT_ID=your_client_id_here
SPOTIFY_CLIENT_SECRET=your_client_secret_here
SPOTIFY_REDIRECT_URI=http://localhost:5000/callback
FLASK_SECRET_KEY=your_secret_key_here
```

4. Run the application:
```bash
python app.py
```

5. Open http://localhost:5000 in your browser

## Environment Variables

- `SPOTIFY_CLIENT_ID`: Your Spotify application client ID
- `SPOTIFY_CLIENT_SECRET`: Your Spotify application client secret
- `SPOTIFY_REDIRECT_URI`: OAuth callback URL
- `FLASK_SECRET_KEY`: Secret key for Flask sessions

## Development

The application is built with:
- Flask (Python web framework)
- Spotipy (Spotify Web API wrapper)
- Bootstrap (Frontend styling)

## License

MIT License
