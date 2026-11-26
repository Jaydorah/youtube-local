'''Local playlists. Each one is a text file in the playlists folder with one
line of JSON per video, in order. Watch later is one of them, made the first
time a video is saved to it.'''
from youtube import util
from youtube import yt_app
import settings

import os
import io
import re
import csv
import json
import math
import time
import random
import shutil
import urllib.parse

import gevent
import gevent.pool
import flask
import werkzeug.datastructures
from flask import request

playlists_directory = os.path.join(settings.data_dir, "playlists")
thumbnails_directory = os.path.join(settings.data_dir, "playlist_thumbnails")

WATCH_LATER = 'Watch later'
VIDEOS_PER_PAGE = 100
MAX_NAME_LENGTH = 100
# Not allowed in file names on Windows
INVALID_NAME_CHARACTERS = set('<>:"/\\|?*')
VIDEO_ID_RE = re.compile(r'[\w-]{11}')
# youtu.be/ID, watch?v=ID, /shorts/ID, /embed/ID, /live/ID
VIDEO_LINK_RE = re.compile(
    r'(?:youtu\.be/|[?&]v=|/shorts/|/embed/|/live/|/v/)([\w-]{11})(?![\w-])')
VIDEO_INFO_KEYS = ('id', 'title', 'author', 'duration', 'author_id')


class PlaylistError(Exception):
    '''Something the user asked for that can't be done, with a message to
    show them'''


# ---------- Names and files ----------

def clean_name(name):
    '''The name with runs of spaces collapsed, or None if it can't be used
    for a new playlist. It has to work as a file name on every system.'''
    if not isinstance(name, str):
        return None
    name = ' '.join(name.split())
    if (not name or len(name) > MAX_NAME_LENGTH or name.startswith('.')
            or any(c in INVALID_NAME_CHARACTERS or ord(c) < 32
                   for c in name)):
        return None
    return name


def require_name(name):
    cleaned = clean_name(name)
    if cleaned is None:
        if not isinstance(name, str) or not name.strip():
            raise PlaylistError('Give the playlist a name')
        if len(name.strip()) > MAX_NAME_LENGTH:
            raise PlaylistError('That name is too long (%d characters at most)'
                                % MAX_NAME_LENGTH)
        raise PlaylistError('Playlist names can\'t start with a dot or use'
                            ' any of these: < > : " / \\ | ? *')
    return cleaned


def playlist_path(name):
    '''Path of an existing playlist's file. Names from older versions weren't
    checked, so only what could reach outside the folder is refused here.'''
    if (not isinstance(name, str) or not name or name.startswith('.')
            or any(c in '/\\\0' for c in name)):
        raise FileNotFoundError(name)
    return os.path.join(playlists_directory, name + '.txt')


def playlist_exists(name):
    try:
        return os.path.isfile(playlist_path(name))
    except FileNotFoundError:
        return False


def playlist_url(name):
    return util.URL_ORIGIN + '/playlists/' + urllib.parse.quote(name, safe='')


def get_playlist_names():
    '''Watch later first, then the rest alphabetically'''
    try:
        items = os.listdir(playlists_directory)
    except FileNotFoundError:
        return []
    names = []
    for item in items:
        name, ext = os.path.splitext(item)
        if ext == '.txt' and not name.startswith('.'):
            names.append(name)
    names.sort(key=lambda name: (name != WATCH_LATER, name.casefold()))
    return names


# ---------- Videos ----------

def video_entry(info):
    '''The part of a video's info a playlist keeps, from a dict or its JSON.
    None if it doesn't have a valid video id.'''
    if isinstance(info, (str, bytes)):
        try:
            info = json.loads(info)
        except ValueError:
            return None
    if not isinstance(info, dict):
        return None
    video_id = info.get('id')
    if not isinstance(video_id, str) or not VIDEO_ID_RE.fullmatch(video_id):
        return None
    entry = {}
    for key in VIDEO_INFO_KEYS:
        value = info.get(key)
        entry[key] = value if isinstance(value, str) and value else None
    added = info.get('added')
    if isinstance(added, (int, float)) and not isinstance(added, bool):
        entry['added'] = int(added)
    return entry


