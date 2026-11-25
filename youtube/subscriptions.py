from youtube import util, yt_data_extract, channel, playlist
from youtube import yt_app, time_display
import settings

import sqlite3
import os
import time
import gevent
import json
import traceback
import contextlib
import defusedxml.ElementTree
import urllib.parse
import math
import secrets
import collections
import calendar # bullshit! https://bugs.python.org/issue6280
import csv
import re

import flask
from flask import request


thumbnails_directory = os.path.join(settings.data_dir, "subscription_thumbnails")

# https://stackabuse.com/a-sqlite-tutorial-with-python/

database_path = os.path.join(settings.data_dir, "subscriptions.sqlite")

def open_database():
    if not os.path.exists(settings.data_dir):
        os.makedirs(settings.data_dir)
    connection = sqlite3.connect(database_path, check_same_thread=False)

    try:
        cursor = connection.cursor()
        cursor.execute('''PRAGMA foreign_keys = 1''')
        # Create tables if they don't exist
        cursor.execute('''CREATE TABLE IF NOT EXISTS subscribed_channels (
                              id integer PRIMARY KEY,
                              yt_channel_id text UNIQUE NOT NULL,
                              channel_name text NOT NULL,
                              time_last_checked integer DEFAULT 0,
                              next_check_time integer DEFAULT 0,
                              muted integer DEFAULT 0
                          )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS videos (
                              id integer PRIMARY KEY,
                              sql_channel_id integer NOT NULL REFERENCES subscribed_channels(id) ON UPDATE CASCADE ON DELETE CASCADE,
                              video_id text UNIQUE NOT NULL,
                              title text NOT NULL,
                              duration text,
                              time_published integer NOT NULL,
                              is_time_published_exact integer DEFAULT 0,
                              time_noticed integer NOT NULL,
                              description text,
                              watched integer default 0
                          )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS tag_associations (
                              id integer PRIMARY KEY,
                              tag text NOT NULL,
                              sql_channel_id integer NOT NULL REFERENCES subscribed_channels(id) ON UPDATE CASCADE ON DELETE CASCADE,
                              UNIQUE(tag, sql_channel_id)
                          )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS db_info (
                              version integer DEFAULT 1
                          )''')

        # Without these, the subscriptions feed reads and sorts the whole
        # videos table on every page load, and checking a channel,
        # unsubscribing, or filtering the feed by tag reads the whole
        # table once per channel.
        # The feed is sorted newest upload first. It used to be sorted by
        # when each video was first noticed, which put whatever a check
        # just found at the top even if it was uploaded days ago.
        cursor.execute('DROP INDEX IF EXISTS videos_feed_order')
        cursor.execute('''CREATE INDEX IF NOT EXISTS videos_by_publish_time
                          ON videos (time_published DESC, time_noticed DESC)''')
        cursor.execute('''CREATE INDEX IF NOT EXISTS videos_by_channel
                          ON videos (sql_channel_id, time_published DESC)''')

        # 'video', 'short', or 'live' (live now, upcoming, or a past stream).
        # NULL means unknown: saved before this column existed, and not
        # seen again since.
        video_columns = [row[1] for row in
                         cursor.execute('PRAGMA table_info(videos)')]
        if 'video_type' not in video_columns:
            cursor.execute('ALTER TABLE videos ADD COLUMN video_type text')

        # Where the user stopped watching each video, saved by the watch page
        cursor.execute('''CREATE TABLE IF NOT EXISTS watch_progress (
                              video_id text PRIMARY KEY,
                              position real NOT NULL,
                              duration real NOT NULL,
                              watched integer NOT NULL DEFAULT 0,
                              time_updated integer NOT NULL
                          )''')

        connection.commit()
    except:
        connection.rollback()
        connection.close()
        raise

    # https://stackoverflow.com/questions/19522505/using-sqlite3-in-python-with-with-keyword
    return contextlib.closing(connection)

def with_open_db(function, *args, **kwargs):
    with open_database() as connection:
        with connection as cursor:
            return function(cursor, *args, **kwargs)

def _is_subscribed(cursor, channel_id):
    result = cursor.execute('''SELECT EXISTS(
                                   SELECT 1
                                   FROM subscribed_channels
                                   WHERE yt_channel_id=?
                                   LIMIT 1
                               )''', [channel_id]).fetchone()
    return bool(result[0])

def is_subscribed(channel_id):
    if not os.path.exists(database_path):
        return False

    return with_open_db(_is_subscribed, channel_id)

def _subscribe(channels):
    ''' channels is a list of (channel_id, channel_name) '''
    channels = list(channels)
    with open_database() as connection:
        with connection as cursor:
            channel_ids_to_check = [channel[0] for channel in channels if not _is_subscribed(cursor, channel[0])]

            rows = ( (channel_id, channel_name, 0, 0) for channel_id, channel_name in channels)
            cursor.executemany('''INSERT OR IGNORE INTO subscribed_channels (yt_channel_id, channel_name, time_last_checked, next_check_time)
                                  VALUES (?, ?, ?, ?)''', rows)

    if settings.autocheck_subscriptions:
        # important that this is after the changes have been committed to database
        # otherwise the autochecker (other thread) tries checking the channel before it's in the database
        channel_names.update(channels)
        check_channels_if_necessary(channel_ids_to_check)

def delete_thumbnails(to_delete):
    for thumbnail in to_delete:
        try:
            video_id = thumbnail[0:-4]
            if video_id in existing_thumbnails:
                os.remove(os.path.join(thumbnails_directory, thumbnail))
                existing_thumbnails.remove(video_id)
        except Exception:
            print('Failed to delete thumbnail: ' + thumbnail)
            traceback.print_exc()

def _unsubscribe(cursor, channel_ids):
    ''' channel_ids is a list of channel_ids '''
    to_delete = []
    for channel_id in channel_ids:
        rows = cursor.execute('''SELECT video_id
                                 FROM videos
                                 WHERE sql_channel_id = (
                                     SELECT id
                                     FROM subscribed_channels
                                     WHERE yt_channel_id=?
                                 )''', (channel_id,)).fetchall()
        to_delete += [row[0] + '.jpg' for row in rows]

    gevent.spawn(delete_thumbnails, to_delete)
    cursor.executemany("DELETE FROM subscribed_channels WHERE yt_channel_id=?", ((channel_id, ) for channel_id in channel_ids))

# What the subscriptions feed can hide. 'watched' hides videos watched to the
# end; the others hide that type of video.
FEED_FILTERS = ('watched', 'video', 'live', 'short')

