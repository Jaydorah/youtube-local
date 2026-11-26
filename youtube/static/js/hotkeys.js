// Ctrl+Right goes to the start of the next chapter. Ctrl+Left goes to the
// start of this chapter, or of the one before it when this one has only
// just started, so pressing it again and again keeps going back.
function skipChapter(v, direction) {
    let chapters = data.chapters;
    let i = 0;
    while (i + 1 < chapters.length && chapters[i + 1].start <= v.currentTime)
        i++;
    if (direction > 0) {
        if (i + 1 < chapters.length)
            v.currentTime = chapters[i + 1].start;
        return;
    }
    // 3 seconds of real time, however fast the video is playing
    let justStarted = v.currentTime - chapters[i].start < 3*v.playbackRate;
    if (justStarted && i > 0)
        i--;
    v.currentTime = chapters[i].start;
}

// Like YouTube's , and . keys: pauses and moves by one frame
function stepFrame(v, direction) {
    let fps = (typeof videoFps !== 'undefined' && videoFps) || 30;
    v.pause();
    v.currentTime = Math.max(0, v.currentTime + direction/fps);
}

// Like YouTube's 0-9 keys: 1 goes to 10% of the video, 2 to 20%, and so on
function seekToPercent(v, digit) {
    let duration = videoDurationNow(v);
    if (!duration) return;
    seekTo(v, duration * digit / 10);
}

function videoDurationNow(v) {
    return isFinite(v.duration) ? v.duration : data.video_duration;
}

// Like YouTube's up and down arrows: volume up or down by 5%
function changeVolume(v, delta) {
    // rounded so repeated presses land on 5, 10, 15...
    let volume = Math.round((v.volume + delta) * 20) / 20;
    v.volume = Math.min(1, Math.max(0, volume));
    if (delta > 0 && v.muted)
        v.muted = false;
    // shown here too, since at 0% or 100% the volume doesn't change and
    // no volumechange event comes
    showVolume(v);
}

// One box over the video, shown for a second when the volume or the time
// changes. Volume and time share it, so they never stack up.
let osd = null;
let osdTimer = null;

function showOsd(v, kind, markup) {
    if (!osd) {
        osd = document.createElement('div');
        osd.className = 'player-osd';
        osd.setAttribute('role', 'status');
    }
    // inside plyr's box so it stays visible in plyr's fullscreen
    let box = v.closest('.plyr') || v.parentElement;
    if (osd.parentElement !== box) {
        if (getComputedStyle(box).position === 'static')
            box.style.position = 'relative';
        box.appendChild(osd);
    }
    if (osd.dataset.kind !== kind) {
        osd.dataset.kind = kind;
        osd.innerHTML = markup;
    }
    osd.classList.add('shown');
    clearTimeout(osdTimer);
    osdTimer = setTimeout(function() {
        osd.classList.remove('shown');
    }, 1000);
    return osd;
}

function osdShowing(kind) {
    return osd && osd.classList.contains('shown') && osd.dataset.kind === kind;
}

// Speaker with 0, 1 or 2 sound waves, or crossed out when silent
const volumeIcons = {
    muted: '<path d="M11 5 6 9H3v6h3l5 4z"/><path d="m16 9 6 6M22 9l-6 6"/>',
    low: '<path d="M11 5 6 9H3v6h3l5 4z"/><path d="M15.5 9.5a3.5 3.5 0 0 1 0 5"/>',
    high: '<path d="M11 5 6 9H3v6h3l5 4z"/><path d="M15.5 9.5a3.5 3.5 0 0 1 0 5"/><path d="M18.5 6.5a7.5 7.5 0 0 1 0 11"/>',
};

function showVolume(v) {
    let box = showOsd(v, 'volume',
        '<svg viewBox="0 0 24 24" aria-hidden="true"></svg><span></span>');
    let percent = v.muted ? 0 : Math.round(v.volume * 100);
    let icon = percent == 0 ? 'muted' : (percent < 50 ? 'low' : 'high');
    box.querySelector('svg').innerHTML = volumeIcons[icon];
    box.querySelector('span').textContent = percent + '%';
}

// 75 -> "1:15", 3725 -> "1:02:05"
function formatTime(seconds) {
    seconds = Math.floor(seconds);
    let h = Math.floor(seconds / 3600);
    let m = Math.floor(seconds / 60) % 60;
    let s = String(seconds % 60).padStart(2, '0');
    if (h)
        return h + ':' + String(m).padStart(2, '0') + ':' + s;
    return m + ':' + s;
}

// Seconds moved since the time box appeared, so pressing right three
// times in a row shows +15 rather than +5
let seekTotal = 0;

