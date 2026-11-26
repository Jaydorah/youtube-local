// Runs on every page:
// - the menu on each video, from its ⋮ button or by right-clicking it
//   (shift+right-click gives the browser's own menu)
// - the Save to playlist and Download dialogs it opens
// - the Playlists button in the header
// - Playlist mode beside Autoplay on the watch page: while it's on, clicking
//   a video there adds it to the playlist instead of opening it. Other pages
//   don't have the switch, so videos open as usual there.
// - the queue beside the video, and dragging videos to reorder a playlist
//   or the queue
(function() {
'use strict';

var API = '/youtube.com/ytl-api/';
var ICONS = '/youtube.com/static/icons.svg#';
var WATCH_LATER = 'Watch later';
var PLAYLIST_MODE_COOKIE = 'playlist_mode';
var LAST_PLAYLIST_KEY = 'ytl-playlist-mode-last';
var LIBRARY_CACHE_KEY = 'ytl-library-cache';
var reduceMotion = window.matchMedia
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;


// ---------- Helpers ----------

function el(tag, attributes, children) {
    var element = document.createElement(tag);
    Object.keys(attributes || {}).forEach(function(name) {
        var value = attributes[name];
        if (value === null || value === undefined || value === false)
            return;
        if (name === 'text')
            element.textContent = value;
        else
            element.setAttribute(name, value === true ? '' : value);
    });
    (children || []).forEach(function(child) {
        if (child === null || child === undefined)
            return;
        element.appendChild(typeof child === 'string'
                            ? document.createTextNode(child) : child);
    });
    return element;
}

function icon(name) {
    var ns = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('class', 'icon');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    var use = document.createElementNS(ns, 'use');
    use.setAttribute('href', ICONS + name);
    svg.appendChild(use);
    return svg;
}

function plural(count, word) {
    return count + ' ' + word + (count === 1 ? '' : 's');
}

function request(method, path, fields) {
    var options = {method: method, cache: 'no-store'};
    if (fields) {
        var form = new FormData();
        Object.keys(fields).forEach(function(name) {
            if (fields[name] !== null && fields[name] !== undefined)
                form.append(name, fields[name]);
        });
        options.body = form;
    }
    return fetch(API + path, options).then(function(response) {
        return response.json().catch(function() {
            return {ok: false, error: 'Error ' + response.status};
        });
    }, function() {
        throw new Error('Couldn\'t reach youtube-local. Is it still running?');
    }).then(function(result) {
        if (!result.ok)
            throw new Error(result.error || 'Something went wrong');
        if (method === 'POST')
            forgetLibrary();
        return result;
    });
}
function post(path, fields) { return request('POST', path, fields); }
function get(path) { return request('GET', path); }

// The video an .item-box (or the watch page's own buttons) is for
function videoOf(box) {
    var video = {};
    if (box.dataset.videoInfo) {
        try {
            video = JSON.parse(box.dataset.videoInfo) || {};
        } catch (e) {}
    } else {
        var title = box.querySelector('a.title');
        video.title = title ? title.textContent.trim() : null;
    }
    video.id = box.dataset.videoId;
    return video;
}
function videoJson(video) {
    return JSON.stringify(video);
}
function watchUrl(video) {
    return '/youtube.com/watch?v=' + encodeURIComponent(video.id);
}

// The Clipboard API is only there on https and localhost, so over the LAN
// this falls back to a hidden textarea
function copyText(text) {
    if (navigator.clipboard && window.isSecureContext)
        return navigator.clipboard.writeText(text);
    return new Promise(function(resolve, reject) {
        var textarea = el('textarea', {readonly: true});
        textarea.value = text;
        textarea.style.position = 'fixed';
        textarea.style.opacity = '0';
        document.body.appendChild(textarea);
        textarea.select();
        var copied = false;
        try {
            copied = document.execCommand('copy');
        } catch (e) {}
        document.body.removeChild(textarea);
        copied ? resolve() : reject(new Error('Couldn\'t copy the link'));
    });
}

// Keys pressed in our menus and dialogs shouldn't reach the watch page's
// hotkeys (arrows would change the volume)
function keepKeysInside(element) {
    element.addEventListener('keydown', function(e) {
        e.stopPropagation();
    });
}


// ---------- Toast: the message at the bottom after doing something ----------

var toastBox = null;
var toastTimer = null;
var toastHideTimer = null;
// A popover is drawn in the top layer, so the toast stays visible above an
// open dialog's backdrop
var canPopover = typeof HTMLElement.prototype.showPopover === 'function';

function hideToast() {
    clearTimeout(toastTimer);
    if (!toastBox)
        return;
    toastBox.classList.remove('is-visible');
    clearTimeout(toastHideTimer);
    toastHideTimer = setTimeout(function() {
        if (canPopover && toastBox.matches(':popover-open'))
            toastBox.hidePopover();
    }, 250);
}

// The video's thumbnail, if it's shown on this page
function thumbnailOf(video) {
    var img = document.querySelector('.item-box[data-video-id="'
        + CSS.escape(video.id) + '"] img.thumbnail-img');
    return img ? img.dataset.src || img.getAttribute('src') : null;
}

// Also keeps the message under the bell in the header (notifications.js).
// about is what it's about: {video}, {url} or {error: true}.
function rememberToast(message, about) {
    if (!window.ytlNotifications)
        return;
    about = about || {};
    var details = {url: about.url, error: about.error};
    var video = about.video;
    if (video && video.id) {
        details.title = video.title;
        details.url = watchUrl(video);
        details.thumbnail = thumbnailOf(video);
    }
    window.ytlNotifications.add(message, details);
}

function toast(message, action, about) {
    rememberToast(message, about);
    if (!toastBox) {
        toastBox = el('div', {class: 'ytl-toast', role: 'status',
                              'aria-live': 'polite',
                              popover: canPopover ? 'manual' : null});
        document.body.appendChild(toastBox);
        toastBox.addEventListener('mouseenter', function() {
            clearTimeout(toastTimer);
        });
        toastBox.addEventListener('mouseleave', function() {
            toastTimer = setTimeout(hideToast, 2500);
        });
    }
    clearTimeout(toastTimer);
    toastBox.textContent = '';
    toastBox.appendChild(el('span', {class: 'ytl-toast-text', text: message}));
    if (action) {
        var button = el('button', {type: 'button', class: 'ytl-toast-action',
                                   text: action.label});
        button.addEventListener('click', function() {
            hideToast();
            action.run();
        });
        toastBox.appendChild(button);
    }
    // restart the transition when one toast replaces another
    clearTimeout(toastHideTimer);
    toastBox.classList.remove('is-visible');
    if (canPopover) {
        // shown again, so it goes above a dialog opened since
        if (toastBox.matches(':popover-open'))
            toastBox.hidePopover();
        toastBox.showPopover();
    }
    void toastBox.offsetWidth;
    toastBox.classList.add('is-visible');
    toastTimer = setTimeout(hideToast, action ? 6000 : 3500);
}

function errorToast(error) {
    toast(error && error.message ? error.message : String(error), null,
          {error: true});
}


// ---------- Taking a video out of a list ----------

// Fades the video out of its list. Returns a function that puts it back.
function removeFromView(box) {
    var target = box.closest('.playlist-row') || box;
    function gone() {
        target.classList.add('ytl-gone');
        renumber(target.parentElement);
    }
    if (reduceMotion) {
        gone();
    } else {
        target.classList.add('is-leaving');
        setTimeout(gone, 180);
    }
    return function() {
        target.classList.remove('is-leaving', 'ytl-gone');
        renumber(target.parentElement);
    };
}

// The numbers beside the videos on the playlist page
function renumber(list) {
    if (!list || !list.matches('.playlist-rows'))
        return;
    var offset = Number(list.dataset.offset) || 0;
    var number = offset;
    list.querySelectorAll(':scope > .playlist-row').forEach(function(row) {
        var index = row.querySelector('.playlist-row-index');
        if (row.classList.contains('ytl-gone'))
            return;
        number += 1;
        if (index)
            index.textContent = number;
    });
}


// ---------- What the menu does ----------

function queueChanged(count) {
    document.dispatchEvent(new CustomEvent('ytl:queue', {detail: {count: count}}));
    refreshQueuePanel();
}

function addToQueue(video, playNext) {
    post('queue', {action: playNext ? 'play_next' : 'add',
                   video_info: videoJson(video)})
    .then(function(result) {
        queueChanged(result.count);
        var undo = {label: 'Undo', run: function() {
            post('queue', {action: 'remove', video_id: video.id})
                .then(function(r) { queueChanged(r.count); }, errorToast);
        }};
        var about = {video: video};
        if (result.result === 'added')
            toast(playNext ? 'Playing next' : 'Added to queue', undo, about);
        else if (result.result === 'moved')
            toast('Moved to the front of the queue', null, about);
        else
            toast(playNext ? 'Already playing next' : 'Already in the queue',
                  null, about);
    }, errorToast);
}

function saveToWatchLater(video) {
    post('playlist', {action: 'add', playlist_name: WATCH_LATER,
                      video_info: videoJson(video)})
    .then(function(result) {
        var remove = function() {
            post('playlist', {action: 'remove', playlist_name: WATCH_LATER,
                              video_id: video.id})
                .then(function() {
                    toast('Removed from Watch later', null, {video: video});
                }, errorToast);
        };
        if (result.added)
            toast('Saved to Watch later', {label: 'Undo', run: remove},
                  {video: video});
        else
            toast('Already in Watch later', {label: 'Remove', run: remove},
                  {video: video});
    }, errorToast);
}

function share(video) {
    copyText('https://youtu.be/' + video.id).then(function() {
        toast('Link copied', null, {video: video});
    }, errorToast);
}

// Only takes it off this page: nothing is saved, so it's back after a reload
function hideVideo(video, box) {
    var restore = removeFromView(box);
    document.dispatchEvent(new CustomEvent('ytl:hidden',
                                           {detail: {id: video.id}}));
    toast('Video hidden', {label: 'Undo', run: restore}, {video: video});
}

function removeFromPlaylist(video, box, name) {
    post('playlist', {action: 'remove', playlist_name: name,
                      video_id: video.id})
    .then(function(result) {
        var restore = removeFromView(box);
        toast('Removed from ' + name, {label: 'Undo', run: function() {
            post('playlist', {action: 'add', playlist_name: name,
                              video_info: videoJson(video),
                              position: result.index})
                .then(restore, errorToast);
        }}, {video: video});
    }, errorToast);
}

function removeFromQueue(video, box) {
    post('queue', {action: 'remove', video_id: video.id})
    .then(function(result) {
        var restore = box.closest('#queue-panel') ? null : removeFromView(box);
        queueChanged(result.count);
        toast('Removed from queue', {label: 'Undo', run: function() {
            post('queue', {action: 'add', video_info: videoJson(video),
                           position: result.index})
                .then(function(r) {
                    if (restore)
                        restore();
                    queueChanged(r.count);
                }, errorToast);
        }}, {video: video});
    }, errorToast);
}

// Move to top or bottom, from the menu
function moveVideo(video, box, where, playlistName) {
    var fields = {action: 'move', video_id: video.id, position: where};
    var path = 'queue';
    if (playlistName !== null) {
        fields.playlist_name = playlistName;
        path = 'playlist';
    }
    post(path, fields).then(function() {
        var row = box.closest('.playlist-row');
        var list = row && row.parentElement;
        if (box.closest('#queue-panel')) {
            refreshQueuePanel();
        } else if (list) {
            // Other pages of the playlist aren't shown, so a video moved
            // there leaves this one
            var pages = document.querySelector('.playlist-main .page-button-row');
            var onFirstPage = !pages || !pages.querySelector('a[rel="prev"]');
            var onLastPage = !pages || !pages.querySelector('a[rel="next"]');
            if (where === 'top' && onFirstPage)
                list.insertBefore(row, list.firstElementChild);
            else if (where === 'bottom' && onLastPage)
                list.appendChild(row);
            else
                row.classList.add('ytl-gone');
            renumber(list);
        }
        toast(where === 'top' ? 'Moved to the top' : 'Moved to the bottom',
              null, {video: video});
    }, errorToast);
}

function openWatchPageDownloads() {
    var details = document.querySelector('.download-dropdown');
    if (!details)
        return;
    details.open = true;
    details.scrollIntoView({block: 'start', behavior: reduceMotion ? 'auto' : 'smooth'});
    details.querySelector('summary').focus({preventScroll: true});
}


// ---------- The menu ----------

var menuState = null;

function menuEntries(box, fromContextMenu) {
    var video = videoOf(box);
    var context = box.dataset.menuContext || '';
    var playlistName = context.indexOf('playlist:') === 0 ? context.slice(9) : null;
    var inSortableList = Boolean(box.closest('ol[data-sortable]'));
    var main = [];
    var more = [];

    if (context === 'queue') {
        main.push(['move-top', 'Move to top', function() { moveVideo(video, box, 'top', null); }]);
        main.push(['move-bottom', 'Move to bottom', function() { moveVideo(video, box, 'bottom', null); }]);
    } else if (context !== 'watch') {
        // not for the video that's already playing
        main.push(['play-next', 'Play next', function() { addToQueue(video, true); }]);
        main.push(['queue', 'Add to queue', function() { addToQueue(video, false); }]);
    }
    if (playlistName !== WATCH_LATER)
        main.push(['clock', 'Save to Watch later', function() { saveToWatchLater(video); }]);
    main.push(['bookmark', 'Save to playlist', function() { openSaveDialog(video); }]);
    main.push(['download', 'Download', function() {
        if (context === 'watch')
            openWatchPageDownloads();
        else
            openDownloadDialog(video);
    }]);
    main.push(['share', 'Share', function() { share(video); }]);

    if (playlistName !== null) {
        if (inSortableList) {
            more.push(['move-top', 'Move to top', function() { moveVideo(video, box, 'top', playlistName); }]);
            more.push(['move-bottom', 'Move to bottom', function() { moveVideo(video, box, 'bottom', playlistName); }]);
        }
        more.push(['trash', 'Remove from ' + playlistName, function() { removeFromPlaylist(video, box, playlistName); }]);
    }
    if (context === 'queue')
        more.push(['trash', 'Remove from queue', function() { removeFromQueue(video, box); }]);
    if (!('noHide' in box.dataset))
        more.push(['hide', 'Hide', function() { hideVideo(video, box); }]);
    if (fromContextMenu)
        more.push(['new-tab', 'Open in new tab', function() {
            window.open(watchUrl(video), '_blank', 'noopener');
        }]);
    return [main, more];
}

function closeMenu(restoreFocus) {
    if (!menuState)
        return;
    var state = menuState;
    menuState = null;
    state.menu.remove();
    document.removeEventListener('pointerdown', state.onPointerDown, true);
    window.removeEventListener('resize', state.onAway);
    window.removeEventListener('blur', state.onAway);
    document.removeEventListener('scroll', state.onScroll, true);
    if (state.button) {
        state.button.setAttribute('aria-expanded', 'false');
        if (restoreFocus)
            state.button.focus({preventScroll: true});
    }
    if (state.box)
        state.box.classList.remove('menu-open');
}

function positionMenu(menu, options) {
    var margin = 8;
    var width = menu.offsetWidth;
    var height = menu.offsetHeight;
    var viewWidth = document.documentElement.clientWidth;
    var viewHeight = document.documentElement.clientHeight;
    var x, y;
    if (options.button) {
        var rect = options.button.getBoundingClientRect();
        // beside the button, flipped to fit
        x = rect.left;
        if (x + width > viewWidth - margin)
            x = rect.right - width;
        y = rect.bottom + 2;
        if (y + height > viewHeight - margin && rect.top - height - 2 >= margin)
            y = rect.top - height - 2;
    } else {
        x = options.x;
        y = options.y;
        if (x + width > viewWidth - margin)
            x -= width;
        if (y + height > viewHeight - margin)
            y -= height;
    }
    x = Math.max(margin, Math.min(x, viewWidth - width - margin));
    y = Math.max(margin, Math.min(y, viewHeight - height - margin));
    menu.style.left = (x + window.scrollX) + 'px';
    menu.style.top = (y + window.scrollY) + 'px';
}

function openMenu(box, options) {
    closeMenu(false);
    var groups = menuEntries(box, Boolean(options.fromContextMenu));
    var menu = el('div', {class: 'ytl-menu', role: 'menu', tabindex: '-1',
                          'aria-label': 'Video actions'});
    groups.forEach(function(group) {
        if (!group.length)
            return;
        if (menu.firstChild)
            menu.appendChild(el('div', {class: 'ytl-menu-separator',
                                        role: 'separator'}));
        group.forEach(function(entry) {
            var item = el('button', {type: 'button', class: 'ytl-menu-item',
                                     role: 'menuitem', tabindex: '-1'},
                          [icon(entry[0]), el('span', {text: entry[1]})]);
            item.addEventListener('click', function() {
                closeMenu(Boolean(options.button));
                entry[2]();
            });
            item.addEventListener('pointermove', function() {
                if (document.activeElement !== item)
                    item.focus({preventScroll: true});
            });
            menu.appendChild(item);
        });
    });
    document.body.appendChild(menu);
    positionMenu(menu, options);
    keepKeysInside(menu);

    menu.addEventListener('keydown', function(e) {
        var items = Array.prototype.slice.call(menu.querySelectorAll('.ytl-menu-item'));
        var index = items.indexOf(document.activeElement);
        var next = null;
        if (e.key === 'ArrowDown')
            next = items[(index + 1) % items.length];
        else if (e.key === 'ArrowUp')
            next = items[index <= 0 ? items.length - 1 : index - 1];
        else if (e.key === 'Home')
            next = items[0];
        else if (e.key === 'End')
            next = items[items.length - 1];
        else if (e.key === 'Escape' || e.key === 'Tab') {
            e.preventDefault();
            closeMenu(true);
            return;
        }
        if (next) {
            e.preventDefault();
            next.focus();
        }
    });

    var state = {
        menu: menu,
        box: box,
        button: options.button || null,
        onPointerDown: function(e) {
            if (!menu.contains(e.target)
                    && !(state.button && state.button.contains(e.target)))
                closeMenu(false);
        },
        onAway: function() { closeMenu(false); },
        onScroll: function(e) {
            // The page scrolling takes the menu along. A list scrolling
            // inside it (the playlist beside a video) would leave it behind.
            if (e.target !== document && !menu.contains(e.target))
                closeMenu(false);
        },
    };
    menuState = state;
    document.addEventListener('pointerdown', state.onPointerDown, true);
    window.addEventListener('resize', state.onAway);
    window.addEventListener('blur', state.onAway);
    document.addEventListener('scroll', state.onScroll, true);
    if (state.button)
        state.button.setAttribute('aria-expanded', 'true');
    box.classList.add('menu-open');

    var first = menu.querySelector('.ytl-menu-item');
    if (options.keyboard && first)
        first.focus({preventScroll: true});
    else
        menu.focus({preventScroll: true});
}

function boxOf(element) {
    return element.closest('.item-box[data-video-id], .watch-actions[data-video-id]');
}

document.addEventListener('click', function(e) {
    var button = e.target.closest('.item-menu-button');
    if (!button)
        return;
    var box = boxOf(button);
    if (!box)
        return;
    e.preventDefault();
    if (menuState && menuState.button === button) {
        closeMenu(false);
        return;
    }
    // detail is 0 when the button was pressed with the keyboard
    openMenu(box, {button: button, keyboard: e.detail === 0});
});

document.addEventListener('contextmenu', function(e) {
    if (e.shiftKey)
        return;
    var box = e.target.closest('.item-box[data-video-id]');
    if (!box)
        return;
    // Leave the browser's menu for copying selected text
    var selection = window.getSelection();
    if (selection && !selection.isCollapsed
            && box.contains(selection.anchorNode))
        return;
    e.preventDefault();
    var button = box.querySelector('.item-menu-button');
    // The menu key or shift+F10 sends a contextmenu event at 0, 0
    if (e.clientX === 0 && e.clientY === 0 && button)
        openMenu(box, {button: button, keyboard: true, fromContextMenu: true});
    else
        openMenu(box, {x: e.clientX, y: e.clientY, fromContextMenu: true});
});


// ---------- Dialogs ----------

function makeDialog(title, subtitle) {
    var heading = el('h2', {class: 'ytl-dialog-title', id: 'ytl-dialog-title',
                            text: title});
    var close = el('button', {type: 'button', class: 'ytl-icon-button',
                              'aria-label': 'Close', title: 'Close'},
                   [icon('close')]);
    var body = el('div', {class: 'ytl-dialog-body'});
    var head = el('div', {class: 'ytl-dialog-head'}, [
        el('div', {class: 'ytl-dialog-heading'}, [
            heading,
            subtitle ? el('p', {class: 'ytl-dialog-subtitle', text: subtitle}) : null,
        ]),
        close,
    ]);
    var dialog = el('dialog', {class: 'ytl-dialog',
                               'aria-labelledby': 'ytl-dialog-title'},
                    [el('div', {class: 'ytl-dialog-inner'}, [head, body])]);
    close.addEventListener('click', function() { dialog.close(); });
    // The inner box fills the dialog, so a click on the dialog itself is on
    // the backdrop
    dialog.addEventListener('click', function(e) {
        if (e.target === dialog)
            dialog.close();
    });
    dialog.addEventListener('close', function() { dialog.remove(); });
    keepKeysInside(dialog);
    document.body.appendChild(dialog);
    dialog.showModal();
    return {dialog: dialog, body: body, inner: dialog.firstChild};
}

function loadingMessage(text) {
    return el('p', {class: 'ytl-dialog-loading', role: 'status'},
              [el('span', {class: 'ytl-spinner', 'aria-hidden': 'true'}), text]);
}

// Save to playlist: a checkbox for each playlist, saved as soon as it's
// ticked, like on YouTube
function openSaveDialog(video) {
    var parts = makeDialog('Save to…', video.title || null);
    var list = el('div', {class: 'save-list', role: 'group',
                          'aria-label': 'Playlists'});
    parts.body.appendChild(loadingMessage('Loading playlists…'));

    function row(playlist) {
        var checkbox = el('input', {type: 'checkbox', class: 'save-checkbox'});
        checkbox.checked = Boolean(playlist.contains);
        var count = el('span', {class: 'save-row-count',
                                text: String(playlist.video_count)});
        var label = el('label', {class: 'save-row'}, [
            checkbox,
            el('span', {class: 'save-row-check', 'aria-hidden': 'true'}, [icon('check')]),
            icon(playlist.is_watch_later ? 'clock' : 'playlist'),
            el('span', {class: 'save-row-name', text: playlist.name}),
            count,
        ]);
        checkbox.addEventListener('change', function() {
            var adding = checkbox.checked;
            checkbox.disabled = true;
            var fields = adding
                ? {action: 'add', playlist_name: playlist.name, video_info: videoJson(video)}
                : {action: 'remove', playlist_name: playlist.name, video_id: video.id};
            post('playlist', fields).then(function(result) {
                count.textContent = String(result.playlist.video_count);
                toast((adding ? 'Saved to ' : 'Removed from ') + playlist.name,
                      null, {video: video});
            }, function(error) {
                checkbox.checked = !adding;
                errorToast(error);
            }).then(function() {
                checkbox.disabled = false;
            });
        });
        return label;
    }

    get('library?video_id=' + encodeURIComponent(video.id)).then(function(result) {
        parts.body.textContent = '';
        result.playlists.forEach(function(playlist) {
            list.appendChild(row(playlist));
        });
        parts.body.appendChild(list);
        parts.body.appendChild(newPlaylistForm(function(name) {
            return post('playlist', {action: 'add', playlist_name: name,
                                     video_info: videoJson(video)})
            .then(function(result) {
                var playlist = result.playlist;
                playlist.contains = true;
                var newRow = row(playlist);
                list.appendChild(newRow);
                newRow.scrollIntoView({block: 'nearest'});
                toast('Saved to ' + playlist.name, null, {video: video});
            });
        }));
        var first = list.querySelector('input');
        if (first)
            first.focus();
    }, function(error) {
        parts.body.textContent = '';
        parts.body.appendChild(el('p', {class: 'ytl-dialog-error', role: 'alert',
                                        text: error.message}));
    });
}

// "+ New playlist", which opens into a name box. create(name) returns a
// promise.
function newPlaylistForm(create) {
    var opener = el('button', {type: 'button', class: 'save-new-button'},
                    [icon('plus'), el('span', {text: 'New playlist'})]);
    var input = el('input', {type: 'text', class: 'save-new-input',
                             maxlength: '100', placeholder: 'Name',
                             'aria-label': 'Name of the new playlist',
                             autocomplete: 'off', required: true});
    var error = el('p', {class: 'ytl-dialog-error', role: 'alert'});
    var submit = el('button', {type: 'submit',
                               class: 'lib-button lib-button-primary',
                               text: 'Create'});
    var cancel = el('button', {type: 'button', class: 'lib-button',
                               text: 'Cancel'});
    var form = el('form', {class: 'save-new-form', hidden: true}, [
        input,
        el('div', {class: 'save-new-buttons'}, [cancel, submit]),
        error,
    ]);
    var wrapper = el('div', {class: 'save-new'}, [opener, form]);
    opener.addEventListener('click', function() {
        opener.hidden = true;
        form.hidden = false;
        input.focus();
    });
    cancel.addEventListener('click', function() {
        form.hidden = true;
        opener.hidden = false;
        error.textContent = '';
        opener.focus();
    });
    form.addEventListener('submit', function(e) {
        e.preventDefault();
        submit.disabled = true;
        error.textContent = '';
        create(input.value).then(function() {
            input.value = '';
            form.hidden = true;
            opener.hidden = false;
        }, function(err) {
            error.textContent = err.message;
            input.focus();
        }).then(function() {
            submit.disabled = false;
        });
    });
    return wrapper;
}

function formatLabel(format) {
    if (format.has_video) {
        var label = format.height ? format.height + 'p' : format.video_quality;
        var fps = /(\d+)fps/.exec(format.video_quality || '');
        if (fps && Number(fps[1]) > 30)
            label += fps[1];
        return label;
    }
    return format.audio_quality;
}

function openDownloadDialog(video) {
    var parts = makeDialog('Download', video.title || null);
    parts.dialog.classList.add('is-wide');

    function load() {
        parts.body.textContent = '';
        parts.body.appendChild(loadingMessage('Getting the download links…'));
        get('download_formats?v=' + encodeURIComponent(video.id)).then(show, function(error) {
            parts.body.textContent = '';
            var retry = el('button', {type: 'button', class: 'lib-button',
                                      text: 'Try again'});
            retry.addEventListener('click', load);
            parts.body.appendChild(el('div', {class: 'ytl-dialog-error-box'}, [
                el('p', {class: 'ytl-dialog-error', role: 'alert', text: error.message}),
                retry,
            ]));
        });
    }

    function link(url, main, details) {
        return el('a', {class: 'download-row', href: url, target: '_blank',
                        rel: 'noopener'}, [
            icon('download'),
            el('span', {class: 'download-row-main', text: main}),
            el('span', {class: 'download-row-details', text: details}),
        ]);
    }

    function show(result) {
        parts.body.textContent = '';
        var groups = [
            ['Video with sound', function(f) { return f.has_video && f.has_audio; }],
            ['Audio only', function(f) { return f.has_audio && !f.has_video; }],
            ['Video only, no sound', function(f) { return f.has_video && !f.has_audio; }],
        ];
        groups.forEach(function(group) {
            var formats = result.formats.filter(group[1]);
            if (!formats.length)
                return;
            var section = el('section', {class: 'download-group'},
                             [el('h3', {text: group[0]})]);
            formats.forEach(function(format) {
                var details = [format.ext];
                if (format.file_size && format.file_size !== 'N/A')
                    details.push(format.file_size);
                details.push(format.codecs);
                section.appendChild(link(format.url, formatLabel(format),
                                         details.join(' · ')));
            });
            parts.body.appendChild(section);
        });
        if (result.others && result.others.length) {
            var section = el('section', {class: 'download-group'},
                             [el('h3', {text: 'Transcripts'})]);
            result.others.forEach(function(other) {
                section.appendChild(link(other.url,
                    other.label.replace(/^Video Transcript: /, ''), other.ext));
            });
            parts.body.appendChild(section);
        }
        if (!parts.body.firstChild)
            parts.body.appendChild(el('p', {class: 'ytl-dialog-error',
                text: 'There\'s nothing to download for this video.'}));
    }
    load();
}

document.addEventListener('click', function(e) {
    var button = e.target.closest('[data-save-video]');
    if (!button)
        return;
    var box = boxOf(button);
    if (box)
        openSaveDialog(videoOf(box));
});


// ---------- The queue beside the video on the watch page ----------

function refreshQueuePanel() {
    var slot = document.getElementById('queue-panel-slot');
    if (!slot)
        return;
    var current = (typeof data !== 'undefined' && data && data.video_id) || '';
    fetch(API + 'queue_panel?current=' + encodeURIComponent(current),
          {cache: 'no-store'})
    .then(function(response) {
        if (!response.ok)
            throw new Error('Error ' + response.status);
        return response.text();
    })
    .then(function(html) {
        slot.innerHTML = html;
    })
    .catch(function() {});
}

document.addEventListener('submit', function(e) {
    var form = e.target.closest('[data-queue-clear]');
    if (!form)
        return;
    e.preventDefault();
    post('queue', {action: 'clear'}).then(function(result) {
        queueChanged(0);
        toast('Queue cleared', {label: 'Undo', run: function() {
            post('queue', {action: 'restore',
                           videos: JSON.stringify(result.videos)})
                .then(function(r) { queueChanged(r.count); }, errorToast);
        }}, {url: '/youtube.com/queue'});
    }, errorToast);
});

// "Add all to queue" on a playlist's page
document.addEventListener('click', function(e) {
    var button = e.target.closest('[data-queue-all]');
    if (!button)
        return;
    var details = button.closest('details');
    if (details)
        details.open = false;
    post('playlist', {action: 'queue_all',
                      playlist_name: button.dataset.queueAll})
    .then(function(result) {
        toast(result.added
              ? 'Added ' + plural(result.added, 'video') + ' to the queue'
              : 'They\'re all in the queue already',
              {label: 'View queue', run: function() {
                  location.href = '/youtube.com/queue';
              }}, {url: '/youtube.com/queue'});
    }, errorToast);
});


// ---------- Dragging to reorder a playlist or the queue ----------

document.addEventListener('pointerdown', function(e) {
    var handle = e.target.closest('.playlist-row-handle');
    if (!handle || e.button !== 0)
        return;
    var row = handle.closest('.playlist-row');
    var list = row && row.parentElement;
    if (!list || !list.dataset.sortable)
        return;
    e.preventDefault();

    var rows = function() {
        return Array.prototype.filter.call(list.children, function(child) {
            return child.matches('.playlist-row:not(.ytl-gone)');
        });
    };
    var startIndex = rows().indexOf(row);
    var scrollTimer = null;
    var lastY = e.clientY;
    var pointerId = e.pointerId;
    row.classList.add('is-dragging');
    list.classList.add('is-sorting');

    function placeAt(y) {
        var others = rows().filter(function(other) { return other !== row; });
        var before = null;
        for (var i = 0; i < others.length; i++) {
            var rect = others[i].getBoundingClientRect();
            if (y < rect.top + rect.height/2) {
                before = others[i];
                break;
            }
        }
        if (before) {
            if (row.nextElementSibling !== before)
                list.insertBefore(row, before);
        } else if (others.length && list.lastElementChild !== row) {
            others[others.length - 1].after(row);
        }
    }
    // Scroll the page while dragging near its top or bottom edge
    function autoScroll() {
        var edge = 70;
        var height = document.documentElement.clientHeight;
        var speed = 0;
        if (lastY < edge)
            speed = -Math.ceil((edge - lastY)/5);
        else if (lastY > height - edge)
            speed = Math.ceil((lastY - (height - edge))/5);
        if (speed) {
            window.scrollBy(0, speed);
            placeAt(lastY);
        }
    }
    // Listened for on the document: moving the row in the page releases
    // any pointer capture on its handle
    function onMove(ev) {
        if (ev.pointerId !== pointerId)
            return;
        lastY = ev.clientY;
        placeAt(lastY);
    }
    function onEnd(ev) {
        if (ev.pointerId !== pointerId)
            return;
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerup', onEnd);
        document.removeEventListener('pointercancel', onEnd);
        clearInterval(scrollTimer);
        row.classList.remove('is-dragging');
        list.classList.remove('is-sorting');
        var newIndex = rows().indexOf(row);
        renumber(list);
        if (newIndex === startIndex)
            return;
        var offset = Number(list.dataset.offset) || 0;
        var fields = {action: 'move', video_id: row.dataset.videoId,
                      position: offset + newIndex};
        var path = 'queue';
        if (list.dataset.sortable === 'playlist') {
            fields.playlist_name = list.dataset.playlistName;
            path = 'playlist';
        }
        post(path, fields).then(function(result) {
            if (path === 'queue')
                queueChanged(result.count);
        }, function(error) {
            // put it back
            var current = rows().filter(function(other) { return other !== row; });
            if (startIndex < current.length)
                list.insertBefore(row, current[startIndex]);
            else
                list.appendChild(row);
            renumber(list);
            errorToast(error);
        });
    }
    document.addEventListener('pointermove', onMove);
    document.addEventListener('pointerup', onEnd);
    document.addEventListener('pointercancel', onEnd);
    scrollTimer = setInterval(autoScroll, 16);
});


// ---------- Playlist mode ----------

function setPlaylistModeCookie(name) {
    if (name)
        document.cookie = PLAYLIST_MODE_COOKIE + '=' + encodeURIComponent(name)
            + '; path=/; max-age=31536000; SameSite=Strict';
    else
        document.cookie = PLAYLIST_MODE_COOKIE + '=; path=/; max-age=0; SameSite=Strict';
    var button = document.getElementById('library-button');
    if (button) {
        button.classList.toggle('playlist-mode-on',
                                Boolean(name) && playlistModeHere());
        button.title = name
            ? 'Playlist mode is adding clicked videos to ' + name
            : 'Your playlists';
    }
    document.documentElement.classList.toggle('playlist-mode-active',
                                              Boolean(name) && playlistModeHere());
}

function playlistModeName() {
    var match = document.cookie.match(/(?:^|; )playlist_mode=([^;]*)/);
    if (!match || !match[1])
        return null;
    try {
        return decodeURIComponent(match[1]);
    } catch (e) {
        return null;
    }
}
// Only the watch page has the switch, so only there does Playlist mode
// change what clicking a video does
function playlistModeHere() {
    return document.getElementById('playlist-mode') !== null;
}
if (playlistModeHere() && playlistModeName())
    document.documentElement.classList.add('playlist-mode-active');

function addInPlaylistMode(video, box, name) {
    post('playlist', {action: 'add', playlist_name: name,
                      video_info: videoJson(video)})
    .then(function(result) {
        box.classList.add('is-added');
        var remove = function() {
            post('playlist', {action: 'remove', playlist_name: name,
                              video_id: video.id})
            .then(function() {
                box.classList.remove('is-added');
                toast('Removed from ' + name, null, {video: video});
            }, errorToast);
        };
        if (result.added)
            toast('Added to ' + name, {label: 'Undo', run: remove},
                  {video: video});
        else
            toast('Already in ' + name, {label: 'Remove', run: remove},
                  {video: video});
    }, errorToast);
}

// While Playlist mode is on, clicking a video on the watch page adds it
// instead of opening it. Ctrl/middle-click still opens it, and so do the
// videos in the queue.
document.addEventListener('click', function(e) {
    if (e.defaultPrevented || e.button !== 0
            || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey)
        return;
    var name = playlistModeName();
    if (!name || !playlistModeHere())
        return;
    var link = e.target.closest('a[href]');
    var box = link && link.closest('.item-box[data-video-id]');
    if (!box || link.getAttribute('href').indexOf('/watch?') === -1)
        return;
    var context = box.dataset.menuContext || '';
    if (context === 'queue' || context.indexOf('playlist:') === 0)
        return;
    e.preventDefault();
    addInPlaylistMode(videoOf(box), box, name);
}, true);

function rememberLastPlaylist(name) {
    try {
        localStorage.setItem(LAST_PLAYLIST_KEY, name);
    } catch (e) {}
}
function lastPlaylist() {
    try {
        return localStorage.getItem(LAST_PLAYLIST_KEY);
    } catch (e) {
        return null;
    }
}

document.addEventListener('click', function(e) {
    var button = e.target.closest('[data-playlist-mode-off]');
    if (!button)
        return;
    setPlaylistModeCookie(null);
    var banner = button.closest('.playlist-mode-banner');
    if (banner)
        banner.remove();
    closeLibraryPopover(false);
    var toggle = document.getElementById('playlist-mode-toggle');
    if (toggle && toggle.checked) {
        toggle.checked = false;
        toggle.dispatchEvent(new Event('change'));
    } else {
        toast('Playlist mode is off');
    }
});

(function() {
    var box = document.getElementById('playlist-mode');
    if (!box)
        return;
    var toggle = document.getElementById('playlist-mode-toggle');
    var select = document.getElementById('playlist-mode-select');
    var status = box.querySelector('.playlist-mode-status');
    var openLink = box.querySelector('.playlist-mode-open');
    var previousValue = select.value;

    // Start from the playlist picked last time
    var last = lastPlaylist();
    if (!toggle.checked && last) {
        for (var i = 0; i < select.options.length; i++) {
            if (select.options[i].value === last)
                select.value = last;
        }
    }
    previousValue = select.value;

    function setStatus(text, done) {
        status.textContent = '';
        if (done)
            status.appendChild(icon('check'));
        status.appendChild(document.createTextNode(text));
    }
    function updateLink(name) {
        openLink.href = '/youtube.com/playlists/' + encodeURIComponent(name);
    }
    function addOption(name) {
        for (var i = 0; i < select.options.length; i++) {
            if (select.options[i].value === name)
                return;
        }
        var option = el('option', {value: name, text: name});
        select.insertBefore(option, select.querySelector('[data-new-playlist]'));
    }

    function turnOn(name, announce) {
        setPlaylistModeCookie(name);
        rememberLastPlaylist(name);
        box.classList.add('is-on');
        updateLink(name);
        setStatus('Click a video to add it to ' + name);
        if (announce)
            toast('Playlist mode is on. Click videos to add them to ' + name,
                  null, {url: '/youtube.com/playlists/' + encodeURIComponent(name)});
    }
    function turnOff(announce) {
        setPlaylistModeCookie(null);
        box.classList.remove('is-on');
        setStatus('');
        if (announce)
            toast('Playlist mode is off');
    }

    // Asks for a name in place of the dropdown
    function askForName() {
        var input = el('input', {type: 'text', maxlength: '100',
                                 placeholder: 'New playlist name',
                                 'aria-label': 'New playlist name',
                                 autocomplete: 'off', required: true});
        var create = el('button', {type: 'submit', class: 'lib-button lib-button-primary lib-button-small', text: 'Create'});
        var cancel = el('button', {type: 'button', class: 'lib-button lib-button-small', text: 'Cancel'});
        var form = el('form', {class: 'playlist-mode-new'}, [input, create, cancel]);
        var target = box.querySelector('.playlist-mode-target');
        target.hidden = true;
        target.after(form);
        input.focus();
        function done(name) {
            form.remove();
            target.hidden = false;
            if (name) {
                addOption(name);
                select.value = name;
                previousValue = name;
                rememberLastPlaylist(name);
                updateLink(name);
                if (toggle.checked)
                    turnOn(name, true);
            } else {
                select.value = previousValue;
            }
            select.focus();
        }
        cancel.addEventListener('click', function() { done(null); });
        form.addEventListener('keydown', function(e) {
            if (e.key === 'Escape')
                done(null);
        });
        form.addEventListener('submit', function(e) {
            e.preventDefault();
            create.disabled = true;
            post('playlist', {action: 'create', playlist_name: input.value})
            .then(function(result) {
                done(result.playlist.name);
            }, function(error) {
                // already there is fine: use it
                var name = input.value.trim();
                var exists = Array.prototype.some.call(select.options, function(o) {
                    return o.value === name;
                });
                if (exists)
                    done(name);
                else
                    errorToast(error);
            }).then(function() {
                create.disabled = false;
            });
        });
    }

    toggle.addEventListener('change', function() {
        if (toggle.checked)
            turnOn(select.value, true);
        else
            turnOff(true);
    });
    select.addEventListener('change', function() {
        var option = select.options[select.selectedIndex];
        if (option && option.hasAttribute('data-new-playlist')) {
            askForName();
            return;
        }
        previousValue = select.value;
        rememberLastPlaylist(select.value);
        updateLink(select.value);
        if (toggle.checked)
            turnOn(select.value, true);
    });
})();


// ---------- The Playlists button in the header ----------

var libraryPopover = null;

// The list is kept in localStorage between pages. Each page carries a stamp
// (data-library-version on <body>) that changes when a playlist or the queue
// does, so it's only fetched again after a change.
function forgetLibrary() {
    try {
        localStorage.removeItem(LIBRARY_CACHE_KEY);
    } catch (e) {}
}
function loadLibrary() {
    var version = document.body.dataset.libraryVersion;
    try {
        var saved = JSON.parse(localStorage.getItem(LIBRARY_CACHE_KEY));
        if (version && saved && saved.version === version)
            return Promise.resolve(saved.library);
    } catch (e) {}
    return get('library').then(function(result) {
        document.body.dataset.libraryVersion = result.version;
        try {
            localStorage.setItem(LIBRARY_CACHE_KEY, JSON.stringify(
                {version: result.version, library: result}));
        } catch (e) {}
        return result;
    });
}

function closeLibraryPopover(restoreFocus) {
    if (!libraryPopover)
        return;
    var state = libraryPopover;
    libraryPopover = null;
    state.popover.remove();
    document.removeEventListener('pointerdown', state.onPointerDown, true);
    window.removeEventListener('resize', state.onResize);
    state.button.setAttribute('aria-expanded', 'false');
    if (restoreFocus)
        state.button.focus();
}

function libraryRow(summary, icons, extraClass) {
    var thumb = summary.thumbnail
        ? el('img', {src: summary.thumbnail, alt: '', loading: 'lazy'})
        : el('span', {class: 'library-row-placeholder'}, [icon(icons)]);
    var row = el('li', {class: 'library-row' + (extraClass ? ' ' + extraClass : '')}, [
        el('a', {class: 'library-row-link', href: summary.url}, [
            el('span', {class: 'library-row-thumb'}, [thumb]),
            el('span', {class: 'library-row-text'}, [
                el('span', {class: 'library-row-name', text: summary.name}),
                el('span', {class: 'library-row-meta',
                            text: plural(summary.video_count, 'video')
                                  + (summary.total_duration ? ' · ' + summary.total_duration : '')}),
            ]),
        ]),
    ]);
    if (summary.play_url) {
        row.appendChild(el('a', {class: 'ytl-icon-button library-row-play',
                                 href: summary.play_url,
                                 title: 'Play', 'aria-label': 'Play ' + summary.name},
                           [icon('play')]));
    }
    return row;
}

function openLibraryPopover(button) {
    closeMenu(false);
    var list = el('ul', {class: 'library-popover-list'});
    var body = el('div', {class: 'library-popover-body'},
                  [loadingMessage('Loading playlists…')]);
    var popover = el('div', {class: 'ytl-popover library-popover',
                             id: 'library-popover', role: 'dialog',
                             'aria-label': 'Playlists'}, [
        el('div', {class: 'library-popover-head'}, [
            el('h2', {text: 'Playlists'}),
            el('a', {class: 'library-popover-all', href: '/youtube.com/playlists',
                     text: 'See all'}),
        ]),
        body,
        el('div', {class: 'library-popover-foot'}, [
            el('a', {class: 'lib-button lib-button-small', href: '/youtube.com/playlists#new-playlist'},
               [icon('plus'), el('span', {text: 'New playlist'})]),
            el('a', {class: 'lib-button lib-button-small', href: '/youtube.com/playlists#import-playlists'},
               [icon('import'), el('span', {text: 'Import'})]),
        ]),
    ]);
    document.body.appendChild(popover);
    keepKeysInside(popover);

    function place() {
        var rect = button.getBoundingClientRect();
        var viewWidth = document.documentElement.clientWidth;
        popover.style.top = (rect.bottom + 6) + 'px';
        popover.style.left = Math.max(8, Math.min(
            rect.left, viewWidth - popover.offsetWidth - 8)) + 'px';
    }
    place();

    var state = {
        popover: popover,
        button: button,
        onPointerDown: function(e) {
            if (!popover.contains(e.target) && !button.contains(e.target))
                closeLibraryPopover(false);
        },
        onResize: place,
    };
    libraryPopover = state;
    document.addEventListener('pointerdown', state.onPointerDown, true);
    window.addEventListener('resize', state.onResize);
    button.setAttribute('aria-expanded', 'true');
    popover.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            e.preventDefault();
            closeLibraryPopover(true);
        }
    });

    loadLibrary().then(function(result) {
        body.textContent = '';
        var modeName = playlistModeHere() && playlistModeName();
        if (modeName) {
            var off = el('button', {type: 'button', class: 'lib-button lib-button-small',
                                    'data-playlist-mode-off': true, text: 'Turn off'});
            body.appendChild(el('div', {class: 'playlist-mode-banner is-compact'}, [
                el('span', {class: 'playlist-mode-dot', 'aria-hidden': 'true'}),
                el('span', {text: 'Playlist mode: clicking a video adds it to ' + modeName}),
                off,
            ]));
        }
        list.appendChild(libraryRow(result.queue, 'queue', 'is-queue'));
        result.playlists.forEach(function(playlist) {
            list.appendChild(libraryRow(playlist,
                playlist.is_watch_later ? 'clock' : 'playlist'));
        });
        body.appendChild(list);
        var first = popover.querySelector('.library-row-link');
        if (first && state.keyboard)
            first.focus();
    }, function(error) {
        body.textContent = '';
        body.appendChild(el('p', {class: 'ytl-dialog-error', role: 'alert',
                                  text: error.message}));
    });
    return state;
}

