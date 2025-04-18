import os
import json
from flask import Flask, render_template, session, redirect, request, url_for, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
import time
from werkzeug.exceptions import HTTPException
import sys

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

# Page size configuration
PLAYLIST_PAGE_SIZE = int(os.getenv('PLAYLIST_PAGE_SIZE', '20'))

def create_spotify_oauth():
    return SpotifyOAuth(
        client_id=SPOTIFY_CLIENT_ID,
        client_secret=SPOTIFY_CLIENT_SECRET,
        redirect_uri=SPOTIFY_REDIRECT_URI,
        scope=SCOPE,
        cache_path='.spotify_cache'  # Enable token caching
    )

def get_spotify():
    """Get an authenticated Spotify client with proper timeout settings"""
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
                # Create a Spotify OAuth instance with timeout settings
                sp_oauth = SpotifyOAuth(
                    client_id=SPOTIFY_CLIENT_ID,
                    client_secret=SPOTIFY_CLIENT_SECRET,
                    redirect_uri=SPOTIFY_REDIRECT_URI,
                    scope=SCOPE,
                    requests_timeout=300,  # 5 minutes timeout
                    retries=3,  # Retry failed requests
                    cache_path='.spotify_cache'
                )
                
                # Try to refresh the token
                token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
                session['token_info'] = token_info
                print("Token refreshed successfully")
            except Exception as e:
                print(f"Error refreshing token: {str(e)}")
                return None
                
        # Create client with timeout settings
        sp = spotipy.Spotify(
            auth=token_info['access_token'],
            requests_timeout=300,  # 5 minutes timeout
            retries=3  # Retry failed requests
        )
        return sp
        
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
    # Calculate total duration
    total_duration_ms = sum(track['duration_ms'] for track in tracks)
    
    # Get unique artists and years
    unique_artists = set()
    years = set()
    for track in tracks:
        for artist in track['artists']:
            unique_artists.add(artist['name'])
        if 'album' in track and track['album'].get('release_date'):
            try:
                year = int(track['album']['release_date'][:4])
                years.add(year)
            except (ValueError, TypeError):
                pass
    
    # Add other playlists information to tracks
    for track in tracks:
        other_playlists = []
        if track['id'] in track_playlist_map:
            other_playlists = [p for p in track_playlist_map[track['id']] 
                             if p['id'] != playlist['id']]
        track['other_playlists'] = other_playlists
    
    # Parse folder path from playlist name
    folder = None
    name = playlist['name']
    if '_' in name:
        parts = name.split('_')
        if len(parts) > 1:
            folder = {
                'name': parts[0].strip(),
                'path': name[:name.rindex('_')].strip()
            }
            name = parts[-1].strip()
    
    return {
        'id': playlist['id'],
        'name': name,
        'description': playlist.get('description', ''),
        'images': playlist.get('images', []),
        'folder': folder,
        'owner': {
            'display_name': playlist['owner']['display_name'],
            'external_urls': playlist['owner']['external_urls'],
            'href': playlist['owner']['href'],
            'id': playlist['owner']['id'],
            'type': playlist['owner']['type'],
            'uri': playlist['owner']['uri']
        },
        'tracks_total': len(tracks),
        'duration_ms': total_duration_ms,
        'unique_artists': len(unique_artists),
        'years': sorted(list(years)) if years else [],
        'tracks': tracks
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
    return dict(deployed_version=get_git_version())

@app.after_request
def after_request(response):
    """Add headers to both force latest IE rendering engine or Chrome Frame,
    and also to cache the rendered page for 10 minutes."""
    response.headers["X-UA-Compatible"] = "IE=Edge,chrome=1"
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    
    # Handle CORS
    origin = request.headers.get('Origin')
    if origin:
        # Allow the specific origin that made the request
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    
    return response

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

def get_playlist_folder_info(sp):
    """Get playlist folder information from Spotify"""
    try:
        # First, get all folders
        folders_response = sp._get('me/folders')  # Using internal _get since this is not in spotipy yet
        folders = folders_response.get('items', [])
        
        # Then get folder contents
        folder_contents = {}
        for folder in folders:
            folder_id = folder['id']
            contents = sp._get(f'folders/{folder_id}/items')
            folder_contents[folder_id] = {
                'name': folder['name'],
                'items': contents.get('items', [])
            }
        
        return folders, folder_contents
    except Exception as e:
        print(f"Error getting folder info: {str(e)}")
        return [], {}

@app.route('/sync_library', methods=['GET', 'OPTIONS'])
def sync_library():
    if request.method == 'OPTIONS':
        response = app.response_class(
            response="",
            status=200,
            mimetype='text/plain'
        )
        return response
    try:
        sp = get_spotify()
        if not sp:
            response = {
                'success': False,
                'error': 'Not authenticated.'
            }
            return app.response_class(
                response=json.dumps(response),
                status=401,
                mimetype='application/json'
            )
        try:
            playlists = []
            track_playlist_map = {}
            # Now load 22 playlists, with pagination (10 per page)
            results = sp.current_user_playlists(limit=PLAYLIST_PAGE_SIZE)
            total_to_process = min(22, results['total'])
            total_batches = (total_to_process + PLAYLIST_PAGE_SIZE - 1) // PLAYLIST_PAGE_SIZE
            current_batch = 1
            processed = 0
            def format_sse(data):
                try:
                    if isinstance(data, dict):
                        if 'progress' in data:
                            progress = data['progress']
                            data = {
                                'type': 'progress',
                                'data': {
                                    'current': int(progress.get('current', 0)),
                                    'total': int(progress.get('total', 0)),
                                    'playlist': str(progress.get('playlist', ''))
                                }
                            }
                        elif 'success' in data:
                            data = {
                                'type': 'complete',
                                'data': {
                                    'success': bool(data.get('success')),
                                    'error': str(data.get('error', '')),
                                    'playlists': data.get('playlists', []),
                                    'last_sync': int(data.get('last_sync', 0))
                                }
                            }
                    json_str = json.dumps(data, default=str)
                    return f"data: {json_str}\n\n"
                except Exception as e:
                    print(f"Error formatting SSE data: {str(e)}")
                    return f"data: {{\"type\":\"error\",\"message\":\"Internal server error\"}}\n\n"
            def generate():
                nonlocal current_batch, processed, playlists, results
                try:
                    yield format_sse({
                        'progress': {
                            'current': current_batch,
                            'total': total_batches,
                            'playlist': 'Starting...'
                        }
                    })
                    sys.stdout.flush()
                    while results and processed < total_to_process:
                        batch_size = min(len(results['items']), total_to_process - processed)
                        items_to_process = results['items'][:batch_size]
                        processed += batch_size
                        for item in items_to_process:
                            try:
                                full_playlist = sp.playlist(item['id'])
                                playlist_tracks = []
                                tracks_results = sp.playlist_tracks(item['id'])
                                while tracks_results:
                                    for track_item in tracks_results['items']:
                                        if track_item['track']:
                                            track = track_item['track']
                                            playlist_tracks.append(track)
                                            if track['id'] not in track_playlist_map:
                                                track_playlist_map[track['id']] = []
                                            track_playlist_map[track['id']].append({
                                                'id': item['id'],
                                                'name': item['name']
                                            })
                                    if not tracks_results['next']:
                                        break
                                    tracks_results = sp.next(tracks_results)
                                optimized_playlist = optimize_playlist_data(full_playlist, playlist_tracks, track_playlist_map)
                                playlists.append(optimized_playlist)
                                yield format_sse({
                                    'progress': {
                                        'current': current_batch,
                                        'total': total_batches,
                                        'playlist': item['name']
                                    }
                                })
                                sys.stdout.flush()
                            except Exception as e:
                                print(f"Error processing playlist {item['name']}: {str(e)}")
                                continue
                        user_id = session.get('user_id')
                        if user_id:
                            batch_data = {
                                'playlists': playlists,
                                'last_sync': int(time.time())
                            }
                            save_user_data(user_id, batch_data)
                        # Go to next batch if needed
                        if processed < total_to_process and results['next']:
                            results = sp.next(results)
                            current_batch += 1
                            yield format_sse({
                                'progress': {
                                    'current': current_batch,
                                    'total': total_batches
                                }
                            })
                            sys.stdout.flush()
                        else:
                            break
                    data = {
                        'playlists': playlists,
                        'last_sync': int(time.time())
                    }
                    user_id = session.get('user_id')
                    if user_id:
                        try:
                            save_user_data(user_id, data)
                        except Exception as e:
                            print(f"Error saving data: {str(e)}")
                            yield format_sse({
                                'success': False,
                                'error': 'Failed to save data'
                            })
                            sys.stdout.flush()
                            return
                    # FINAL SSE message
                    yield format_sse({
                        'success': True,
                        'playlists': playlists,
                        'last_sync': data['last_sync']
                    })
                    sys.stdout.flush()
                except Exception as e:
                    print(f"Exception in SSE generator: {str(e)}")
                    yield format_sse({'type': 'error', 'message': str(e)})
                    sys.stdout.flush()
            try:
                response = Response(
                    stream_with_context(generate()),
                    mimetype='text/event-stream',
                    headers={
                        'Cache-Control': 'no-cache, no-store, must-revalidate, no-transform',
                        'Connection': 'keep-alive',
                        'X-Accel-Buffering': 'no',
                        'Content-Type': 'text/event-stream; charset=utf-8'
                    }
                )
                response.timeout = None
                return response
            except Exception as e:
                print(f"Error in SSE generator: {str(e)}")
                return app.response_class(
                    response=json.dumps({'success': False, 'error': 'Failed to sync playlists. Please try again.'}),
                    status=500,
                    mimetype='application/json'
                )
        except Exception as e:
            print(f"Error in playlist sync: {str(e)}")
            return app.response_class(
                response=json.dumps({'success': False, 'error': 'Failed to sync playlists. Please try again.'}),
                status=500,
                mimetype='application/json'
            )
    except Exception as e:
        print(f"Error in sync_library: {str(e)}")
        return app.response_class(
            response=json.dumps({'success': False, 'error': 'An unexpected error occurred. Please try again.'}),
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
            
        # The tracks are now included in the playlist object itself
        return jsonify({
            'success': True,
            'playlist': playlist,
            'tracks': playlist['tracks']  # Get tracks directly from the playlist object
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
            
        # Get page size from environment variable
        page_size = PLAYLIST_PAGE_SIZE
            
        return jsonify({
            'success': True,
            'playlists': data['playlists'],
            'last_sync': data.get('last_sync', 0),
            'page_size': page_size
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