def read_playlist(name):
    '''The videos in the playlist, in order. FileNotFoundError if there's no
    such playlist.'''
    with open(playlist_path(name), 'r', encoding='utf-8') as file:
        data = file.read()

    videos = []
    for line in data.splitlines():
        if not line.strip():
            continue
        try:
            info = json.loads(line)
        except json.decoder.JSONDecodeError:
            print('Corrupt playlist video entry: ' + line)
            continue
        if isinstance(info, dict) and isinstance(info.get('id'), str):
            videos.append(info)
    return videos


def write_playlist(name, videos):
    '''Replaces the playlist's videos. Writes a new file and swaps it in, so
    a crash halfway can't leave half a playlist.'''
    os.makedirs(playlists_directory, exist_ok=True)
    path = playlist_path(name)
    temp_path = path + '.tmp'
    with open(temp_path, 'w', encoding='utf-8') as file:
        for video in videos:
            entry = video_entry(video)
            if entry is not None:
                file.write(json.dumps(entry, ensure_ascii=False) + '\n')
    os.replace(temp_path, path)


def video_ids_in_playlist(name):
    try:
        return set(video['id'] for video in read_playlist(name))
    except FileNotFoundError:
        return set()


def add_to_playlist(name, video_info_list, position=None):
    '''Adds the videos that aren't in the playlist yet, at the end or at
    position. Makes the playlist if there isn't one by that name. Returns how
    many were added.'''
    try:
        videos = read_playlist(name)
    except FileNotFoundError:
        name = require_name(name)
        videos = []
    ids = set(video['id'] for video in videos)
    now = int(time.time())
    new_videos = []
    for info in video_info_list:
        entry = video_entry(info)
        if entry is None or entry['id'] in ids:
            continue
        entry.setdefault('added', now)
        ids.add(entry['id'])
        new_videos.append(entry)

    if position is None or not 0 <= position <= len(videos):
        position = len(videos)
    videos[position:position] = new_videos
    if new_videos or not playlist_exists(name):
        write_playlist(name, videos)
    if new_videos:
        gevent.spawn(util.download_thumbnails,
                     os.path.join(thumbnails_directory, name),
                     [video['id'] for video in new_videos])
    if any(not video['title'] for video in new_videos):
        gevent.spawn(fill_missing_titles, name)
    return len(new_videos)


def remove_from_playlist(name, video_ids):
    '''Returns how many videos are left'''
    video_ids = set(video_ids)
    videos = read_playlist(name)
    remaining = [video for video in videos if video['id'] not in video_ids]
    write_playlist(name, remaining)

    try:
        thumbnails = set(os.listdir(os.path.join(thumbnails_directory, name)))
    except FileNotFoundError:
        pass
    else:
        for file in thumbnails & set(id + '.jpg' for id in video_ids):
            os.remove(os.path.join(thumbnails_directory, name, file))

    return len(remaining)


def move_in_playlist(name, video_id, position):
    '''Moves the video to position (an index, or 'top' or 'bottom')'''
    videos = read_playlist(name)
    for index, video in enumerate(videos):
        if video['id'] == video_id:
            break
    else:
        raise PlaylistError('That video isn\'t in the playlist anymore')
    video = videos.pop(index)
    if position == 'top':
        position = 0
    elif position == 'bottom':
        position = len(videos)
    position = max(0, min(int(position), len(videos)))
    videos.insert(position, video)
    write_playlist(name, videos)
    return position


def create_playlist(name):
    name = require_name(name)
    if playlist_exists(name):
        raise PlaylistError('There\'s already a playlist called "%s"' % name)
    write_playlist(name, [])
    return name