def _get_videos(cursor, number_per_page, offset, tag = None, hide = ()):
    '''Returns a full page of videos with an offset, and a value good enough to be used as the total number of videos'''
    # We ask for the next 9 pages from the database
    # Then the actual length of the results tell us if there are more than 9 pages left, and if not, how many there actually are
    # This is done since there are only 9 page buttons on display at a time
    # If there are more than 9 pages left, we give a fake value in place of the real number of results if the entire database was queried without limit
    # This fake value is sufficient to get the page button generation macro to display 9 page buttons
    # If we wish to display more buttons this logic must change
    # We cannot use tricks with the sql id for the video since we frequently have filters and other restrictions in place on the results anyway
    # TODO: This is probably not the ideal solution
    joins = ''
    conditions = ['muted = 0']
    parameters = []
    if tag is not None:
        joins = '''INNER JOIN tag_associations
                       ON videos.sql_channel_id
                          = tag_associations.sql_channel_id'''
        conditions.append('tag = ?')
        parameters.append(tag)
    if 'watched' in hide:
        conditions.append('''video_id NOT IN (
                                 SELECT video_id FROM watch_progress
                                 WHERE watched = 1
                             )''')
    hidden_types = [video_type for video_type in ('video', 'live', 'short')
                    if video_type in hide]
    if hidden_types:
        # videos of unknown type are treated as regular videos
        conditions.append("COALESCE(video_type, 'video') NOT IN ("
                          + ', '.join('?'*len(hidden_types)) + ')')
        parameters += hidden_types

    db_videos = cursor.execute(
        '''SELECT video_id, title, duration, time_published,
                  is_time_published_exact, channel_name, yt_channel_id,
                  video_type
           FROM videos
           INNER JOIN subscribed_channels
               ON videos.sql_channel_id = subscribed_channels.id
           ''' + joins + '''
           WHERE ''' + ' AND '.join(conditions) + '''
           ORDER BY time_published DESC, time_noticed DESC
           LIMIT ? OFFSET ?''',
        parameters + [number_per_page*9, offset]).fetchall()

    pseudo_number_of_videos = offset + len(db_videos)

    videos = []
    for db_video in db_videos[0:number_per_page]:
        videos.append({
            'id':   db_video[0],
            'title':    db_video[1],
            'duration': db_video[2],
            'time_published': time_display.relative_time(db_video[3]),
            # The page shows both how long ago and the date and time,
            # in the viewer's timezone
            'timestamp': db_video[3],
            # Otherwise it was estimated from YouTube's "3 weeks ago"
            'is_time_published_exact': bool(db_video[4]),
            'author':   db_video[5],
            'author_id': db_video[6],
            'author_url': '/https://www.youtube.com/channel/' + db_video[6],
            'video_type': db_video[7] or 'video',
        })

    return videos, pseudo_number_of_videos




def _get_subscribed_channels(cursor):
    for item in cursor.execute('''SELECT channel_name, yt_channel_id, muted
                                  FROM subscribed_channels
                                  ORDER BY channel_name COLLATE NOCASE'''):
        yield item


def _add_tags(cursor, channel_ids, tags):
    pairs = [(tag, yt_channel_id) for tag in tags for yt_channel_id in channel_ids]
    cursor.executemany('''INSERT OR IGNORE INTO tag_associations (tag, sql_channel_id)
                          SELECT ?, id FROM subscribed_channels WHERE yt_channel_id = ? ''', pairs)


def _remove_tags(cursor, channel_ids, tags):
    pairs = [(tag, yt_channel_id) for tag in tags for yt_channel_id in channel_ids]
    cursor.executemany('''DELETE FROM tag_associations
                          WHERE tag = ? AND sql_channel_id = (
                              SELECT id FROM subscribed_channels WHERE yt_channel_id = ?
                           )''', pairs)



def _get_tags(cursor, channel_id):
    return [row[0] for row in cursor.execute('''SELECT tag
                                                FROM tag_associations
                                                WHERE sql_channel_id = (
                                                    SELECT id FROM subscribed_channels WHERE yt_channel_id = ?
                                                )''', (channel_id,))]

def _get_all_tags(cursor):
    return [row[0] for row in cursor.execute('''SELECT DISTINCT tag FROM tag_associations''')]

def _get_channel_names(cursor, channel_ids):
    ''' returns list of (channel_id, channel_name) '''
    result = []
    for channel_id in channel_ids:
        row = cursor.execute('''SELECT channel_name
                                FROM subscribed_channels
                                WHERE yt_channel_id = ?''', (channel_id,)).fetchone()
        result.append( (channel_id, row[0]) )
    return result


def _channels_with_tag(cursor, tag, order=False, exclude_muted=False, include_muted_status=False):
    ''' returns list of (channel_id, channel_name) '''

    statement = '''SELECT yt_channel_id, channel_name'''

    if include_muted_status:
        statement += ''', muted'''

    statement += '''
                   FROM subscribed_channels
                   WHERE subscribed_channels.id IN (
                       SELECT tag_associations.sql_channel_id FROM tag_associations WHERE tag=?
                   )
                '''
    if exclude_muted:
        statement += '''AND muted != 1\n'''
    if order:
        statement += '''ORDER BY channel_name COLLATE NOCASE'''

    return cursor.execute(statement, [tag]).fetchall()

def _schedule_checking(cursor, channel_id, next_check_time):
    cursor.execute('''UPDATE subscribed_channels SET next_check_time = ? WHERE yt_channel_id = ?''', [int(next_check_time), channel_id])

def _is_muted(cursor, channel_id):
    return bool(cursor.execute('''SELECT muted FROM subscribed_channels WHERE yt_channel_id=?''', [channel_id]).fetchone()[0])

units = collections.OrderedDict([
    ('year', 31536000),   # 365*24*3600
    ('month', 2592000),   # 30*24*3600
    ('week', 604800),     # 7*24*3600
    ('day',  86400),      # 24*3600
    ('hour', 3600),
    ('minute', 60),
    ('second', 1),
])
def youtube_timestamp_to_posix(dumb_timestamp):
    ''' Given a dumbed down timestamp such as 1 year ago, 3 hours ago,
         approximates the unix time (seconds since 1/1/1970) '''
    dumb_timestamp = dumb_timestamp.lower()
    now = time.time()
    if dumb_timestamp == "just now":
        return now
    split = dumb_timestamp.split(' ')
    quantifier, unit = int(split[0]), split[1]
    if quantifier > 1:
        unit = unit[:-1]    # remove s from end
    return now - quantifier*units[unit]

def youtube_timestamp_range(dumb_timestamp):
    '''The earliest and latest unix times that YouTube's "3 days ago" can
    mean. YouTube rounds down, so that's between 3 and 4 days ago. None if
    it can't be read.'''
    try:
        latest = youtube_timestamp_to_posix(dumb_timestamp)
    except (ValueError, KeyError, IndexError):
        return None
    split = dumb_timestamp.lower().split(' ')
    unit = 'second' if split[0] == 'just' else split[1].rstrip('s')
    # a minute of leeway for the time between YouTube's page and now
    return latest - units[unit], latest + 60

def posix_to_dumbed_down(posix_time):
    '''Inverse of youtube_timestamp_to_posix.'''
    delta = int(time.time() - posix_time)
    assert delta >= 0

    if delta == 0:
        return '0 seconds ago'

    for unit_name, unit_time in units.items():
        if delta >= unit_time:
            quantifier = round(delta/unit_time)
            if quantifier == 1:
                return '1 ' + unit_name + ' ago'
            else:
                return str(quantifier) + ' ' + unit_name + 's ago'
    else:
        raise Exception()

def exact_timestamp(posix_time):
    result = time.strftime('%I:%M %p %m/%d/%y', time.localtime(posix_time))
    if result[0] == '0':    # remove 0 infront of hour (like 01:00 PM)
        return result[1:]
    return result

try:
    existing_thumbnails = set(os.path.splitext(name)[0] for name in os.listdir(thumbnails_directory))
except FileNotFoundError:
    existing_thumbnails = set()


# --- Manual checking system. Rate limited in order to support very large numbers of channels to be checked ---
# Auto checking system plugs into this for convenience, though it doesn't really need the rate limiting

