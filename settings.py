from youtube import util
import ast
import re
import os
import collections
import sys
import urllib.parse

import flask
from flask import request

# The old way of adding videos to playlists: a checkbox on each video and a
# +Playlist form in the header. Videos are saved from the menu on each video
# now (Save to playlist). Set to True to bring the checkboxes back.
local_playlists_enabled = False

SETTINGS_INFO = collections.OrderedDict([
    ('ui_style', {
        'type': int,
        'default': 1,
        'label': 'Style',
        'comment': '''0 - Classic: the original look
1 - Modern: rounded cards, a sticky header and a grid of videos
2 - YouTube: laid out like youtube.com''',
        'options': [
            (0, 'Classic'),
            (1, 'Modern'),
            (2, 'YouTube'),
        ],
        'category': 'appearance',
        'description': 'Classic is the original look, and keeps its own color and font settings.',
    }),

    ('color_scheme', {
        'type': int,
        'default': 0,
        'comment': '''Colors for the Modern and YouTube styles
0 - Follow the system (light or dark, whichever your browser or OS is set to)
1 - Light
2 - Dark''',
        'options': [
            (0, 'Same as system'),
            (1, 'Light'),
            (2, 'Dark'),
        ],
        'category': 'appearance',
        'shown_for': ('ui_style', (1, 2)),
    }),

    ('theme', {
        'type': int,
        'default': 0,
        'label': 'Classic theme',
        'comment': '',
        'options': [
            (0, 'Light'),
            (1, 'Gray'),
            (2, 'Dark'),
        ],
        'category': 'appearance',
        'shown_for': ('ui_style', (0,)),
    }),

    ('font', {
        'type': int,
        'default': 1,
        'label': 'Classic font',
        'comment': '',
        'options': [
            (0, 'Browser default'),
            (1, 'Arial'),
            (2, 'Liberation Serif'),
            (3, 'Verdana'),
            (4, 'Tahoma'),
        ],
        'category': 'appearance',
        'shown_for': ('ui_style', (0,)),
    }),

    ('youtube_sign_in', {
        'type': int,
        'default': 0,
        'label': 'Sign in to YouTube',
        'comment': '''When YouTube refuses a video to confirm this isn't a bot (or because of its age),
ask for it again signed in. Needs Node.js installed.
YouTube then knows that account watched those videos, and Google has suspended
accounts used like this, so a spare account is safer.
0 - Off
1 - With Google, in my browser: uses the account a browser on this computer is signed in to
2 - Cookies file: uses a cookies.txt file exported from a browser''',
        'options': [
            (0, 'Off'),
            (1, 'With Google, in my browser'),
            (2, 'Cookies file'),
        ],
        'category': 'network',
        'description': ('Only for videos YouTube refuses with its bot check or for age; '
                        'those are asked for again signed in. YouTube links them to the '
                        'account, and Google has suspended accounts used this way, so '
                        'a spare one is safer. Needs Node.js.'),
    }),

    ('youtube_sign_in_browser', {
        'type': int,
        'default': 0,
        'label': 'Browser',
        'comment': '''The browser whose YouTube sign-in is used
0 - Automatic: the one used most recently
1 - Brave
2 - Chrome
3 - Chromium
4 - Firefox
5 - LibreWolf''',
        'options': [
            (0, 'Automatic (used most recently)'),
            (1, 'Brave'),
            (2, 'Chrome'),
            (3, 'Chromium'),
            (4, 'Firefox'),
            (5, 'LibreWolf'),
        ],
        'category': 'network',
        'shown_for': ('youtube_sign_in', (1,)),
        'description': 'Sign in to YouTube in that browser, then check here.',
    }),

    ('youtube_cookies_file', {
        'type': str,
        'default': '',
        'label': 'Cookies file',
        'comment': '''Path to a cookies.txt file (Netscape format) exported from a browser
that's signed in to YouTube, for Sign in to YouTube = 2''',
        'category': 'network',
        'shown_for': ('youtube_sign_in', (2,)),
        'description': ('Path to a cookies.txt file exported from a browser that\'s '
                        'signed in to YouTube, with an extension like "Get cookies.txt '
                        'LOCALLY".'),
    }),

    ('route_tor', {
        'type': int,
        'default': 0,
        'label': 'Route Tor',
        'comment': '''0 - Off
1 - On, except video
2 - On, including video (see warnings)''',
        'options': [
            (0, 'Off'),
            (1, 'On, except video'),
            (2, 'On, including video (see warnings)'),
        ],
        'category': 'network',
    }),

    ('tor_port', {
        'type': int,
        'default': 9150,
        'comment': '',
        'category': 'network',
    }),

    ('tor_control_port', {
        'type': int,
        'default': 9151,
        'comment': '',
        'category': 'network',
    }),

    ('port_number', {
        'type': int,
        'default': 8080,
        'comment': '',
        'category': 'network',
    }),

    ('allow_foreign_addresses', {
        'type': bool,
        'default': False,
        'comment': '''This will allow others to connect to your Youtube Local instance as a website.
For security reasons, enabling this is not recommended.''',
        'hidden': True,
        'category': 'network',
    }),

    ('allow_foreign_post_requests', {
        'type': bool,
        'default': False,
        'comment': '''Enables requests from foreign addresses to make post requests.
For security reasons, enabling this is not recommended.''',
        'hidden': True,
        'category': 'network',
    }),

    ('subtitles_mode', {
        'type': int,
        'default': 0,
        'comment': '''0 - off by default
1 - only manually created subtitles on by default
2 - enable even if automatically generated is all that's available''',
        'label': 'Default subtitles mode',
        'options': [
            (0, 'Off'),
            (1, 'Manually created only'),
            (2, 'Automatic if manual unavailable'),
        ],
        'category': 'playback',
    }),

    ('subtitles_language', {
        'type': str,
        'default': 'en',
        'comment': '''ISO 639 language code: https://en.wikipedia.org/wiki/List_of_ISO_639-1_codes''',
        'category': 'playback',
    }),

    ('default_volume', {
        'type': int,
        'default': -1,
        'max': 100,
        'min': -1,
        'comment': '''Sets a default volume.
Defaults to -1, which means no default value is forced and the browser will set the volume.''',
        'category': 'playback',
    }),

    ('related_videos_mode', {
        'type': int,
        'default': 1,
        'comment': '''0 - Related videos disabled
1 - Related videos always shown
2 - Related videos hidden; shown by clicking a button''',
        'options': [
            (0, 'Disabled'),
            (1, 'Always shown'),
            (2, 'Shown by clicking button'),
        ],
        'category': 'interface',
    }),

    ('comments_mode', {
        'type': int,
        'default': 1,
        'comment': '''0 - Video comments disabled
1 - Video comments always shown
2 - Video comments hidden; shown by clicking a button''',
        'options': [
            (0, 'Disabled'),
            (1, 'Always shown'),
            (2, 'Shown by clicking button'),
        ],
        'category': 'interface',
    }),

    ('expand_description', {
        'label': 'Show description by default',
        'type': bool,
        'default': True,
        'comment': '''On: the description under a video is open.
Off: it's closed until its bar is clicked, like the download list''',
        'category': 'interface',
    }),

    ('abbreviate_numbers', {
        'label': 'Abbreviate numbers',
        'type': bool,
        'default': False,
        'comment': '''On: counts are shortened, such as 1.5K views or 2.51M subscribers.
Off: counts are shown in full, such as 1,523 views''',
        'category': 'interface',
        'description': 'Shows 1.5K views or 2.51M subscribers instead of the full count. Hover a count on the watch page to see it in full.',
    }),

    ('enable_comment_avatars', {
        'type': bool,
        'default': True,
        'comment': '',
        'category': 'interface',
    }),

    ('default_comment_sorting', {
        'type': int,
        'default': 0,
        'comment': '''0 to sort by top
1 to sort by newest''',
        'options': [
            (0, 'Top'),
            (1, 'Newest'),
        ],
    }),

    ('theater_mode', {
        'type': bool,
        'default': False,
        'comment': '''On: the video goes across the whole window, with the info and related videos below it.
Off: like YouTube's default view, with the related videos to the right of the video''',
        'category': 'interface',
    }),

    ('default_resolution', {
        'type': int,
        'default': 720,
        'comment': '',
        'options': [
            (144, '144p'),
            (240, '240p'),
            (360, '360p'),
            (480, '480p'),
            (720, '720p'),
            (1080, '1080p'),
            (1440, '1440p'),
            (2160, '2160p'),
        ],
        'category': 'playback',
    }),

    ('autoplay_videos', {
        'type': bool,
        'default': False,
        'comment': '',
        'category': 'playback',
    }),

    ('codec_rank_h264', {
        'type': int,
        'default': 1,
        'label': 'H.264 Codec Ranking',
        'comment': '',
        'options': [(1, '#1'), (2, '#2'), (3, '#3')],
        'category': 'playback',
        'description': (
            'Which video codecs to prefer. Codecs given the same '
            'ranking will use smaller file size as a tiebreaker.'
        )
    }),

    ('codec_rank_vp', {
        'type': int,
        'default': 2,
        'label': 'VP8/VP9 Codec Ranking',
        'comment': '',
        'options': [(1, '#1'), (2, '#2'), (3, '#3')],
        'category': 'playback',
    }),

    ('codec_rank_av1', {
        'type': int,
        'default': 3,
        'label': 'AV1 Codec Ranking',
        'comment': '',
        'options': [(1, '#1'), (2, '#2'), (3, '#3')],
        'category': 'playback',
    }),

    ('prefer_uni_sources', {
        'label': 'Use integrated sources',
        'type': int,
        'default': 1,
        'comment': '',
        'options': [
            (0, 'Prefer not'),
            (1, 'Prefer'),
            (2, 'Always'),
        ],
        'category': 'playback',
        'description': 'If set to Prefer or Always and the default resolution is set to 360p or 720p, uses the unified (integrated) video files which contain audio and video, with buffering managed by the browser. If set to prefer not, uses the separate audio and video files through custom buffer management in av-merge via MediaSource unless they are unavailable.',
    }),

    ('use_video_hotkeys', {
        'label': 'Enable video hotkeys',
        'type': bool,
        'default': True,
        'comment': '',
        'category': 'interface',
    }),

    ('video_player', {
        'type': int,
        'default': 1,
        'comment': '',
        'options': [
            (0, 'Browser Default'),
            (1, 'Plyr'),
        ],
        'category': 'interface',
    }),

    ('proxy_images', {
        'label': 'Route images',
        'type': bool,
        'default': True,
        'comment': '',
        'category': 'network',
    }),

    ('use_comments_js', {
        'label': 'Enable comments.js',
        'type': bool,
        'default': True,
        'comment': '',
        'category': 'interface',
    }),

    ('use_sponsorblock_js', {
        'label': 'Enable SponsorBlock',
        'type': bool,
        'default': False,
        'comment': '',
        'category': 'playback',
    }),

    ('use_return_youtube_dislike', {
        'label': 'Show dislikes',
        'type': bool,
        'default': True,
        'comment': '''YouTube no longer gives out the number of dislikes. With this on, it's
fetched from returnyoutubedislike.com, which estimates it. That site is
sent the id of each video you open.''',
        'category': 'interface',
    }),

    ('homepage', {
        'type': int,
        'default': 0,
        'label': 'Default homepage',
        'comment': '''Page opened at the root address (for example localhost:8080)
0 - Home menu
1 - Subscriptions
2 - Playlists''',
        'options': [
            (0, 'Home menu'),
            (1, 'Subscriptions'),
            (2, 'Playlists'),
        ],
        'category': 'interface',
    }),

    ('utc_offset_hours', {
        'type': int,
        'default': 0,
        'max': 14,
        'min': -12,
        'label': 'Timezone: hours from UTC',
        'comment': '''Upload dates and times are shown in UTC shifted by this many hours.
For example -5 for New York in winter, 1 for Berlin in winter''',
        'category': 'interface',
    }),

    ('utc_offset_minutes', {
        'type': int,
        'default': 0,
        'max': 59,
        'min': 0,
        'label': 'Timezone: extra minutes',
        'comment': '''Added to the hours above, in the same direction (for example 30 with 5 hours
for India, or 30 with -3 hours for Newfoundland)''',
        'category': 'interface',
    }),

    ('embed_page_mode', {
        'type': bool,
        'label': 'Enable embed page',
        'default': True,
        'comment': '',
        'category': 'interface',
    }),

    ('autocheck_subscriptions', {
        'type': bool,
        'default': 0,
        'comment': '',
    }),
    ('subscription_check_pace', {
        'type': int,
        'default': 1,
        'label': 'How often to check channels',
        'comment': '''How often each channel is checked for new videos, going by how often it uploads.
Every check is a request to YouTube.
0 - Often: about 10 checks per upload
1 - Normal: about 4 checks per upload, at most every 6 hours
2 - Rarely: about 2 checks per upload, at most once a day''',
        'options': [
            (0, 'Often'),
            (1, 'Normal'),
            (2, 'Rarely'),
        ],
        'description': 'Each check is a request to YouTube. Normal makes about a third as many as Often; new videos can show up a few hours later.',
        'shown_for': ('autocheck_subscriptions', (True,)),
    }),
    ('include_shorts_in_subscriptions', {
        'type': bool,
        'default': 0,
        'comment': '',
    }),
    ('include_shorts_in_channel', {
        'type': bool,
        'default': 1,
        'comment': '',
    }),

    ('debugging_save_responses', {
        'type': bool,
        'default': False,
        'comment': '''Save all responses from youtube for debugging''',
        'hidden': True,
    }),

    ('settings_version', {
        'type': int,
        'default': 7,
        'comment': '''Do not change, remove, or comment out this value, or else your settings may be lost or corrupted''',
        'hidden': True,
    }),
])