def rename_playlist(old_name, new_name):
    new_name = require_name(new_name)
    old_path = playlist_path(old_name)
    if not os.path.isfile(old_path):
        raise PlaylistError('That playlist doesn\'t exist anymore')
    if new_name == old_name:
        return new_name
    new_path = playlist_path(new_name)
    # Allow changing only the capitalization on case-insensitive systems
    if (os.path.exists(new_path)
            and new_name.casefold() != old_name.casefold()):
        raise PlaylistError('There\'s already a playlist called "%s"'
                            % new_name)
    os.replace(old_path, new_path)
    old_thumbnails = os.path.join(thumbnails_directory, old_name)
    if os.path.isdir(old_thumbnails):
        new_thumbnails = os.path.join(thumbnails_directory, new_name)
        shutil.rmtree(new_thumbnails, ignore_errors=True)
        os.replace(old_thumbnails, new_thumbnails)
    return new_name


def delete_playlist(name):
    try:
        os.remove(playlist_path(name))
    except FileNotFoundError:
        pass
    shutil.rmtree(os.path.join(thumbnails_directory, name), ignore_errors=True)


# ---------- Showing playlists ----------

def thumbnail_url(playlist_name, video_id, cached=None):
    '''The copy saved with the playlist if there is one, otherwise YouTube's'''
    if cached is None:
        cached = os.path.isfile(os.path.join(thumbnails_directory,
                                             playlist_name, video_id + '.jpg'))
    if cached:
        return ('/https://youtube.com/data/playlist_thumbnails/'
                + urllib.parse.quote(playlist_name, safe='')
                + '/' + video_id + '.jpg')
    return util.get_thumbnail_url(video_id)


def add_extra_info_to_videos(videos, playlist_name):
    '''Adds what's needed for rendering the videos with the item macro.
    Downloads missing thumbnails.'''
    try:
        thumbnails = set(os.listdir(os.path.join(thumbnails_directory,
                                                 playlist_name)))
    except FileNotFoundError:
        thumbnails = set()
    missing_thumbnails = []

    for video in videos:
        video['type'] = 'video'
        util.add_extra_html_info(video)
        if not video.get('title'):
            # Imported with only its id, and the title hasn't been looked up
            # yet. Set after video_info, so saving it elsewhere doesn't make
            # the id its title.
            video['title'] = video['id']
            video['title_missing'] = True
        cached = video['id'] + '.jpg' in thumbnails
        video['thumbnail'] = thumbnail_url(playlist_name, video['id'], cached)
        if not cached:
            missing_thumbnails.append(video['id'])

    if missing_thumbnails:
        gevent.spawn(util.download_thumbnails,
                     os.path.join(thumbnails_directory, playlist_name),
                     missing_thumbnails)


def duration_seconds(duration):
    '''"1:02:03" -> 3723. 0 when unknown.'''
    if not isinstance(duration, str) or not duration:
        return 0
    seconds = 0
    for part in duration.split(':'):
        try:
            seconds = 60*seconds + int(part)
        except ValueError:
            return 0
    return seconds


def format_total_duration(seconds):
    '''3723 -> "1 hr 2 min"'''
    hours, remainder = divmod(int(seconds), 3600)
    minutes = remainder // 60
    if hours and minutes:
        return '%d hr %d min' % (hours, minutes)
    if hours:
        return '%d hr' % hours
    if minutes:
        return '%d min' % minutes
    return '%d sec' % seconds if seconds else ''


def summarize(name, videos, time_updated=None):
    total = sum(duration_seconds(video.get('duration')) for video in videos)
    return {
        'name': name,
        'url': playlist_url(name),
        'video_count': len(videos),
        'thumbnail': (thumbnail_url(name, videos[0]['id'])
                      if videos else None),
        'total_duration': format_total_duration(total),
        'time_updated': time_updated,
        'is_watch_later': name == WATCH_LATER,
        'play_url': play_url(name, videos),
    }


def playlist_summary(name):
    videos = read_playlist(name)
    return summarize(name, videos, os.path.getmtime(playlist_path(name)))


