import youtube
from youtube import yt_app
from youtube import util, comments, local_playlist, yt_data_extract
from youtube import watch_progress, subscriptions, library, signed_in
from youtube.chapters import parse_chapters
import settings

from flask import request
import flask

import json
import html
import gevent
import os
import math
import traceback
import urllib
import re
import time
import datetime
import urllib3.exceptions
import copy
import cachetools
from urllib.parse import parse_qs, urlencode
from types import SimpleNamespace
from math import ceil


try:
    with open(os.path.join(settings.data_dir, 'decrypt_function_cache.json'), 'r') as f:
        decrypt_cache = json.loads(f.read())['decrypt_cache']
except FileNotFoundError:
    decrypt_cache = {}


def parse_publish_time(time_published):
    '''YouTube gives the publish time in its own timezone, e.g.
    2026-11-14T09:00:05-08:00, or sometimes only the date (2026-11-14).
    Returns (unix time, precision) for time_display.date_time, or
    (None, None) if it can't be read.'''
    if not isinstance(time_published, str):
        return None, None
    try:
        published = datetime.datetime.fromisoformat(time_published)
    except ValueError:
        published = None
    if published is not None and published.tzinfo is not None:
        return int(published.timestamp()), 'exact'
    match = re.fullmatch(r'(\d{4})-(\d{1,2})-(\d{1,2})', time_published)
    if match:
        year, month, day = (int(part) for part in match.groups())
        # Noon UTC falls on the same day in nearly every timezone
        noon = datetime.datetime(year, month, day, 12,
                                 tzinfo=datetime.timezone.utc)
        return int(noon.timestamp()), 'date'
    return None, None


# How long loading the watch page waits for the dislike count
DISLIKES_WAIT_SECONDS = 2


def fetch_dislike_count(video_id):
    '''YouTube stopped giving out the dislike count in 2021. Return YouTube
    Dislike estimates it from the users of its browser extension.
    Returns None if it can't be fetched.'''
    try:
        content = util.fetch_url(
            'https://returnyoutubedislikeapi.com/votes?'
            + urlencode({'videoId': video_id}),
            # it refuses the default Python user agent
            headers=util.desktop_ua,
            timeout=5, debug_name='dislikes',
            report_text='Fetched dislike count')
        return int(json.loads(content)['dislikes'])
    except Exception as e:
        print('Could not fetch the dislike count:', repr(e))
        return None


def codec_name(vcodec):
    if vcodec.startswith('avc'):
        return 'h264'
    elif vcodec.startswith('av01'):
        return 'av1'
    elif vcodec.startswith('vp'):
        return 'vp'
    else:
        return 'unknown'


