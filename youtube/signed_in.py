'''Signed-in fallback for when YouTube asks to confirm this isn't a bot.

YouTube flags IP addresses, and then refuses every client that isn't signed
in. With the youtube_sign_in setting on, a refused video is asked for again
as the TV app, signed in to the account that a browser on this computer is
signed in to (browser_cookies.py), or that a cookies.txt file is from. Only videos YouTube refused are asked
for this way, so the account only sees those.

The TV client's stream urls have to be decoded with YouTube's player script
(js_challenges.py, which needs Node).'''
from youtube import util, yt_data_extract, js_challenges, browser_cookies
from youtube import yt_app
import settings

import os
import json
import flask
import time
import hashlib
import http.cookiejar

ORIGIN = 'https://www.youtube.com'
# yt-dlp's tv_samsung client, which it uses for signed-in requests
CLIENT = {
    'clientName': 'TVHTML5',
    'clientVersion': '5.20260707',
    'deviceMake': 'Samsung',
    'deviceModel': 'UKS9800',
    'userAgent': ('Mozilla/5.0 (SMART-TV; Linux; Tizen 2.4.0) '
                  'AppleWebKit/538.1 (KHTML, like Gecko) Version/2.4.0 '
                  'TV Safari/538.1'),
    'osName': 'Tizen',
    'osVersion': '2.4.0',
    'hl': 'en',
    'gl': 'US',
}
CLIENT_NAME_NUMBER = '7'


class SignInError(Exception):
    pass


def cookies_file():
    path = (settings.youtube_cookies_file or '').strip()
    return os.path.expanduser(path) if path else None


def is_set_up():
    if settings.youtube_sign_in == 1:
        return True
    return settings.youtube_sign_in == 2 and bool(cookies_file())


def browser_setting(value):
    try:
        return browser_cookies.BROWSERS[int(value)]
    except (ValueError, IndexError):
        return 'auto'


def get_cookies():
    '''{name: value} of the youtube.com cookies to sign in with'''
    if settings.youtube_sign_in == 1:
        try:
            name, cookies = browser_cookies.youtube_cookies(
                browser_setting(settings.youtube_sign_in_browser))
        except browser_cookies.BrowserCookieError as e:
            raise SignInError(str(e))
        if not (cookies.get('SAPISID') or cookies.get('__Secure-3PAPISID')):
            raise SignInError(name + ' isn\'t signed in to YouTube. Sign in '
                              'with Google there.')
        return cookies
    if not cookies_file():
        raise SignInError('No cookies file is set in Settings.')
    return load_cookies(cookies_file())


@yt_app.route('/ytl-api/sign_in_status')
def sign_in_status():
    '''Whether the browser picked in Settings is signed in to YouTube. Only
    says yes or no: the cookies stay on the server.'''
    signed_in, message = browser_cookies.sign_in_status(
        browser_setting(flask.request.args.get('browser', 0)))
    response = flask.jsonify({'signed_in': signed_in, 'message': message})
    response.headers['Cache-Control'] = 'no-store'
    return response


def load_cookies(path):
    '''{name: value} of the youtube.com cookies in a cookies.txt file (the
    Netscape format that browser extensions like "Get cookies.txt LOCALLY"
    export)'''
    jar = http.cookiejar.MozillaCookieJar(path)
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except FileNotFoundError:
        raise SignInError('The cookies file in Settings doesn\'t exist: '
                          + path)
    except (http.cookiejar.LoadError, OSError) as e:
        raise SignInError('Couldn\'t read the cookies file in Settings. It '
                          'has to be a cookies.txt file (Netscape format): '
                          + str(e))
    cookies = {}
    for cookie in jar:
        if cookie.domain.lstrip('.').endswith('youtube.com'):
            cookies[cookie.name] = cookie.value
    return cookies


def auth_header(cookies, now=None):
    '''The Authorization header YouTube expects along with the cookies'''
    sapisid = cookies.get('SAPISID') or cookies.get('__Secure-3PAPISID')
    if not sapisid:
        raise SignInError('The cookies file has no YouTube sign-in in it. '
                          'Export it while signed in to youtube.com.')
    timestamp = str(int(now if now is not None else time.time()))
    digest = hashlib.sha1(
        (timestamp + ' ' + sapisid + ' ' + ORIGIN).encode('utf-8')).hexdigest()
    return 'SAPISIDHASH ' + timestamp + '_' + digest


def fetch_player_response(video_id, sts):
    cookies = get_cookies()
    headers = (
        ('Content-Type', 'application/json'),
        ('User-Agent', CLIENT['userAgent']),
        ('X-YouTube-Client-Name', CLIENT_NAME_NUMBER),
        ('X-YouTube-Client-Version', CLIENT['clientVersion']),
        ('Origin', ORIGIN),
        ('X-Origin', ORIGIN),
        ('X-Goog-AuthUser', '0'),
        ('Authorization', auth_header(cookies)),
        ('Cookie', '; '.join(name + '=' + value
                             for name, value in cookies.items())),
    )
    data = {
        'context': {'client': CLIENT},
        'videoId': video_id,
        'contentCheckOk': True,
        'racyCheckOk': True,
    }
    if sts:
        data['playbackContext'] = {'contentPlaybackContext': {
            'html5Preference': 'HTML5_PREF_WANTS',
            'signatureTimestamp': sts,
        }}
    return util.fetch_url(
        ORIGIN + '/youtubei/v1/player?prettyPrint=false',
        headers=headers, data=json.dumps(data),
        debug_name='youtubei_player_signed_in',
        report_text='Fetched signed-in player',
    ).decode('utf-8')


def update_info(info, video_id):
    '''Asks for the video again, signed in, and puts its formats in info.
    Raises SignInError if that didn't work.'''
    player = js_challenges.Player(info.get('base_js')
                                  or js_challenges.current_player_url())
    if not info.get('base_js'):
        info['base_js'] = player.url
        info['player_name'] = player.id
    response = fetch_player_response(video_id, player.sts)

    yt_data_extract.update_with_new_urls(info, response)
    if not info['formats']:
        status = json.loads(response).get('playabilityStatus', {})
        raise SignInError('Signed in, YouTube still said: '
                          + (status.get('reason') or status.get('status')
                             or 'no video'))
    try:
        js_challenges.decode_formats(info, player)
    except js_challenges.ChallengeError as e:
        info['formats'] = []
        raise SignInError('Couldn\'t decode the video urls: ' + str(e))
    if not info['formats']:
        raise SignInError('Couldn\'t decode the video urls')
    try:
        check_stream(info['formats'][0]['url'])
    except SignInError:
        info['formats'] = []
        raise


def check_stream(url):
    '''One small request for the first byte, so a page whose video wouldn't
    play shows why instead'''
    try:
        response, cleanup = util.fetch_url_response(
            url, (('Range', 'bytes=0-0'), ('User-Agent', CLIENT['userAgent'])))
    except Exception as e:
        raise SignInError('Couldn\'t reach the video stream: ' + str(e))
    status = response.status
    cleanup(response)
    if status not in (200, 206):
        raise SignInError('YouTube refused the decoded video stream (error %d)'
                          % status)
