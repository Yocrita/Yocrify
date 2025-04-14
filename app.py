import os
import json
from flask import Flask, render_template, session, redirect, request, url_for, jsonify, send_from_directory, Response
from flask_cors import CORS
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
import time
from werkzeug.exceptions import HTTPException

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-here')  # Use consistent secret key
CORS(app)  # Enable CORS for all routes

# Error handling middleware
@app.errorhandler(Exception)
def handle_error(error):
    print(f"\n=== Error Handler ===")
    print(f"Error type: {type(error)}")
    print(f"Error message: {str(error)}")
    
    response = {
        'success': False,
        'error': 'An unexpected error occurred. Please try again.'
    }
    
    if isinstance(error, HTTPException):
        response['error'] = error.description
        status_code = error.code
    else:
        status_code = 500
        
    print(f"Returning error response: {response}")
    return jsonify(response), status_code

# Ensure data directory exists
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'user_data')
try:
    os.makedirs(DATA_DIR, exist_ok=True)
    # Test write permissions
    test_file = os.path.join(DATA_DIR, 'test.txt')
    with open(test_file, 'w') as f:
        f.write('test')
    os.remove(test_file)
    print("Data directory is writable:", DATA_DIR)
except Exception as e:
    print(f"Error setting up data directory: {str(e)}")

# Spotify OAuth Configuration
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')
SPOTIFY_REDIRECT_URI = os.getenv('SPOTIFY_REDIRECT_URI')
SCOPE = 'user-library-read playlist-read-private playlist-read-collaborative'

def create_spotify_oauth():
    return SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SCOPE,
        cache_path='.spotify_cache'  # Enable token caching
    )

def get_spotify():
    """Get an authenticated Spotify client or None if not authenticated"""
    try:
        if 'token_info' not in session:
            print("No token info in session")
            return None
            
        # Create Spotify client
        token_info = session['token_info']
        
        # Check if token is expired
        now = int(time.time())
        is_expired = token_info['expires_at'] - now < 60
        
        if is_expired:
            print("Token is expired, trying to refresh...")
            try:
                # Create a Spotify OAuth instance
                sp_oauth = SpotifyOAuth(
                    client_id=SPOTIFY_CLIENT_ID,
                    client_secret=SPOTIFY_CLIENT_SECRET,
                    redirect_uri=SPOTIFY_REDIRECT_URI,
                    scope='playlist-read-private playlist-read-collaborative user-library-read'
                )
                
                # Try to refresh the token
                token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
                session['token_info'] = token_info
                print("Token refreshed successfully")
            except Exception as e:
                print(f"Error refreshing token: {str(e)}")
                return None
                
        return spotipy.Spotify(auth=token_info['access_token'])
        
    except Exception as e:
        print(f"Error in get_spotify: {str(e)}")
        return None

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
    try:
        file_path = os.path.join(DATA_DIR, f'{user_id}.json')
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f)
        print(f"Successfully saved data for user {user_id}")
        return True
    except Exception as e:
        print(f"Error saving data for user {user_id}: {str(e)}")
        return False

def load_user_data(user_id):
    try:
        file_path = os.path.join(DATA_DIR, f'{user_id}.json')
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                print(f"Successfully loaded data for user {user_id}")
                return data
        print(f"No data file found for user {user_id}")
        return None
    except Exception as e:
        print(f"Error loading data for user {user_id}: {str(e)}")
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

def get_git_version():
    try:
        with open('.git/refs/heads/main', 'r') as f:
            return f.read().strip()[:7]  # Get first 7 characters of commit hash
    except Exception as e:
        print(f"Error getting git version: {str(e)}")
        return 'unknown'

@app.context_processor
def inject_git_version():
    return dict(git_version=get_git_version())

@app.route('/')
def index():
    sp = get_spotify()
    if not sp:
        return redirect(url_for('login'))
        
    try:
        # Get user ID
        user_info = sp.current_user()
        user_id = user_info['id']
        session['user_id'] = user_id
        
        # Look for user's data file
        data_dir = DATA_DIR
        user_file = os.path.join(data_dir, f'{user_id}.json')
        
        if os.path.exists(user_file):
            print(f"Found data file for user: {user_id}")
            with open(user_file, 'r', encoding='utf-8') as f:
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
                                    
        # No data file found, start sync process
        print(f"No data file found for user: {user_id}")
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
    # Clear any existing session
    session.clear()
    
    sp_oauth = create_spotify_oauth()
    auth_url = sp_oauth.get_authorize_url()
    return redirect(auth_url)

