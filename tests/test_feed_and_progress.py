'''Tests for watch progress, video types and filters in the subscriptions
feed, and how upload times are shown'''
import datetime
import time

import pytest

# youtube has to be imported before settings, like in test_util.py
import youtube
from youtube import subscriptions, time_display, watch, watch_progress
from youtube import yt_app
import settings


NOW = 1_800_000_000


@pytest.mark.parametrize('seconds_ago, expected', [
    (0, 'just now'),
    (1, '1 second ago'),
    (59, '59 seconds ago'),
    (60, '1 minute ago'),
    (2*3600 + 59*60, '2 hours ago'),     # rounds down like YouTube
    (24*3600, '1 day ago'),
    (13*24*3600, '1 week ago'),
    (45*24*3600, '1 month ago'),
    (400*24*3600, '1 year ago'),
    (-3*3600, 'in 3 hours'),             # scheduled livestream
])
def test_relative_time(seconds_ago, expected):
    assert time_display.relative_time(NOW - seconds_ago, now=NOW) == expected


def utc_time(year, month, day, hour, minute=0):
    return datetime.datetime(year, month, day, hour, minute,
                             tzinfo=datetime.timezone.utc).timestamp()


@pytest.fixture
def utc_offset(monkeypatch):
    def set_offset(hours, minutes=0):
        monkeypatch.setattr(settings, 'utc_offset_hours', hours, raising=False)
        monkeypatch.setattr(settings, 'utc_offset_minutes', minutes,
                            raising=False)
    set_offset(0)
    return set_offset


@pytest.mark.parametrize('hours, minutes, seconds, name', [
    (0, 0, 0, 'UTC'),
    (2, 0, 7200, 'UTC+2'),
    (5, 30, 5*3600 + 1800, 'UTC+05:30'),
    (-3, 30, -(3*3600 + 1800), 'UTC-03:30'),
    (-8, 0, -8*3600, 'UTC-8'),
])
def test_utc_offset(utc_offset, hours, minutes, seconds, name):
    utc_offset(hours, minutes)
    assert time_display.utc_offset_seconds() == seconds
    assert time_display.utc_offset_name() == name


def test_date_time_uses_utc_offset(utc_offset):
    published = utc_time(2026, 11, 15, 3, 7)
    assert time_display.date_time(published) == 'Nov 15, 2026, 3:07 AM'
    utc_offset(-5)
    assert time_display.date_time(published) == 'Nov 14, 2026, 10:07 PM'
    assert time_display.date_time(published, 'approximate') == \
        'around Nov 14, 2026'
    utc_offset(5, 30)
    assert time_display.date_time(published) == 'Nov 15, 2026, 8:37 AM'


def test_date_precision_keeps_youtubes_date(utc_offset):
    # watch.parse_publish_time keeps a date as noon UTC
    noon = utc_time(2026, 11, 15, 12)
    for hours in (-12, 0, 14):
        utc_offset(hours)
        assert time_display.date_time(noon, 'date') == 'Nov 15, 2026'


def test_relative_time_date_precision_counts_calendar_days(utc_offset):
    relative_time = time_display.relative_time
    utc_offset(-5)
    now = utc_time(2027, 1, 15, 14)   # 9 AM on the 15th at UTC-5
    assert relative_time(utc_time(2027, 1, 15, 12), now,
                         precision='date') == 'today'
    assert relative_time(utc_time(2027, 1, 14, 12), now,
                         precision='date') == '1 day ago'
    assert relative_time(utc_time(2027, 1, 13, 12), now,
                         precision='date') == '2 days ago'
    assert relative_time(utc_time(2027, 1, 16, 12), now,
                         precision='date') == 'in 1 day'
    # 2 AM UTC on the 16th is still the 15th at UTC-5
    assert relative_time(utc_time(2027, 1, 15, 12), utc_time(2027, 1, 16, 2),
                         precision='date') == 'today'


