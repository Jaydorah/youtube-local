'''Tests for asking again signed in, and decoding the TV client's urls'''
import hashlib
import os
import shutil
import sqlite3
import subprocess
import urllib.parse

import pytest

# youtube has to be imported before settings, like in test_util.py
import youtube
from youtube import signed_in, js_challenges, browser_cookies
import settings


COOKIES_TXT = '''# Netscape HTTP Cookie File
.youtube.com\tTRUE\t/\tTRUE\t1900000000\tSAPISID\tabc123/xyz
.youtube.com\tTRUE\t/\tTRUE\t1900000000\tLOGIN_INFO\tlogin
.google.com\tTRUE\t/\tTRUE\t1900000000\tSID\tgoogle-only
'''


def test_cookies_file_is_read(tmp_path):
    path = tmp_path / 'cookies.txt'
    path.write_text(COOKIES_TXT)
    cookies = signed_in.load_cookies(str(path))
    assert cookies == {'SAPISID': 'abc123/xyz', 'LOGIN_INFO': 'login'}


def test_missing_cookies_file(tmp_path):
    with pytest.raises(signed_in.SignInError, match='doesn\'t exist'):
        signed_in.load_cookies(str(tmp_path / 'nope.txt'))


def test_auth_header():
    header = signed_in.auth_header({'SAPISID': 'abc'}, now=1700000000)
    digest = hashlib.sha1(
        b'1700000000 abc https://www.youtube.com').hexdigest()
    assert header == 'SAPISIDHASH 1700000000_' + digest


def test_auth_header_needs_a_sign_in():
    with pytest.raises(signed_in.SignInError, match='no YouTube sign-in'):
        signed_in.auth_header({'PREF': 'x'})


def test_only_used_when_set_up(monkeypatch):
    monkeypatch.setattr(settings, 'youtube_sign_in', 0)
    monkeypatch.setattr(settings, 'youtube_cookies_file', '~/c.txt')
    assert not signed_in.is_set_up()
    monkeypatch.setattr(settings, 'youtube_sign_in', 2)
    assert signed_in.is_set_up()
    monkeypatch.setattr(settings, 'youtube_cookies_file', '')
    assert not signed_in.is_set_up()
    monkeypatch.setattr(settings, 'youtube_sign_in', 1)
    assert signed_in.is_set_up()


class FakePlayer:
    def solve(self, n_challenges, sig_challenges):
        return ({n: n[::-1] for n in n_challenges},
                {s: 'decoded-' + s for s in sig_challenges})


def query(url):
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)


def test_decode_formats():
    info = {'formats': [
        {'url': 'https://r1.googlevideo.com/videoplayback?itag=18&n=abcd',
         's': 'SIG1', 'sp': 'sig'},
        {'url': 'https://r1.googlevideo.com/videoplayback?itag=140&n=abcd',
         's': None, 'sp': None},
        {'url': None, 's': None, 'sp': None},
    ]}
    js_challenges.decode_formats(info, FakePlayer())
    assert len(info['formats']) == 2
    first, second = info['formats']
    assert query(first['url'])['n'] == ['dcba']
    assert query(first['url'])['sig'] == ['decoded-SIG1']
    assert first['s'] is None   # so the old decryption leaves it alone
    assert query(second['url'])['n'] == ['dcba']


def test_player_urls():
    url = ('https://www.youtube.com/s/player/b485e332/'
           'player-plasma-ias-phone-en_US.vflset/base.js')
    assert js_challenges.player_id(url) == 'b485e332'
    assert js_challenges.main_player_url('b485e332').endswith(
        '/b485e332/tv-player-ias.vflset/tv-player-ias.js')


# ---------- Reading a browser's sign-in ----------

@pytest.fixture
def browsers(tmp_path, monkeypatch):
    roots = {name: [str(tmp_path / name)] for name in browser_cookies.NAMES}
    monkeypatch.setattr(browser_cookies, 'PROFILE_ROOTS', roots)
    monkeypatch.setattr(browser_cookies, '_keyring_password', lambda b: None)
    return tmp_path


def make_firefox(root, cookies):
    profile = root / 'firefox' / 'abc.default-release'
    profile.mkdir(parents=True)
    db = sqlite3.connect(str(profile / 'cookies.sqlite'))
    db.execute('CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT)')
    db.executemany('INSERT INTO moz_cookies VALUES (?, ?, ?)', cookies)
    db.commit()
    db.close()
    return profile / 'cookies.sqlite'


