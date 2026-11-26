'''Tests for local playlists, the queue and their pages'''
import csv
import io
import json

import pytest

# youtube has to be imported before settings, like in test_util.py
import youtube
from youtube import yt_app, util, local_playlist, library
from youtube.local_playlist import PlaylistError


def video(video_id, title='A video', **extra):
    info = {'id': video_id, 'title': title, 'author': 'Someone',
            'duration': '3:21', 'author_id': 'UC' + 'x'*22}
    info.update(extra)
    return info


IDS = ['aaaaaaaaaaa', 'bbbbbbbbbbb', 'ccccccccccc', 'ddddddddddd']


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(local_playlist, 'playlists_directory',
                        str(tmp_path / 'playlists'))
    monkeypatch.setattr(local_playlist, 'thumbnails_directory',
                        str(tmp_path / 'playlist_thumbnails'))
    monkeypatch.setattr(library, 'queue_path', str(tmp_path / 'queue.json'))
    monkeypatch.setattr(util, 'download_thumbnails', lambda *args: None)
    monkeypatch.setattr(local_playlist, 'fill_missing_titles',
                        lambda name: None)
    return tmp_path


@pytest.fixture
def client(data):
    return yt_app.test_client()


def ids(videos):
    return [v['id'] for v in videos]


@pytest.mark.parametrize('name, cleaned', [
    ('Music', 'Music'),
    ('  Late   night  ', 'Late night'),
    ('Watch later', 'Watch later'),
    ('', None),
    ('   ', None),
    ('.hidden', None),
    ('a/b', None),
    ('a\\b', None),
    ('what?', None),
    ('x'*101, None),
    ('line\nbreak', 'line break'),
    ('日本語', '日本語'),
])
def test_clean_name(name, cleaned):
    assert local_playlist.clean_name(name) == cleaned


def test_add_skips_duplicates_and_keeps_order(data):
    assert local_playlist.add_to_playlist('Music', [video(IDS[0]), video(IDS[1])]) == 2
    assert local_playlist.add_to_playlist('Music', [video(IDS[1]), video(IDS[2])]) == 1
    assert ids(local_playlist.read_playlist('Music')) == IDS[:3]
    # put back where it was, for Undo
    local_playlist.remove_from_playlist('Music', [IDS[1]])
    local_playlist.add_to_playlist('Music', [video(IDS[1])], position=1)
    assert ids(local_playlist.read_playlist('Music')) == IDS[:3]


def test_add_takes_json_and_ignores_junk(data):
    added = local_playlist.add_to_playlist('Music', [
        json.dumps(video(IDS[0])), 'not json', {'id': 'short'}, {'title': 'x'}])
    assert added == 1
    entry = local_playlist.read_playlist('Music')[0]
    assert set(entry) == {'id', 'title', 'author', 'duration', 'author_id',
                          'added'}


def test_add_refuses_bad_new_name(data):
    with pytest.raises(PlaylistError):
        local_playlist.add_to_playlist('../escape', [video(IDS[0])])
    assert local_playlist.get_playlist_names() == []


def test_move_rename_delete(data):
    local_playlist.add_to_playlist('Music', [video(i) for i in IDS])
    local_playlist.move_in_playlist('Music', IDS[3], 'top')
    local_playlist.move_in_playlist('Music', IDS[1], 'bottom')
    local_playlist.move_in_playlist('Music', IDS[2], 1)
    assert ids(local_playlist.read_playlist('Music')) == [
        IDS[3], IDS[2], IDS[0], IDS[1]]

    local_playlist.create_playlist('Other')
    with pytest.raises(PlaylistError):
        local_playlist.rename_playlist('Music', 'Other')
    assert local_playlist.rename_playlist('Music', 'Songs') == 'Songs'
    assert local_playlist.get_playlist_names() == ['Other', 'Songs']
    local_playlist.delete_playlist('Songs')
    assert local_playlist.get_playlist_names() == ['Other']


def test_names_put_watch_later_first(data):
    for name in ('b', 'Watch later', 'A'):
        local_playlist.create_playlist(name)
    assert local_playlist.get_playlist_names() == ['Watch later', 'A', 'b']