def get_video_sources(info, target_resolution):
    '''return dict with organized sources: {
        'uni_sources': [{}, ...],   # video and audio in one file
        'uni_idx': int,     # default unified source index
        'pair_sources': [{video: {}, audio: {}, quality: ..., ...}, ...],
        'pair_idx': int,    # default pair source index
    }
    '''
    audio_sources = []
    video_only_sources = {}
    uni_sources = []
    pair_sources = []


    for fmt in info['formats']:
        if not all(fmt[attr] for attr in ('ext', 'url', 'itag')):
            continue

        # unified source
        if fmt['acodec'] and fmt['vcodec']:
            source = {
                'type': 'video/' + fmt['ext'],
                'quality_string': short_video_quality_string(fmt),
            }
            source['quality_string'] += ' (integrated)'
            source.update(fmt)
            uni_sources.append(source)
            continue

        if not (fmt['init_range'] and fmt['index_range']):
            continue

        # audio source
        if fmt['acodec'] and not fmt['vcodec'] and (
                fmt['audio_bitrate'] or fmt['bitrate']):
            if fmt['bitrate']:  # prefer this one, more accurate right now
                fmt['audio_bitrate'] = int(fmt['bitrate']/1000)
            source = {
                'type': 'audio/' + fmt['ext'],
                'quality_string': audio_quality_string(fmt),
            }
            source.update(fmt)
            source['mime_codec'] = (source['type'] + '; codecs="'
                                    + source['acodec'] + '"')
            audio_sources.append(source)
        # video-only source
        elif all(fmt[attr] for attr in ('vcodec', 'quality', 'width', 'fps',
                                        'file_size')):
            if codec_name(fmt['vcodec']) == 'unknown':
                continue
            source = {
                'type': 'video/' + fmt['ext'],
                'quality_string': short_video_quality_string(fmt),
            }
            source.update(fmt)
            source['mime_codec'] = (source['type'] + '; codecs="'
                                    + source['vcodec'] + '"')
            quality = str(fmt['quality']) + 'p' + str(fmt['fps'])
            if quality in video_only_sources:
                video_only_sources[quality].append(source)
            else:
                video_only_sources[quality] = [source]

    audio_sources.sort(key=lambda source: source['audio_bitrate'])
    uni_sources.sort(key=lambda src: src['quality'])

    webm_audios = [a for a in audio_sources if a['ext'] == 'webm']
    mp4_audios = [a for a in audio_sources if a['ext'] == 'mp4']

    for quality_string, sources in video_only_sources.items():
        # choose an audio source to go with it
        # 0.5 is semiarbitrary empirical constant to spread audio sources
        # between 144p and 1080p. Use something better eventually.
        quality, fps = map(int, quality_string.split('p'))
        target_audio_bitrate = quality*fps/30*0.5
        pair_info = {
            'quality_string': quality_string,
            'quality': quality,
            'height': sources[0]['height'],
            'width': sources[0]['width'],
            'fps': fps,
            'videos': sources,
            'audios': [],
        }
        for audio_choices in (webm_audios, mp4_audios):
            if not audio_choices:
                continue
            closest_audio_source = audio_choices[0]
            best_err = target_audio_bitrate - audio_choices[0]['audio_bitrate']
            best_err = abs(best_err)
            for audio_source in audio_choices[1:]:
                err = abs(audio_source['audio_bitrate'] - target_audio_bitrate)
                # once err gets worse we have passed the closest one
                if err > best_err:
                    break
                best_err = err
                closest_audio_source = audio_source
            pair_info['audios'].append(closest_audio_source)

        if not pair_info['audios']:
            continue

        def video_rank(src):
            ''' Sort by settings preference. Use file size as tiebreaker '''
            setting_name = 'codec_rank_' + codec_name(src['vcodec'])
            return (settings.current_settings_dict[setting_name],
                    src['file_size'])
        pair_info['videos'].sort(key=video_rank)

        pair_sources.append(pair_info)

    pair_sources.sort(key=lambda src: src['quality'])

    uni_idx = 0 if uni_sources else None
    for i, source in enumerate(uni_sources):
        if source['quality'] > target_resolution:
            break
        uni_idx = i

    pair_idx = 0 if pair_sources else None
    for i, pair_info in enumerate(pair_sources):
        if pair_info['quality'] > target_resolution:
            break
        pair_idx = i

    return {
        'uni_sources': uni_sources,
        'uni_idx': uni_idx,
        'pair_sources': pair_sources,
        'pair_idx': pair_idx,
    }



def make_caption_src(info, lang, auto=False, trans_lang=None):
    label = lang
    if auto:
        label += ' (Automatic)'
    if trans_lang:
        label += ' -> ' + trans_lang
    return {
        'url': util.prefix_url(yt_data_extract.get_caption_url(info, lang, 'vtt', auto, trans_lang)),
        'label': label,
        'srclang': trans_lang[0:2] if trans_lang else lang[0:2],
        'on': False,
    }

def lang_in(lang, sequence):
    '''Tests if the language is in sequence, with e.g. en and en-US considered the same'''
    if lang is None:
        return False
    lang = lang[0:2]
    return lang in (l[0:2] for l in sequence)

def lang_eq(lang1, lang2):
    '''Tests if two iso 639-1 codes are equal, with en and en-US considered the same.
       Just because the codes are equal does not mean the dialects are mutually intelligible, but this will have to do for now without a complex language model'''
    if lang1 is None or lang2 is None:
        return False
    return lang1[0:2] == lang2[0:2]

def equiv_lang_in(lang, sequence):
    '''Extracts a language in sequence which is equivalent to lang.
    e.g. if lang is en, extracts en-GB from sequence.
    Necessary because if only a specific variant like en-GB is available, can't ask Youtube for simply en. Need to get the available variant.'''
    lang = lang[0:2]
    for l in sequence:
        if l[0:2] == lang:
            return l
    return None