// Moves to `time` and shows the new time with how far it moved above it
function seekTo(v, time) {
    let duration = videoDurationNow(v);
    let from = v.currentTime;
    time = Math.max(0, duration ? Math.min(duration, time) : time);
    v.currentTime = time;

    if (!osdShowing('seek'))
        seekTotal = 0;
    seekTotal += time - from;
    let box = showOsd(v, 'seek',
        '<span class="osd-delta"></span><span class="osd-time"></span>');
    let moved = Math.round(seekTotal);
    let sign = moved < 0 ? '−' : '+';     // a real minus sign
    let amount = Math.abs(moved);
    box.querySelector('.osd-delta').textContent =
        moved == 0 ? '' : sign + (amount < 60 ? amount : formatTime(amount));
    box.querySelector('.osd-time').textContent = formatTime(time);
}

// Also show the volume when it's changed some other way: plyr's slider or
// mute button, or the browser's own controls. Changes before the person
// has touched the page are the player setting its starting volume.
let pageTouched = false;
['pointerdown', 'keydown', 'wheel'].forEach(function(type) {
    document.addEventListener(type, function(e) {
        if (e.isTrusted) pageTouched = true;
    }, {capture: true, passive: true});
});

function onKeyDown(e) {
    if (['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) return false;

    // console.log(e);
    let v = Q("video");
    if (!e.isTrusted) return;  // plyr CustomEvent
    let c = e.key.toLowerCase();
    if (e.ctrlKey) {
        let isArrow = c == "arrowleft" || c == "arrowright";
        if (isArrow && !e.shiftKey && !e.altKey
                && data.chapters && data.chapters.length) {
            e.preventDefault();
            skipChapter(v, c == "arrowright" ? 1 : -1);
        }
        return;
    }
    else if (c == " ") {
        // Space already does something on these: presses a button or
        // link, opens a dropdown, or (on a focused video with the
        // browser's own controls) plays and pauses by itself
        let focused = document.activeElement;
        if (focused.isContentEditable || focused.closest(
                'button, a, summary, select, video, [role^="menuitem"]'))
            return;
        e.preventDefault();     // instead of scrolling the page down
        if (!e.repeat)
            v.paused ? v.play() : v.pause();
    }
    else if (c == "k") {
        v.paused ? v.play() : v.pause();
    }
    else if (c == "arrowleft") {
        e.preventDefault();
        seekTo(v, v.currentTime - 5);
    }
    else if (c == "arrowright") {
        e.preventDefault();
        seekTo(v, v.currentTime + 5);
    }
    else if (c == "arrowup" || c == "arrowdown") {
        // arrows already pick an option in a focused dropdown
        if (e.shiftKey || e.altKey || e.metaKey
                || document.activeElement.tagName == 'SELECT')
            return;
        e.preventDefault();     // instead of scrolling the page
        changeVolume(v, c == "arrowup" ? 0.05 : -0.05);
    }
    else if (c == "m") {
        e.preventDefault();
        v.muted = !v.muted;
        // unmuting at 0% would stay silent, so bring it back to 5%
        if (!v.muted && v.volume == 0)
            v.volume = 0.05;
        showVolume(v);
    }
    else if (c >= "0" && c <= "9" && c.length == 1) {
        if (e.altKey || e.metaKey) return;
        e.preventDefault();
        seekToPercent(v, Number(c));
    }
    else if (c == "j") {
        e.preventDefault();
        seekTo(v, v.currentTime - 10);
    }
    else if (c == "l") {
        e.preventDefault();
        seekTo(v, v.currentTime + 10);
    }
    else if (c == ",") {
        e.preventDefault();
        stepFrame(v, -1);
    }
    else if (c == ".") {
        e.preventDefault();
        stepFrame(v, 1);
    }
    else if (c == "f") {
        e.preventDefault();
        if (data.settings.video_player == 1)
            player.fullscreen.toggle()
        else {
            if (document.fullscreen) document.exitFullscreen();
            else v.requestFullscreen();
        }
    }
    else if (c == "c") {
        e.preventDefault();
        if (data.settings.video_player == 1)
            player.toggleCaptions();
        else {
            let tt = getActiveTranscriptTrack();
            if (tt == null) return;
            if (tt.mode == "showing") tt.mode = "disabled";
            else tt.mode = "showing";
        }
    }
    else if (c == "t") {
        let ts = Math.floor(Q("video").currentTime);
        copyTextToClipboard(`https://youtu.be/${data.video_id}?t=${ts}`);
    }
}

window.addEventListener('DOMContentLoaded', function() {
    document.addEventListener('keydown', onKeyDown);
    let v = Q('video');
    if (v)
        v.addEventListener('volumechange', function() {
            if (pageTouched) showVolume(v);
        });
});