program_directory = os.path.dirname(os.path.realpath(__file__))
acceptable_targets = SETTINGS_INFO.keys() | {
    'enable_comments', 'enable_related_videos', 'preferred_video_codec'
}


def comment_string(comment):
    result = ''
    for line in comment.splitlines():
        result += '# ' + line + '\n'
    return result

def save_settings(settings_dict):
    with open(settings_file_path, 'w', encoding='utf-8') as file:
        for setting_name, setting_info in SETTINGS_INFO.items():
            file.write(comment_string(setting_info['comment']) + setting_name + ' = ' + repr(settings_dict[setting_name]) + '\n\n')

def add_missing_settings(settings_dict):
    result = default_settings()
    result.update(settings_dict)
    return result

def default_settings():
    return {key: setting_info['default'] for key, setting_info in SETTINGS_INFO.items()}

def upgrade_to_2(settings_dict):
    '''Upgrade to settings version 2'''
    new_settings = settings_dict.copy()
    if 'enable_comments' in settings_dict:
        new_settings['comments_mode'] = int(settings_dict['enable_comments'])
        del new_settings['enable_comments']
    if 'enable_related_videos' in settings_dict:
        new_settings['related_videos_mode'] = int(settings_dict['enable_related_videos'])
        del new_settings['enable_related_videos']
    new_settings['settings_version'] = 2
    return new_settings