def get_subtitle_sources(info):
    '''Returns these sources, ordered from least to most intelligible:
    native_video_lang (Automatic)
    foreign_langs (Manual)
    native_video_lang (Automatic) -> pref_lang
    foreign_langs (Manual) -> pref_lang
    native_video_lang (Manual) -> pref_lang
    pref_lang (Automatic)
    pref_lang (Manual)'''
    sources = []
    if not yt_data_extract.captions_available(info):
        return []
    pref_lang = settings.subtitles_language
    native_video_lang = None
    if info['automatic_caption_languages']:
        native_video_lang = info['automatic_caption_languages'][0]

    highest_fidelity_is_manual = False

    # Sources are added in very specific order outlined above
    # More intelligible sources are put further down to avoid browser bug when there are too many languages
    # (in firefox, it is impossible to select a language near the top of the list because it is cut off)

    # native_video_lang (Automatic)
    if native_video_lang and not lang_eq(native_video_lang, pref_lang):
        sources.append(make_caption_src(info, native_video_lang, auto=True))

    # foreign_langs (Manual)
    for lang in info['manual_caption_languages']:
        if not lang_eq(lang, pref_lang):
            sources.append(make_caption_src(info, lang))

    if (lang_in(pref_lang, info['translation_languages'])
            and not lang_in(pref_lang, info['automatic_caption_languages'])
            and not lang_in(pref_lang, info['manual_caption_languages'])):
        # native_video_lang (Automatic) -> pref_lang
        if native_video_lang and not lang_eq(pref_lang, native_video_lang):
            sources.append(make_caption_src(info, native_video_lang, auto=True, trans_lang=pref_lang))

        # foreign_langs (Manual) -> pref_lang
        for lang in info['manual_caption_languages']:
            if not lang_eq(lang, native_video_lang) and not lang_eq(lang, pref_lang):
                sources.append(make_caption_src(info, lang, trans_lang=pref_lang))

        # native_video_lang (Manual) -> pref_lang
        if lang_in(native_video_lang, info['manual_caption_languages']):
            sources.append(make_caption_src(info, native_video_lang, trans_lang=pref_lang))

    # pref_lang (Automatic)
    if lang_in(pref_lang, info['automatic_caption_languages']):
        sources.append(make_caption_src(info, equiv_lang_in(pref_lang, info['automatic_caption_languages']), auto=True))

    # pref_lang (Manual)
    if lang_in(pref_lang, info['manual_caption_languages']):
        sources.append(make_caption_src(info, equiv_lang_in(pref_lang, info['manual_caption_languages'])))
        highest_fidelity_is_manual = True

    if sources and sources[-1]['srclang'] == pref_lang:
        # set as on by default since it's manual a default-on subtitles mode is in settings
        if highest_fidelity_is_manual and settings.subtitles_mode > 0:
            sources[-1]['on'] = True
        # set as on by default since settings indicate to set it as such even if it's not manual
        elif settings.subtitles_mode == 2:
            sources[-1]['on'] = True

    if len(sources) == 0:
        assert len(info['automatic_caption_languages']) == 0 and len(info['manual_caption_languages']) == 0

    return sources


def get_ordered_music_list_attributes(music_list):
    # get the set of attributes which are used by atleast 1 track
    # so there isn't an empty, extraneous album column which no tracks use, for example
    used_attributes = set()
    for track in music_list:
        used_attributes = used_attributes | track.keys()

    # now put them in the right order
    ordered_attributes = []
    for attribute in ('Artist', 'Title', 'Album'):
        if attribute.lower() in used_attributes:
            ordered_attributes.append(attribute)

    return ordered_attributes

def save_decrypt_cache():
    try:
        f = open(os.path.join(settings.data_dir, 'decrypt_function_cache.json'), 'w')
    except FileNotFoundError:
        os.makedirs(settings.data_dir)
        f = open(os.path.join(settings.data_dir, 'decrypt_function_cache.json'), 'w')

    f.write(json.dumps({'version': 1, 'decrypt_cache':decrypt_cache}, indent=4, sort_keys=True))
    f.close()

def decrypt_signatures(info, video_id):
    '''return error string, or False if no errors'''
    if not yt_data_extract.requires_decryption(info):
        return False
    if not info['player_name']:
        return 'Could not find player name'

    player_name = info['player_name']
    if player_name in decrypt_cache:
        print('Using cached decryption function for: ' + player_name)
        info['decryption_function'] = decrypt_cache[player_name]
    else:
        base_js = util.fetch_url(info['base_js'], debug_name='base.js', report_text='Fetched player ' + player_name)
        base_js = base_js.decode('utf-8')
        err = yt_data_extract.extract_decryption_function(info, base_js)
        if err:
            return err
        decrypt_cache[player_name] = info['decryption_function']
        save_decrypt_cache()
    err = yt_data_extract.decrypt_signatures(info)
    return err


def _add_to_error(info, key, additional_message):
    if key in info and info[key]:
        info[key] += additional_message
    else:
        info[key] = additional_message

def fetch_player_response(client, video_id):
    return util.call_youtube_api(client, 'player', {
        'videoId': video_id,
    })

def fetch_watch_page_info(video_id, playlist_id, index):
    # bpctr=9999999999 will bypass are-you-sure dialogs for controversial
    # videos
    url = 'https://m.youtube.com/embed/' + video_id + '?bpctr=9999999999'
    if playlist_id:
        url += '&list=' + playlist_id
    if index:
        url += '&index=' + index

    headers = (
        ('Accept', '*/*'),
        ('Accept-Language', 'en-US,en;q=0.5'),
        ('X-YouTube-Client-Name', '2'),
        ('X-YouTube-Client-Version', '2.20180830'),
    ) + util.mobile_ua

    watch_page = util.fetch_url(url, headers=headers,
                                debug_name='watch')
    watch_page = watch_page.decode('utf-8')
    return yt_data_extract.extract_watch_info_from_html(watch_page)