def all_playlist_summaries(include_watch_later=True, contains_video=None):
    '''Watch later first (even before it exists), then the playlists
    changed most recently. With contains_video (an id), each one says
    whether that video is in it.'''
    summaries = []
    for name in get_playlist_names():
        try:
            videos = read_playlist(name)
            summary = summarize(name, videos,
                                os.path.getmtime(playlist_path(name)))
        except FileNotFoundError:
            continue
        if contains_video:
            summary['contains'] = any(video['id'] == contains_video
                                      for video in videos)
        summaries.append(summary)
    summaries.sort(key=lambda s: (not s['is_watch_later'],
                                  -(s['time_updated'] or 0)))
    if include_watch_later and not any(s['is_watch_later']
                                       for s in summaries):
        summaries.insert(0, summarize(WATCH_LATER, []))
        if contains_video:
            summaries[0]['contains'] = False
    return summaries


def play_url(name, videos, shuffle=False):
    '''The watch page for the first video, with the playlist beside it'''
    if not videos:
        return None
    first = videos[0]
    extra = ''
    if shuffle:
        seed = str(random.randrange(1, 10**9))
        first = videos[shuffled_order(len(videos), seed)[0]]
        extra = '&shuffle=' + seed
    return (util.URL_ORIGIN + '/watch?v=' + first['id'] + '&local_list='
            + urllib.parse.quote(name, safe='') + extra + '&index=0')


def shuffled_order(length, seed):
    '''The same seed always gives the same order, so a shuffled playlist
    keeps its order from one video to the next'''
    order = list(range(length))
    random.Random(seed).shuffle(order)
    return order


def watch_page_playlist(name, video_id, index=None, shuffle=None):
    '''The playlist beside the video on the watch page, in the same shape as
    YouTube playlists there. None if there's no such playlist.'''
    try:
        videos = read_playlist(name)
    except FileNotFoundError:
        return None
    if shuffle:
        videos = [videos[i] for i in shuffled_order(len(videos), shuffle)]
    add_extra_info_to_videos(videos, name)

    current_index = None
    try:
        index = int(index)
        if 0 <= index < len(videos) and videos[index]['id'] == video_id:
            current_index = index
    except (TypeError, ValueError):
        pass
    if current_index is None:
        for i, video in enumerate(videos):
            if video['id'] == video_id:
                current_index = i
                break

    url_suffix = '&local_list=' + urllib.parse.quote(name, safe='')
    if shuffle:
        url_suffix += '&shuffle=' + urllib.parse.quote(shuffle, safe='')
    for i, video in enumerate(videos):
        video['url'] = (util.URL_ORIGIN + '/watch?v=' + video['id']
                        + url_suffix + '&index=' + str(i))
    return {
        # Autoplay is remembered for all local playlists together
        'id': 'local',
        'is_local': True,
        'shuffle': bool(shuffle),
        'title': name,
        'url': playlist_url(name),
        'author': 'Your playlist',
        'author_url': playlist_url(name),
        'video_count': len(videos),
        'current_index': current_index,
        'items': videos,
    }


# ---------- Export ----------

EXPORT_FORMATS = (
    # (format, label, mimetype, file extension)
    ('json', 'JSON (can be imported back)', 'application/json', '.json'),
    ('csv', 'CSV spreadsheet', 'text/csv', '.csv'),
    ('m3u', 'M3U, for mpv or VLC', 'audio/x-mpegurl', '.m3u'),
    ('urls', 'List of links', 'text/plain', '.txt'),
    ('ids', 'List of video ids', 'text/plain', '.txt'),
)
EXPORT_FORMAT_INFO = {fmt[0]: fmt for fmt in EXPORT_FORMATS}
CSV_COLUMNS = ('Video ID', 'Title', 'Channel', 'Channel ID', 'Duration',
               'URL', 'Date added')


def watch_url(video_id):
    return 'https://www.youtube.com/watch?v=' + video_id


def csv_row(video):
    added = video.get('added')
    return [
        video['id'],
        video.get('title') or '',
        video.get('author') or '',
        video.get('author_id') or '',
        video.get('duration') or '',
        watch_url(video['id']),
        time.strftime('%Y-%m-%d', time.localtime(added)) if added else '',
    ]


