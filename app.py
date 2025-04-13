import os
import json
from flask import Flask, render_template, session, redirect, request, url_for, jsonify, send_from_directory
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

        # Get user ID for data storage
        user_info = sp.current_user()
        user_id = user_info['id']
        session['user_id'] = user_id

        # Initialize empty lists for all data
        playlists = []
        all_tracks = {}
        track_playlist_map = {}
        
        try:
            # Get user's playlists with retry
            results = fetch_with_retry(sp.current_user_playlists)
            
            while True:
                for item in results['items']:
                    try:
                        # Get full playlist data with retry
                        full_playlist = fetch_with_retry(sp.playlist, item['id'])
                        
                        # Basic playlist info
                        playlist_data = {
                            'id': item['id'],
                            'name': item['name'],
                            'description': item.get('description', ''),
                            'image': item['images'][0]['url'] if item['images'] else None,
                            'tracks': []
                        }
                        
                        # Get all tracks for this playlist
                        tracks = []
                        track_results = full_playlist['tracks']
                        
                        while True:
                            for track_item in track_results['items']:
                                if track_item['track']:  # Ensure track exists
                                    track = track_item['track']
                                    track_data = {
                                        'id': track['id'],
                                        'name': track['name'],
                                        'duration_ms': track['duration_ms'],
                                        'artists': [artist['name'] for artist in track['artists']],
                                        'album': track['album']['name'],
                                        'image': track['album']['images'][0]['url'] if track['album']['images'] else None
                                    }
                                    tracks.append(track_data)
                                    
                                    # Update track-playlist mapping
                                    if track['id'] not in track_playlist_map:
                                        track_playlist_map[track['id']] = []
                                    track_playlist_map[track['id']].append({
                                        'id': playlist_data['id'],
                                        'name': playlist_data['name']
                                    })
                            
                            if not track_results['next']:
                                break
                                
                            track_results = fetch_with_retry(sp.next, track_results)
                        
                        # Store tracks for this playlist
                        all_tracks[item['id']] = tracks
                        playlist_data['tracks'] = tracks
                        playlists.append(playlist_data)
                        
                    except Exception as e:
                        print(f"Error processing playlist {item['id']}: {str(e)}")
                        continue
                
                if not results['next']:
                    break
                    
                results = fetch_with_retry(sp.next, results)
            
            # Save optimized data
            data = {
                'playlists': playlists,
                'tracks': all_tracks,
                'last_sync': int(time.time())
            }
            
            # Save to user-specific file
            save_user_data(user_id, data)
            
            return jsonify({
                'success': True,
                'playlists': playlists,
                'last_sync': data['last_sync']
            })
            
        except Exception as e:
            print(f"Error in playlist sync: {str(e)}")
            return jsonify({'success': False, 'error': str(e)})
            
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

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                             'favicon.ico', mimetype='image/vnd.microsoft.icon')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
