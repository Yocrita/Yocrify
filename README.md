# Spotify Python App

A Python web application that interacts with the Spotify API to display your music information, search tracks, and manage playlists.

## Features

- View your top tracks and playlists
- Search for tracks and artists
- Create new playlists
- Modern, responsive UI with dark theme
- Secure authentication with Spotify

## Setup

1. Create a Spotify Developer account and register your application at https://developer.spotify.com/dashboard
2. Get your Client ID and Client Secret
3. Add `http://localhost:5000/callback` to your application's Redirect URIs in the Spotify Dashboard
4. Copy the `.env.example` file to `.env` and fill in your Spotify credentials:
   ```
   SPOTIFY_CLIENT_ID=your_client_id_here
   SPOTIFY_CLIENT_SECRET=your_client_secret_here
   SPOTIFY_REDIRECT_URI=http://localhost:5000/callback
   ```

## Installation

1. Create a virtual environment:
   ```bash
   python -m venv venv
   ```

2. Activate the virtual environment:
   - Windows:
     ```bash
     .\venv\Scripts\activate
     ```
   - Unix/MacOS:
     ```bash
     source venv/bin/activate
     ```

3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

4. Run the application:
   ```bash
   python app.py
   ```

5. Open your browser and navigate to `http://localhost:5000`

## Technologies Used

- Flask: Web framework
- Spotipy: Spotify Web API wrapper for Python
- Bootstrap 5: Frontend framework
- Font Awesome: Icons
- Python-dotenv: Environment variable management
