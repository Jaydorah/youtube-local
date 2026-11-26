'''Tests for the things that keep requests to YouTube down: how often
channels are checked, reusing a video's info for a few minutes, stable
thumbnail addresses and comments that wait until they're wanted'''
import time

import cachetools
import pytest

# youtube has to be imported before settings, like in test_util.py
import youtube
from youtube import yt_app, util, watch, comments, subscriptions
import settings

HOUR = 3600


def hourly_uploads():
    '''Published times of a channel that uploads every hour, newest first'''
    now = time.time()
    return [now - (i + 1)*HOUR for i in range(10)]


@pytest.mark.parametrize('pace, at_least, at_most', [
    (0, 0, 1*HOUR),         # Often: 10 checks per upload
    (1, 6*HOUR, 8*HOUR),    # Normal: no more than every 6 hours
    (2, 24*HOUR, 31*HOUR),  # Rarely: no more than once a day
])
def test_check_pace(monkeypatch, pace, at_least, at_most):
    monkeypatch.setattr(settings, 'subscription_check_pace', pace,
                        raising=False)
    delay = subscriptions._next_check_time(hourly_uploads()) - time.time()
    assert at_least <= delay <= at_most


def test_slow_channels_go_by_their_upload_rate(monkeypatch):
    monkeypatch.setattr(settings, 'subscription_check_pace', 1,
                        raising=False)
    now = time.time()
    weekly = [now - (i + 1)*7*24*HOUR for i in range(10)]
    delay = subscriptions._next_check_time(weekly) - now
    # A quarter of a week, give or take the rounding and randomness
    assert 1.5*24*HOUR <= delay <= 3*24*HOUR


@pytest.mark.parametrize('thumbnail, expected', [
    ('https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg?sqp=-oaymwEc&rs=AOn4',
     'https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg'),
    ('https://i.ytimg.com/vi_webp/dQw4w9WgXcQ/mqdefault.webp',
     'https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg'),
    ('https://i.ytimg.com/vi/dQw4w9WgXcQ/hq720.jpg?sqp=x',
     'https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg'),
    # Shorts have tall thumbnails of their own
    ('https://i.ytimg.com/vi/dQw4w9WgXcQ/oar2.jpg?sqp=x',
     'https://i.ytimg.com/vi/dQw4w9WgXcQ/oar2.jpg?sqp=x'),
])
def test_thumbnails_get_a_stable_address(monkeypatch, thumbnail, expected):
    monkeypatch.setattr(settings, 'proxy_images', False)
    item = {'type': 'video', 'id': 'dQw4w9WgXcQ', 'thumbnail': thumbnail}
    util.prefix_urls(item)
    assert item['thumbnail'] == expected


def test_playlist_thumbnails_are_left_alone(monkeypatch):
    monkeypatch.setattr(settings, 'proxy_images', False)
    thumbnail = 'https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg?sqp=x'
    item = {'type': 'playlist', 'id': 'PL' + 'x'*32, 'thumbnail': thumbnail}
    util.prefix_urls(item)
    assert item['thumbnail'] == thumbnail


def video_info(**changes):
    info = {'error': None, 'playability_error': None, 'urls_ready': True,
            'live': False, 'hls_formats': [],
            'formats': [{'url': 'https://example.googlevideo.com/videoplayback'}]}
    info.update(changes)
    return info


@pytest.fixture
def fake_youtube(monkeypatch):
    '''Stands in for extract_info, which asks YouTube. .calls lists the
    videos asked about; .result makes what it returns.'''
    monkeypatch.setattr(watch, 'info_cache', cachetools.TTLCache(32, 600))
    def fake_extract_info(video_id, use_invidious, playlist_id=None,
                          index=None):
        fake_extract_info.calls.append(video_id)
        return fake_extract_info.result()
    fake_extract_info.calls = []
    fake_extract_info.result = video_info
    monkeypatch.setattr(watch, 'extract_info', fake_extract_info)
    return fake_extract_info


def test_video_info_is_reused(fake_youtube):
    fetches = fake_youtube.calls
    first = watch.get_info('dQw4w9WgXcQ', True)
    # The watch page changes what it gets, which mustn't reach the cache
    first['formats'][0]['url'] = '/changed'
    second = watch.get_info('dQw4w9WgXcQ', True)
    assert fetches == ['dQw4w9WgXcQ']
    assert second['formats'][0]['url'].startswith('https://')
    watch.get_info('jNQXAC9IVRw', True)
    assert fetches == ['dQw4w9WgXcQ', 'jNQXAC9IVRw']


@pytest.mark.parametrize('changes', [
    {'playability_error': 'YouTube is asking to confirm this isn\'t a bot'},
    {'error': 'Something went wrong'},
    {'live': True},
    {'formats': []},
])
def test_failures_and_livestreams_are_asked_again(fake_youtube, changes):
    fake_youtube.result = lambda: video_info(**changes)
    watch.get_info('dQw4w9WgXcQ', True)
    watch.get_info('dQw4w9WgXcQ', True)
    assert fake_youtube.calls == ['dQw4w9WgXcQ', 'dQw4w9WgXcQ']


def test_comments_are_fetched_on_their_own(monkeypatch):
    asked = []
    def fake_video_comments(video_id, sort=0, offset=0, lc='',
                            secret_key=''):
        asked.append((video_id, lc))
        return {'error': None, 'comments': [], 'comment_links': []}
    monkeypatch.setattr(comments, 'video_comments', fake_video_comments)
    client = yt_app.test_client()

    assert client.get('/ytl-api/video_comments?v=bad').status_code == 400
    page = client.get('/ytl-api/video_comments?v=dQw4w9WgXcQ&lc=abc')
    assert page.status_code == 200
    assert 'No comments' in page.get_data(as_text=True)
    assert asked == [('dQw4w9WgXcQ', 'abc')]