def export_text(name, videos, fmt):
    entries = [entry for entry in map(video_entry, videos) if entry]
    if fmt == 'json':
        return json.dumps({'name': name, 'videos': entries}, indent=2,
                          ensure_ascii=False) + '\n'
    if fmt == 'csv':
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(CSV_COLUMNS)
        writer.writerows(csv_row(entry) for entry in entries)
        return out.getvalue()
    if fmt == 'm3u':
        lines = ['#EXTM3U', '#PLAYLIST:' + name]
        for entry in entries:
            seconds = duration_seconds(entry.get('duration')) or -1
            title = entry.get('title') or entry['id']
            if entry.get('author'):
                title = entry['author'] + ' - ' + title
            lines.append('#EXTINF:%d,%s' % (seconds, ' '.join(title.split())))
            lines.append(watch_url(entry['id']))
        return '\n'.join(lines) + '\n'
    if fmt == 'urls':
        return ''.join(watch_url(entry['id']) + '\n' for entry in entries)
    if fmt == 'ids':
        return ''.join(entry['id'] + '\n' for entry in entries)
    raise PlaylistError('Unknown export format')


def export_all_text(fmt):
    names = get_playlist_names()
    if fmt == 'json':
        playlists = []
        for name in names:
            entries = [e for e in map(video_entry, read_playlist(name)) if e]
            playlists.append({'name': name, 'videos': entries})
        return json.dumps({'playlists': playlists}, indent=2,
                          ensure_ascii=False) + '\n'
    if fmt == 'csv':
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(('Playlist',) + CSV_COLUMNS)
        for name in names:
            for entry in map(video_entry, read_playlist(name)):
                if entry:
                    writer.writerow([name] + csv_row(entry))
        return out.getvalue()
    raise PlaylistError('Unknown export format')


def download_response(text, filename, mimetype):
    response = flask.Response(text, mimetype=mimetype)
    # The plain filename is for old browsers; filename* keeps non-ASCII names
    fallback = re.sub(r'[^\w .()-]', '_', filename.encode(
        'ascii', 'replace').decode('ascii'))
    response.headers['Content-Disposition'] = (
        'attachment; filename="%s"; filename*=UTF-8\'\'%s'
        % (fallback, urllib.parse.quote(filename, safe='')))
    return response


# ---------- Import ----------

def video_ids_in_text(text):
    '''Video ids in links, or a line that's only an id'''
    ids = VIDEO_LINK_RE.findall(text)
    if not ids and VIDEO_ID_RE.fullmatch(text.strip()):
        ids = [text.strip()]
    return ids


def entries_from_lines(text):
    '''One video per line: links, ids, or an M3U playlist'''
    entries = []
    title = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('#EXTINF:'):
            title = line.partition(',')[2].strip() or None
            continue
        if not line or line.startswith('#'):
            continue
        for video_id in video_ids_in_text(line):
            entries.append({'id': video_id, 'title': title})
            title = None
    return entries


def entries_from_csv(text):
    '''Returns (playlist name or None, videos). Reads our own CSV export and
    the playlists in a Google Takeout, which only have the video ids. Older
    Takeouts start with a block about the playlist itself, with its title.'''
    rows = list(csv.reader(io.StringIO(text)))
    header_index = None
    for i, row in enumerate(rows):
        lowered = [cell.strip().lower() for cell in row]
        if any(cell in ('video id', 'video_id', 'videoid', 'id')
               for cell in lowered):
            header_index = i
            break
    if header_index is None:
        return None, entries_from_lines(text)

    name = None
    if header_index >= 2:
        top = [cell.strip().lower() for cell in rows[0]]
        if 'title' in top and len(rows[1]) > top.index('title'):
            name = rows[1][top.index('title')].strip() or None

    header = [cell.strip().lower() for cell in rows[header_index]]

    def column(*names):
        for column_name in names:
            if column_name in header:
                return header.index(column_name)
        return None
    id_column = column('video id', 'video_id', 'videoid', 'id')
    columns = {
        'title': column('title', 'video title'),
        'author': column('channel', 'author', 'channel name', 'uploader'),
        'author_id': column('channel id', 'author_id'),
        'duration': column('duration'),
    }
    entries = []
    for row in rows[header_index + 1:]:
        if len(row) <= id_column:
            continue
        ids = video_ids_in_text(row[id_column])
        if not ids:
            continue
        entry = {'id': ids[0]}
        for key, index in columns.items():
            if index is not None and index < len(row) and row[index].strip():
                entry[key] = row[index].strip()
        entries.append(entry)
    return name, entries