(function() {
    var button = document.getElementById('library-button');
    if (!button)
        return;
    button.setAttribute('role', 'button');
    button.setAttribute('aria-haspopup', 'dialog');
    button.setAttribute('aria-expanded', 'false');
    button.setAttribute('aria-controls', 'library-popover');
    function toggle(keyboard) {
        if (libraryPopover) {
            closeLibraryPopover(keyboard);
            return;
        }
        var state = openLibraryPopover(button);
        state.keyboard = keyboard;
    }
    button.addEventListener('click', function(e) {
        // ctrl+click or middle-click still opens the Playlists page
        if (e.ctrlKey || e.metaKey || e.shiftKey)
            return;
        e.preventDefault();
        toggle(e.detail === 0);
    });
    button.addEventListener('keydown', function(e) {
        if (e.key === ' ') {
            e.preventDefault();
            toggle(true);
        }
    });
})();


// ---------- The dropdowns on the playlist pages ----------

// <details class="popout"> work without JavaScript. This closes them when
// clicking elsewhere or pressing Escape, and keeps one open at a time.
document.addEventListener('click', function(e) {
    document.querySelectorAll('details.popout[open]').forEach(function(details) {
        if (!details.contains(e.target))
            details.open = false;
    });
});
document.addEventListener('keydown', function(e) {
    if (e.key !== 'Escape')
        return;
    var open = document.querySelectorAll('details.popout[open]');
    if (!open.length)
        return;
    var details = open[open.length - 1];
    // the inner one (Rename, Delete) first
    var sub = details.querySelector('details.popout-sub[open]');
    if (sub) {
        sub.open = false;
        sub.querySelector('summary').focus();
    } else {
        details.open = false;
        details.querySelector('summary').focus();
    }
});
document.addEventListener('toggle', function(e) {
    var details = e.target;
    if (!details.matches || !details.matches('details.popout') || !details.open)
        return;
    document.querySelectorAll('details.popout[open]').forEach(function(other) {
        if (other !== details && !other.contains(details))
            other.open = false;
    });
    var field = details.querySelector('.popout-panel > input:not([type="file"]), .popout-panel input[type="text"], .popout-panel input:not([type])');
    if (field && details.querySelector('.popout-form') && !details.querySelector('.popout-sub'))
        field.focus();
}, true);
document.addEventListener('toggle', function(e) {
    var details = e.target;
    if (!details.matches || !details.matches('details.popout-sub') || !details.open)
        return;
    var field = details.querySelector('input:not([type="hidden"])');
    if (field) {
        field.focus();
        field.select();
    }
}, true);

// /playlists#new-playlist and #import-playlists, from the Playlists button
function openFromHash() {
    var id = location.hash.slice(1);
    if (id !== 'new-playlist' && id !== 'import-playlists')
        return;
    var details = document.getElementById(id);
    if (details)
        details.open = true;
}
openFromHash();
window.addEventListener('hashchange', openFromHash);

})();