# The limit counts requests to YouTube: 60 at first, then 20 every 5 seconds.
# That is the same request rate as when the limit was 30 and then 10
# channels with 2 requests each. Taking a channel from the queue counts as 1
# request (its feed). Channels that turn out to need the full check count
# the extra requests themselves in _get_upstream_videos.
check_channels_queue = util.RateLimitedQueue(initial_burst=60,
                                             subsequent_bursts=20)
checking_channels = set()

# Just to use for printing channel checking status to console without opening database
channel_names = dict()

# Progress of the current round of checking, shown on the subscriptions page.
# A round starts when channels are queued while none are being checked.
check_progress = {
    'total': 0,         # channels queued this round
    'done': 0,          # channels finished this round
    'videos_added': 0,  # videos added to the feed this round
}

def check_channel_worker():
    while True:
        channel_id = check_channels_queue.get()
        try:
            _get_upstream_videos(channel_id)
        except Exception:
            traceback.print_exc()
        finally:
            checking_channels.remove(channel_id)
            check_progress['done'] += 1

for i in range(0,5):
    gevent.spawn(check_channel_worker)
# ----------------------------



# --- Auto checking system - Spaghetti code ---
def autocheck_dispatcher():
    '''Scans the auto_check_list. Sleeps until the earliest job is due, then adds that channel to the checking queue above. Can be sent a new job through autocheck_job_application'''
    while True:
        if len(autocheck_jobs) == 0:
            new_job = autocheck_job_application.get()
            autocheck_jobs.append(new_job)
        else:
            earliest_job_index = min(range(0, len(autocheck_jobs)), key=lambda index: autocheck_jobs[index]['next_check_time']) # https://stackoverflow.com/a/11825864
            earliest_job = autocheck_jobs[earliest_job_index]
            time_until_earliest_job = earliest_job['next_check_time'] - time.time()

            if time_until_earliest_job <= -5:   # should not happen unless we're running extremely slow
                print('ERROR: autocheck_dispatcher got job scheduled in the past, skipping and rescheduling: ' + earliest_job['channel_id'] + ', ' + earliest_job['channel_name'] + ', ' + str(earliest_job['next_check_time']))
                next_check_time = time.time() + 3600*secrets.randbelow(60)/60
                with_open_db(_schedule_checking, earliest_job['channel_id'], next_check_time)
                autocheck_jobs[earliest_job_index]['next_check_time'] = next_check_time
                continue

            # make sure it's not muted
            if with_open_db(_is_muted, earliest_job['channel_id']):
                del autocheck_jobs[earliest_job_index]
                continue

            if time_until_earliest_job > 0: # it can become less than zero (in the past) when it's set to go off while the dispatcher is doing something else at that moment
                try:
                    new_job = autocheck_job_application.get(timeout = time_until_earliest_job)  # sleep for time_until_earliest_job time, but allow to be interrupted by new jobs
                except gevent.queue.Empty: # no new jobs
                    pass
                else: # new job, add it to the list
                    autocheck_jobs.append(new_job)
                    continue

            # no new jobs, time to execute the earliest job
            channel_names[earliest_job['channel_id']] = earliest_job['channel_name']
            checking_channels.add(earliest_job['channel_id'])
            check_channels_queue.put(earliest_job['channel_id'])
            del autocheck_jobs[earliest_job_index]

dispatcher_greenlet = None
def start_autocheck_system():
    global autocheck_job_application
    global autocheck_jobs
    global dispatcher_greenlet

    # job application format: dict with keys (channel_id, channel_name, next_check_time)
    autocheck_job_application = gevent.queue.Queue() # only really meant to hold 1 item, just reusing gevent's wait and timeout machinery

    autocheck_jobs = [] # list of dicts with the keys (channel_id, channel_name, next_check_time). Stores all the channels that need to be autochecked and when to check them
    with open_database() as connection:
        with connection as cursor:
            now = time.time()
            for row in cursor.execute('''SELECT yt_channel_id, channel_name, next_check_time FROM subscribed_channels WHERE muted != 1''').fetchall():

                if row[2] is None:
                    next_check_time = 0
                else:
                    next_check_time = row[2]

                # expired, check randomly within the next hour
                # note: even if it isn't scheduled in the past right now, it might end up being if it's due soon and we dont start dispatching by then, see below where time_until_earliest_job is negative
                if next_check_time < now:
                    next_check_time = now + 3600*secrets.randbelow(60)/60
                    row = (row[0], row[1], next_check_time)
                    _schedule_checking(cursor, row[0], next_check_time)
                autocheck_jobs.append({'channel_id': row[0], 'channel_name': row[1], 'next_check_time': next_check_time})
    dispatcher_greenlet = gevent.spawn(autocheck_dispatcher)

def stop_autocheck_system():
    if dispatcher_greenlet is not None:
        dispatcher_greenlet.kill()

def autocheck_setting_changed(old_value, new_value):
    if new_value:
        start_autocheck_system()
    else:
        stop_autocheck_system()

settings.add_setting_changed_hook('autocheck_subscriptions',
    autocheck_setting_changed)
if settings.autocheck_subscriptions:
    start_autocheck_system()
# ----------------------------



def check_channels_if_necessary(channel_ids):
    for channel_id in channel_ids:
        if channel_id not in checking_channels:
            if not checking_channels:
                # nothing is being checked, so this starts a new round
                check_progress.update(total=0, done=0, videos_added=0)
            checking_channels.add(channel_id)
            check_progress['total'] += 1
            check_channels_queue.put(channel_id)

def _get_atoma_feed(channel_id):
    url = 'https://www.youtube.com/feeds/videos.xml?channel_id=' + channel_id
    try:
        return util.fetch_url(url).decode('utf-8')
    except util.FetchError as e:
        # 404 is expected for terminated channels
        if e.code in ('404', '429'):
            return ''
        if e.code == '502':
            return str(e)
        raise

def _get_channel_videos_first_page(channel_id, channel_status_name):
    try:
        # First try the playlist method.
        # Shorts are always fetched so the feed's "Hide shorts" option can
        # show or hide them; the include_shorts_in_subscriptions setting is
        # now only that option's default.
        pl_json = playlist.get_videos(
            'UU' + channel_id[2:],
            1,
            include_shorts=True,
            report_text=None
        )
        pl_info = yt_data_extract.extract_playlist_info(pl_json)
        if pl_info.get('items'):
            # Keep the whole first page (up to 100), not just the newest 30:
            # on channels that post lots of shorts, 30 uploads can all be
            # shorts and the regular videos would be missed.
            return pl_info

        # Try the channel api method
        channel_json = channel.get_channel_first_page(channel_id=channel_id)
        channel_info = yt_data_extract.extract_channel_info(
            json.loads(channel_json), 'videos'
        )
        return channel_info
    except util.FetchError as e:
        if e.code == '429' and settings.route_tor:
            error_message = ('Error checking channel ' + channel_status_name
                + ': Youtube blocked the request because the'
                + ' Tor exit node is overutilized. Try getting a new exit node'
                + ' by using the New Identity button in the Tor Browser.')
            if e.ip:
                error_message += ' Exit node IP address: ' + e.ip
            print(error_message)
            return None
        elif e.code == '502':
            print('Error checking channel', channel_status_name + ':', str(e))
            return None
        raise

