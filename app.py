import os
import json
import logging
from flask import Flask, render_template, session, redirect, request, url_for, jsonify
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
import time

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-here')

# Spotify OAuth Configuration
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')
SPOTIFY_REDIRECT_URI = os.getenv('SPOTIFY_REDIRECT_URI')
SCOPE = 'user-library-read playlist-read-private playlist-read-collaborative'

# Log configuration values (without secrets)
logger.info(f"Starting app with REDIRECT_URI: {SPOTIFY_REDIRECT_URI}")
logger.info(f"Client ID configured: {'Yes' if SPOTIFY_CLIENT_ID else 'No'}")
logger.info(f"Client Secret configured: {'Yes' if SPOTIFY_CLIENT_SECRET else 'No'}")

def create_spotify_oauth():
    try:
        oauth = SpotifyOAuth(
            client_id=SPOTIFY_CLIENT_ID,
            client_secret=SPOTIFY_CLIENT_SECRET,
            redirect_uri=SPOTIFY_REDIRECT_URI,
            scope=SCOPE,
            cache_path='.spotify_cache'  # Enable token caching
        )
        return oauth
    except Exception as e:
        logger.error(f"Error creating Spotify OAuth: {str(e)}")
        raise

def get_spotify_client():
    try:
        if not session.get('token_info'):
            # Try to get token from cache if available
            sp_oauth = create_spotify_oauth()
            token_info = sp_oauth.get_cached_token()
            if token_info:
                session['token_info'] = token_info
            else:
                return None
        
        token_info = session['token_info']
        
        # Check if token needs refresh
        now = int(time.time())
        is_expired = token_info['expires_at'] - now < 60
        
        if is_expired:
            sp_oauth = create_spotify_oauth()
            token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
            session['token_info'] = token_info
        
        return spotipy.Spotify(auth=token_info['access_token'], requests_timeout=20)
    except Exception as e:
        logger.error(f"Error getting Spotify client: {str(e)}")
        session.pop('token_info', None)  # Clear invalid token
        return None

def get_spotify():
    return get_spotify_client()

def fetch_with_retry(func, *args, max_retries=3, **kwargs):
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(1)

def get_all_items(sp, initial_request, get_next):
    items = []
    results = initial_request
    
    while results:
        items.extend(results['items'])
        if not results['next']:
            break
        try:
            results = fetch_with_retry(get_next, results)
        except Exception as e:
            break
    
    return items

# Add data storage functions
def save_user_data(user_id, data):
    data_dir = os.path.join(os.path.dirname(__file__), 'data')
    os.makedirs(data_dir, exist_ok=True)
    
    file_path = os.path.join(data_dir, f'{user_id}.json')
    with open(file_path, 'w') as f:
        json.dump(data, f)

def load_user_data(user_id):
    file_path = os.path.join(os.path.dirname(__file__), 'data', f'{user_id}.json')
    if os.path.exists(file_path):
        with open(file_path, 'r') as f:
            return json.load(f)
    return None

def optimize_playlist_data(playlist, tracks, track_playlist_map):
    """Pre-calculate and cache useful playlist information"""
    total_duration_ms = sum(track['duration_ms'] for track in tracks)
    genres = set()
    artists = set()
    release_years = set()
    
    # Add track occurrence information
    tracks_with_occurrences = []
    for track in tracks:
        # Get other playlists containing this track
        other_playlists = track_playlist_map.get(track['id'], [])
        # Filter out current playlist and sort by name
        other_playlists = [p for p in other_playlists if p['id'] != playlist['id']]
        other_playlists.sort(key=lambda x: x['name'].lower())
        
        track_with_occurrences = track.copy()
        track_with_occurrences['other_playlists'] = other_playlists
        tracks_with_occurrences.append(track_with_occurrences)
        
        # Extract artist info
        for artist in track.get('artists', []):
            artists.add(artist['name'])
            
        # Extract release year
        if 'album' in track and 'release_date' in track['album']:
            try:
                year = track['album']['release_date'].split('-')[0]
                release_years.add(year)
            except (IndexError, AttributeError):
                pass
    
    # Sort tracks by name for consistency
    tracks_with_occurrences.sort(key=lambda x: x['name'].lower())
    
    return {
        'id': playlist['id'],
        'name': playlist['name'],
        'description': playlist.get('description', ''),
        'images': playlist.get('images', []),
        'owner': playlist['owner'],
        'tracks_total': len(tracks),
        'duration_ms': total_duration_ms,
        'duration_formatted': format_duration(total_duration_ms),
        'artists_total': len(artists),
        'artists': sorted(list(artists)),
        'years_range': [min(release_years), max(release_years)] if release_years else [],
        'tracks': tracks_with_occurrences,  # Store all tracks with sorted occurrences
        'modified_at': int(time.time())
    }