def upgrade_to_3(settings_dict):
    new_settings = settings_dict.copy()
    if 'route_tor' in settings_dict:
        new_settings['route_tor'] = int(settings_dict['route_tor'])
    new_settings['settings_version'] = 3
    return new_settings
def upgrade_to_4(settings_dict):
    new_settings = settings_dict.copy()
    if 'preferred_video_codec' in settings_dict:
        pref = settings_dict['preferred_video_codec']
        if pref == 0:
            new_settings['codec_rank_h264'] = 1
            new_settings['codec_rank_vp'] = 2
            new_settings['codec_rank_av1'] = 3
        else:
            new_settings['codec_rank_h264'] = 3
            new_settings['codec_rank_vp'] = 2
            new_settings['codec_rank_av1'] = 1
        del new_settings['preferred_video_codec']
    new_settings['settings_version'] = 4
    return new_settings

def upgrade_to_5(settings_dict):
    new_settings = settings_dict.copy()
    if 'prefer_uni_sources' in settings_dict:
        new_settings['prefer_uni_sources'] = int(settings_dict['prefer_uni_sources'])
    new_settings['settings_version'] = 5
    return new_settings

def upgrade_to_6(settings_dict):
    new_settings = settings_dict.copy()
    if 'gather_googlevideo_domains' in new_settings:
        del new_settings['gather_googlevideo_domains']
    new_settings['settings_version'] = 6
    return new_settings