def extract_info(video_id, use_invidious, playlist_id=None, index=None):
    tasks = (
        # Get video metadata from here
        gevent.spawn(fetch_watch_page_info, video_id, playlist_id, index),


        gevent.spawn(fetch_player_response, 'visionos', video_id)
    )
    gevent.joinall(tasks)
    util.check_gevent_exceptions(*tasks)
    info, player_response = tasks[0].value, tasks[1].value

    yt_data_extract.update_with_new_urls(info, player_response)

    # Age restricted video, retry. Not when YouTube wants to confirm this
    # isn't a bot: it refuses every client then, so asking again is just
    # another request.
    bot_checked = bool(info['playability_error']
                       and 'not a bot' in info['playability_error'])
    if (info['age_restricted'] or info['player_urls_missing']) and not bot_checked:
        if info['age_restricted']:
            print('Age restricted video, retrying')
        else:
            print('Player urls missing, retrying')
        # tv_embedded used to get around age restrictions, but YouTube
        # turned it off ("no longer supported in this application")
        player_response = fetch_player_response('android_vr', video_id)
        yt_data_extract.update_with_new_urls(info, player_response)

    # Refused because of the bot check or age: ask again signed in, when
    # Settings has a cookies file
    sign_in_error = None
    still_refused = not info['formats'] and info['playability_error']
    if (still_refused and (bot_checked or info['age_restricted'])
            and signed_in.is_set_up()):
        print('Asking again signed in')
        try:
            signed_in.update_info(info, video_id)
        except (signed_in.SignInError, util.FetchError) as e:
            sign_in_error = str(e)
            print('Signed-in request failed:', sign_in_error)

    # YouTube flags the IP address, so every other video is refused too
    # until it lifts
    info['bot_check'] = bool(not info['formats'] and (
        bot_checked or (info['playability_error']
                        and 'not a bot' in info['playability_error'])))
    if info['bot_check']:
        info['playability_error'] = (
            'YouTube is asking to confirm this isn\'t a bot, so it won\'t '
            'give out this video right now. It usually stops after a while; '
            'Route Tor in Settings, with Tor running, sometimes gets around '
            'it.')
        if sign_in_error:
            info['playability_error'] += (' Asking again signed in didn\'t '
                                          'work either: ' + sign_in_error)
        elif not signed_in.is_set_up():
            info['playability_error'] += (' Turning on "Sign in to YouTube" '
                                          'in Settings lets it ask again '
                                          'signed in.')
    elif sign_in_error:
        info['playability_error'] = ((info['playability_error'] or '')
            + ' Asking again signed in didn\'t work: ' + sign_in_error)

    # signature decryption
    decryption_error = decrypt_signatures(info, video_id)
    if decryption_error:
        decryption_error = 'Error decrypting url signatures: ' + decryption_error
        info['playability_error'] = decryption_error

    # check if urls ready (non-live format) in former livestream
    # urls not ready if all of them have no filesize
    if info['was_live']:
        info['urls_ready'] = False
        for fmt in info['formats']:
            if fmt['file_size'] is not None:
                info['urls_ready'] = True
    else:
        info['urls_ready'] = True

    # livestream urls
    # sometimes only the livestream urls work soon after the livestream is over
    if (info['hls_manifest_url']
        and (info['live'] or not info['formats'] or not info['urls_ready'])
    ):
        manifest = util.fetch_url(info['hls_manifest_url'],
            debug_name='hls_manifest.m3u8',
            report_text='Fetched hls manifest'
        ).decode('utf-8')

        info['hls_formats'], err = yt_data_extract.extract_hls_formats(manifest)
        if not err:
            info['playability_error'] = None
        for fmt in info['hls_formats']:
            fmt['video_quality'] = video_quality_string(fmt)
    else:
        info['hls_formats'] = []

    # check for 403. Unnecessary for tor video routing b/c ip address is same
    info['invidious_used'] = False
    info['invidious_reload_button'] = False
    info['tor_bypass_used'] = False
    if (settings.route_tor == 1
            and info['formats'] and info['formats'][0]['url']):
        try:
            response = util.head(info['formats'][0]['url'],
                report_text='Checked for URL access')
        except urllib3.exceptions.HTTPError:
            print('Error while checking for URL access:\n')
            traceback.print_exc()
            return info

        if response.status == 403:
            print('Access denied (403) for video urls.')
            print('Routing video through Tor')
            info['tor_bypass_used'] = True
            for fmt in info['formats']:
                fmt['url'] += '&use_tor=1'
        elif 300 <= response.status < 400:
            print('Error: exceeded max redirects while checking video URL')
    return info