def format_duration(ms):
    """Format milliseconds into human readable duration"""
    seconds = ms // 1000
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m {seconds}s"

@app.route('/')
def index():
    try:
        sp = get_spotify()
        if not sp:
            return redirect(url_for('login'))
        
        try:
            # Look for any existing JSON data file
            data_dir = os.path.join(os.path.dirname(__file__), 'data')
            if os.path.exists(data_dir):
                # Get the first JSON file we find
                json_files = [f for f in os.listdir(data_dir) if f.endswith('.json')]
                if json_files:
                    logger.info(f"Found data file: {json_files[0]}")
                    with open(os.path.join(data_dir, json_files[0]), 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        logger.info("Loaded data:", {
                            'num_playlists': len(data.get('playlists', [])),
                            'num_tracks': len(data.get('tracks', {})),
                            'last_sync': data.get('last_sync', 0)
                        })
                        return render_template('playlists.html',
                                            playlists=json.dumps(data.get('playlists', [])),
                                            tracks=json.dumps(data.get('tracks', {})),
                                            current_playlist=json.dumps(None),
                                            last_sync=data.get('last_sync', 0))
                                        
            # No data file found, render empty state
            logger.info("No data file found")
            return render_template('playlists.html',
                                playlists=json.dumps([]),
                                tracks=json.dumps({}),
                                current_playlist=json.dumps(None),
                                last_sync=0)
        
        except Exception as e:
            logger.error(f"Error loading data: {str(e)}")
            return render_template('playlists.html',
                                playlists=json.dumps([]),
                                tracks=json.dumps({}),
                                current_playlist=json.dumps(None),
                                last_sync=0)

    except Exception as e:
        logger.error(f"Error rendering index page: {str(e)}")
        return "Internal Server Error", 500

@app.route('/login')
def login():
    try:
        # Clear any existing session
        session.clear()
        
        sp_oauth = create_spotify_oauth()
        auth_url = sp_oauth.get_authorize_url()
        logger.info(f"Generated auth URL: {auth_url}")
        return redirect(auth_url)
    except Exception as e:
        logger.error(f"Error in login route: {str(e)}")
        return "Error during login process", 500

@app.route('/callback')
def callback():
    try:
        sp_oauth = create_spotify_oauth()
        session.clear()
        
        code = request.args.get('code')
        error = request.args.get('error')
        
        if error:
            return f"Error during authentication: {error}"
        
        if not code:
            return redirect(url_for('login'))
        
        try:
            token_info = sp_oauth.get_access_token(code)
            session['token_info'] = token_info
            return redirect(url_for('index'))
        except Exception as e:
            logger.error(f"Error getting access token: {str(e)}")
            return "Error during authentication", 500
    except Exception as e:
        logger.error(f"Error in callback route: {str(e)}")
        return "Internal Server Error", 500

@app.route('/logout')
def logout():
    try:
        # Clear Flask session
        session.clear()
        
        # Delete Spotify cache file if it exists
        cache_path = '.spotify_cache'
        if os.path.exists(cache_path):
            try:
                os.remove(cache_path)
                logger.info("Deleted Spotify cache file")
            except Exception as e:
                logger.error(f"Error deleting cache file: {str(e)}")
        
        # Clear any existing data files
        data_dir = os.path.join(os.path.dirname(__file__), 'data')
        if os.path.exists(data_dir):
            try:
                for file in os.listdir(data_dir):
                    if file.endswith('.json'):
                        os.remove(os.path.join(data_dir, file))
                        logger.info(f"Deleted data file: {file}")
            except Exception as e:
                logger.error(f"Error clearing data files: {str(e)}")
        
        return redirect(url_for('login'))  # Redirect to login instead of index
    except Exception as e:
        logger.error(f"Error in logout route: {str(e)}")
        return "Internal Server Error", 500

@app.route('/sync_library')
def sync_library():
    try:
        logger.info("Starting sync process...")
        sp = get_spotify()
        if not sp:
            logger.error("No Spotify client - not authenticated")
            return jsonify({'success': False, 'error': 'not_authenticated'})

        logger.info("Getting current user...")
        try:
            current_user = sp.current_user()
            user_id = current_user['id']
        except Exception as e:
            logger.error(f"Error getting user info: {str(e)}")
            return jsonify({'success': False, 'error': 'not_authenticated'})

        logger.info("Getting playlists...")
        # Get all playlists
        playlists = []
        offset = 0
        try:
            # First, get total number of playlists
            initial_results = sp.current_user_playlists(limit=1)
            total_playlists = initial_results['total']
            logger.info(f"Total playlists to fetch: {total_playlists}")

            while offset < total_playlists:
                logger.info(f"Fetching playlists batch at offset {offset}...")
                results = sp.current_user_playlists(offset=offset, limit=50)  # Fetch 50 at a time
                batch_items = results['items']
                batch_size = len(batch_items)
                
                if batch_size == 0:
                    logger.info("No more playlists found, breaking loop")
                    break
                
                logger.info(f"Retrieved {batch_size} playlists in this batch")
                playlists.extend(batch_items)
                offset += batch_size
                logger.info(f"Total playlists fetched so far: {len(playlists)}/{total_playlists}")

        except Exception as e:
            logger.error(f"Error fetching playlists at offset {offset}: {str(e)}")
            return jsonify({'success': False, 'error': str(e)})

        logger.info(f"Successfully fetched all {len(playlists)} playlists")

        # Track which playlists each track appears in
        track_playlist_map = {}
        playlist_tracks = {}
        optimized_playlists = []

        logger.info(f"Processing {len(playlists)} playlists...")
        total_processed = 0
        for playlist in playlists:
            try:
                playlist_name = playlist.get('name', 'Unknown')
                logger.info(f"Processing playlist {total_processed + 1}/{len(playlists)}: {playlist_name}")
                tracks = []
                track_offset = 0

                while True:
                    try:
                        results = sp.playlist_tracks(
                            playlist['id'],
                            offset=track_offset,
                            fields='items.track(id,name,duration_ms,artists(name),album(name,release_date)),total,next'
                        )
                        
                        if not results['items']:
                            break

                        for item in results['items']:
                            if not item['track']:
                                continue

                            track = item['track']
                            tracks.append(track)

                            # Map this track to the playlist
                            if track['id'] not in track_playlist_map:
                                track_playlist_map[track['id']] = []
                            track_playlist_map[track['id']].append({
                                'id': playlist['id'],
                                'name': playlist['name']
                            })

                        track_offset += len(results['items'])
                        if not results.get('next'):
                            break

                    except Exception as e:
                        logger.error(f"Error fetching tracks at offset {track_offset}: {str(e)}")
                        continue

                # Store the tracks for this playlist
                playlist_tracks[playlist['id']] = tracks
                
                # Create optimized playlist data
                optimized_playlist = optimize_playlist_data(playlist, tracks, track_playlist_map)
                optimized_playlists.append(optimized_playlist)

                total_processed += 1

            except Exception as e:
                logger.error(f"Error processing playlist {playlist_name}: {str(e)}")
                continue

        logger.info("Creating data structure...")
        # Save optimized data
        data = {
            'playlists': optimized_playlists,
            'tracks': playlist_tracks,
            'last_sync': int(time.time())
        }

        logger.info("Ensuring data directory exists...")
        # Ensure data directory exists
        data_dir = os.path.join(os.path.dirname(__file__), 'data')
        os.makedirs(data_dir, exist_ok=True)

        logger.info("Saving data...")
        # Save the data
        save_user_data(user_id, data)

        logger.info("Sync completed successfully")
        return jsonify({
            'success': True,
            'playlists': optimized_playlists,
            'last_sync': data['last_sync']
        })

    except Exception as e:
        logger.error(f"Error during sync: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/playlist/<playlist_id>')
def get_playlist(playlist_id):
    try:
        # Load data from JSON
        data_dir = os.path.join(os.path.dirname(__file__), 'data')
        json_files = [f for f in os.listdir(data_dir) if f.endswith('.json')]
        
        if not json_files:
            return jsonify({'success': False, 'error': 'no_data'})
            
        with open(os.path.join(data_dir, json_files[0]), 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        # Find the playlist in optimized data
        playlist = next((p for p in data['playlists'] if p['id'] == playlist_id), None)
        if not playlist:
            return jsonify({'success': False, 'error': 'playlist_not_found'})
            
        # Get the full tracks for this playlist
        tracks = data['tracks'].get(playlist_id, [])
            
        return jsonify({
            'success': True,
            'playlist': playlist,
            'tracks': tracks
        })
        
    except Exception as e:
        logger.error(f"Error getting playlist: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/playlists')
def get_playlists():
    try:
        # Load data from JSON
        data_dir = os.path.join(os.path.dirname(__file__), 'data')
        json_files = [f for f in os.listdir(data_dir) if f.endswith('.json')]
        
        if not json_files:
            return jsonify({'success': False, 'error': 'no_data'})
            
        with open(os.path.join(data_dir, json_files[0]), 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        return jsonify({
            'success': True,
            'playlists': data['playlists'],
            'last_sync': data.get('last_sync', 0)
        })
        
    except Exception as e:
        logger.error(f"Error getting playlists: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    app.run(host='localhost', port=5000, debug=True)