def upgrade_to_7(settings_dict):
    '''Keep dark mode for people who had the Dark theme when the new styles,
    which have their own light/dark setting, became the default'''
    new_settings = settings_dict.copy()
    if settings_dict.get('theme') == 2:
        new_settings['color_scheme'] = 2
    elif 'theme' in settings_dict:
        new_settings['color_scheme'] = 1
    new_settings['settings_version'] = 7
    return new_settings

upgrade_functions = {
    1: upgrade_to_2,
    2: upgrade_to_3,
    3: upgrade_to_4,
    4: upgrade_to_5,
    5: upgrade_to_6,
    6: upgrade_to_7,
}

def log_ignored_line(line_number, message):
    print("WARNING: Ignoring settings.txt line " + str(node.lineno) + " (" + message + ")")




if os.path.isfile("settings.txt"):
    print("Running in portable mode")
    settings_dir = os.path.normpath('./')
    data_dir = os.path.normpath('./data')
else:
    print("Running in non-portable mode")
    settings_dir = os.path.expanduser(os.path.normpath("~/.youtube-local"))
    data_dir = os.path.expanduser(os.path.normpath("~/.youtube-local/data"))
    if not os.path.exists(settings_dir):
        os.makedirs(settings_dir)

settings_file_path = os.path.join(settings_dir, 'settings.txt')