def _get_live_video_ids(channel_id):
    '''Ids of the channel's recent livestreams: live now, upcoming, and past
    streams. The normal uploads list doesn't mark past streams, but YouTube
    keeps a separate list of them (playlist UULV + channel id without UC).
    Returns None if it couldn't be fetched.'''
    try:
        pl_json = playlist.get_videos('UULV' + channel_id[2:], 1,
                                      report_text=None)
        pl_info = yt_data_extract.extract_playlist_info(pl_json)
    except Exception:
        traceback.print_exc()
        return None
    if pl_info.get('error'):
        # Channels that have never streamed don't have this list
        return set()
    return set(item['id'] for item in pl_info.get('items', ())
               if item.get('id'))

def _classify_video(video_item, live_ids, feed_types):
    '''Sets video_item['video_type'] to 'video', 'short', or 'live', and
    video_item['type_is_certain'] to whether YouTube said so (rather than
    it being a guess). live_ids is None if the livestream list couldn't be
    fetched.'''
    v_id = video_item['id']
    duration = (video_item.get('duration') or '').lower()
    if (live_ids and v_id in live_ids) or duration in ('live', 'upcoming'):
        video_item['video_type'], video_item['type_is_certain'] = 'live', True
    elif feed_types.get(v_id) == 'short':
        video_item['video_type'], video_item['type_is_certain'] = 'short', True
    elif v_id in feed_types:
        # The feed doesn't mark past livestreams, so this is only certain
        # if we know it's not in the livestream list
        video_item['video_type'] = 'video'
        video_item['type_is_certain'] = live_ids is not None
    else:
        # Older than the 15 videos in the feed. Shorts are up to 3 minutes
        # now, but anything a minute or under is very likely one.
        seconds = _duration_seconds(duration)
        is_short = seconds is not None and seconds <= 60
        video_item['video_type'] = 'short' if is_short else 'video'
        video_item['type_is_certain'] = False

def _duration_seconds(duration):
    '''"1:02:03" -> 3723. None if it isn't a duration (e.g. LIVE)'''
    if not duration or not re.fullmatch(r'\d+(:\d\d)*', duration):
        return None
    seconds = 0
    for part in duration.split(':'):
        seconds = seconds*60 + int(part)
    return seconds

def _parse_atoma_feed(feed, channel_status_name):
    '''Returns two dicts about the videos listed in a channel's atoma feed:
    video id -> time published, and video id -> 'short' or 'video'.
    Both are empty if the feed couldn't be read.'''
    # extract published times from atoma feed
    times_published = {}
    # The feed links shorts as youtube.com/shorts/<id>
    feed_types = {}
    try:
        def remove_bullshit(tag):
            '''Remove XML namespace bullshit from tagname. https://bugs.python.org/issue18304'''
            if '}' in tag:
                return tag[tag.rfind('}')+1:]
            return tag

        def find_element(base, tag_name):
            for element in base:
                if remove_bullshit(element.tag) == tag_name:
                    return element
            return None

        root = defusedxml.ElementTree.fromstring(feed)
        assert remove_bullshit(root.tag) == 'feed'
        for entry in root:
            if (remove_bullshit(entry.tag) != 'entry'):
                continue

            # it's yt:videoId in the xml but the yt: is turned into a namespace which is removed by remove_bullshit
            video_id_element = find_element(entry, 'videoId')
            time_published_element = find_element(entry, 'published')
            assert video_id_element is not None
            assert time_published_element is not None

            time_published = int(calendar.timegm(time.strptime(time_published_element.text, '%Y-%m-%dT%H:%M:%S+00:00')))
            times_published[video_id_element.text] = time_published

            link_element = find_element(entry, 'link')
            if link_element is not None:
                is_short = '/shorts/' in link_element.get('href', '')
                feed_types[video_id_element.text] = (
                    'short' if is_short else 'video')

    except AssertionError:
        print('Failed to read atoma feed for ' + channel_status_name)
        traceback.print_exc()
    except defusedxml.ElementTree.ParseError:
        print('Failed to read atoma feed for ' + channel_status_name)

    return times_published, feed_types

# What Youtube gives in place of a duration for videos that don't have one
# yet, such as livestreams and premieres
NON_DURATIONS = ('upcoming', 'none', 'live', '')

def _feed_shows_changes(cursor, channel_id, feed_times):
    '''Whether a channel needs the full check, going by the videos in its
    feed (video id -> time published). False only if all of those videos are
    already saved and nothing about the channel's recent videos is waiting
    to be filled in or corrected.'''
    feed_video_ids = set(feed_times)
    if not feed_video_ids:
        # No feed to go by. Some channels don't have one
        return True

    time_last_checked = cursor.execute(
        '''SELECT time_last_checked FROM subscribed_channels
           WHERE yt_channel_id=?''', [channel_id]).fetchone()
    if time_last_checked is None or time_last_checked[0] in (None, 0):
        return True     # first check

    saved_videos = cursor.execute(
        '''SELECT video_id, duration, is_time_published_exact, video_type,
                  time_published
           FROM videos
           INNER JOIN subscribed_channels
               ON videos.sql_channel_id = subscribed_channels.id
           WHERE yt_channel_id=?
           ORDER BY time_published DESC
           LIMIT 30''', [channel_id]).fetchall()
    if not feed_video_ids <= set(video[0] for video in saved_videos):
        return True     # new video

    # The feed lists the channel's newest videos, so a saved video that
    # isn't in it can't be newer than all of them. If its estimated time
    # says it is, the estimate is wrong (for example, made up when a check
    # couldn't get any upload times), and the full check corrects it.
    newest_in_feed = max(feed_times.values())

    for (video_id, duration, is_time_exact, video_type,
            time_published) in saved_videos:
        # A livestream or premiere, which gets a duration once it's over
        if duration is not None and duration.lower() in NON_DURATIONS:
            return True
        # Saved before the feed listed it, so its published time is an
        # estimate, or saved before video types were recorded
        if video_id in feed_video_ids and (
                not is_time_exact or video_type is None):
            return True
        if (not is_time_exact and video_id not in feed_video_ids
                and time_published > newest_in_feed):
            return True

    return False

def _next_check_time(times_published):
    '''When to automatically check a channel next. times_published is the
    published times of its videos, newest first.'''
    if len(times_published) == 0:
        average_upload_period = 4*7*24*3600 # assume 1 month for channel with no videos
    elif len(times_published) < 5:
        average_upload_period = int((time.time() - times_published[-1])/len(times_published))
    else:
        average_upload_period = int((time.time() - times_published[4])/5) # equivalent to averaging the time between videos for the last 5 videos

    # calculate when to check next for auto checking
    # add some quantization and randomness to make pattern analysis by Youtube slightly harder
    quantized_upload_period = average_upload_period - (average_upload_period % (4*3600)) + 4*3600   # round up to nearest 4 hours
    randomized_upload_period = quantized_upload_period*(1 + secrets.randbelow(50)/50*0.5) # randomly between 1x and 1.5x
    next_check_delay = randomized_upload_period/10    # check at 10x the channel posting rate. might want to fine tune this number
    return int(time.time() + next_check_delay)

def _record_check(cursor, channel_id, next_check_time):
    '''Saves that the channel was just checked and when to check it next'''
    cursor.execute('''UPDATE subscribed_channels
                      SET time_last_checked = ?, next_check_time = ?
                      WHERE yt_channel_id=?''', [int(time.time()), next_check_time, channel_id])

    if settings.autocheck_subscriptions:
        if not _is_muted(cursor, channel_id):
            autocheck_job_application.put({'channel_id': channel_id, 'channel_name': channel_names[channel_id], 'next_check_time': next_check_time})

