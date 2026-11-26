'''Decodes stream URLs from the clients that need YouTube's player script
(base.js): the signature ("s") of each format, and the "n" parameter, without
which googlevideo slows the stream to a crawl or refuses it.

YouTube changes the player every week or two, so this doesn't pick it apart
with regular expressions. It runs yt-dlp's solver (youtube/vendor/ejs) with
Node, which has to be installed. Node runs with its permission model on, so
the solver can't touch files or the network.'''
from youtube import util
import settings

import os
import re
import json
import shutil
import subprocess
import urllib.parse

VENDOR_DIR = os.path.join(os.path.dirname(__file__), 'vendor', 'ejs')
CACHE_DIR = os.path.join(settings.data_dir, 'player_cache')
# Players kept on disk. Each is about 1 MB once the solver has trimmed it.
PLAYERS_KEPT = 3
NODE_TIMEOUT = 60

PLAYER_ID_RE = re.compile(r'/s/player/([0-9a-zA-Z_-]+)/')
STS_RE = re.compile(r'(?:signatureTimestamp|sts)\s*:\s*(\d{5})')
IFRAME_PLAYER_RE = re.compile(r'player\\?/([0-9a-zA-Z_-]+)\\?/')


class ChallengeError(Exception):
    pass


def node_path():
    return shutil.which('node')


def player_id(base_js_url):
    match = PLAYER_ID_RE.search(base_js_url or '')
    return match.group(1) if match else None


def main_player_url(player):
    '''The TV variant of a player, which yt-dlp uses too. The watch info comes
    from the phone embed page, whose player gives the wrong answers for the
    TV client, and the solver can't run the desktop one.'''
    return ('https://www.youtube.com/s/player/' + player
            + '/tv-player-ias.vflset/tv-player-ias.js')


def current_player_url():
    '''The player url when the watch page didn't give one'''
    content = util.fetch_url('https://www.youtube.com/iframe_api',
                             report_text='Got player id').decode('utf-8')
    match = IFRAME_PLAYER_RE.search(content)
    if match is None:
        raise ChallengeError('Couldn\'t find YouTube\'s player')
    return main_player_url(match.group(1))


def _cache_path(player):
    return os.path.join(CACHE_DIR, player + '.json')


def _load_cached(player):
    try:
        with open(_cache_path(player), 'r', encoding='utf-8') as file:
            return json.load(file)
    except (OSError, ValueError):
        return None


def _save_cached(player, content):
    os.makedirs(CACHE_DIR, exist_ok=True)
    temp_path = _cache_path(player) + '.tmp'
    with open(temp_path, 'w', encoding='utf-8') as file:
        json.dump(content, file)
    os.replace(temp_path, _cache_path(player))
    # drop the oldest players
    names = [name for name in os.listdir(CACHE_DIR) if name.endswith('.json')]
    names.sort(key=lambda name: os.path.getmtime(os.path.join(CACHE_DIR, name)),
               reverse=True)
    for name in names[PLAYERS_KEPT:]:
        try:
            os.remove(os.path.join(CACHE_DIR, name))
        except OSError:
            pass