def test_unsafe_names_are_not_found(data):
    for name in ('../x', '.', '..', 'a/b'):
        with pytest.raises(FileNotFoundError):
            local_playlist.read_playlist(name)


def test_summaries(data):
    local_playlist.add_to_playlist('Music', [
        video(IDS[0], duration='1:00:00'), video(IDS[1], duration='2:30')])
    summaries = local_playlist.all_playlist_summaries(contains_video=IDS[1])
    # Watch later is always there, first
    assert [s['name'] for s in summaries] == ['Watch later', 'Music']
    music = summaries[1]
    assert music['video_count'] == 2
    assert music['total_duration'] == '1 hr 2 min'
    assert music['contains'] is True
    assert summaries[0]['contains'] is False


def test_export_formats(data):
    local_playlist.add_to_playlist('Mix', [
        video(IDS[0], title='First, "quoted"'), video(IDS[1], title='Second')])
    videos = local_playlist.read_playlist('Mix')

    exported = json.loads(local_playlist.export_text('Mix', videos, 'json'))
    assert exported['name'] == 'Mix'
    assert ids(exported['videos']) == IDS[:2]

    rows = list(csv.reader(io.StringIO(
        local_playlist.export_text('Mix', videos, 'csv'))))
    assert rows[0][:3] == ['Video ID', 'Title', 'Channel']
    assert rows[1][:2] == [IDS[0], 'First, "quoted"']

    m3u = local_playlist.export_text('Mix', videos, 'm3u').splitlines()
    assert m3u[0] == '#EXTM3U'
    assert m3u[2] == '#EXTINF:201,Someone - First, "quoted"'
    assert m3u[3] == 'https://www.youtube.com/watch?v=' + IDS[0]

    assert local_playlist.export_text('Mix', videos, 'ids') == (
        IDS[0] + '\n' + IDS[1] + '\n')


@pytest.mark.parametrize('fmt', ['json', 'csv', 'm3u', 'urls', 'ids'])
def test_export_round_trips_through_import(data, fmt):
    local_playlist.add_to_playlist('Mix', [video(i) for i in IDS])
    text = local_playlist.export_text(
        'Mix', local_playlist.read_playlist('Mix'), fmt)
    extension = local_playlist.EXPORT_FORMAT_INFO[fmt][3]
    [(name, entries)] = local_playlist.parse_import('Mix' + extension,
                                                    text.encode('utf-8'))
    assert name == 'Mix'
    assert ids(entries) == IDS


def test_import_everything_export(data):
    local_playlist.add_to_playlist('One', [video(IDS[0])])
    local_playlist.add_to_playlist('Two', [video(IDS[1]), video(IDS[2])])
    text = local_playlist.export_all_text('json').encode('utf-8')
    parsed = local_playlist.parse_import('backup.json', text)
    assert [(name, ids(entries)) for name, entries in parsed] == [
        ('One', [IDS[0]]), ('Two', [IDS[1], IDS[2]])]

    rows = list(csv.reader(io.StringIO(local_playlist.export_all_text('csv'))))
    assert rows[0][0] == 'Playlist'
    assert [row[0] for row in rows[1:]] == ['One', 'Two', 'Two']


def test_import_google_takeout_csv(data):
    # newer Takeouts: <name>-videos.csv with only ids and times
    new = ('Video ID,Playlist Video Creation Timestamp\n'
           + IDS[0] + ',2023-01-01T00:00:00+00:00\n'
           + IDS[1] + ',2023-01-02T00:00:00+00:00\n')
    [(name, entries)] = local_playlist.parse_import('Road trip-videos.csv',
                                                    new.encode('utf-8'))
    assert name == 'Road trip'
    assert ids(entries) == IDS[:2]

    # older ones start with a block about the playlist
    old = ('Playlist Id,Channel Id,Time Created,Time Updated,Title,'
           'Description,Visibility\n'
           'PLxyz,UCabc,2020-01-01,2020-01-02,Old favourites,,Private\n'
           '\n'
           'Video Id,Time Added\n'
           + IDS[2] + ',2020-01-01 00:00:00 UTC\n')
    [(name, entries)] = local_playlist.parse_import('playlist.csv',
                                                    old.encode('utf-8'))
    assert name == 'Old favourites'
    assert ids(entries) == [IDS[2]]