def test_date_time_precision(utc_offset):
    assert time_display.date_time(NOW, 'approximate').startswith('around ')
    assert ':' in time_display.date_time(NOW, 'exact')
    assert ':' not in time_display.date_time(NOW, 'date')


def test_parse_publish_time():
    timestamp, precision = watch.parse_publish_time(
        '2025-02-08T11:05:06-08:00')
    assert precision == 'exact'
    assert timestamp == int(datetime.datetime(
        2025, 2, 8, 19, 5, 6, tzinfo=datetime.timezone.utc).timestamp())

    timestamp, precision = watch.parse_publish_time('2019-3-9')
    assert precision == 'date'
    assert time.gmtime(timestamp)[0:3] == (2019, 3, 9)

    assert watch.parse_publish_time(None) == (None, None)
    assert watch.parse_publish_time('yesterday') == (None, None)


def test_is_new_video():
    now = time.time()
    assert youtube.is_new_video({'type': 'video', 'timestamp': now - 3600})
    assert not youtube.is_new_video(
        {'type': 'video', 'timestamp': now - 13*3600})
    # scheduled for later, not uploaded yet
    assert not youtube.is_new_video({'type': 'video', 'timestamp': now + 60})
    # pages other than subscriptions only have YouTube's text
    assert youtube.is_new_video(
        {'type': 'video', 'time_published': '11 hours ago'})
    assert not youtube.is_new_video(
        {'type': 'video', 'time_published': '12 hours ago'})
    assert not youtube.is_new_video(
        {'type': 'video', 'time_published': '1 day ago'})
    assert not youtube.is_new_video(
        {'type': 'playlist', 'time_published': '1 hour ago'})


@pytest.mark.parametrize('duration, expected', [
    ('0:59', 59), ('12:34', 754), ('1:02:03', 3723),
    ('LIVE', None), ('', None), (None, None),
])
def test_duration_seconds(duration, expected):
    assert subscriptions._duration_seconds(duration) == expected


def classify(video_id, duration, live_ids, feed_types):
    video_item = {'id': video_id, 'duration': duration}
    subscriptions._classify_video(video_item, live_ids, feed_types)
    return video_item['video_type'], video_item['type_is_certain']


def test_classify_video():
    feed_types = {'short1': 'short', 'video1': 'video', 'stream1': 'video'}
    live_ids = {'stream1', 'stream2'}
    assert classify('stream1', '3:00:00', live_ids, feed_types) == (
        'live', True)
    assert classify('stream2', '1:00:00', live_ids, feed_types) == (
        'live', True)
    assert classify('upcoming', 'UPCOMING', set(), {}) == ('live', True)
    assert classify('short1', '0:30', live_ids, feed_types) == (
        'short', True)
    assert classify('video1', '10:00', live_ids, feed_types) == (
        'video', True)
    # without the livestream list, a video could still be a past stream
    assert classify('video1', '10:00', None, feed_types) == ('video', False)
    # older than the feed: guessed from the duration
    assert classify('old1', '0:45', live_ids, feed_types) == ('short', False)
    assert classify('old2', '8:00', live_ids, feed_types) == ('video', False)


@pytest.fixture
def database(tmp_path, monkeypatch):
    '''Use an empty subscriptions database in a temporary folder'''
    monkeypatch.setattr(settings, 'data_dir', str(tmp_path))
    monkeypatch.setattr(subscriptions, 'database_path',
                        str(tmp_path / 'subscriptions.sqlite'))
    # otherwise a check queues the test channel for the autocheck thread,
    # which only knows the channels in the real database
    monkeypatch.setattr(settings, 'autocheck_subscriptions', False)
    with subscriptions.open_database() as connection:
        with connection as cursor:
            cursor.execute(
                '''INSERT INTO subscribed_channels (yt_channel_id,
                                                    channel_name)
                   VALUES ('UCaaaaaaaaaaaaaaaaaaaaaa', 'Channel')''')
            for number, video_type in enumerate(
                    ['video', 'short', 'live', None]):
                cursor.execute(
                    '''INSERT INTO videos (sql_channel_id, video_id, title,
                                           duration, time_published,
                                           is_time_published_exact,
                                           time_noticed, video_type)
                       VALUES (1, ?, 'Title', '1:00', ?, 1, ?, ?)''',
                    ['vid%08d' % number, NOW - number, NOW - number,
                     video_type])
    return tmp_path


