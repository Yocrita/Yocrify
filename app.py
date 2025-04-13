import os
import json
import logging
from flask import Flask, render_template, session, redirect, request, url_for, jsonify, make_response
from flask_cors import CORS
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
import time

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

app = Flask(__name__)
CORS(app, supports_credentials=True)  # Enable CORS with credentials
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-here')  # Use consistent secret key

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
    return SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SCOPE,
        cache_path='.spotify_cache'  # Enable token caching
    )

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
        print(f"Error getting Spotify client: {str(e)}")
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

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin', '*'))
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
    response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
    response.headers.add('Access-Control-Allow-Credentials', 'true')
    return response

@app.route('/')
def index():
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
                print(f"Found data file: {json_files[0]}")
                with open(os.path.join(data_dir, json_files[0]), 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    print("Loaded data:", {
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
        print("No data file found")
        return render_template('playlists.html',
                            playlists=json.dumps([]),
                            tracks=json.dumps({}),
                            current_playlist=json.dumps(None),
                            last_sync=0)
                            
    except Exception as e:
        print(f"Error loading data: {str(e)}")
        return render_template('playlists.html',
                            playlists=json.dumps([]),
                            tracks=json.dumps({}),
                            current_playlist=json.dumps(None),
                            last_sync=0)

@app.route('/login')
def login():
    try:
        # Create OAuth instance
        sp_oauth = create_spotify_oauth()
        
        # Get the authorization URL
        auth_url = sp_oauth.get_authorize_url()
        
        # Store any 'next' URL in the session
        if 'next_url' not in session:
            session['next_url'] = '/'
            
        logger.info(f"Redirecting to Spotify auth, will return to: {session.get('next_url')}")
        return redirect(auth_url)
        
    except Exception as e:
        logger.error(f"Error in login: {str(e)}")
        return redirect('/')

@app.route('/callback')
def callback():
    try:
        # Get OAuth instance
        sp_oauth = create_spotify_oauth()
        
        # Clear existing session
        session.clear()
        
        # Get the code from request parameters
        code = request.args.get('code')
        
        # Exchange code for token
        token_info = sp_oauth.get_access_token(code)
        
        # Store token info in session
        session['token_info'] = token_info
        
        # Get user info
        sp = spotipy.Spotify(auth=token_info['access_token'])
        user_info = sp.current_user()
        
        # Store user ID in session
        session['user_id'] = user_info['id']
        logger.info(f"User {user_info['id']} successfully authenticated")
        
        # Redirect to the stored next_url or home
        next_url = session.pop('next_url', '/')
        logger.info(f"Redirecting to: {next_url}")
        return redirect(next_url)
        
    except Exception as e:
        logger.error(f"Error in callback: {str(e)}")
        return redirect('/')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/sync_library')
def sync_library():
    try:
        # Log request
        logger.info("Starting sync_library request")
        
        # Check if user is authenticated
        if 'token_info' not in session:
            logger.info("User not authenticated, redirecting to Spotify login")
            # Store the intended destination
            session['next_url'] = '/sync_library'
            return redirect('/login')

        # Get user ID from session
        user_id = session.get('user_id')
        if not user_id:
            logger.error("User ID not found in session")
            return make_response(jsonify({'success': False, 'error': 'User ID not found in session'}), 400)

        # Get Spotify client
        sp = get_spotify()
        if not sp:
            logger.error("Failed to create Spotify client")
            return make_response(jsonify({'success': False, 'error': 'Failed to create Spotify client'}), 500)

        # Get all user playlists
        playlists = []
        offset = 0
        
        try:
            while True:
                try:
                    logger.info(f"Fetching playlists at offset {offset}")
                    results = sp.current_user_playlists(offset=offset, limit=50)
                except Exception as e:
                    logger.error(f"Error fetching playlists at offset {offset}: {str(e)}")
                    return make_response(jsonify({'success': False, 'error': f"Error fetching playlists: {str(e)}"}), 502)
                
                if not results or not results.get('items'):
                    break
                
                for playlist in results['items']:
                    try:
                        # Get full playlist details including tracks
                        full_playlist = sp.playlist(playlist['id'])
                        
                        # Extract basic playlist info
                        playlist_data = {
                            'id': playlist['id'],
                            'name': playlist['name'],
                            'description': playlist.get('description', ''),
                            'images': playlist.get('images', []),
                            'tracks': []
                        }
                        
                        # Extract track information
                        if full_playlist.get('tracks', {}).get('items'):
                            for track in full_playlist['tracks']['items']:
                                if track and track.get('track'):
                                    track_data = {
                                        'id': track['track']['id'],
                                        'name': track['track']['name'],
                                        'duration_ms': track['track'].get('duration_ms', 0),
                                        'artists': [artist['name'] for artist in track['track'].get('artists', [])],
                                        'album': track['track'].get('album', {}).get('name', ''),
                                        'release_date': track['track'].get('album', {}).get('release_date', ''),
                                        'other_playlists': []
                                    }
                                    playlist_data['tracks'].append(track_data)
                        
                        playlists.append(playlist_data)
                        logger.info(f"Successfully processed playlist {playlist['id']}")
                    except Exception as e:
                        logger.error(f"Error processing playlist {playlist['id']}: {str(e)}")
                        continue
                
                offset += len(results['items'])
                if len(results['items']) < 50:
                    break

            if not playlists:
                logger.warning("No playlists found")
                return make_response(jsonify({'success': False, 'error': 'No playlists found'}), 404)

            # Create a map of track IDs to playlists for finding duplicates
            track_playlist_map = {}
            for playlist in playlists:
                for track in playlist['tracks']:
                    if track['id'] not in track_playlist_map:
                        track_playlist_map[track['id']] = []
                    track_playlist_map[track['id']].append({
                        'id': playlist['id'],
                        'name': playlist['name']
                    })

            # Update tracks with other playlist information
            for playlist in playlists:
                for track in playlist['tracks']:
                    other_playlists = track_playlist_map.get(track['id'], [])
                    track['other_playlists'] = [p for p in other_playlists if p['id'] != playlist['id']]

            # Save optimized data
            data = {
                'playlists': playlists,
                'last_sync': int(time.time())
            }
            
            try:
                # Save to user-specific file
                filename = os.path.join('data', f'{user_id}.json')
                os.makedirs('data', exist_ok=True)
                
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False)
                logger.info(f"Successfully saved data for user {user_id}")
            except Exception as e:
                logger.error(f"Error saving data: {str(e)}")
                # Continue even if save fails - return data to client
            
            response = {
                'success': True,
                'playlists': playlists,
                'last_sync': data['last_sync']
            }
            logger.info("Sync completed successfully")
            return make_response(jsonify(response), 200)

        except Exception as e:
            logger.error(f"Error in playlist processing: {str(e)}")
            return make_response(jsonify({'success': False, 'error': f"Error processing playlists: {str(e)}"}), 500)

    except Exception as e:
        logger.error(f"Error in sync_library: {str(e)}")
        return make_response(jsonify({'success': False, 'error': str(e)}), 500)

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
        print(f"Error getting playlist: {str(e)}")
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
        print(f"Error getting playlists: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