@app.route('/callback')
def callback():
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
        return f"Error getting access token: {str(e)}"

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/sync_library')
def sync_library():
    print("\n=== Starting sync_library ===")
    try:
        # First check authentication
        sp = get_spotify()
        if not sp:
            print("User not authenticated")
            response = {
                'success': False,
                'error': 'Not authenticated. Please log in again.',
                'redirect': url_for('login')
            }
            print("Returning response:", response)
            return app.response_class(
                response=json.dumps(response),
                status=401,
                mimetype='application/json'
            )

        # Get user info first
        try:
            user_info = sp.current_user()
            if not user_info or 'id' not in user_info:
                print("Could not get user info")
                response = {
                    'success': False,
                    'error': 'Could not get user info. Please try logging in again.',
                    'redirect': url_for('login')
                }
                print("Returning response:", response)
                return app.response_class(
                    response=json.dumps(response),
                    status=401,
                    mimetype='application/json'
                )
                
            user_id = user_info['id']
            session['user_id'] = user_id
            print(f"Got user ID: {user_id}")
            
        except Exception as e:
            print(f"Error getting user info: {str(e)}")
            response = {
                'success': False,
                'error': 'Failed to get user info. Please try logging in again.',
                'redirect': url_for('login')
            }
            print("Returning response:", response)
            return app.response_class(
                response=json.dumps(response),
                status=401,
                mimetype='application/json'
            )

        # Check data directory
        if not os.path.exists(DATA_DIR):
            print("Data directory does not exist, creating...")
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
            except Exception as e:
                print(f"Failed to create data directory: {str(e)}")
                response = {
                    'success': False,
                    'error': 'Server storage is not accessible. Please try again later.'
                }
                print("Returning response:", response)
                return app.response_class(
                    response=json.dumps(response),
                    status=500,
                    mimetype='application/json'
                )
            
        # Test write permissions
        test_file = os.path.join(DATA_DIR, 'test.txt')
        try:
            with open(test_file, 'w') as f:
                f.write('test')
            os.remove(test_file)
            print("Data directory is writable")
        except Exception as e:
            print(f"Data directory is not writable: {str(e)}")
            response = {
                'success': False,
                'error': 'Server storage is not accessible. Please try again later.'
            }
            print("Returning response:", response)
            return app.response_class(
                response=json.dumps(response),
                status=500,
                mimetype='application/json'
            )

        # Initialize empty lists for all data
        playlists = []
        all_tracks = {}
        
        try:
            # Get user's playlists with retry
            print("Fetching user playlists...")
            results = fetch_with_retry(sp.current_user_playlists)
            if not results:
                print("Failed to fetch playlists")
                response = {
                    'success': False,
                    'error': 'Failed to fetch playlists. Please try again.'
                }
                print("Returning response:", response)
                return app.response_class(
                    response=json.dumps(response),
                    status=500,
                    mimetype='application/json'
                )
            
            print(f"Found {len(results['items'])} playlists")
            
            # Process playlists
            while results:
                for item in results['items']:
                    try:
                        print(f"Processing playlist: {item['name']}")
                        playlist_data = {
                            'id': item['id'],
                            'name': item['name'],
                            'description': item.get('description', ''),
                            'image': item['images'][0]['url'] if item.get('images') else None,
                            'tracks': []
                        }
                        playlists.append(playlist_data)
                    except Exception as e:
                        print(f"Error processing playlist {item.get('id', 'unknown')}: {str(e)}")
                        continue
                
                if not results.get('next'):
                    break
                    
                print("Fetching next page of playlists...")
                results = fetch_with_retry(sp.next, results)
                if not results:
                    break
            
            # Save data
            print("Saving data...")
            data = {
                'playlists': playlists,
                'tracks': all_tracks,
                'last_sync': int(time.time())
            }
            
            try:
                save_user_data(user_id, data)
                print("Data saved successfully")
            except Exception as e:
                print(f"Error saving data: {str(e)}")
                response = {
                    'success': False,
                    'error': 'Failed to save data. Please try again.'
                }
                print("Returning response:", response)
                return app.response_class(
                    response=json.dumps(response),
                    status=500,
                    mimetype='application/json'
                )
            
            print("Sync completed successfully")
            response = {
                'success': True,
                'playlists': playlists,
                'last_sync': data['last_sync']
            }
            print("Returning response:", response)
            return app.response_class(
                response=json.dumps(response),
                status=200,
                mimetype='application/json'
            )
            
        except Exception as e:
            print(f"Error in playlist sync: {str(e)}")
            response = {
                'success': False,
                'error': 'Failed to sync playlists. Please try again.'
            }
            print("Returning response:", response)
            return app.response_class(
                response=json.dumps(response),
                status=500,
                mimetype='application/json'
            )
            
    except Exception as e:
        print(f"Error in sync_library: {str(e)}")
        response = {
            'success': False,
            'error': 'An unexpected error occurred. Please try again.'
        }
        print("Returning response:", response)
        return app.response_class(
            response=json.dumps(response),
            status=500,
            mimetype='application/json'
        )

@app.route('/playlist/<playlist_id>')
def get_playlist(playlist_id):
    try:
        # Load data from JSON
        data_dir = DATA_DIR
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
        data_dir = DATA_DIR
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

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                             'favicon.ico', mimetype='image/vnd.microsoft.icon')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
