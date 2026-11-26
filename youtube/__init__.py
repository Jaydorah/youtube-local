from youtube import util
from youtube import time_display
import flask
from flask import request
import jinja2
import settings
import traceback
import re
import time
from sys import exc_info
yt_app = flask.Flask(__name__)
yt_app.url_map.strict_slashes = False
# yt_app.jinja_env.trim_blocks = True
# yt_app.jinja_env.lstrip_blocks = True

# https://stackoverflow.com/questions/39858191/do-statement-not-working-in-jinja
yt_app.jinja_env.add_extension('jinja2.ext.do') # why

# A global so that macros imported without context can see it too
yt_app.jinja_env.globals['local_playlists_enabled'] = settings.local_playlists_enabled

yt_app.add_url_rule('/settings', 'settings_page', settings.settings_page, methods=['POST', 'GET'])
yt_app.add_url_rule('/settings/server', 'server_control', settings.server_control, methods=['POST'])

HOMEPAGE_PATHS = {
    1: '/youtube.com/subscriptions',
    2: '/youtube.com/playlists',
}

@yt_app.route('/')
def homepage():
    target = HOMEPAGE_PATHS.get(settings.homepage)
    if target:
        return flask.redirect(target, 302)
    return home_menu()

# The menu stays reachable here whatever the homepage setting is, because it
# holds the only link to the settings page
@yt_app.route('/home')
def home_menu():
    return flask.render_template('home.html', title="Youtube local")


theme_names = {
    0: 'light_theme',
    1: 'gray_theme',
    2: 'dark_theme',
}

ui_style_names = {
    0: 'classic',
    1: 'modern',
    2: 'youtube',
}

color_scheme_names = {
    0: 'system',
    1: 'light',
    2: 'dark',
}

# Loaded after each page's own styles so they can restyle them. The YouTube
# style builds on the Modern one.
ui_style_sheets = {
    'classic': [],
    'modern': ['/youtube.com/static/modern_ui.css'],
    'youtube': ['/youtube.com/static/modern_ui.css',
                '/youtube.com/static/youtube_ui.css'],
}

@yt_app.context_processor
def inject_theme_preference():
    from youtube import library
    ui_style = ui_style_names.get(settings.ui_style, 'modern')
    if ui_style == 'classic':
        # The Classic style has themes instead, so the attribute says how
        # dark the theme is, for the styles shared by all of them
        color_scheme = 'dark' if settings.theme == 2 else 'light'
    else:
        color_scheme = color_scheme_names.get(settings.color_scheme, 'system')
    return {
        # shown on the Playlists button in the header while it's on
        'playlist_mode': library.playlist_mode() if request else None,
        'theme_path': '/youtube.com/static/' + theme_names[settings.theme] + '.css',
        'ui_style': ui_style,
        'ui_style_sheets': ui_style_sheets[ui_style],
        'color_scheme': color_scheme,
        'settings': settings,
        'utc_offset_seconds': time_display.utc_offset_seconds(),
        'utc_offset_name': time_display.utc_offset_name(),
    }

@yt_app.template_filter('commatize')
def commatize(num):
    if num is None:
        return ''
    if isinstance(num, str):
        try:
            num = int(num)
        except ValueError:
            return num
    return '{:,}'.format(num)

yt_app.add_template_filter(util.format_count, 'count')

# The icon sprite almost never changes and library.js draws icons from it on
# every page, so the browser keeps it instead of asking again each time
def get_send_file_max_age(filename):
    if filename == 'icons.svg':
        return 24*3600
    return None
yt_app.get_send_file_max_age = get_send_file_max_age

@yt_app.template_filter('iso_utc')
def iso_utc(unix_time):
    '''For the datetime attribute of <time>'''
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(unix_time))

yt_app.add_template_filter(time_display.relative_time, 'relative_time')
yt_app.add_template_filter(time_display.date_time, 'date_time')

NEW_VIDEO_SECONDS = 12*3600
RELATIVE_TIME_RE = re.compile(r'(\d+) (second|minute|hour)s? ago')

@yt_app.template_global()
def is_new_video(info):
    '''Whether to put a NEW badge on a video: uploaded in the last 12 hours'''
    if info.get('type') != 'video':
        return False
    if info.get('timestamp') is not None:
        age = time.time() - info['timestamp']
        return 0 <= age < NEW_VIDEO_SECONDS
    # Other pages only have YouTube's "5 hours ago", which rounds down
    match = RELATIVE_TIME_RE.fullmatch(info.get('time_published') or '')
    if match is None:
        return False
    amount, unit = int(match.group(1)), match.group(2)
    return unit != 'hour' or amount < NEW_VIDEO_SECONDS//3600

def timestamp_replacement(match):
    time_seconds = 0
    for part in match.group(0).split(':'):
        time_seconds = 60*time_seconds + int(part)
    return (
        '<a href="#" onclick="document.querySelector(\'video\').currentTime='
        + str(time_seconds)
        + '">' + match.group(0)
        + '</a>'
    )

TIMESTAMP_RE = re.compile(r'\b(\d?\d:)?\d?\d:\d\d\b')
@yt_app.template_filter('timestamps')
def timestamps(text):
    return TIMESTAMP_RE.sub(timestamp_replacement, text)

@yt_app.errorhandler(500)
def error_page(e):
    slim = request.args.get('slim', False) # whether it was an ajax request
    if (exc_info()[0] == util.FetchError
        and exc_info()[1].code == '429'
        and settings.route_tor
    ):
        error_message = ('Error: Youtube blocked the request because the Tor'
            ' exit node is overutilized. Try getting a new exit node by'
            ' using the New Identity button in the Tor Browser.')
        if exc_info()[1].error_message:
            error_message += '\n\n' + exc_info()[1].error_message
        if exc_info()[1].ip:
            error_message += '\n\nExit node IP address: ' + exc_info()[1].ip
        return flask.render_template('error.html', error_message=error_message, slim=slim), 502
    elif exc_info()[0] == util.FetchError and exc_info()[1].error_message:
        return (flask.render_template(
                    'error.html',
                    error_message=exc_info()[1].error_message,
                    slim=slim
                ), 502)
    return flask.render_template('error.html', traceback=traceback.format_exc(), slim=slim), 500

font_choices = {
    0: 'initial',
    1: 'arial, "liberation sans", sans-serif',
    2: '"liberation serif", "times new roman", calibri, carlito, serif',
    3: 'verdana, sans-serif',
    4: 'tahoma, sans-serif',
}

@yt_app.route('/shared.css')
def get_css():
    return flask.Response(
        flask.render_template('shared.css',
            font_family = font_choices[settings.font]
        ),
        mimetype='text/css',
    )


# This is okay because the flask urlize function puts the href as the first
# property
YOUTUBE_LINK_RE = re.compile(r'<a href="(' + util.YOUTUBE_URL_RE_STR + ')"')
old_urlize = jinja2.filters.urlize
def prefix_urlize(*args, **kwargs):
    result = old_urlize(*args, **kwargs)
    return YOUTUBE_LINK_RE.sub(r'<a href="/\1"', result)
jinja2.filters.urlize = prefix_urlize