# What YouTube said about each video opened in the last few minutes, so
# reloading the page, going back to it, or its Download menu doesn't ask
# YouTube again. The stream urls in it stay valid for hours.
INFO_CACHE_SECONDS = 10*60
info_cache = cachetools.TTLCache(maxsize=32, ttl=INFO_CACHE_SECONDS)

def get_info(video_id, use_invidious, playlist_id=None, index=None):
    '''extract_info, from the cache when possible. Returns a copy the caller
    can change.'''
    key = (video_id, use_invidious, playlist_id, index)
    info = info_cache.get(key)
    if info is None:
        info = extract_info(video_id, use_invidious,
                            playlist_id=playlist_id, index=index)
        # Errors aren't kept, so trying again asks again. Neither are
        # livestreams and premieres, which change from minute to minute.
        if (not info['error'] and not info['playability_error']
                and info['formats'] and info['urls_ready']
                and not info['live'] and not info['hls_formats']):
            info_cache[key] = copy.deepcopy(info)
        return info
    return copy.deepcopy(info)


def video_quality_string(format):
    if format['vcodec']:
        result =str(format['width'] or '?') + 'x' + str(format['height'] or '?')
        if format['fps']:
            result += ' ' + str(format['fps']) + 'fps'
        return result
    elif format['acodec']:
        return 'audio only'

    return '?'


def short_video_quality_string(fmt):
    result = str(fmt['quality'] or '?') + 'p'
    if fmt['fps']:
        result += str(fmt['fps'])
    if fmt['vcodec'].startswith('av01'):
        result += ' AV1'
    elif fmt['vcodec'].startswith('avc'):
        result += ' h264'
    else:
        result += ' ' + fmt['vcodec']
    return result


def audio_quality_string(fmt):
    if fmt['acodec']:
        if fmt['audio_bitrate']:
            result = '%d' % fmt['audio_bitrate'] + 'k'
        else:
            result = '?k'
        if fmt['audio_sample_rate']:
            result += ' ' + '%.3G' % (fmt['audio_sample_rate']/1000) + 'kHz'
        return result
    elif fmt['vcodec']:
        return 'video only'
    return '?'


# from https://github.com/ytdl-org/youtube-dl/blob/master/youtube_dl/utils.py
def format_bytes(bytes):
    if bytes is None:
        return 'N/A'
    if type(bytes) is str:
        bytes = float(bytes)
    if bytes == 0.0:
        exponent = 0
    else:
        exponent = int(math.log(bytes, 1024.0))
    suffix = ['B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB', 'EiB', 'ZiB', 'YiB'][exponent]
    converted = float(bytes) / float(1024 ** exponent)
    return '%.2f%s' % (converted, suffix)

@yt_app.route('/ytl-api/storyboard.vtt')
def get_storyboard_vtt():
    """
    See:
        https://github.com/iv-org/invidious/blob/9a8b81fcbe49ff8d88f197b7f731d6bf79fc8087/src/invidious.cr#L3603
        https://github.com/iv-org/invidious/blob/3bb7fbb2f119790ee6675076b31cd990f75f64bb/src/invidious/videos.cr#L623
    """

    spec_url = request.args.get('spec_url')
    url, *boards = spec_url.split('|')
    base_url, q = url.split('?')
    q = parse_qs(q)  # for url query

    storyboard = None
    wanted_height = 90

    for i, board in enumerate(boards):
        *t, _, sigh = board.split("#")
        width, height, count, width_cnt, height_cnt, interval = map(int, t)
        if height != wanted_height: continue
        q['sigh'] = [sigh]
        url = f"{base_url}?{urlencode(q, doseq=True)}"
        storyboard = SimpleNamespace(
            url               = url.replace("$L", str(i)).replace("$N", "M$M"),
            width             = width,
            height            = height,
            interval          = interval,
            width_cnt         = width_cnt,
            height_cnt        = height_cnt,
            storyboard_count  = ceil(count / (width_cnt * height_cnt))
        )

    if not storyboard:
        flask.abort(404)

    def to_ts(ms):
        s, ms = divmod(ms, 1000)
        h, s = divmod(s, 3600)
        m, s = divmod(s, 60)
        return f"{h:02}:{m:02}:{s:02}.{ms:03}"

    r = "WEBVTT"  # result
    ts = 0  # current timestamp

    for i in range(storyboard.storyboard_count):
        url = '/' + storyboard.url.replace("$M", str(i))
        interval = storyboard.interval
        w, h = storyboard.width, storyboard.height
        w_cnt, h_cnt = storyboard.width_cnt, storyboard.height_cnt

        for j in range(h_cnt):
            for k in range(w_cnt):
                r += f"{to_ts(ts)} --> {to_ts(ts+interval)}\n"
                r += f"{url}#xywh={w * k},{h * j},{w},{h}\n\n"
                ts += interval

    return flask.Response(r, mimetype='text/vtt')