def save_watch_page_time(video_id, timestamp, precision):
    '''The watch page gives the publish time, as returned by
    watch.parse_publish_time. If the video is in the subscriptions feed with
    an estimated time, use that one instead.'''
    if timestamp is None or not os.path.exists(database_path):
        return
    exact = precision == 'exact'
    try:
        with open_database() as connection:
            with connection as cursor:
                cursor.execute(
                    '''UPDATE videos SET
                           time_noticed = CASE WHEN time_noticed = time_published
                                          THEN ?1 ELSE time_noticed END,
                           time_published = ?1,
                           is_time_published_exact = ?2
                       WHERE video_id = ?3 AND is_time_published_exact = 0''',
                    [timestamp, int(exact), video_id])
    except sqlite3.Error:
        traceback.print_exc()

def _correct_estimated_times(cursor, channel_id, videos):
    '''Corrects the saved estimated publish times of a channel's videos
    that don't fit what the check just found. videos is the channel's
    newest uploads, newest first, as _get_upstream_videos prepared them.

    An estimate is kept while it still fits, so it doesn't creep later on
    each check: "1 year ago" fits anything from 1 to 2 years ago.'''
    saved = cursor.execute(
        '''SELECT video_id, time_published, duration
           FROM videos
           INNER JOIN subscribed_channels
               ON videos.sql_channel_id = subscribed_channels.id
           WHERE yt_channel_id=? AND is_time_published_exact=0''',
        [channel_id]).fetchall()
    if not saved:
        return
    listed = {video['id']: video for video in videos}
    # Times of the listed videos that came from YouTube rather than being
    # filled in from the videos around them
    known_times = [video['time_published'] for video in videos
                   if video['is_time_published_exact']
                   or video['time_range'] is not None]
    oldest_listed = min(known_times) if known_times else None

    corrections = []
    for video_id, time_published, duration in saved:
        video = listed.get(video_id)
        if video is not None:
            if video['is_time_published_exact']:
                continue    # already replaced with the exact time
            time_range = video['time_range']
            if time_range is None:
                continue
            earliest, latest = time_range
            if not earliest <= time_published <= latest:
                corrections.append((video['time_published'], video_id))
        elif (oldest_listed is not None and time_published > oldest_listed
              and (duration or '').lower() not in NON_DURATIONS):
            # Not in the channel's newest uploads, so it's older than all
            # of them. Upcoming streams are left alone, since they may
            # not be listed yet.
            corrections.append((oldest_listed - 1, video_id))

    # Like for exact times, also fix the feed position if it came from the
    # time being replaced
    cursor.executemany(
        '''UPDATE videos SET
               time_noticed = CASE WHEN time_noticed = time_published
                              THEN ?1 ELSE time_noticed END,
               time_published = ?1
           WHERE video_id = ?2''', corrections)