try:
    with open(settings_file_path, 'r', encoding='utf-8') as file:
        settings_text = file.read()
except FileNotFoundError:
    current_settings_dict = default_settings()
    save_settings(current_settings_dict)
else:
    if re.fullmatch(r'\s*', settings_text):     # blank file
        current_settings_dict = default_settings()
        save_settings(current_settings_dict)
    else:
        # parse settings in a safe way, without exec
        current_settings_dict = {}
        try: # Old python versions
            attributes = {
                ast.Constant: 'value',
                ast.NameConstant: 'value',
                ast.Num: 'n',
                ast.Str: 's',
            }
        except Exception: # Python >= 3.14
            attributes = {
                ast.Constant: 'value',
            }
        module_node = ast.parse(settings_text)
        for node in module_node.body:
            if type(node) != ast.Assign:
                log_ignored_line(node.lineno, "only assignments are allowed")
                continue

            if len(node.targets) > 1:
                log_ignored_line(node.lineno, "only simple single-variable assignments allowed")
                continue

            target = node.targets[0]
            if type(target) != ast.Name:
                log_ignored_line(node.lineno, "only simple single-variable assignments allowed")
                continue

            if target.id not in acceptable_targets:
                log_ignored_line(node.lineno,  target.id + " is not a valid setting")
                continue

            value = None
            # Negative values
            if (
                type(node.value) is ast.UnaryOp
                and type(node.value.op) is ast.USub
                and type(node.value.operand) in attributes
            ):
                value = -node.value.operand.__getattribute__(
                    attributes[type(node.value.operand)]
                )
            elif type(node.value) not in attributes:
                print(type(node.value))
                log_ignored_line(node.lineno, "only literals allowed for values")
                continue

            # Regular values
            if not value:
                value = node.value.__getattribute__(
                    attributes[type(node.value)]
                )
            current_settings_dict[target.id] = value

        # upgrades
        latest_version = SETTINGS_INFO['settings_version']['default']
        while current_settings_dict.get('settings_version',1) < latest_version:
            current_version = current_settings_dict.get('settings_version', 1)
            print('Upgrading settings.txt to version', current_version+1)
            upgrade_func = upgrade_functions[current_version]
            # Must add missing settings here rather than below because
            # save_settings needs all settings to be present
            current_settings_dict = add_missing_settings(
                upgrade_func(current_settings_dict))
            save_settings(current_settings_dict)

        # some settings not in the file, add those missing settings to the file
        if not current_settings_dict.keys() >= SETTINGS_INFO.keys():
            print('Adding missing settings to settings.txt')
            current_settings_dict = add_missing_settings(current_settings_dict)
            save_settings(current_settings_dict)

globals().update(current_settings_dict)



if route_tor:
    print("Tor routing is ON")
else:
    print("Tor routing is OFF - your Youtube activity is NOT anonymous")




hooks = {}
def add_setting_changed_hook(setting, func):
    '''Called right after new settings take effect'''
    if setting in hooks:
        hooks[setting].append(func)
    else:
        hooks[setting] = [func]


def set_img_prefix(old_value=None, value=None):
    global img_prefix
    if value is None:
        value = proxy_images
    if value:
        img_prefix = '/'
    else:
        img_prefix = ''