@pytest.fixture
def client(database):
    return yt_app.test_client()


def test_save_and_get_progress(client):
    response = client.post('/watch_progress', data={
        'video_id': 'video000000', 'position': '120', 'duration': '600'})
    assert response.status_code == 204
    assert client.get('/watch_progress?ids=video000000,video0000001').json \
        == {'video000000': {'position': 120, 'duration': 600,
                            'watched': False}}
    assert watch_progress.resume_position('video000000') == 117

    # finishing it marks it watched, and then it starts from the beginning
    client.post('/watch_progress', data={
        'video_id': 'video000000', 'position': '590', 'duration': '600'})
    assert client.get('/watch_progress?ids=video000000').json[
        'video000000']['watched'] is True
    assert watch_progress.resume_position('video000000') == 0

    # watching it again doesn't make it unwatched
    client.post('/watch_progress', data={
        'video_id': 'video000000', 'position': '30', 'duration': '600'})
    progress = client.get('/watch_progress?ids=video000000').json
    assert progress['video000000']['watched'] is True
    assert progress['video000000']['position'] == 30


@pytest.mark.parametrize('form', [
    {'video_id': 'bad', 'position': '5', 'duration': '10'},
    {'video_id': 'video000000', 'position': 'nan', 'duration': '10'},
    {'video_id': 'video000000', 'position': '10', 'duration': 'inf'},
    {'video_id': 'video000000', 'position': '50', 'duration': '10'},
    {'video_id': 'video000000'},
])
def test_rejects_bad_progress(client, form):
    assert client.post('/watch_progress', data=form).status_code == 400


def test_ignores_barely_started_video(client):
    client.post('/watch_progress', data={
        'video_id': 'video000000', 'position': '2', 'duration': '600'})
    assert client.get('/watch_progress?ids=video000000').json == {}


def feed_video_ids(hide):
    with subscriptions.open_database() as connection:
        with connection as cursor:
            videos, _ = subscriptions._get_videos(cursor, 60, 0, hide=hide)
    return [video['id'] for video in videos]


def test_feed_filters(client):
    all_videos = ['vid00000000', 'vid00000001', 'vid00000002',
                  'vid00000003']
    assert feed_video_ids(()) == all_videos
    assert feed_video_ids(('short',)) == [
        'vid00000000', 'vid00000002', 'vid00000003']
    # videos of unknown type count as regular videos
    assert feed_video_ids(('video',)) == ['vid00000001', 'vid00000002']
    assert feed_video_ids(('live', 'short')) == [
        'vid00000000', 'vid00000003']

    client.post('/watch_progress', data={
        'video_id': 'vid00000000', 'position': '600', 'duration': '600'})
    assert feed_video_ids(('watched',)) == all_videos[1:]


def test_feed_filter_choice_is_remembered(client):
    response = client.get(
        '/subscriptions?set_filters=1&hide=watched&hide=live&hide=bogus')
    assert response.status_code == 303
    page = client.get('/subscriptions').get_data(as_text=True)
    assert 'value="watched" checked' in page
    assert 'value="live" checked' in page
    assert 'value="short" checked' not in page


def test_feed_is_sorted_by_upload_time(database):
    with subscriptions.open_database() as connection:
        with connection as cursor:
            # found by the latest check, but uploaded 3 days ago
            cursor.execute(
                '''INSERT INTO videos (sql_channel_id, video_id, title,
                                       duration, time_published,
                                       is_time_published_exact,
                                       time_noticed, video_type)
                   VALUES (1, 'oldbutnew01', 'Title', '1:00', ?, 1, ?,
                           'video')''',
                [NOW - 3*24*3600, NOW + 60])
            videos, _ = subscriptions._get_videos(cursor, 10, 0)
    ids = [video['id'] for video in videos]
    assert ids[-1] == 'oldbutnew01'
    published = [video['timestamp'] for video in videos]
    assert published == sorted(published, reverse=True)


