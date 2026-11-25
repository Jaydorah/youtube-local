'''Remembers where the user stopped watching each video, so thumbnails can
show a progress bar and the watch page can pick up where they left off.
Stored in the subscriptions database so the feed can hide watched videos.'''
from youtube import yt_app
from youtube.subscriptions import open_database

import re
import time

import flask
from flask import request

# A video counts as watched once this much of it has been played, so
# skipping the end screen still counts
WATCHED_FRACTION = 0.9
# Less than this many seconds in doesn't count as having started the video
MIN_POSITION = 5
# Go back this many seconds when resuming, to make it easier to pick up
# where you were
RESUME_REWIND = 3

VIDEO_ID_RE = re.compile(r'[\w-]{11}')


def is_watched(position, duration):
    return position >= duration*WATCHED_FRACTION


def get_progress(video_id):
    '''Returns (position, duration, watched) or None'''
    with open_database() as connection:
        with connection as cursor:
            return cursor.execute(
                '''SELECT position, duration, watched FROM watch_progress
                   WHERE video_id = ?''', [video_id]).fetchone()


def resume_position(video_id):
    '''Where to start the video if the user didn't finish it last time'''
    progress = get_progress(video_id)
    if progress is None:
        return 0
    position, duration, watched = progress
    if is_watched(position, duration) or position < MIN_POSITION:
        return 0
    return max(0, int(position) - RESUME_REWIND)


def save_progress(video_id, position, duration):
    watched = int(is_watched(position, duration))
    with open_database() as connection:
        with connection as cursor:
            # Once watched, a video stays watched even if it's restarted.
            # (Not using INSERT ... ON CONFLICT because the SQLite that
            # comes with Python 3.7 can be too old for it.)
            update = cursor.execute(
                '''UPDATE watch_progress
                   SET position = ?, duration = ?,
                       watched = MAX(watched, ?), time_updated = ?
                   WHERE video_id = ?''',
                [position, duration, watched, int(time.time()), video_id])
            if update.rowcount == 0:
                cursor.execute(
                    '''INSERT INTO watch_progress
                       (video_id, position, duration, watched, time_updated)
                       VALUES (?, ?, ?, ?, ?)''',
                    [video_id, position, duration, watched,
                     int(time.time())])


@yt_app.route('/watch_progress', methods=['GET'])
def get_watch_progress():
    '''?ids=id1,id2,...
    -> {id: {"position": .., "duration": .., "watched": ..}}'''
    requested_ids = request.args.get('ids', '').split(',')
    video_ids = [video_id for video_id in requested_ids
                 if VIDEO_ID_RE.fullmatch(video_id)][0:500]
    result = {}
    if video_ids:
        with open_database() as connection:
            with connection as cursor:
                rows = cursor.execute(
                    '''SELECT video_id, position, duration, watched
                       FROM watch_progress
                       WHERE video_id IN ('''
                    + ','.join('?'*len(video_ids)) + ')',
                    video_ids).fetchall()
        for video_id, position, duration, watched in rows:
            result[video_id] = {
                'position': position,
                'duration': duration,
                'watched': bool(watched),
            }
    response = flask.jsonify(result)
    response.headers['Cache-Control'] = 'no-store'
    return response


@yt_app.route('/watch_progress', methods=['POST'])
def post_watch_progress():
    '''Form fields: video_id, position, duration (seconds)'''
    video_id = request.values.get('video_id', '')
    try:
        position = float(request.values['position'])
        duration = float(request.values['duration'])
    except (KeyError, ValueError):
        flask.abort(400)
    if (not VIDEO_ID_RE.fullmatch(video_id)
            # NaN and infinity (livestreams) fail these too
            or not 0 < duration < 7*24*3600
            or not 0 <= position <= duration + 1):
        flask.abort(400)
    if position >= MIN_POSITION:
        save_progress(video_id, min(position, duration), duration)
    return '', 204