def test_import_links_and_m3u(data):
    text = ('#EXTM3U\n'
            '#EXTINF:100,Some title\n'
            'https://www.youtube.com/watch?v=' + IDS[0] + '&t=10\n'
            'https://youtu.be/' + IDS[1] + '?si=abc\n'
            'https://www.youtube.com/shorts/' + IDS[2] + '\n'
            + IDS[3] + '\n'
            'not a video\n')
    [(name, entries)] = local_playlist.parse_import('list.m3u',
                                                    text.encode('utf-8'))
    assert ids(entries) == IDS
    assert entries[0]['title'] == 'Some title'
    assert entries[1]['title'] is None


def test_import_page(client):
    response = client.post('/playlists_import', data={
        'links': 'https://youtu.be/%s\nhttps://youtu.be/%s' % (IDS[0], IDS[1]),
        'playlist_name': 'Pasted',
    })
    assert response.status_code == 303
    assert ids(local_playlist.read_playlist('Pasted')) == IDS[:2]

    response = client.post('/playlists_import', data={
        'files': (io.BytesIO(b'{"name": "From file", "videos": [{"id": "'
                             + IDS[2].encode() + b'"}]}'), 'x.json'),
    }, content_type='multipart/form-data')
    assert response.status_code == 303
    assert ids(local_playlist.read_playlist('From file')) == [IDS[2]]

    response = client.post('/playlists_import', data={'links': 'nothing'})
    assert 'error=1' in response.headers['Location']


def test_export_download_names_the_file(client):
    local_playlist.add_to_playlist('Café', [video(IDS[0])])
    response = client.get('/playlists/Café/export?format=csv')
    assert response.status_code == 200
    assert response.mimetype == 'text/csv'
    disposition = response.headers['Content-Disposition']
    assert "filename*=UTF-8''Caf%C3%A9.csv" in disposition
    assert client.get('/playlists/Café/export?format=exe').status_code == 400
    assert client.get('/playlists_export?format=json').status_code == 200


def test_playlist_api(client):
    response = client.post('/ytl-api/playlist', data={
        'action': 'add', 'playlist_name': 'Watch later',
        'video_info': json.dumps(video(IDS[0]))}).get_json()
    assert response['ok'] and response['added']
    assert response['playlist']['video_count'] == 1

    again = client.post('/ytl-api/playlist', data={
        'action': 'add', 'playlist_name': 'Watch later',
        'video_info': json.dumps(video(IDS[0]))}).get_json()
    assert again['ok'] and not again['added']

    library_info = client.get('/ytl-api/library?video_id=' + IDS[0]).get_json()
    assert library_info['playlists'][0]['contains'] is True

    removed = client.post('/ytl-api/playlist', data={
        'action': 'remove', 'playlist_name': 'Watch later',
        'video_id': IDS[0]}).get_json()
    assert removed['index'] == 0
    assert removed['playlist']['video_count'] == 0

    bad = client.post('/ytl-api/playlist', data={
        'action': 'add', 'playlist_name': 'a/b',
        'video_info': json.dumps(video(IDS[0]))})
    assert bad.status_code == 400 and not bad.get_json()['ok']

    missing = client.post('/ytl-api/playlist', data={
        'action': 'remove', 'playlist_name': 'Nope', 'video_id': IDS[0]})
    assert missing.status_code == 404


def test_queue(client):
    def queue_action(action, **fields):
        return client.post('/ytl-api/queue',
                           data=dict(fields, action=action)).get_json()

    for i in IDS[:3]:
        assert queue_action('add', video_info=json.dumps(video(i)))['result'] == 'added'
    assert queue_action('add', video_info=json.dumps(video(IDS[0])))['result'] == 'already'
    # Play next puts it first, or moves it there
    assert queue_action('play_next', video_info=json.dumps(video(IDS[3])))['result'] == 'added'
    assert queue_action('play_next', video_info=json.dumps(video(IDS[2])))['result'] == 'moved'
    assert ids(library.get_queue()) == [IDS[2], IDS[3], IDS[0], IDS[1]]

    assert queue_action('remove', video_id=IDS[3])['index'] == 1
    queue_action('add', video_info=json.dumps(video(IDS[3])), position=1)
    assert ids(library.get_queue()) == [IDS[2], IDS[3], IDS[0], IDS[1]]

    queue_action('move', video_id=IDS[1], position=0)
    assert ids(library.get_queue())[0] == IDS[1]

    cleared = queue_action('clear')
    assert cleared['count'] == 0 and len(cleared['videos']) == 4
    restored = queue_action('restore', videos=json.dumps(cleared['videos']))
    assert restored['count'] == 4