def prepare_download_formats(info):
    '''Points the format urls through this server if set to, and names the
    files after the video. Returns the list for the Download section.'''
    if settings.img_prefix:
        # Don't prefix hls_formats for now because the urls inside the manifest
        # would need to be prefixed as well.
        for fmt in info['formats']:
            fmt['url'] = util.prefix_url(fmt['url'])

    # Add video title to end of url path so it has a filename other than just
    # "videoplayback" when downloaded
    title = urllib.parse.quote(util.to_valid_filename(info['title'] or ''))
    for fmt in info['formats']:
        filename = title
        ext = fmt.get('ext')
        if ext:
            filename += '.' + ext
        fmt['url'] = fmt['url'].replace(
            '/videoplayback',
            '/videoplayback/name/' + filename)

    download_formats = []
    for format in (info['formats'] + info['hls_formats']):
        if format['acodec'] and format['vcodec']:
            codecs_string = format['acodec'] + ', ' + format['vcodec']
        else:
            codecs_string = format['acodec'] or format['vcodec'] or '?'
        download_formats.append({
            'url': format['url'],
            'ext': format['ext'] or '?',
            'audio_quality': audio_quality_string(format),
            'video_quality': video_quality_string(format),
            'file_size': format_bytes(format['file_size']),
            'codecs': codecs_string,
            'has_audio': bool(format['acodec']),
            'has_video': bool(format['vcodec']),
            'height': format.get('height') or format.get('quality'),
        })
    return download_formats


def transcript_downloads(subtitle_sources):
    downloads = []
    for source in subtitle_sources:
        best_caption_parse = urllib.parse.urlparse(
            source['url'].lstrip('/'))
        transcript_url = (util.URL_ORIGIN
            + '/watch/transcript'
            + best_caption_parse.path
            + '?' + best_caption_parse.query)
        downloads.append({
            'label': 'Video Transcript: ' + source['label'],
            'ext': 'txt',
            'url': transcript_url
        })
    return downloads


@yt_app.route('/ytl-api/download_formats')
def get_download_formats():
    '''For Download in the menu on each video'''
    video_id = request.args.get('v', '')
    if not re.fullmatch(r'[\w-]{11}', video_id):
        return flask.jsonify({'ok': False, 'error': 'Invalid video id'}), 400
    try:
        info = get_info(video_id, use_invidious=True)
    except util.FetchError as e:
        return flask.jsonify({'ok': False, 'error': str(e)}), 502
    if info['error']:
        return flask.jsonify({'ok': False, 'error': info['error']}), 502
    formats = prepare_download_formats(info)
    if not formats and info['playability_error']:
        return flask.jsonify({'ok': False,
                              'error': info['playability_error']}), 502
    return flask.jsonify({
        'ok': True,
        'title': info['title'],
        'formats': formats,
        'others': transcript_downloads(get_subtitle_sources(info)),
    })