class Player:
    '''One version of YouTube's player. Fetched once and kept on disk, so
    later videos don't download it again.'''

    def __init__(self, base_js_url):
        self.id = player_id(base_js_url)
        if self.id is None:
            raise ChallengeError('Unrecognized player url: ' + str(base_js_url))
        self.url = main_player_url(self.id)
        cached = _load_cached(self.id)
        if cached:
            self.sts = cached.get('sts')
            self.preprocessed = cached.get('preprocessed')
            self.code = None
        else:
            self.code = util.fetch_url(
                self.url, report_text='Fetched player ' + self.id,
                debug_name='base.js').decode('utf-8')
            match = STS_RE.search(self.code)
            self.sts = int(match.group(1)) if match else None
            self.preprocessed = None

    def solve(self, n_challenges=(), sig_challenges=()):
        '''Returns ({n: decoded n}, {s: decoded signature})'''
        n_challenges = sorted(set(n_challenges))
        sig_challenges = sorted(set(sig_challenges))
        requests = [{'type': 'n', 'challenges': n_challenges},
                    {'type': 'sig', 'challenges': sig_challenges}]
        if self.preprocessed:
            data = {'type': 'preprocessed',
                    'preprocessed_player': self.preprocessed,
                    'requests': requests}
        else:
            data = {'type': 'player', 'player': self.code,
                    'requests': requests, 'output_preprocessed': True}
        output = _run_solver(data)
        if output.get('type') == 'error':
            raise ChallengeError(output.get('error') or 'Solver failed')
        if output.get('preprocessed_player'):
            self.preprocessed = output['preprocessed_player']
            self.code = None
            _save_cached(self.id, {'sts': self.sts,
                                   'preprocessed': self.preprocessed})
        results = []
        for response in output['responses']:
            if response.get('type') == 'error':
                raise ChallengeError(response.get('error') or 'Solver failed')
            results.append(response.get('data') or {})
        return results[0], results[1]


def _run_solver(data):
    node = node_path()
    if node is None:
        raise ChallengeError('Node.js isn\'t installed, and it\'s needed to '
                             'decode the signed-in video urls')
    with open(os.path.join(VENDOR_DIR, 'yt.solver.lib.min.js'),
              encoding='utf-8') as file:
        lib = file.read()
    with open(os.path.join(VENDOR_DIR, 'yt.solver.core.min.js'),
              encoding='utf-8') as file:
        core = file.read()
    script = (lib + '\nObject.assign(globalThis, lib);\n' + core
              + '\nconsole.log(JSON.stringify(jsc(' + json.dumps(data)
              + ')));\n')
    args = [node]
    version = subprocess.run([node, '--version'], capture_output=True,
                             text=True).stdout.strip().lstrip('v')
    try:
        version_tuple = tuple(int(part) for part in version.split('.')[:3])
    except ValueError:
        version_tuple = (0,)
    # The permission flag stopped being experimental in Node 23.5
    if version_tuple >= (23, 5):
        args.append('--permission')
    elif version_tuple >= (20,):
        args += ['--experimental-permission',
                 '--no-warnings=ExperimentalWarning']
    args.append('-')
    try:
        process = subprocess.run(args, input=script, capture_output=True,
                                 text=True, timeout=NODE_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise ChallengeError('Decoding the video urls took too long')
    if process.returncode:
        raise ChallengeError('Node failed: ' + process.stderr.strip()[-500:])
    try:
        return json.loads(process.stdout)
    except ValueError:
        raise ChallengeError('Unexpected output from the solver')


def decode_formats(info, player):
    '''Puts the decoded signature and n into the url of each format in info.
    Formats whose url couldn't be decoded are dropped.'''
    n_values = []
    signatures = []
    for fmt in info['formats']:
        if not fmt.get('url'):
            continue
        query = urllib.parse.parse_qs(urllib.parse.urlparse(fmt['url']).query)
        if 'n' in query:
            n_values.append(query['n'][0])
        if fmt.get('s'):
            signatures.append(fmt['s'])
    if not n_values and not signatures:
        return
    decoded_n, decoded_signatures = player.solve(n_values, signatures)

    formats = []
    for fmt in info['formats']:
        if not fmt.get('url'):
            continue
        parts = urllib.parse.urlparse(fmt['url'])
        query = urllib.parse.parse_qs(parts.query)
        if 'n' in query:
            n = decoded_n.get(query['n'][0])
            if not n:
                continue
            query['n'] = [n]
        if fmt.get('s'):
            signature = decoded_signatures.get(fmt['s'])
            if not signature:
                continue
            query[fmt.get('sp') or 'signature'] = [signature]
            fmt['s'] = None
        fmt['url'] = urllib.parse.urlunparse(parts._replace(
            query=urllib.parse.urlencode(query, doseq=True)))
        formats.append(fmt)
    info['formats'] = formats
