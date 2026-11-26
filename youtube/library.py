'''The queue and Playlist mode, and the JSON API behind the
menu on each video and the Playlists button in the header.

The queue is what plays next: each video in it is taken out once it's
opened, and when a video ends the first one left plays, before Autoplay
would pick a related video.'''
from youtube import yt_app, util, local_playlist
from youtube.local_playlist import video_entry, PlaylistError, WATCH_LATER
import settings

import os
import json
import random
import urllib.parse

import flask
from flask import request

queue_path = os.path.join(settings.data_dir, 'queue.json')

# Holds the name of the playlist Playlist mode adds clicked videos to while
# it's on
PLAYLIST_MODE_COOKIE = 'playlist_mode'


def _load_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as file:
            content = json.load(file)
    except FileNotFoundError:
        return {}
    except ValueError:
        print('Couldn\'t read ' + path + ', starting over')
        return {}
    return content if isinstance(content, dict) else {}


def _save_json(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = path + '.tmp'
    with open(temp_path, 'w', encoding='utf-8') as file:
        json.dump(content, file, ensure_ascii=False, indent=1)
    os.replace(temp_path, path)


def display_items(entries):
    '''Saved video entries, ready for the item macro'''
    items = []
    for entry in entries:
        item = dict(entry, type='video')
        util.add_extra_html_info(item)
        item['thumbnail'] = util.get_thumbnail_url(item['id'])
        if not item.get('title'):
            item['title'] = item['id']
        items.append(item)
    return items


# ---------- Queue ----------

def get_queue():
    videos = _load_json(queue_path).get('videos')
    if not isinstance(videos, list):
        return []
    return [entry for entry in map(video_entry, videos) if entry]


def save_queue(videos):
    _save_json(queue_path, {'videos': videos})


def queue_add(info, play_next=False, position=None):
    '''Adds the video at the end, or the front for Play next, or at position
    (for Undo). Returns "added", "moved" (to the front, for Play next) or
    "already".'''
    entry = video_entry(info)
    if entry is None:
        raise PlaylistError('That isn\'t a video')
    queue = get_queue()
    for index, video in enumerate(queue):
        if video['id'] == entry['id']:
            if not play_next or index == 0:
                return 'already'
            queue.insert(0, queue.pop(index))
            save_queue(queue)
            return 'moved'
    if play_next:
        position = 0
    elif position is None or not 0 <= position <= len(queue):
        position = len(queue)
    queue.insert(position, entry)
    save_queue(queue)
    return 'added'


def queue_add_many(entries):
    '''Adds the ones that aren't queued yet. Returns how many were added.'''
    queue = get_queue()
    ids = set(video['id'] for video in queue)
    added = 0
    for entry in map(video_entry, entries):
        if entry and entry['id'] not in ids:
            ids.add(entry['id'])
            queue.append(entry)
            added += 1
    save_queue(queue)
    return added


def queue_remove(video_id):
    '''Returns where the video was, or None if it wasn't queued'''
    queue = get_queue()
    for index, video in enumerate(queue):
        if video['id'] == video_id:
            queue.pop(index)
            save_queue(queue)
            return index
    return None


def queue_move(video_id, position):
    queue = get_queue()
    for index, video in enumerate(queue):
        if video['id'] == video_id:
            break
    else:
        raise PlaylistError('That video isn\'t in the queue anymore')
    video = queue.pop(index)
    if position == 'top':
        position = 0
    elif position == 'bottom':
        position = len(queue)
    position = max(0, min(int(position), len(queue)))
    queue.insert(position, video)
    save_queue(queue)
    return position


def queue_summary():
    queue = get_queue()
    total = sum(local_playlist.duration_seconds(video.get('duration'))
                for video in queue)
    return {
        'name': 'Queue',
        'url': util.URL_ORIGIN + '/queue',
        'video_count': len(queue),
        'thumbnail': util.get_thumbnail_url(queue[0]['id']) if queue else None,
        'total_duration': local_playlist.format_total_duration(total),
        'play_url': util.URL_ORIGIN + '/queue/play' if queue else None,
    }


# ---------- Playlist mode ----------

def playlist_mode():
    '''The playlist that Playlist mode adds clicked videos to, or None when
    it's off'''
    value = request.cookies.get(PLAYLIST_MODE_COOKIE)
    if not value:
        return None
    return local_playlist.clean_name(urllib.parse.unquote(value))


# ---------- JSON API ----------

def ok(**fields):
    return flask.jsonify(dict(fields, ok=True))


def api_error(message, status=400):
    return flask.jsonify({'ok': False, 'error': message}), status


def optional_position(value):
    if value in (None, ''):
        return None
    if value in ('top', 'bottom'):
        return value
    return int(value)


def _mtime(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return 0


@yt_app.template_global()
def library_version():
    '''Changes whenever a playlist or the queue does, so library.js can keep
    what the Playlists button shows until then. Playlists are always saved by
    replacing their file, which updates the folder's time.'''
    return '%d-%d' % (_mtime(local_playlist.playlists_directory),
                      _mtime(queue_path))


@yt_app.route('/ytl-api/library')
def api_library():
    '''Everything the Playlists button and the Save dialog show'''
    video_id = request.args.get('video_id') or None
    return ok(
        version=library_version(),
        playlists=local_playlist.all_playlist_summaries(
            contains_video=video_id),
        queue=queue_summary(),
        playlist_mode=playlist_mode(),
        watch_later=WATCH_LATER,
    )


@yt_app.route('/ytl-api/playlist', methods=['POST'])
def api_playlist():
    action = request.values.get('action')
    name = request.values.get('playlist_name', '')
    video_id = request.values.get('video_id')
    try:
        if action == 'add':
            entry = video_entry(request.values.get('video_info'))
            if entry is None:
                return api_error('That isn\'t a video')
            if not local_playlist.playlist_exists(name):
                name = local_playlist.require_name(name)
            added = local_playlist.add_to_playlist(
                name, [entry], optional_position(request.values.get('position')))
            return ok(added=bool(added),
                      playlist=local_playlist.playlist_summary(name))
        elif action == 'remove':
            videos = local_playlist.read_playlist(name)
            index = next((i for i, video in enumerate(videos)
                          if video['id'] == video_id), None)
            local_playlist.remove_from_playlist(name, [video_id])
            return ok(index=index,
                      playlist=local_playlist.playlist_summary(name))
        elif action == 'create':
            name = local_playlist.create_playlist(name)
            return ok(playlist=local_playlist.playlist_summary(name))
        elif action == 'move':
            position = local_playlist.move_in_playlist(
                name, video_id,
                optional_position(request.values.get('position')))
            return ok(position=position)
        elif action == 'queue_all':
            added = queue_add_many(local_playlist.read_playlist(name))
            return ok(added=added, count=len(get_queue()))
    except PlaylistError as e:
        return api_error(str(e))
    except FileNotFoundError:
        return api_error('That playlist doesn\'t exist anymore', 404)
    except (TypeError, ValueError):
        return api_error('Bad request')
    return api_error('Unknown action')


@yt_app.route('/ytl-api/queue', methods=['GET', 'POST'])
def api_queue():
    if request.method == 'GET':
        queue = get_queue()
        return ok(videos=queue, count=len(queue))
    action = request.values.get('action')
    video_id = request.values.get('video_id')
    result = {}
    try:
        if action in ('add', 'play_next'):
            position = optional_position(request.values.get('position'))
            result['result'] = queue_add(
                request.values.get('video_info'),
                play_next=(action == 'play_next'),
                position=position if isinstance(position, int) else None)
        elif action == 'remove':
            result['index'] = queue_remove(video_id)
        elif action == 'move':
            result['position'] = queue_move(
                video_id, optional_position(request.values.get('position')))
        elif action == 'clear':
            # sent back so Undo can put it back
            result['videos'] = get_queue()
            save_queue([])
        elif action == 'restore':
            videos = json.loads(request.values.get('videos', '[]'))
            if not isinstance(videos, list):
                return api_error('Bad request')
            save_queue([entry for entry in map(video_entry, videos)
                        if entry])
        else:
            return api_error('Unknown action')
    except PlaylistError as e:
        return api_error(str(e))
    except (TypeError, ValueError):
        return api_error('Bad request')
    return ok(count=len(get_queue()), **result)


@yt_app.route('/ytl-api/queue_panel')
def api_queue_panel():
    '''The queue beside the video on the watch page, redrawn after it
    changes'''
    return flask.render_template('queue_panel.html',
        queue_videos=display_items(get_queue()),
        current_video_id=request.args.get('current'),
    )


# ---------- Pages ----------

@yt_app.route('/queue', methods=['GET'])
def queue_page():
    queue = get_queue()
    return flask.render_template('local_playlist.html',
        kind = 'queue',
        header_playlist_names = local_playlist.get_playlist_names(),
        playlist_name = 'Queue',
        summary = queue_summary(),
        videos = display_items(queue),
        offset = 0,
        num_pages = 1,
        play_url = queue_summary()['play_url'],
        shuffle_url = None,
        export_formats = local_playlist.EXPORT_FORMATS,
        parameters_dictionary = request.args,
        message = local_playlist.page_message(),
    )


@yt_app.route('/queue', methods=['POST'])
def edit_queue_page():
    action = request.values.get('action')
    back = util.URL_ORIGIN + '/queue'
    if action == 'clear':
        save_queue([])
    elif action == 'shuffle':
        queue = get_queue()
        random.shuffle(queue)
        save_queue(queue)
    elif action == 'remove':
        queue_remove(request.values.get('video_id'))
    elif action == 'move':
        try:
            queue_move(request.values.get('video_id'),
                       optional_position(request.values.get('position')))
        except (PlaylistError, TypeError, ValueError):
            pass
    elif action == 'save':
        name = request.values.get('playlist_name', '')
        try:
            if not local_playlist.playlist_exists(name):
                name = local_playlist.require_name(name)
            added = local_playlist.add_to_playlist(name, get_queue())
        except PlaylistError as e:
            return local_playlist.redirect_with_message(back, str(e),
                                                        error=True)
        return local_playlist.redirect_with_message(
            local_playlist.playlist_url(name),
            'Saved %d video%s from the queue' % (added, 's'*(added != 1)))
    else:
        flask.abort(400)
    return flask.redirect(back, 303)


@yt_app.route('/queue/play')
def play_queue():
    '''Opens the first queued video, or the one given. The watch page takes
    it out of the queue.'''
    video_id = request.args.get('v')
    queue = get_queue()
    if video_id:
        return flask.redirect(util.URL_ORIGIN + '/watch?v='
                              + urllib.parse.quote(video_id), 303)
    if not queue:
        return flask.redirect(util.URL_ORIGIN + '/queue', 303)
    return flask.redirect(util.URL_ORIGIN + '/watch?v=' + queue[0]['id'], 303)