DAY = 24*3600
CHANNEL = 'UCaaaaaaaaaaaaaaaaaaaaaa'


def add_video(cursor, video_id, time_published, exact=False,
              time_noticed=None, duration='10:00'):
    cursor.execute(
        '''INSERT INTO videos (sql_channel_id, video_id, title, duration,
                               time_published, is_time_published_exact,
                               time_noticed, video_type)
           VALUES (1, ?, 'Title', ?, ?, ?, ?, 'video')''',
        [video_id, duration, time_published, int(exact),
         time_published if time_noticed is None else time_noticed])


def saved_time(cursor, video_id):
    return cursor.execute(
        '''SELECT time_published, is_time_published_exact, time_noticed
           FROM videos WHERE video_id=?''', [video_id]).fetchone()


@pytest.fixture
def frozen_time(monkeypatch):
    monkeypatch.setattr(time, 'time', lambda: NOW)


@pytest.mark.parametrize('text, earliest, latest', [
    ('1 year ago', NOW - 2*365*DAY, NOW - 365*DAY + 60),
    ('3 days ago', NOW - 4*DAY, NOW - 3*DAY + 60),
    ('just now', NOW - 1, NOW + 60),
])
def test_youtube_timestamp_range(frozen_time, text, earliest, latest):
    assert subscriptions.youtube_timestamp_range(text) == (earliest, latest)


def test_youtube_timestamp_range_unreadable():
    assert subscriptions.youtube_timestamp_range('Streamed live') is None


def test_feed_check_notices_impossible_estimate(database, frozen_time):
    feed = {'vid00000000': NOW - 3000, 'vid00000001': NOW - 3600}
    with subscriptions.open_database() as connection:
        with connection as cursor:
            cursor.execute('UPDATE subscribed_channels SET time_last_checked=?',
                           [NOW - 3600])
            cursor.execute("UPDATE videos SET duration='1:00'")
            # older than the feed's videos, so not in the feed: fine
            add_video(cursor, 'oldvideo001', NOW - 400*DAY)
            assert not subscriptions._feed_shows_changes(cursor, CHANNEL,
                                                         feed)
            # claims to be newer than the feed's videos but isn't in the
            # feed: the time was made up
            add_video(cursor, 'madeup00001', NOW - 10)
            assert subscriptions._feed_shows_changes(cursor, CHANNEL, feed)


def test_correct_estimated_times(database, frozen_time):
    one_year_ago = subscriptions.youtube_timestamp_range('1 year ago')
    videos = [
        {'id': 'vid00000000', 'time_published': NOW,
         'is_time_published_exact': True, 'time_range': None},
        {'id': 'madeup00001', 'time_published': NOW - 365*DAY,
         'is_time_published_exact': False, 'time_range': one_year_ago},
        {'id': 'estimate001', 'time_published': NOW - 365*DAY,
         'is_time_published_exact': False, 'time_range': one_year_ago},
    ]
    with subscriptions.open_database() as connection:
        with connection as cursor:
            # made up as "just now" by an old check
            add_video(cursor, 'madeup00001', NOW - 3600)
            # an earlier "1 year ago" estimate, which still fits
            add_video(cursor, 'estimate001', NOW - 500*DAY,
                      time_noticed=NOW - 600*DAY)
            # not among the newest uploads, but claims to be newer
            add_video(cursor, 'notlisted01', NOW - 7200)
            # an upcoming stream that isn't listed yet
            add_video(cursor, 'upcoming001', NOW + DAY, duration='upcoming')
            subscriptions._correct_estimated_times(cursor, CHANNEL, videos)

            assert saved_time(cursor, 'madeup00001') == \
                (NOW - 365*DAY, 0, NOW - 365*DAY)
            assert saved_time(cursor, 'estimate001') == \
                (NOW - 500*DAY, 0, NOW - 600*DAY)
            assert saved_time(cursor, 'notlisted01') == \
                (NOW - 365*DAY - 1, 0, NOW - 365*DAY - 1)
            assert saved_time(cursor, 'upcoming001')[0] == NOW + DAY