def encrypt(password, value, host, version):
    plain = value.encode('utf-8')
    if version >= 24:
        plain = hashlib.sha256(host.encode('utf-8')).digest() + plain
    padding = 16 - len(plain) % 16
    plain += bytes([padding]) * padding
    key = hashlib.pbkdf2_hmac('sha1', password, b'saltysalt', 1, 16)
    return subprocess.run(
        ['openssl', 'enc', '-e', '-aes-128-cbc', '-nopad', '-K', key.hex(),
         '-iv', (b' ' * 16).hex()],
        input=plain, capture_output=True, check=True).stdout


def make_brave(root, cookies, version):
    profile = root / 'brave' / 'Default'
    profile.mkdir(parents=True)
    db = sqlite3.connect(str(profile / 'Cookies'))
    db.execute('CREATE TABLE meta (key TEXT, value TEXT)')
    db.execute('INSERT INTO meta VALUES ("version", ?)', (str(version),))
    db.execute('CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, '
               'encrypted_value BLOB)')
    for host, name, value, prefix, password in cookies:
        db.execute('INSERT INTO cookies VALUES (?, ?, "", ?)',
                   (host, name, prefix + encrypt(password, value, host,
                                                 version)))
    db.commit()
    db.close()
    return profile / 'Cookies'


def test_firefox_sign_in(browsers):
    make_firefox(browsers, [('.youtube.com', 'SAPISID', 'abc'),
                            ('.youtube.com', 'PREF', 'f6=1'),
                            ('.notyoutube.com', 'SID', 'other'),
                            ('.google.com', 'SID', 'google')])
    name, cookies = browser_cookies.youtube_cookies('firefox')
    assert name == 'Firefox'
    assert cookies == {'SAPISID': 'abc', 'PREF': 'f6=1'}
    assert browser_cookies.sign_in_status('firefox') == (
        True, 'Signed in to YouTube in Firefox.')


needs_openssl = pytest.mark.skipif(shutil.which('openssl') is None,
                                   reason='needs openssl to encrypt')


@needs_openssl
@pytest.mark.parametrize('version', [23, 24])
def test_brave_sign_in(browsers, version):
    make_brave(browsers, [
        ('.youtube.com', 'SAPISID', 'abc/def', b'v10', b'peanuts'),
        ('.youtube.com', 'LOGIN_INFO', 'x' * 40, b'v11', b''),
        ('accounts.google.com', 'SID', 'google', b'v10', b'peanuts'),
    ], version)
    name, cookies = browser_cookies.youtube_cookies('brave')
    assert name == 'Brave'
    assert cookies == {'SAPISID': 'abc/def', 'LOGIN_INFO': 'x' * 40}


@needs_openssl
def test_brave_with_a_locked_keyring(browsers):
    make_brave(browsers, [('.youtube.com', 'SAPISID', 'abc', b'v11',
                           b'keyring-password')], 24)
    with pytest.raises(browser_cookies.BrowserCookieError, match='keyring'):
        browser_cookies.youtube_cookies('brave')


def test_automatic_picks_the_last_used_browser(browsers):
    old = make_firefox(browsers, [('.youtube.com', 'PREF', 'x')])
    os.utime(old, (1000, 1000))
    librewolf = browsers / 'librewolf' / 'p.default'
    shutil.copytree(old.parent, librewolf)
    assert browser_cookies.pick_browser('auto') == 'librewolf'


def test_not_signed_in(browsers):
    make_firefox(browsers, [('.youtube.com', 'PREF', 'x')])
    signed_in_now, message = browser_cookies.sign_in_status('firefox')
    assert not signed_in_now
    assert 'isn\'t signed in' in message


def test_browser_not_installed(browsers):
    with pytest.raises(browser_cookies.BrowserCookieError,
                       match='Couldn\'t find'):
        browser_cookies.pick_browser('auto')


def test_status_page_says_only_yes_or_no(browsers, monkeypatch):
    make_firefox(browsers, [('.youtube.com', 'SAPISID', 'secret-value')])
    client = youtube.yt_app.test_client()
    result = client.get('/ytl-api/sign_in_status?browser=4').get_json()
    assert result['signed_in'] is True
    assert 'secret-value' not in str(result)


def test_signed_in_requests_use_the_browser(browsers, monkeypatch):
    make_firefox(browsers, [('.youtube.com', 'SAPISID', 'abc')])
    monkeypatch.setattr(settings, 'youtube_sign_in', 1)
    monkeypatch.setattr(settings, 'youtube_sign_in_browser', 4)
    assert signed_in.get_cookies() == {'SAPISID': 'abc'}
