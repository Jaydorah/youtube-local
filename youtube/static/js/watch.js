var video = document.querySelector('video');

// Frames per second of the quality that's playing, for stepping one frame
// at a time in hotkeys.js
var videoFps = null;
if (data.using_pair_sources && data.pair_sources[data.pair_idx])
    videoFps = data.pair_sources[data.pair_idx].fps;
else if (data.uni_sources && data.uni_sources[data.uni_idx])
    videoFps = data.uni_sources[data.uni_idx].fps;

function setVideoDimensions(height, width){
    var body = document.querySelector('body');
    body.style.setProperty('--video_height', String(height));
    body.style.setProperty('--video_width', String(width));
    if (height < 240)
        body.style.setProperty('--plyr-control-spacing-num', '3');
    else
        body.style.setProperty('--plyr-control-spacing-num', '10');
    var theaterWidth = Math.max(640, data['video_duration'] || 0, width);
    body.style.setProperty('--theater_video_target_width', String(theaterWidth));

    // This will set the correct media query
    document.querySelector('#video-container').className = 'h' + height;
}
function changeQuality(selection) {
    var currentVideoTime = video.currentTime;
    var videoPaused = video.paused;
    var videoSpeed = video.playbackRate;
    var srcInfo;
    if (avMerge)
        avMerge.close();
    if (selection.type == 'uni'){
        srcInfo = data['uni_sources'][selection.index];
        video.src = srcInfo.url;
    } else {
        srcInfo = data['pair_sources'][selection.index];
        avMerge = new AVMerge(video, srcInfo, currentVideoTime);
    }
    setVideoDimensions(srcInfo.height, srcInfo.width);
    videoFps = srcInfo.fps;
    video.currentTime = currentVideoTime;
    if (!videoPaused){
        video.play();
    }
    video.playbackRate = videoSpeed;
}

// Initialize av-merge
var avMerge;
if (data.using_pair_sources) {
    var srcPair = data['pair_sources'][data['pair_idx']];
    avMerge = new AVMerge(video, srcPair, 0);
}

// Quality selector
var qualitySelector = document.querySelector('#quality-select')
if (qualitySelector)
    qualitySelector.addEventListener(
        'change', function(e) {
            changeQuality(JSON.parse(this.value))
        }
    );

// Set up video start time from &t parameter
if (data.time_start != 0 && video)
    video.currentTime = data.time_start;

// Playlist lazy image loading
if (data.playlist && data.playlist['id'] !== null) {
    // lazy load playlist images
    // copied almost verbatim from
    // https://css-tricks.com/tips-for-rolling-your-own-lazy-loading/
    // IntersectionObserver isn't supported in pre-quantum
    // firefox versions, but the alternative of making it
    // manually is a performance drain, so oh well
    var observer = new IntersectionObserver(lazyLoad, {

      // where in relation to the edge of the viewport, we are observing
      rootMargin: "100px",

      // how much of the element needs to have intersected
      // in order to fire our loading function
      threshold: 1.0

    });

    function lazyLoad(elements) {
      elements.forEach(item => {
        if (item.intersectionRatio > 0) {

          // set the src attribute to trigger a load
          item.target.src = item.target.dataset.src;

          // stop observing this element. Our work here is done!
          observer.unobserve(item.target);
        };
      });
    };

    // Tell our observer to observe all img elements with a "lazy" class
    var lazyImages = document.querySelectorAll('img.lazy');
    lazyImages.forEach(img => {
      observer.observe(img);
    });
}


