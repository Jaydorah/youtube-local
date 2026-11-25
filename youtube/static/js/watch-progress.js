// Runs on every page:
// - shows how far you got on each video thumbnail
// - on the watch page, saves how far you got
// - keeps "3 days ago" up to date (the server writes the dates themselves,
//   in the timezone from the settings)
(function() {
'use strict';

var PROGRESS_URL = '/youtube.com/watch_progress';
var SAVE_INTERVAL_MS = 10000;
var RELATIVE_TIME_REFRESH_MS = 30000;

// Same as RELATIVE_TIME_UNITS in time_display.py
var RELATIVE_TIME_UNITS = [
    ['year', 365*24*3600],
    ['month', 30*24*3600],
    ['week', 7*24*3600],
    ['day', 24*3600],
    ['hour', 3600],
    ['minute', 60],
    ['second', 1],
];

// Same rules as relative_time in time_display.py
function relativeTime(unixTime, precision) {
    var seconds;
    if (precision === 'date') {
        // Count calendar days, so something from yesterday is "1 day ago"
        // this morning, not "today". The date is YouTube's own, kept at
        // noon UTC, so compare it with today's date in the timezone from
        // the settings (data-utc-offset on <html>, in seconds).
        var offset = Number(document.documentElement.dataset.utcOffset) || 0;
        var day = 24*3600;
        var today = Math.floor((Date.now()/1000 + offset)/day);
        var publishedDay = Math.floor(unixTime/day);
        seconds = (today - publishedDay)*day;
    } else {
        seconds = Math.trunc(Date.now()/1000 - unixTime);
    }
    var inFuture = seconds < 0;
    seconds = Math.abs(seconds);
    var smallestUnit = precision === 'date' ? 24*3600 : 1;
    for (var i = 0; i < RELATIVE_TIME_UNITS.length; i++) {
        var unitName = RELATIVE_TIME_UNITS[i][0];
        var unitSeconds = RELATIVE_TIME_UNITS[i][1];
        if (seconds >= unitSeconds && unitSeconds >= smallestUnit) {
            var amount = Math.floor(seconds/unitSeconds);
            var text = amount + ' ' + unitName + (amount !== 1 ? 's' : '');
            return inFuture ? 'in ' + text : text + ' ago';
        }
    }
    return precision === 'date' ? 'today' : 'just now';
}

function updateRelativeTimes() {
    document.querySelectorAll('[data-relative-time]').forEach(function(el) {
        el.textContent = relativeTime(Number(el.dataset.relativeTime),
                                      el.dataset.precision);
    });
}

function showProgress() {
    var boxes = document.querySelectorAll('.thumbnail-box[data-video-id]');
    if (!boxes.length)
        return;
    var ids = {};
    boxes.forEach(function(box) { ids[box.dataset.videoId] = true; });

    fetch(PROGRESS_URL + '?ids=' + Object.keys(ids).join(','),
          {cache: 'no-store'})
    .then(function(response) { return response.ok ? response.json() : {}; })
    .then(function(progress) {
        boxes.forEach(function(box) {
            box.querySelectorAll('.watch-progress, .watched-badge')
               .forEach(function(el) { el.remove(); });
            box.classList.remove('has-progress');

            var p = progress[box.dataset.videoId];
            if (!p)
                return;
            var fraction = p.watched ? 1 : Math.min(1, p.position/p.duration);

            var bar = document.createElement('div');
            bar.className = 'watch-progress';
            var fill = document.createElement('div');
            fill.className = 'watch-progress-fill';
            fill.style.width = (fraction*100).toFixed(1) + '%';
            bar.appendChild(fill);
            box.appendChild(bar);
            box.classList.add('has-progress');

            if (p.watched) {
                var badge = document.createElement('span');
                badge.className = 'watched-badge';
                badge.textContent = 'WATCHED';
                box.appendChild(badge);
            }
        });
    })
    .catch(function() {});
}

function saveProgressWhileWatching() {
    if (typeof data === 'undefined' || !data || !data.video_id)
        return;
    var video = document.querySelector('video');
    if (!video)
        return;

    // Don't save anything until it actually plays, so opening a video and
    // leaving doesn't overwrite where you were
    var hasPlayed = false;
    var lastSaved = 0;

    function save() {
        if (!hasPlayed)
            return;
        var duration = video.duration;
        if (!isFinite(duration) || duration <= 0)
            duration = data.video_duration;
        // livestreams have no length
        if (!duration || !isFinite(duration))
            return;
        var form = new FormData();
        form.append('video_id', data.video_id);
        form.append('position', String(video.currentTime));
        form.append('duration', String(duration));
        lastSaved = Date.now();
        // sendBeacon still gets sent when the page is closing
        if (!(navigator.sendBeacon && navigator.sendBeacon(PROGRESS_URL, form)))
            fetch(PROGRESS_URL, {method: 'POST', body: form, keepalive: true})
                .catch(function() {});
    }

    video.addEventListener('playing', function() { hasPlayed = true; });
    video.addEventListener('timeupdate', function() {
        if (hasPlayed && Date.now() - lastSaved > SAVE_INTERVAL_MS)
            save();
    });
    video.addEventListener('pause', save);
    video.addEventListener('ended', save);
    window.addEventListener('pagehide', save);
    document.addEventListener('visibilitychange', function() {
        if (document.visibilityState === 'hidden')
            save();
    });
}

updateRelativeTimes();
setInterval(updateRelativeTimes, RELATIVE_TIME_REFRESH_MS);
showProgress();
saveProgressWhileWatching();

// Going back to a page restores it from the cache without running scripts
// again, so refresh the bars to include what was just watched
window.addEventListener('pageshow', function(e) {
    if (e.persisted) {
        showProgress();
        updateRelativeTimes();
    }
});
})();