def test_check_without_any_times_saves_nothing(database, monkeypatch):
    monkeypatch.setattr(subscriptions, '_get_atoma_feed',
                        lambda channel_id: '')
    monkeypatch.setattr(subscriptions, '_get_live_video_ids',
                        lambda channel_id: set())
    monkeypatch.setattr(subscriptions.check_channels_queue, 'count_extra',
                        lambda amount: None)
    monkeypatch.setattr(
        subscriptions, '_get_channel_videos_first_page',
        lambda channel_id, name: {'error': None, 'items': [
            {'id': 'notime00001', 'title': 'Title', 'duration': '10:00',
             'description': None, 'type': 'video'},
        ]})
    subscriptions._get_upstream_videos(CHANNEL)
    with subscriptions.open_database() as connection:
        with connection as cursor:
            assert saved_time(cursor, 'notime00001') is None


def test_watch_page_time_replaces_estimate(database):
    with subscriptions.open_database() as connection:
        with connection as cursor:
            add_video(cursor, 'madeup00001', NOW - 3600)
    subscriptions.save_watch_page_time('madeup00001', NOW - 300*DAY, 'exact')
    # an exact time isn't replaced
    subscriptions.save_watch_page_time('vid00000000', NOW - 300*DAY, 'exact')
    with subscriptions.open_database() as connection:
        with connection as cursor:
            assert saved_time(cursor, 'madeup00001') == \
                (NOW - 300*DAY, 1, NOW - 300*DAY)
            assert saved_time(cursor, 'vid00000000')[0] == NOW


def test_estimate_is_not_newer_than_the_video_before_it(database,
                                                         monkeypatch,
                                                         frozen_time):
    # The channel's newest video is from 500 days ago (exact, from the
    # feed). The one before it says "1 year ago", which on its own would
    # be estimated as 365 days ago, newer than the newest video.
    exact_time = NOW - 500*DAY
    published = time.strftime('%Y-%m-%dT%H:%M:%S+00:00',
                              time.gmtime(exact_time))
    feed = ('<feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:yt="http://www.youtube.com/xml/schemas/2015"><entry>'
            '<yt:videoId>newest00001</yt:videoId>'
            '<published>' + published + '</published></entry></feed>')
    monkeypatch.setattr(subscriptions, '_get_atoma_feed',
                        lambda channel_id: feed)
    monkeypatch.setattr(subscriptions, '_get_live_video_ids',
                        lambda channel_id: set())
    monkeypatch.setattr(subscriptions.check_channels_queue, 'count_extra',
                        lambda amount: None)
    monkeypatch.setattr(
        subscriptions, '_get_channel_videos_first_page',
        lambda channel_id, name: {'error': None, 'items': [
            {'id': video_id, 'title': 'Title', 'duration': '10:00',
             'description': None, 'type': 'video',
             'time_published': time_text}
            for video_id, time_text in [('newest00001', '1 year ago'),
                                        ('older000001', '1 year ago')]
        ]})
    with subscriptions.open_database() as connection:
        with connection as cursor:
            # saved by an earlier check as 365 days ago
            add_video(cursor, 'older000001', NOW - 365*DAY)
    subscriptions._get_upstream_videos(CHANNEL)
    with subscriptions.open_database() as connection:
        with connection as cursor:
            assert saved_time(cursor, 'newest00001')[:2] == (exact_time, 1)
            assert saved_time(cursor, 'older000001')[:2] == \
                (exact_time - 1, 0)