// Up next. When the video ends, the first queued video plays, whatever
// Autoplay is set to. Without a queue, Autoplay goes on to the next video in
// the playlist, or else the first related video.
(function() {
    let isPlaylist = data.playlist !== null && data.playlist['current_index'] !== null;
    let checkbox = document.querySelector('#autoplay-toggle');

    // https://developer.mozilla.org/en-US/docs/Web/API/Document/cookie
    function escapeRegExp(string) {
        // from https://stackoverflow.com/a/6969486
        return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    }
    function readCookie(name) {
        let match = document.cookie.match(new RegExp(
            '(?:^|;\\s*)' + escapeRegExp(name) + '\\s*=\\s*([^;]*)'));
        return match ? match[1] : '';
    }

    // Remembered separately for each playlist
    let cookie = 'autoplay';
    if (isPlaylist)
        cookie += '_' + data.playlist['id'];
    let autoplayEnabled = Boolean(checkbox) && Number(readCookie(cookie)) === 1;
    if (checkbox) {
        checkbox.checked = autoplayEnabled;
        checkbox.addEventListener('change', function() {
            autoplayEnabled = this.checked;
            document.cookie = cookie + '=' + (this.checked ? '1' : '0') + '; SameSite=Strict';
        });
    }

    // play the video if autoplay brought us here
    if (autoplayEnabled && !data.playability_error && video)
        video.play();

    let autoplayUrl = null;
    function findAutoplayUrl() {
        if (isPlaylist) {
            let next = data.playlist['items'][data.playlist['current_index'] + 1];
            autoplayUrl = next ? next['url'] : null;
        } else {
            autoplayUrl = data.related.length ? data.related[0]['url'] : null;
        }
    }
    findAutoplayUrl();

    if (isPlaylist) {
        // scroll playlist to proper position
        // item height + gap == 100
        let pl = document.querySelector('.playlist-videos');
        if (pl)
            pl.scrollTop = 100*data.playlist['current_index'];
    }

    // library.js tells us when the queue changes, or a related video is
    // hidden for this visit
    let queueLength = data.queue_length || 0;
    document.addEventListener('ytl:queue', function(e) {
        queueLength = e.detail.count;
    });
    document.addEventListener('ytl:hidden', function(e) {
        data.related = data.related.filter(function(item) {
            return item.id !== e.detail.id;
        });
        findAutoplayUrl();
    });

    function nextUrl() {
        if (queueLength > 0)
            return '/youtube.com/queue/play';
        return autoplayEnabled ? autoplayUrl : null;
    }
    function goToNextVideo() {
        let url = nextUrl();
        if (!url)
            return;
        window.location.href = url;
    }
    function videoEnded() {
        window.setTimeout(goToNextVideo, 1000);
    }

    // go to next video when video ends
    // https://stackoverflow.com/a/2880950
    // A video YouTube won't give out (deleted, private) is skipped. Not when
    // it's the bot check though: the next one would be refused as well, and
    // skipping would run through the whole queue or playlist, sending
    // YouTube more requests and keeping the block going.
    if (data.playability_error) {
        if (!data.bot_check)
            videoEnded();
    }
    else if (video)
        video.addEventListener('ended', videoEnded, false);
})();

// Share button: copies the direct link
function copyText(text) {
    // The Clipboard API only exists on https and localhost, so when the
    // server is reached over the LAN fall back to a hidden textarea
    if (navigator.clipboard && window.isSecureContext)
        return navigator.clipboard.writeText(text);
    return new Promise(function(resolve, reject) {
        var textarea = document.createElement('textarea');
        textarea.value = text;
        textarea.setAttribute('readonly', '');
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        var copied = false;
        try {
            copied = document.execCommand('copy');
        } catch (e) {}
        document.body.removeChild(textarea);
        copied ? resolve() : reject();
    });
}
var shareButton = document.querySelector('.share-button');
if (shareButton) {
    var shareLabel = shareButton.querySelector('.share-button-label');
    var shareResetTimer = null;
    shareButton.addEventListener('click', function() {
        copyText(shareButton.dataset.shareUrl).then(function() {
            shareLabel.textContent = 'Copied';
        }, function() {
            shareLabel.textContent = 'Copy failed';
        });
        clearTimeout(shareResetTimer);
        shareResetTimer = setTimeout(function() {
            shareLabel.textContent = 'Share';
        }, 2000);
    });
}

// Comments are fetched only when they're scrolled to or opened, so watching
// a video without reading them doesn't ask YouTube for them
(function() {
    var area = document.querySelector('.comments-area[data-comments-url]');
    if (!area)
        return;
    var details = area.closest('details');
    var placeholder = area.querySelector('.comments-placeholder');
    var state = 'waiting';
    var observer = null;

    function load() {
        if (state !== 'waiting')
            return;
        state = 'loading';
        if (observer)
            observer.disconnect();
        placeholder.textContent = 'Loading comments…';
        fetch(area.dataset.commentsUrl).then(function(response) {
            if (!response.ok)
                throw new Error('Error ' + response.status);
            return response.text();
        }).then(function(html) {
            state = 'loaded';
            area.innerHTML = html;
        }, function(error) {
            state = 'waiting';
            placeholder.textContent = 'Couldn\'t load the comments (' + error.message + '). ';
            var retry = document.createElement('button');
            retry.type = 'button';
            retry.textContent = 'Try again';
            retry.addEventListener('click', load);
            placeholder.appendChild(retry);
        });
    }

    // Without JavaScript this link opens the comments on their own page
    var link = placeholder.querySelector('a');
    if (link) {
        link.addEventListener('click', function(e) {
            if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0)
                return;
            e.preventDefault();
            load();
        });
    }

    if (details)
        details.addEventListener('toggle', function() {
            if (details.open)
                load();
        });
    if (details && !details.open)
        return;
    if (!('IntersectionObserver' in window)) {
        load();
        return;
    }
    observer = new IntersectionObserver(function(entries) {
        if (entries.some(function(entry) { return entry.isIntersecting; }))
            load();
    }, {rootMargin: '100px'});
    observer.observe(area);
})();