def entries_from_json_list(items):
    entries = []
    for item in items:
        if isinstance(item, str):
            entries.extend({'id': id} for id in video_ids_in_text(item))
        elif isinstance(item, dict):
            if 'id' not in item:
                for key in ('video_id', 'videoId', 'url'):
                    if isinstance(item.get(key), str):
                        ids = video_ids_in_text(item[key])
                        if ids:
                            item = dict(item, id=ids[0])
                            break
            entry = video_entry(item)
            if entry is not None:
                entries.append(entry)
    return entries


def parse_import(filename, data):
    '''Returns a list of (playlist name or None, videos) from a file'''
    text = data.decode('utf-8-sig', errors='replace')
    base_name, extension = os.path.splitext(os.path.basename(filename or ''))
    extension = extension.lower()
    # Google Takeout calls the file "<playlist name>-videos.csv"
    base_name = re.sub(r'-videos$', '', base_name) or None

    if extension == '.json' or text.lstrip().startswith(('{', '[')):
        try:
            content = json.loads(text)
        except ValueError:
            raise PlaylistError('%s isn\'t valid JSON' % filename)
        if isinstance(content, dict) and isinstance(
                content.get('playlists'), list):
            return [(p.get('name') or base_name,
                     entries_from_json_list(p.get('videos') or []))
                    for p in content['playlists'] if isinstance(p, dict)]
        if isinstance(content, dict):
            videos = content.get('videos') or content.get('items') or []
            return [(content.get('name') or content.get('title')
                     or base_name, entries_from_json_list(videos))]
        if isinstance(content, list):
            return [(base_name, entries_from_json_list(content))]
        raise PlaylistError('%s doesn\'t have any videos in it' % filename)
    if extension == '.csv':
        name, entries = entries_from_csv(text)
        return [(name or base_name, entries)]
    return [(base_name, entries_from_lines(text))]


def unused_name(name):
    '''"Imported", "Imported (2)", ...'''
    candidate = name
    number = 2
    while playlist_exists(candidate):
        candidate = '%s (%d)' % (name, number)
        number += 1
    return candidate


def import_playlists(parsed, name_override=None):
    '''Adds the videos to the playlists with those names (merging into
    existing ones). Returns [(name, number of videos added)].'''
    results = []
    for name, entries in parsed:
        name = clean_name(name_override or '') or clean_name(name or '')
        if name is None:
            name = unused_name('Imported playlist')
        results.append((name, add_to_playlist(name, entries)))
    return results


OEMBED_URL = 'https://www.youtube.com/oembed?format=json&url='
_titles_being_filled = set()

def fetch_title(video_id):
    url = OEMBED_URL + urllib.parse.quote(watch_url(video_id), safe='')
    try:
        info = json.loads(util.fetch_url(url).decode('utf-8'))
    except (util.FetchError, ValueError, OSError) as e:
        # deleted and private videos come back 401 or 404
        print('Couldn\'t look up the title of %s: %s' % (video_id, e))
        return None
    if not isinstance(info, dict) or not info.get('title'):
        return None
    return {'title': info['title'], 'author': info.get('author_name')}


def fill_missing_titles(name):
    '''Imported lists of links and Google Takeout files only have video ids,
    and a video saved while YouTube wouldn't give its info has none either.
    Looks up their titles and channels in the background, a few at a time.'''
    if name in _titles_being_filled:
        return
    _titles_being_filled.add(name)
    tried = set()
    pool = gevent.pool.Pool(3)
    try:
        # Again until there are none left to try, for videos added meanwhile
        while True:
            try:
                missing = [video['id'] for video in read_playlist(name)
                           if not video.get('title')
                           and video['id'] not in tried]
            except FileNotFoundError:
                return
            if not missing:
                return
            tried.update(missing)
            for start in range(0, len(missing), 30):
                batch = missing[start:start+30]
                found = dict(zip(batch, pool.map(fetch_title, batch)))
                found = {id: info for id, info in found.items() if info}
                if not found:
                    continue
                # read again, since the playlist may have changed meanwhile
                try:
                    videos = read_playlist(name)
                except FileNotFoundError:
                    return
                for video in videos:
                    if video['id'] in found and not video.get('title'):
                        video.update(found[video['id']])
                write_playlist(name, videos)
    finally:
        _titles_being_filled.discard(name)