time_table = {'h': 3600, 'm': 60, 's': 1}
@yt_app.route('/watch')
@yt_app.route('/embed')
@yt_app.route('/embed/<video_id>')
@yt_app.route('/shorts')
@yt_app.route('/shorts/<video_id>')
def get_watch_page(video_id=None):
    video_id = request.args.get('v') or video_id
    if not video_id:
        return flask.render_template('error.html', error_message='Missing video id'), 404
    if len(video_id) < 11:
        return flask.render_template('error.html', error_message='Incomplete video id (too short): ' + video_id), 404

    time_start_str = request.args.get('t', '0s')
    time_start = 0
    if re.fullmatch(r'(\d+(h|m|s))+', time_start_str):
        for match in re.finditer(r'(\d+)(h|m|s)', time_start_str):
            time_start += int(match.group(1))*time_table[match.group(2)]
    elif re.fullmatch(r'\d+', time_start_str):
        time_start = int(time_start_str)
    if 't' not in request.args:
        # pick up where the user left off
        time_start = watch_progress.resume_position(video_id)

    lc = request.args.get('lc', '')
    playlist_id = request.args.get('list')
    index = request.args.get('index')
    use_invidious = bool(int(request.args.get('use_invidious', '1')))
    is_embed = request.path.startswith('/embed') and settings.embed_page_mode
    dislikes_task = None
    if settings.use_return_youtube_dislike and not is_embed:
        dislikes_task = gevent.spawn(fetch_dislike_count, video_id)
    info = get_info(video_id, use_invidious,
                    playlist_id=playlist_id, index=index)

    if info['error']:
        return flask.render_template('error.html', error_message = info['error'])

    if dislikes_task is not None and info.get('dislike_count') is None:
        # It has had as long as the video's info took, so it's usually
        # there already. The page isn't held up for it beyond this.
        dislikes_task.join(timeout=DISLIKES_WAIT_SECONDS)
        info['dislike_count'] = dislikes_task.value
        dislikes_task.kill(block=False)

    video_info = {
        'duration':  (util.seconds_to_timestamp(info['duration'])
                      if info['duration'] else None),
        # info['id'] is None when YouTube wouldn't give the video's info
        'id':        info['id'] or video_id,
        'title':     info['title'],
        'author':    info['author'],
        'author_id': info['author_id'],
    }

    is_watch_page = not (request.path.startswith('/embed')
                         and settings.embed_page_mode)
    # It's playing now, so it doesn't need to wait in the queue anymore.
    # Unless YouTube refused it for the bot check: then it stays for later.
    if is_watch_page and not info['bot_check']:
        library.queue_remove(video_id)
    queue = library.get_queue() if is_watch_page else []

    # prefix urls, and other post-processing not handled by yt_data_extract
    for item in info['related_videos']:
        util.prefix_urls(item)
        util.add_extra_html_info(item)
    for song in info['music_list']:
        song['url'] = util.prefix_url(song['url'])
    if info['playlist']:
        playlist_id = info['playlist']['id']
        for item in info['playlist']['items']:
            util.prefix_urls(item)
            util.add_extra_html_info(item)
            if playlist_id:
                item['url'] += '&list=' + playlist_id
            if item['index']:
                item['url'] += '&index=' + str(item['index'])
        info['playlist']['author_url'] = util.prefix_url(
            info['playlist']['author_url'])
    local_list = request.args.get('local_list')
    if local_list:
        info['playlist'] = local_playlist.watch_page_playlist(
            local_list, video_id, index, request.args.get('shuffle'))
    download_formats = prepare_download_formats(info)

    if (settings.route_tor == 2) or info['tor_bypass_used']:
        target_resolution = 240
    else:
        target_resolution = settings.default_resolution

    source_info = get_video_sources(info, target_resolution)
    uni_sources = source_info['uni_sources']
    pair_sources = source_info['pair_sources']
    uni_idx, pair_idx = source_info['uni_idx'], source_info['pair_idx']

    pair_quality = yt_data_extract.deep_get(pair_sources, pair_idx, 'quality')
    uni_quality = yt_data_extract.deep_get(uni_sources, uni_idx, 'quality')

    pair_error = abs((pair_quality or 360) - target_resolution)
    uni_error = abs((uni_quality or 360) - target_resolution)
    if uni_error == pair_error:
        # use settings.prefer_uni_sources as a tiebreaker
        closer_to_target = 'uni' if settings.prefer_uni_sources else 'pair'
    elif uni_error < pair_error:
        closer_to_target = 'uni'
    else:
        closer_to_target = 'pair'

    if settings.prefer_uni_sources == 2:
        # Use uni sources unless there's no choice.
        using_pair_sources = (
            bool(pair_sources) and (not uni_sources)
        )
    else:
        # Use the pair sources if they're closer to the desired resolution
        using_pair_sources = (
            bool(pair_sources)
            and (not uni_sources or closer_to_target == 'pair')
        )
    if using_pair_sources:
        video_height = pair_sources[pair_idx]['height']
        video_width = pair_sources[pair_idx]['width']
    else:
        video_height = yt_data_extract.deep_get(
            uni_sources, uni_idx, 'height', default=360
        )
        video_width = yt_data_extract.deep_get(
            uni_sources, uni_idx, 'width', default=640
        )



    # 1 second per pixel, or the actual video width
    theater_video_target_width = max(640, info['duration'] or 0, video_width)

    # Comments are only fetched once the user scrolls down to them or opens
    # them (comments.js), so pages where nobody reads them don't ask YouTube.
    # Without JavaScript the link opens them on their own page.
    comment_sort = int(settings.default_comment_sorting)
    comments_url = (util.URL_ORIGIN + '/ytl-api/video_comments?'
                    + urlencode({'v': video_id, 'lc': lc}))
    comments_page_url = (util.URL_ORIGIN + '/comments?ctoken='
                         + comments.make_comment_ctoken(video_id,
                                                        sort=comment_sort,
                                                        lc=lc))

    # captions and transcript
    subtitle_sources = get_subtitle_sources(info)
    other_downloads = transcript_downloads(subtitle_sources)

    published_timestamp, published_precision = parse_publish_time(
        info['time_published'])
    subscriptions.save_watch_page_time(video_id, published_timestamp,
                                       published_precision)

    chapters = parse_chapters(info['description'], info['duration'])

    if request.path.startswith('/embed') and settings.embed_page_mode:
        template_name = 'embed.html'
    else:
        template_name = 'watch.html'
    return flask.render_template(template_name,
        header_playlist_names   = local_playlist.get_playlist_names(),
        queue_videos            = library.display_items(queue),
        uploader_channel_url    = ('/' + info['author_url']) if info['author_url'] else '',
        time_published             = info['time_published'],
        published_timestamp        = published_timestamp,
        published_precision        = published_precision,
        view_count    = info.get('view_count'),
        like_count    = info.get('like_count'),
        dislike_count = info.get('dislike_count'),
        download_formats        = download_formats,
        other_downloads         = other_downloads,
        video_info              = json.dumps(video_info),
        hls_formats             = info['hls_formats'],
        subtitle_sources        = subtitle_sources,
        related                 = info['related_videos'],
        playlist                = info['playlist'],
        music_list              = info['music_list'],
        music_attributes        = get_ordered_music_list_attributes(info['music_list']),
        comments_url            = comments_url,
        comments_page_url       = comments_page_url,
        # The count is "0" when the page had no comment section, which no
        # longer means there are none
        comment_count           = (None if info['comments_disabled']
                                   else info['comment_count']),

        video_height            = video_height,
        video_width             = video_width,
        theater_video_target_width = theater_video_target_width,

        title       = info['title'],
        uploader    = info['author'],
        description = info['description'],
        chapters    = chapters,
        unlisted    = info['unlisted'],
        limited_state = info['limited_state'],
        age_restricted    = info['age_restricted'],
        live              = info['live'],
        playability_error = info['playability_error'],

        allowed_countries = info['allowed_countries'],
        ip_address   = info['ip_address'] if settings.route_tor else None,
        invidious_used    = info['invidious_used'],
        invidious_reload_button = info['invidious_reload_button'],
        video_url = util.URL_ORIGIN + '/watch?v=' + video_id,
        video_id = video_id,
        storyboard_url = (util.URL_ORIGIN + '/ytl-api/storyboard.vtt?' +
            urlencode([('spec_url', info['storyboard_spec_url'])])
            if info['storyboard_spec_url'] else None),

        js_data = {
            'video_id': info['id'],
            'video_duration': info['duration'],
            'chapters': chapters,
            'settings': settings.current_settings_dict,
            'has_manual_captions': any(s.get('on') for s in subtitle_sources),
            **source_info,
            'using_pair_sources': using_pair_sources,
            'time_start': time_start,
            'playlist': info['playlist'],
            'related': info['related_videos'],
            'playability_error': info['playability_error'],
            'bot_check': info['bot_check'],
            'queue_length': len(queue),
            'video_info': video_info,
        },
        font_family = youtube.font_choices[settings.font], # for embed page
        **source_info,
        using_pair_sources = using_pair_sources,
    )