def _get_upstream_videos(channel_id):
    try:
        channel_status_name = channel_names[channel_id]
    except KeyError:
        channel_status_name = channel_id

    print("Checking channel: " + channel_status_name)

    # The feed is one small request. It lists the channel's 15 newest videos
    # and is needed anyway for their exact published times. Get it first,
    # because when it has nothing we don't already know, the bigger requests
    # below can be skipped, and most channels have nothing new on a given
    # check. The cost: the feed can be several minutes behind, so a video
    # uploaded just now may only be picked up by the check after this one.
    try:
        feed = _get_atoma_feed(channel_id)
    except Exception:
        # The full check still works without the feed
        traceback.print_exc()
        feed = ''
    times_published, feed_types = _parse_atoma_feed(feed, channel_status_name)

    with open_database() as connection:
        with connection as cursor:
            if not _feed_shows_changes(cursor, channel_id, times_published):
                newest_first = sorted(times_published.values(), reverse=True)
                _record_check(cursor, channel_id,
                              _next_check_time(newest_first))
                print('No new videos from ' + channel_status_name)
                return

    # The feed request was already counted when this channel was taken from
    # the queue. Count the two requests below.
    check_channels_queue.count_extra(2)
    tasks = (
        # channel page, need for video duration
        gevent.spawn(_get_channel_videos_first_page, channel_id,
                     channel_status_name),
        # to tell livestreams apart from regular videos
        gevent.spawn(_get_live_video_ids, channel_id),
    )
    gevent.joinall(tasks)

    channel_info, live_ids = tasks[0].value, tasks[1].value

    if channel_info is None: # there was an error
        return
    if channel_info['error']:
        print('Error checking channel ' + channel_status_name + ': ' + channel_info['error'])
        return

    videos = channel_info['items']
    for i, video_item in enumerate(videos):
        if not video_item.get('description'):
            video_item['description'] = ''
        else:
            video_item['description'] = ''.join(run.get('text', '') for run in video_item['description'])

        # The times the video could have been published in, when YouTube
        # said roughly when (such as "3 days ago"). None if it's exact, or
        # if YouTube didn't say and it gets filled in from the videos
        # around it below.
        video_item['time_range'] = None
        if video_item['id'] in times_published:
            video_item['time_published'] = times_published[video_item['id']]
            video_item['is_time_published_exact'] = True
        elif video_item.get('time_published'):
            video_item['is_time_published_exact'] = False
            time_text = video_item['time_published']
            video_item['time_range'] = youtube_timestamp_range(time_text)
            if video_item['time_range'] is None:
                print('Unknown time published for ' + video_item['id']
                      + ': ' + time_text)
                video_item['time_published'] = None
            else:
                # subtract a few seconds off the videos so they will be in the right order
                video_item['time_published'] = (
                    youtube_timestamp_to_posix(time_text) - i)
        else:
            video_item['is_time_published_exact'] = False
            video_item['time_published'] = None
        video_item['channel_id'] = channel_id
        _classify_video(video_item, live_ids, feed_types)
    if len(videos) > 1:
        # Go back and fill in any videos that don't have a time published
        # using the time published of the surrounding ones
        for i in range(len(videos)-1):
            if (videos[i+1]['time_published'] is None
                and videos[i]['time_published'] is not None
            ):
                videos[i+1]['time_published'] = videos[i]['time_published'] - 1
        for i in reversed(range(1,len(videos))):
            if (videos[i-1]['time_published'] is None
                and videos[i]['time_published'] is not None
            ):
                videos[i-1]['time_published'] = videos[i]['time_published'] + 1
    # The list is newest first, so a video can't be newer than the one
    # before it. YouTube's "1 year ago" alone could put it anywhere from 1
    # to 2 years ago, so this narrows estimates down using the exact times.
    for i in range(1, len(videos)):
        video, previous = videos[i], videos[i-1]
        if (video['is_time_published_exact']
                or video['time_published'] is None
                or previous['time_published'] is None
                or video['time_published'] < previous['time_published']):
            continue
        video['time_published'] = previous['time_published'] - 1
        if video['time_range'] is not None:
            earliest, latest = video['time_range']
            video['time_range'] = (min(earliest, video['time_published']),
                                   video['time_published'])

    # None of the videos have a time published: neither the feed nor the
    # channel page gave one. That happens when YouTube has trouble for a
    # while, so try again on the next check. Making times up here used to
    # put the channel's newest video at the top of the feed as "just
    # uploaded", even if it was a year old.
    if videos and videos[0]['time_published'] is None:
        print('Error checking channel ' + channel_status_name
              + ': YouTube gave no upload times. Will try again on the next'
              + ' check')
        return


    next_check_time = _next_check_time(
        [video['time_published'] for video in videos])

    with open_database() as connection:
        with connection as cursor:

            # Get video ids and duration of existing vids so we
            # can see how many new ones there are and update
            # livestreams/premiers
            existing_vids = list(cursor.execute(
                '''SELECT video_id, duration
                   FROM videos
                   INNER JOIN subscribed_channels
                       ON videos.sql_channel_id = subscribed_channels.id
                   WHERE yt_channel_id=?
                   ORDER BY time_published DESC
                   LIMIT 30''', [channel_id]).fetchall())
            existing_vid_ids = set(row[0] for row in existing_vids)
            existing_durs = dict(existing_vids)

            # new videos the channel has uploaded since last time we checked
            number_of_new_videos = 0
            for video in videos:
                if video['id'] in existing_vid_ids:
                    break
                number_of_new_videos += 1

            is_first_check = cursor.execute('''SELECT time_last_checked FROM subscribed_channels WHERE yt_channel_id=?''', [channel_id]).fetchone()[0] in (None, 0)
            # Shorts used to be left out of checks by default. The first
            # time a channel's shorts are seen, the older ones aren't new
            # uploads and shouldn't jump to the top of the feed.
            has_known_shorts = cursor.execute(
                '''SELECT EXISTS(
                       SELECT 1 FROM videos
                       INNER JOIN subscribed_channels
                           ON videos.sql_channel_id = subscribed_channels.id
                       WHERE yt_channel_id=? AND video_type='short'
                   )''', [channel_id]).fetchone()[0]
            time_videos_retrieved = int(time.time())
            rows = []
            update_rows = []
            for i, video_item in enumerate(videos):
                if (is_first_check
                        or number_of_new_videos > 6
                        or i >= number_of_new_videos
                        or (video_item['video_type'] == 'short'
                            and not has_known_shorts)):
                    # time_noticed only breaks ties in the feed now, but keep it
                    # meaning "first seen" only for videos that really are new
                    # Last condition is for when the channel deleting videos
                    # causes new videos to appear at the end of the backlog.
                    # For instance, if we have 30 vids in the DB, and 1 vid
                    # that we previously saw has since been deleted,
                    # then a video we haven't seen before will appear as the
                    # 30th. Don't want this to be considered a newly noticed
                    # vid which would appear at top of subscriptions feed
                    time_noticed = video_item['time_published']
                else:
                    time_noticed = time_videos_retrieved

                # videos which need durations updated
                v_id = video_item['id']
                if (existing_durs.get(v_id) is not None
                    and existing_durs[v_id].lower() in NON_DURATIONS
                    and video_item['duration'] not in NON_DURATIONS
                ):
                    update_rows.append((
                        video_item['title'],
                        video_item['duration'],
                        video_item['time_published'],
                        video_item['is_time_published_exact'],
                        video_item['description'],
                        video_item['id'],
                    ))
                # all other videos
                else:
                    rows.append((
                        video_item['channel_id'],
                        video_item['id'],
                        video_item['title'],
                        video_item['duration'],
                        video_item['time_published'],
                        video_item['is_time_published_exact'],
                        time_noticed,
                        video_item['description'],
                        video_item['video_type'],
                    ))


            insert = cursor.executemany('''INSERT OR IGNORE INTO videos (
                                      sql_channel_id,
                                      video_id,
                                      title,
                                      duration,
                                      time_published,
                                      is_time_published_exact,
                                      time_noticed,
                                      description,
                                      video_type
                                  )
                                  VALUES ((SELECT id FROM subscribed_channels
                                           WHERE yt_channel_id=?),
                                          ?, ?, ?, ?, ?, ?, ?, ?)''', rows)
            # rowcount only counts the rows that weren't already there
            check_progress['videos_added'] += max(insert.rowcount, 0)
            cursor.executemany('''UPDATE videos SET
                                      title=?,
                                      duration=?,
                                      time_published=?,
                                      is_time_published_exact=?,
                                      description=?
                                  WHERE video_id=?''', update_rows)

            # Label videos saved before, now that we know more about them.
            # A guess only fills in a missing label.
            cursor.executemany(
                'UPDATE videos SET video_type=? WHERE video_id=?',
                [(v['video_type'], v['id']) for v in videos
                 if v['type_is_certain']])
            cursor.executemany(
                '''UPDATE videos SET video_type=?
                   WHERE video_id=? AND video_type IS NULL''',
                [(v['video_type'], v['id']) for v in videos
                 if not v['type_is_certain']])
            # Replace publish times that were estimated, or made up because
            # YouTube didn't give one, with the exact time from the feed.
            # If the feed position came from that time too (rather than from
            # when the video was first noticed), fix it as well, or a made-up
            # "just now" keeps the video stuck at the top of the feed.
            cursor.executemany(
                '''UPDATE videos SET
                       time_noticed = CASE WHEN time_noticed = time_published
                                      THEN ? ELSE time_noticed END,
                       time_published = ?,
                       is_time_published_exact = 1
                   WHERE video_id=? AND is_time_published_exact=0''',
                [(v['time_published'], v['time_published'], v['id'])
                 for v in videos if v['is_time_published_exact']])
            _correct_estimated_times(cursor, channel_id, videos)
            # The livestream list goes further back than the uploads we
            # fetch, so this also labels older streams
            if live_ids:
                cursor.executemany(
                    "UPDATE videos SET video_type='live' WHERE video_id=?",
                    [(v_id,) for v_id in live_ids])
            _record_check(cursor, channel_id, next_check_time)

    if number_of_new_videos == 0:
        print('No new videos from ' + channel_status_name)
    elif number_of_new_videos == 1:
        print('1 new video from ' + channel_status_name)
    else:
        print(str(number_of_new_videos) + ' new videos from ' + channel_status_name)



def check_all_channels():
    with open_database() as connection:
        with connection as cursor:
            channel_id_name_list = cursor.execute('''SELECT yt_channel_id, channel_name
                                                     FROM subscribed_channels
                                                     WHERE muted != 1''').fetchall()

    channel_names.update(channel_id_name_list)
    check_channels_if_necessary([item[0] for item in channel_id_name_list])


def check_tags(tags):
    channel_id_name_list = []
    with open_database() as connection:
        with connection as cursor:
            for tag in tags:
                channel_id_name_list += _channels_with_tag(cursor, tag, exclude_muted=True)

    channel_names.update(channel_id_name_list)
    check_channels_if_necessary([item[0] for item in channel_id_name_list])


def check_specific_channels(channel_ids):
    with open_database() as connection:
        with connection as cursor:
            channel_id_name_list = []
            for channel_id in channel_ids:
                channel_id_name_list += cursor.execute('''SELECT yt_channel_id, channel_name
                                                          FROM subscribed_channels
                                                          WHERE yt_channel_id=?''', [channel_id]).fetchall()
    channel_names.update(channel_id_name_list)
    check_channels_if_necessary(channel_ids)