def test_queue_play_opens_first_video(client):
    assert client.get('/queue/play').headers['Location'].endswith('/queue')
    library.queue_add(video(IDS[1]))
    location = client.get('/queue/play').headers['Location']
    assert location.endswith('/watch?v=' + IDS[1])


def test_queue_page_and_panel(client):
    assert 'Your queue is empty' in client.get('/queue').get_data(as_text=True)
    library.queue_add(video(IDS[0], title='Queued one'))
    page = client.get('/queue').get_data(as_text=True)
    assert 'Queued one' in page
    assert 'data-menu-context="queue"' in page
    panel = client.get('/ytl-api/queue_panel').get_data(as_text=True)
    assert 'id="queue-panel"' in panel and 'Queued one' in panel

    client.post('/queue', data={'action': 'save', 'playlist_name': 'Saved'})
    assert ids(local_playlist.read_playlist('Saved')) == [IDS[0]]


def test_hide_is_not_saved(client):
    # Hide only takes a video off the page in library.js
    routes = {rule.rule for rule in yt_app.url_map.iter_rules()}
    assert '/ytl-api/hidden' not in routes
    assert '/hidden_videos' not in routes


def test_library_version_changes_with_playlists_and_queue(client):
    response = client.get('/ytl-api/library').get_json()
    assert response['version'] == library.library_version()
    before = library.library_version()
    local_playlist.add_to_playlist('Music', [video(IDS[0])])
    after_playlist = library.library_version()
    assert after_playlist != before
    library.queue_add(video(IDS[1]))
    assert library.library_version() != after_playlist
    page = client.get('/playlists').get_data(as_text=True)
    assert 'data-library-version="%s"' % library.library_version() in page


def test_pages_render(client):
    local_playlist.add_to_playlist('Music', [video(IDS[0], title='Song')])
    library_page = client.get('/playlists').get_data(as_text=True)
    assert 'Music' in library_page and 'Watch later' in library_page
    page = client.get('/playlists/Music').get_data(as_text=True)
    assert 'Song' in page
    assert 'data-menu-context="playlist:Music"' in page
    assert 'local_list=Music' in page
    # Watch later has a page before anything is saved to it
    assert client.get('/playlists/Watch later').status_code == 200
    assert client.get('/playlists/Nope').status_code == 404


def test_playlist_page_forms(client):
    local_playlist.add_to_playlist('Music', [video(i) for i in IDS[:2]])
    client.post('/playlists/Music', data={'action': 'rename',
                                          'new_name': 'Tunes'})
    assert local_playlist.get_playlist_names() == ['Tunes']
    response = client.post('/playlists/Tunes', data={'action': 'rename',
                                                     'new_name': 'a/b'})
    assert 'error=1' in response.headers['Location']
    client.post('/playlists/Tunes', data={'action': 'delete'})
    assert local_playlist.get_playlist_names() == []


def test_watch_page_playlist(data):
    local_playlist.add_to_playlist('Music', [video(i) for i in IDS])
    playlist = local_playlist.watch_page_playlist('Music', IDS[2], '2')
    assert playlist['current_index'] == 2
    assert playlist['video_count'] == 4
    assert playlist['items'][3]['url'].endswith(
        '/watch?v=' + IDS[3] + '&local_list=Music&index=3')

    shuffled = local_playlist.watch_page_playlist('Music', IDS[0], None, '42')
    again = local_playlist.watch_page_playlist('Music', IDS[0], None, '42')
    assert ids(shuffled['items']) == ids(again['items'])
    assert sorted(ids(shuffled['items'])) == sorted(IDS)
    assert shuffled['items'][shuffled['current_index']]['id'] == IDS[0]
    assert '&shuffle=42&' in shuffled['items'][0]['url']
    assert local_playlist.watch_page_playlist('Nope', IDS[0]) is None