# ---------- Pages ----------

def page_message():
    '''Message after a form on these pages was sent, from the redirect'''
    return {
        'text': request.args.get('message'),
        'is_error': request.args.get('error') == '1',
    }


def redirect_with_message(url, message, error=False):
    separator = '&' if '?' in url else '?'
    url += separator + urllib.parse.urlencode(
        {'message': message, 'error': '1' if error else '0'})
    return flask.redirect(url, 303)


@yt_app.route('/playlists', methods=['GET'])
@yt_app.route('/playlists/<playlist_name>', methods=['GET'])
def get_local_playlist_page(playlist_name=None):
    from youtube import library
    if playlist_name is None:
        return flask.render_template('local_playlists_list.html',
            playlists = all_playlist_summaries(),
            queue = library.queue_summary(),
            export_formats = EXPORT_FORMATS,
            message = page_message(),
        )

    try:
        videos = read_playlist(playlist_name)
        time_updated = os.path.getmtime(playlist_path(playlist_name))
    except FileNotFoundError:
        if playlist_name != WATCH_LATER:
            return flask.render_template('error.html',
                error_message='There\'s no playlist called "%s"'
                              % playlist_name), 404
        videos, time_updated = [], None

    summary = summarize(playlist_name, videos, time_updated)
    num_pages = max(1, math.ceil(len(videos)/VIDEOS_PER_PAGE))
    try:
        page = max(1, min(int(request.args.get('page', 1)), num_pages))
    except ValueError:
        page = 1
    offset = VIDEOS_PER_PAGE*(page - 1)
    page_videos = videos[offset:offset + VIDEOS_PER_PAGE]
    add_extra_info_to_videos(page_videos, playlist_name)
    return flask.render_template('local_playlist.html',
        kind = 'playlist',
        header_playlist_names = get_playlist_names(),
        playlist_name = playlist_name,
        summary = summary,
        videos = page_videos,
        offset = offset,
        num_pages = num_pages,
        play_url = play_url(playlist_name, videos),
        shuffle_url = play_url(playlist_name, videos, shuffle=True),
        export_formats = EXPORT_FORMATS,
        # so the message isn't shown again on the other pages
        parameters_dictionary = werkzeug.datastructures.MultiDict(
            (key, value) for key, value in request.args.items(multi=True)
            if key not in ('message', 'error')),
        message = page_message(),
    )


@yt_app.route('/playlists/<playlist_name>/export')
def export_playlist(playlist_name):
    fmt = request.args.get('format', 'json')
    if fmt not in EXPORT_FORMAT_INFO:
        flask.abort(400)
    try:
        videos = read_playlist(playlist_name)
    except FileNotFoundError:
        flask.abort(404)
    _, _, mimetype, extension = EXPORT_FORMAT_INFO[fmt]
    return download_response(export_text(playlist_name, videos, fmt),
                             playlist_name + extension, mimetype)


@yt_app.route('/playlists_export')
def export_all_playlists():
    fmt = request.args.get('format', 'json')
    if fmt not in ('json', 'csv'):
        flask.abort(400)
    extension = '.json' if fmt == 'json' else '.csv'
    filename = 'youtube-local playlists ' + time.strftime('%Y-%m-%d')
    return download_response(export_all_text(fmt), filename + extension,
                             EXPORT_FORMAT_INFO[fmt][2])