CHANNEL_ID_RE = re.compile(r'UC[-_\w]{22}')
@yt_app.route('/import_subscriptions', methods=['POST'])
def import_subscriptions():

    # check if the post request has the file part
    if 'subscriptions_file' not in request.files:
        #flash('No file part')
        return flask.redirect(util.URL_ORIGIN + request.full_path)
    file = request.files['subscriptions_file']
    # if user does not select file, browser also
    # submit an empty part without filename
    if file.filename == '':
        #flash('No selected file')
        return flask.redirect(util.URL_ORIGIN + request.full_path)


    mime_type = file.mimetype

    if mime_type == 'application/json':
        info = file.read().decode('utf-8')
        if info == '':
            return '400 Bad Request: File is empty', 400
        try:
            info = json.loads(info)
        except json.decoder.JSONDecodeError:
            traceback.print_exc()
            return '400 Bad Request: Invalid json file', 400

        channels = []
        try:
            if 'app_version_int' in info:   # NewPipe Format
                for item in info['subscriptions']:
                    # Other service, such as SoundCloud
                    if item.get('service_id', 0) != 0:
                        continue
                    channel_url = item['url']
                    channel_id_match = CHANNEL_ID_RE.search(channel_url)
                    if channel_id_match:
                        channel_id = channel_id_match.group(0)
                    else:
                        print('WARNING: Could not find channel id in url',
                              channel_url)
                        continue
                    channels.append((channel_id, item['name']))
            else:   # Old Google Takeout format
                for item in info:
                    snippet = item['snippet']
                    channel_id = snippet['resourceId']['channelId']
                    channels.append((channel_id, snippet['title']))
        except (KeyError, IndexError):
            traceback.print_exc()
            return '400 Bad Request: Unknown json structure', 400
    elif mime_type in ('application/xml', 'text/xml', 'text/x-opml'):
        file = file.read().decode('utf-8')
        try:
            root = defusedxml.ElementTree.fromstring(file)
            assert root.tag == 'opml'
            channels = []
            for outline_element in root[0][0]:
                if (outline_element.tag != 'outline') or ('xmlUrl' not in outline_element.attrib):
                    continue


                channel_name = outline_element.attrib['text']
                channel_rss_url = outline_element.attrib['xmlUrl']
                channel_id = channel_rss_url[channel_rss_url.find('channel_id=')+11:].strip()
                channels.append( (channel_id, channel_name) )

        except (AssertionError, IndexError, defusedxml.ElementTree.ParseError) as e:
            return '400 Bad Request: Unable to read opml xml file, or the file is not the expected format', 400
    elif mime_type in ('text/csv', 'application/vnd.ms-excel'):
        content = file.read().decode('utf-8')
        reader = csv.reader(content.splitlines())
        channels = []
        for row in reader:
            if not row or row[0].lower().strip() == 'channel id':
                continue
            elif len(row) > 1 and CHANNEL_ID_RE.fullmatch(row[0].strip()):
                channels.append( (row[0], row[-1]) )
            else:
                print('WARNING: Unknown row format:', row)
    else:
            error = 'Unsupported file format: ' + mime_type
            error += (' . Only subscription.json, subscriptions.csv files'
                      ' (from Google Takeouts)'
                      ' and XML OPML files exported from Youtube\'s'
                      ' subscription manager page are supported')
            return (flask.render_template('error.html', error_message=error),
                    400)

    _subscribe(channels)

    return flask.redirect(util.URL_ORIGIN + '/subscription_manager', 303)


@yt_app.route('/export_subscriptions', methods=['POST'])
def export_subscriptions():
    include_muted = request.values.get('include_muted') == 'on'
    with open_database() as connection:
        with connection as cursor:
            sub_list = []
            for channel_name, channel_id, muted in (
                    _get_subscribed_channels(cursor)):
                if muted and not include_muted:
                    continue
                if request.values['export_format'] == 'json_google_takeout':
                    sub_list.append({
                        'kind': 'youtube#subscription',
                        'snippet': {
                            'muted': bool(muted),
                            'resourceId': {
                                'channelId': channel_id,
                                'kind': 'youtube#channel',
                            },
                            'tags': _get_tags(cursor, channel_id),
                            'title': channel_name,
                        },
                    })
                elif request.values['export_format'] == 'json_newpipe':
                    sub_list.append({
                        'service_id': 0,
                        'url': 'https://www.youtube.com/channel/' + channel_id,
                        'name': channel_name,
                    })
                elif request.values['export_format'] == 'opml':
                    sub_list.append({
                        'channel_name': channel_name,
                        'channel_id': channel_id,
                    })
    date_time = time.strftime('%Y%m%d%H%M', time.localtime())
    if request.values['export_format'] == 'json_google_takeout':
        r = flask.Response(json.dumps(sub_list), mimetype='text/json')
        cd = 'attachment; filename="subscriptions_%s.json"' % date_time
        r.headers['Content-Disposition'] = cd
        return r
    elif request.values['export_format'] == 'json_newpipe':
        r = flask.Response(json.dumps({
            'app_version': '0.21.9',
            'app_version_int': 975,
            'subscriptions': sub_list,
        }), mimetype='text/json')
        file_name = 'newpipe_subscriptions_%s_youtube-local.json' % date_time
        cd = 'attachment; filename="%s"' % file_name
        r.headers['Content-Disposition'] = cd
        return r
    elif request.values['export_format'] == 'opml':
        r = flask.Response(
            flask.render_template('subscriptions.xml', sub_list=sub_list),
            mimetype='text/xml')
        cd = 'attachment; filename="subscriptions_%s.xml"' % date_time
        r.headers['Content-Disposition'] = cd
        return r
    else:
        return '400 Bad Request', 400


@yt_app.route('/subscription_manager', methods=['GET'])
def get_subscription_manager_page():
    group_by_tags = request.args.get('group_by_tags', '0') == '1'
    with open_database() as connection:
        with connection as cursor:
            if group_by_tags:
                tag_groups = []

                for tag in _get_all_tags(cursor):
                    sub_list = []
                    for channel_id, channel_name, muted in _channels_with_tag(cursor, tag, order=True, include_muted_status=True):
                        sub_list.append({
                            'channel_url': util.URL_ORIGIN + '/channel/' + channel_id,
                            'channel_name': channel_name,
                            'channel_id': channel_id,
                            'muted': muted,
                            'tags': [t for t in _get_tags(cursor, channel_id) if t != tag],
                        })

                    tag_groups.append( (tag, sub_list) )

                # Channels with no tags
                channel_list = cursor.execute('''SELECT yt_channel_id, channel_name, muted
                                                 FROM subscribed_channels
                                                 WHERE id NOT IN (
                                                     SELECT sql_channel_id FROM tag_associations
                                                 )
                                                 ORDER BY channel_name COLLATE NOCASE''').fetchall()
                if channel_list:
                    sub_list = []
                    for channel_id, channel_name, muted in channel_list:
                        sub_list.append({
                            'channel_url': util.URL_ORIGIN + '/channel/' + channel_id,
                            'channel_name': channel_name,
                            'channel_id': channel_id,
                            'muted': muted,
                            'tags': [],
                        })

                    tag_groups.append( ('No tags', sub_list) )
            else:
                sub_list = []
                for channel_name, channel_id, muted in _get_subscribed_channels(cursor):
                    sub_list.append({
                        'channel_url': util.URL_ORIGIN + '/channel/' + channel_id,
                        'channel_name': channel_name,
                        'channel_id': channel_id,
                        'muted': muted,
                        'tags': _get_tags(cursor, channel_id),
                    })




            total = cursor.execute(
                'SELECT COUNT(*) FROM subscribed_channels').fetchone()[0]

    if group_by_tags:
        return flask.render_template('subscription_manager.html',
            total = total,
            group_by_tags = True,
            tag_groups = tag_groups,
        )
    else:
        return flask.render_template('subscription_manager.html',
            total = total,
            group_by_tags = False,
            sub_list = sub_list,
        )