set_img_prefix()
add_setting_changed_hook('proxy_images', set_img_prefix)


categories = ['appearance', 'interface', 'playback', 'network', 'other']

def is_setting_shown(setting_info, values):
    '''Whether a setting applies, given the values of the other settings.
    'shown_for': (other_setting, values_it_applies_for)'''
    if setting_info.get('hidden', False):
        return False
    if 'shown_for' not in setting_info:
        return True
    other_setting, applies_for = setting_info['shown_for']
    return values[other_setting] in applies_for

def setting_help(setting_info):
    '''Text under a setting's name. Comments of settings with options just
    repeat the options, so those are left out.'''
    if 'description' in setting_info:
        return setting_info['description']
    if 'options' in setting_info:
        return ''
    return ' '.join(setting_info['comment'].splitlines())

def settings_page():
    if request.method == 'GET':
        settings_by_category = {categ: [] for categ in categories}
        for setting_name, setting_info in SETTINGS_INFO.items():
            if setting_info.get('hidden', False):
                continue
            categ = setting_info.get('category', 'other')
            settings_by_category[categ].append({
                'name': setting_name,
                'info': setting_info,
                'value': current_settings_dict[setting_name],
                'help': setting_help(setting_info),
                'shown': is_setting_shown(setting_info,
                                          current_settings_dict),
            })
        return flask.render_template('settings.html',
            categories = categories,
            settings_by_category = settings_by_category,
        )
    elif request.method == 'POST':
        for key, value in request.values.items():
            if key in SETTINGS_INFO:
                if SETTINGS_INFO[key]['type'] is bool and value == 'on':
                    current_settings_dict[key] = True
                else:
                    current_settings_dict[key] = SETTINGS_INFO[key]['type'](value)
            else:
                flask.abort(400)

        # need this bullshit because browsers don't send anything when an input is unchecked
        expected_inputs = {setting_name for setting_name, setting_info in SETTINGS_INFO.items() if not SETTINGS_INFO[setting_name].get('hidden', False)}
        missing_inputs = expected_inputs - set(request.values.keys())
        for setting_name in missing_inputs:
            assert SETTINGS_INFO[setting_name]['type'] is bool, missing_inputs
            current_settings_dict[setting_name] = False

        # find settings that have changed to prepare setting hook calls
        to_call = []
        for setting_name, value in current_settings_dict.items():
            old_value = globals()[setting_name]
            if value != old_value and setting_name in hooks:
                for func in hooks[setting_name]:
                    to_call.append((func, old_value, value))

        globals().update(current_settings_dict)
        save_settings(current_settings_dict)

        # call setting hooks
        for func, old_value, value in to_call:
            func(old_value, value)

        return flask.redirect(util.URL_ORIGIN + '/settings', 303)
    else:
        flask.abort(400)


# Set by server.py when it runs its own server. Under uWSGI, gunicorn and the
# like, restarting means something else and is up to them, so the buttons stay
# hidden.
server_controls_enabled = False

def _restart_server():
    sys.stdout.flush()
    sys.stderr.flush()
    # Replaces this process with a fresh copy of itself, keeping the same
    # PID, log file and nohup. The listening socket isn't inherited, so the
    # new copy can bind the port again.
    args = getattr(sys, 'orig_argv', [sys.executable] + sys.argv)[1:]
    os.execv(sys.executable, [sys.executable] + args)

def _stop_server():
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)

def server_control():
    if not server_controls_enabled:
        flask.abort(404)
    # server.py only lets POSTs through from localhost, but a web page open in
    # the browser can still submit a form here. Browsers send Origin with
    # every form POST, so refuse ones from other sites.
    origin = request.headers.get('Origin')
    if origin and urllib.parse.urlsplit(origin).netloc != request.host:
        flask.abort(403)

    import gevent
    action = request.form.get('action')
    if action == 'Restart':
        print('Restarting from the settings page')
        # Waits so this page reaches the browser before the process goes away
        gevent.spawn_later(0.5, _restart_server)
        return flask.render_template('server_control.html', restarting=True)
    elif action == 'Stop':
        print('Stopping from the settings page')
        gevent.spawn_later(0.5, _stop_server)
        return flask.render_template('server_control.html', restarting=False)
    flask.abort(400)
