import os
import json
from flask import Flask, render_template, session, redirect, request, url_for, jsonify
from flask_cors import CORS
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from dotenv import load_dotenv
import time

load_dotenv()

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-here')  # Use consistent secret key

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
    try:
        sp = get_spotify()
        if not sp:
            return jsonify({'success': False, 'error': 'Not authenticated'})

        # Get all user playlists
        playlists = []
        offset = 0
        while True:
            results = sp.current_user_playlists(offset=offset)
            if not results['items']:
                break
            
            for playlist in results['items']:
                # Get full playlist details including tracks
                full_playlist = sp.playlist(playlist['id'])
                
                # Extract folder name if present (format: "folder / playlist")
                name_parts = full_playlist['name'].split(' / ', 1)
                folder = name_parts[0] if len(name_parts) > 1 else None
                playlist_name = name_parts[1] if len(name_parts) > 1 else name_parts[0]
                
                playlist_data = {
                    'id': full_playlist['id'],
                    'name': full_playlist['name'],  # Keep original name with folder
                    'images': full_playlist['images'],
                    'tracks_total': full_playlist['tracks']['total'],
                    'duration_ms': sum(track['track']['duration_ms'] for track in full_playlist['tracks']['items'] if track['track']),
                    'tracks': [{'id': track['track']['id'], 
                              'name': track['track']['name'],
                              'other_playlists': []} for track in full_playlist['tracks']['items'] if track['track']]
                }
                playlists.append(playlist_data)
            
            offset += len(results['items'])
            if len(results['items']) < 50:
                break

        # Create a map of track IDs to playlists for finding duplicates
        track_playlist_map = {}
        for playlist in playlists:
            for track in playlist['tracks']:
                if track['id'] not in track_playlist_map:
                    track_playlist_map[track['id']] = []
                track_playlist_map[track['id']].append(playlist['id'])

        # Update tracks with other playlist information
        for playlist in playlists:
            for track in playlist['tracks']:
                track['other_playlists'] = [p_id for p_id in track_playlist_map[track['id']] 
                                          if p_id != playlist['id']]

        # Save optimized data
        data = {
            'playlists': playlists,
            'last_sync': int(time.time())
        }
        
        # Save to user-specific file
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'success': False, 'error': 'User ID not found in session'})
            
        filename = os.path.join('data', f'{user_id}.json')
        os.makedirs('data', exist_ok=True)
        
        with open(filename, 'w') as f:
            json.dump(data, f)

        return jsonify({
            'success': True,
            'playlists': playlists,
            'last_sync': data['last_sync']
        })

    except Exception as e:
        print(f"Error in sync_library: {str(e)}")
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