def list_from_comma_separated_tags(string):
    return [tag.strip() for tag in string.split(',') if tag.strip()]


@yt_app.route('/subscription_manager', methods=['POST'])
def post_subscription_manager_page():
    action = request.values['action']

    with open_database() as connection:
        with connection as cursor:
            if action == 'add_tags':
                _add_tags(cursor, request.values.getlist('channel_ids'), [tag.lower() for tag in list_from_comma_separated_tags(request.values['tags'])])
            elif action == 'remove_tags':
                _remove_tags(cursor, request.values.getlist('channel_ids'), [tag.lower() for tag in list_from_comma_separated_tags(request.values['tags'])])
            elif action == 'unsubscribe':
                _unsubscribe(cursor, request.values.getlist('channel_ids'))
            elif action == 'unsubscribe_verify':
                unsubscribe_list = _get_channel_names(cursor, request.values.getlist('channel_ids'))
                return flask.render_template('unsubscribe_verify.html', unsubscribe_list = unsubscribe_list)

            elif action == 'mute':
                cursor.executemany('''UPDATE subscribed_channels
                                      SET muted = 1
                                      WHERE yt_channel_id = ?''', [(ci,) for ci in request.values.getlist('channel_ids')])
            elif action == 'unmute':
                cursor.executemany('''UPDATE subscribed_channels
                                      SET muted = 0
                                      WHERE yt_channel_id = ?''', [(ci,) for ci in request.values.getlist('channel_ids')])
            else:
                flask.abort(400)

    return flask.redirect(util.URL_ORIGIN + request.full_path, 303)

FEED_FILTERS_COOKIE = 'subscriptions_feed_hide'
# Set by subscriptions.js when the sidebar is hidden
SIDEBAR_HIDDEN_COOKIE = 'subscriptions_sidebar_hidden'

def _get_feed_filters():
    '''Which kinds of videos the subscriptions feed hides. Saved in a cookie
    so the choice sticks.'''
    cookie = request.cookies.get(FEED_FILTERS_COOKIE)
    if cookie is None:
        # Shorts used to be controlled only by this setting
        return () if settings.include_shorts_in_subscriptions else ('short',)
    return tuple(f for f in cookie.split('.') if f in FEED_FILTERS)

@yt_app.route('/subscriptions', methods=['GET'])
@yt_app.route('/feed/subscriptions', methods=['GET'])
def get_subscriptions_page():
    # The filter form was submitted: save the choice, then reload without
    # the form fields so they don't end up in the page links
    if 'set_filters' in request.args:
        hide = [f for f in request.args.getlist('hide') if f in FEED_FILTERS]
        args = {}
        if request.args.get('tag'):
            args['tag'] = request.args['tag']
        url = util.URL_ORIGIN + '/subscriptions'
        if args:
            url += '?' + urllib.parse.urlencode(args)
        response = flask.redirect(url, 303)
        response.set_cookie(FEED_FILTERS_COOKIE, '.'.join(hide),
                            max_age=10*365*24*3600, path='/',
                            samesite='Lax')
        return response

    page = int(request.args.get('page', 1))
    hide = _get_feed_filters()
    with open_database() as connection:
        with connection as cursor:
            tag = request.args.get('tag', None)
            videos, number_of_videos_in_db = _get_videos(
                cursor, 60, (page - 1)*60, tag, hide)
            for video in videos:
                video['thumbnail'] = util.URL_ORIGIN + '/data/subscription_thumbnails/' + video['id'] + '.jpg'
                video['type'] = 'video'
                video['item_size'] = 'small'
                util.add_extra_html_info(video)

            tags = _get_all_tags(cursor)


            subscription_list = []
            for channel_name, channel_id, muted in _get_subscribed_channels(cursor):
                subscription_list.append({
                    'channel_url': util.URL_ORIGIN + '/channel/' + channel_id,
                    'channel_name': channel_name,
                    'channel_id': channel_id,
                    'muted': muted,
                })

    return flask.render_template('subscriptions.html',
        videos = videos,
        num_pages = math.ceil(number_of_videos_in_db/60),
        parameters_dictionary = request.args,
        tags = tags,
        current_tag = tag,
        subscription_list = subscription_list,
        hide = hide,
        sidebar_hidden = request.cookies.get(SIDEBAR_HIDDEN_COOKIE) == '1',
    )

@yt_app.route('/subscriptions', methods=['POST'])
@yt_app.route('/feed/subscriptions', methods=['POST'])
def post_subscriptions_page():
    action = request.values['action']
    if action == 'subscribe':
        if len(request.values.getlist('channel_id')) != len(request.values.getlist('channel_name')):
            return '400 Bad Request, length of channel_id != length of channel_name', 400
        _subscribe(zip(request.values.getlist('channel_id'), request.values.getlist('channel_name')))

    elif action == 'unsubscribe':
        with_open_db(_unsubscribe, request.values.getlist('channel_id'))

    elif action == 'refresh':
        type = request.values['type']
        if type == 'all':
            check_all_channels()
        elif type == 'tag':
            check_tags(request.values.getlist('tag_name'))
        elif type == 'channel':
            check_specific_channels(request.values.getlist('channel_id'))
        else:
            flask.abort(400)
    else:
        flask.abort(400)

    return '', 204


@yt_app.route('/subscriptions/check_status', methods=['GET'])
def get_check_status():
    '''How far along checking for new videos is, for the status bar on the
    subscriptions page'''
    # Only list the channels when there are few, which is when the page
    # shows them individually (after pressing one channel's Check button)
    channel_ids = []
    if len(checking_channels) <= 50:
        channel_ids = list(checking_channels)
    response = flask.jsonify({
        'checking': len(checking_channels),
        'total': check_progress['total'],
        'done': check_progress['done'],
        'videos_added': check_progress['videos_added'],
        'channel_ids': channel_ids,
    })
    response.headers['Cache-Control'] = 'no-store'
    return response


@yt_app.route('/data/subscription_thumbnails/<thumbnail>')
def serve_subscription_thumbnail(thumbnail):
    '''Serves thumbnail from disk if it's been saved already. If not, downloads the thumbnail, saves to disk, and serves it.'''
    assert thumbnail[-4:] == '.jpg'
    video_id = thumbnail[0:-4]
    thumbnail_path = os.path.join(thumbnails_directory, thumbnail)

    if video_id in existing_thumbnails:
        try:
            f = open(thumbnail_path, 'rb')
        except FileNotFoundError:
            existing_thumbnails.remove(video_id)
        else:
            image = f.read()
            f.close()
            return flask.Response(image, mimetype='image/jpeg')

    url = "https://i.ytimg.com/vi/" + video_id + "/mqdefault.jpg"
    try:
        image = util.fetch_url(url, report_text="Saved thumbnail: " + video_id)
    except urllib.error.HTTPError as e:
        print("Failed to download thumbnail for " + video_id + ": " + str(e))
        abort(e.code)
    try:
        f = open(thumbnail_path, 'wb')
    except FileNotFoundError:
        os.makedirs(thumbnails_directory, exist_ok = True)
        f = open(thumbnail_path, 'wb')
    f.write(image)
    f.close()
    existing_thumbnails.add(video_id)

    return flask.Response(image, mimetype='image/jpeg')







