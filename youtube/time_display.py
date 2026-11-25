'''Formatting of video upload times, such as "3 days ago".

Times are shown in UTC shifted by the utc_offset_hours and
utc_offset_minutes settings, not in the server's or the browser's timezone.
static/js/watch-progress.js keeps "3 days ago" current using the same
offset, which pages put in the data-utc-offset attribute of <html>.'''
import datetime
import time

import settings

# Same lengths as youtube_timestamp_to_posix in subscriptions.py uses for
# reading YouTube's "3 days ago", and as watch-progress.js uses
RELATIVE_TIME_UNITS = (
    ('year', 365*24*3600),
    ('month', 30*24*3600),
    ('week', 7*24*3600),
    ('day', 24*3600),
    ('hour', 3600),
    ('minute', 60),
    ('second', 1),
)


def utc_offset_seconds():
    '''The offset from the settings. The minutes go in the same direction
    as the hours, so -3 hours and 30 minutes is UTC-03:30.'''
    hours = settings.utc_offset_hours
    minutes = settings.utc_offset_minutes
    sign = -1 if hours < 0 else 1
    return hours*3600 + sign*minutes*60


def utc_offset_name():
    '''Like "UTC", "UTC+2" or "UTC-03:30"'''
    offset = utc_offset_seconds()
    if offset == 0:
        return 'UTC'
    sign = '-' if offset < 0 else '+'
    hours, seconds = divmod(abs(offset), 3600)
    if seconds:
        return 'UTC%s%02d:%02d' % (sign, hours, seconds//60)
    return 'UTC%s%d' % (sign, hours)


def _date(unix_time, offset):
    return datetime.datetime.fromtimestamp(
        unix_time + offset, datetime.timezone.utc).date()


def relative_time(unix_time, now=None, precision='exact'):
    '''"3 days ago", or "in 2 hours" for a time in the future (such as a
    scheduled livestream). Rounds down like YouTube does. With precision
    'date', the time of day isn't known, so it doesn't go below days.'''
    if now is None:
        now = time.time()
    if precision == 'date':
        # Count calendar days, so something from yesterday is "1 day ago"
        # this morning, not "today". The date is YouTube's own, kept at
        # noon UTC (see watch.parse_publish_time), so compare it with
        # today's date in the chosen timezone.
        days = (_date(now, utc_offset_seconds()) - _date(unix_time, 0)).days
        seconds = days*24*3600
    else:
        seconds = int(now - unix_time)
    in_future = seconds < 0
    seconds = abs(seconds)
    smallest_unit = 24*3600 if precision == 'date' else 1
    for unit_name, unit_seconds in RELATIVE_TIME_UNITS:
        if seconds >= unit_seconds and unit_seconds >= smallest_unit:
            amount = seconds//unit_seconds
            plural = 's' if amount != 1 else ''
            text = str(amount) + ' ' + unit_name + plural
            return ('in ' + text) if in_future else (text + ' ago')
    return 'today' if precision == 'date' else 'just now'


def date_time(unix_time, precision='exact'):
    '''precision is 'exact' (date and time, like "Nov 15, 2026, 3:04 PM"),
    'date' (only the day is known), or 'approximate' (estimated from
    YouTube's "3 weeks ago", so only "around <date>")'''
    if precision == 'date':
        # YouTube's own date, which shifting by the offset could move to
        # the day before or after
        shown = time.gmtime(unix_time)
    else:
        shown = time.gmtime(unix_time + utc_offset_seconds())
    date_text = '%s %d, %d' % (time.strftime('%b', shown), shown.tm_mday,
                               shown.tm_year)
    if precision == 'exact':
        hour = shown.tm_hour % 12 or 12
        am_pm = 'AM' if shown.tm_hour < 12 else 'PM'
        return '%s, %d:%02d %s' % (date_text, hour, shown.tm_min, am_pm)
    if precision == 'approximate':
        return 'around ' + date_text
    return date_text