@yt_app.route('/playlists/<playlist_name>', methods=['POST'])
def path_edit_playlist(playlist_name):
    '''Changes to the playlist from that playlist's page'''
    action = request.values.get('action')
    url = playlist_url(playlist_name)
    try:
        if action == 'remove':
            video_ids = request.values.getlist('video_id')
            for info in request.values.getlist('video_info_list'):
                entry = video_entry(info)
                if entry:
                    video_ids.append(entry['id'])
            remaining = remove_from_playlist(playlist_name, video_ids)
            page = min(int(request.values.get('page', 1) or 1),
                       max(1, math.ceil(remaining/VIDEOS_PER_PAGE)))
            return flask.redirect(url + '?page=' + str(page), 303)
        elif action in ('remove_playlist', 'delete'):
            delete_playlist(playlist_name)
            return redirect_with_message(
                util.URL_ORIGIN + '/playlists',
                'Deleted "%s"' % playlist_name)
        elif action == 'rename':
            new_name = rename_playlist(playlist_name,
                                       request.values.get('new_name'))
            return flask.redirect(playlist_url(new_name), 303)
        elif action == 'move':
            move_in_playlist(playlist_name, request.values.get('video_id'),
                             request.values.get('position'))
            return flask.redirect(url, 303)
        elif action == 'export':
            # the export form from before the export links
            fmt = request.values.get('export_format', 'json')
            if fmt not in EXPORT_FORMAT_INFO:
                flask.abort(400)
            _, _, mimetype, extension = EXPORT_FORMAT_INFO[fmt]
            return download_response(
                export_text(playlist_name, read_playlist(playlist_name), fmt),
                playlist_name + extension, mimetype)
    except PlaylistError as e:
        return redirect_with_message(url, str(e), error=True)
    except FileNotFoundError:
        flask.abort(404)
    flask.abort(400)


@yt_app.route('/playlists_create', methods=['POST'])
def create_playlist_page():
    try:
        name = create_playlist(request.values.get('playlist_name'))
    except PlaylistError as e:
        return redirect_with_message(util.URL_ORIGIN + '/playlists', str(e),
                                     error=True)
    return flask.redirect(playlist_url(name), 303)


@yt_app.route('/playlists_import', methods=['POST'])
def import_playlists_page():
    back = util.URL_ORIGIN + '/playlists'
    parsed = []
    try:
        for file in request.files.getlist('files'):
            if file and file.filename:
                parsed.extend(parse_import(file.filename, file.read()))
        pasted = request.values.get('links', '')
        if pasted.strip():
            parsed.append((None, entries_from_lines(pasted)))
        if not parsed:
            raise PlaylistError('Choose a file or paste some links to import')
        if not any(entries for _, entries in parsed):
            raise PlaylistError('Didn\'t find any videos in that')
        # The name box is for when there's only one playlist
        name_override = request.values.get('playlist_name', '').strip()
        if name_override and len(parsed) > 1:
            name_override = None
        if name_override:
            name_override = require_name(name_override)
        results = import_playlists(parsed, name_override)
    except PlaylistError as e:
        return redirect_with_message(back, str(e), error=True)

    total = sum(added for _, added in results)
    if len(results) == 1:
        name = results[0][0]
        return redirect_with_message(
            playlist_url(name),
            'Imported %d video%s into "%s"' % (total, 's'*(total != 1), name))
    return redirect_with_message(
        back, 'Imported %d video%s into %d playlists'
              % (total, 's'*(total != 1), len(results)))


@yt_app.route('/edit_playlist', methods=['POST'])
def edit_playlist():
    '''Called when adding videos to a playlist from elsewhere'''
    if request.values['action'] == 'add':
        try:
            add_to_playlist(request.values['playlist_name'],
                            request.values.getlist('video_info_list'))
        except PlaylistError as e:
            return str(e), 400
        return '', 204
    else:
        flask.abort(400)


@yt_app.route('/data/playlist_thumbnails/<playlist_name>/<thumbnail>')
def serve_thumbnail(playlist_name, thumbnail):
    # .. is necessary because flask always uses the application directory at ./youtube, not the working directory
    return flask.send_from_directory(os.path.join('..', thumbnails_directory, playlist_name), thumbnail)