@yt_app.route('/api/<path:dummy>')
def get_captions(dummy):
    result = util.fetch_url('https://www.youtube.com' + request.full_path)
    result = result.replace(b"align:start position:0%", b"")
    return result


times_reg = re.compile(r'^\d\d:\d\d:\d\d\.\d\d\d --> \d\d:\d\d:\d\d\.\d\d\d.*$')
inner_timestamp_removal_reg = re.compile(r'<[^>]+>')
@yt_app.route('/watch/transcript/<path:caption_path>')
def get_transcript(caption_path):
    try:
        captions = util.fetch_url('https://www.youtube.com/'
            + caption_path
            + '?' + request.environ['QUERY_STRING']).decode('utf-8')
    except util.FetchError as e:
        msg = ('Error retrieving captions: ' + str(e) + '\n\n'
            + 'The caption url may have expired.')
        print(msg)
        return flask.Response(msg,
            status = e.code,
            mimetype='text/plain;charset=UTF-8')

    lines = captions.splitlines()
    segments = []

    # skip captions file header
    i = 0
    while lines[i] != '':
        i += 1

    current_segment = None
    while i < len(lines):
        line = lines[i]
        if line == '':
            if ((current_segment is not None)
                    and (current_segment['begin'] is not None)):
                segments.append(current_segment)
            current_segment = {
                'begin': None,
                'end': None,
                'lines': [],
            }
        elif times_reg.fullmatch(line.rstrip()):
            current_segment['begin'], current_segment['end'] = line.split(' --> ')
        else:
            current_segment['lines'].append(
                inner_timestamp_removal_reg.sub('', line))
        i += 1

    # if automatic captions, but not translated
    if request.args.get('kind') == 'asr' and not request.args.get('tlang'):
        # Automatic captions repeat content. The new segment is displayed
        # on the bottom row; the old one is displayed on the top row.
        # So grab the bottom row only
        for seg in segments:
            seg['text'] = seg['lines'][1]
    else:
        for seg in segments:
            seg['text'] = ' '.join(map(str.rstrip, seg['lines']))

    result = ''
    for seg in segments:
        if seg['text'] != ' ':
            result += seg['begin'] + ' ' + seg['text'] + '\r\n'

    return flask.Response(result.encode('utf-8'),
        mimetype='text/plain;charset=UTF-8')



