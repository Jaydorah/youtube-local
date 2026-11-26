// The bell in the header. Every message that pops up at the bottom left
// (Saved to Watch later, Removed from queue, checking subscriptions
// finished...) is also kept here, so one you missed can be read later.
// They're kept in localStorage, so all tabs share them.
//
// Other scripts add to it with ytlNotifications.add(text, details), where
// details can have:
//   title      a second line, such as the video's title
//   url        where clicking it goes
//   thumbnail  an image to show beside it
//   error      true for something that went wrong
(function() {
'use strict';

var LIST_KEY = 'ytl-notifications';
// The time of the newest one seen when the list was last opened. Newer ones
// count as unread.
var SEEN_KEY = 'ytl-notifications-seen';
var MAX_KEPT = 50;
var ICONS = '/youtube.com/static/icons.svg#';

function load() {
    try {
        var list = JSON.parse(localStorage.getItem(LIST_KEY));
        return Array.isArray(list) ? list : [];
    } catch (e) {
        return [];
    }
}
function save(list) {
    try {
        localStorage.setItem(LIST_KEY, JSON.stringify(list.slice(0, MAX_KEPT)));
    } catch (e) {}
}
function seenUpTo() {
    try {
        return Number(localStorage.getItem(SEEN_KEY)) || 0;
    } catch (e) {
        return 0;
    }
}
function markAllSeen(list) {
    if (!list.length || list[0].time <= seenUpTo())
        return;
    try {
        localStorage.setItem(SEEN_KEY, String(list[0].time));
    } catch (e) {}
}
function unreadCount(list) {
    var seen = seenUpTo();
    return list.filter(function(entry) { return entry.time > seen; }).length;
}

function add(text, details) {
    details = details || {};
    var list = load();
    // Two in the same millisecond would otherwise look read after opening
    var time = Math.max(Date.now(), list.length ? list[0].time + 1 : 0);
    var entry = {text: String(text), time: time};
    ['title', 'url', 'thumbnail'].forEach(function(name) {
        if (details[name])
            entry[name] = String(details[name]);
    });
    if (details.error)
        entry.error = true;
    list.unshift(entry);
    save(list);
    update();
}

window.ytlNotifications = {add: add};


// ---------- The bell and its list ----------

var button = document.getElementById('notifications-button');
var badge = button && button.querySelector('.notifications-badge');
var panel = null;

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
        if (child)
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

function timeAgo(time) {
    var minutes = Math.floor((Date.now() - time) / 60000);
    if (minutes < 1)
        return 'Just now';
    if (minutes < 60)
        return minutes + ' min ago';
    var hours = Math.floor(minutes / 60);
    if (hours < 24)
        return hours + (hours === 1 ? ' hour ago' : ' hours ago');
    var days = Math.floor(hours / 24);
    if (days === 1)
        return 'Yesterday';
    if (days < 7)
        return days + ' days ago';
    return new Date(time).toLocaleDateString();
}

function updateBadge(list) {
    if (!button)
        return;
    var count = unreadCount(list);
    badge.textContent = count > 9 ? '9+' : String(count);
    badge.hidden = count === 0;
    button.setAttribute('aria-label', count
        ? 'Notifications, ' + count + ' unread' : 'Notifications');
}

function row(entry, unread) {
    var leading = el('span', {class: 'notification-icon'},
                     [icon(entry.error ? 'alert' : 'bell')]);
    var text = el('span', {class: 'notification-text'}, [
        el('span', {class: 'notification-message', text: entry.text}),
        entry.title ? el('span', {class: 'notification-title', text: entry.title}) : null,
        el('span', {class: 'notification-time', text: timeAgo(entry.time)}),
    ]);
    var thumb = entry.thumbnail
        ? el('span', {class: 'notification-thumb'},
             [el('img', {src: entry.thumbnail, alt: '', loading: 'lazy'})])
        : null;
    var classes = 'notification-row'
        + (unread ? ' is-unread' : '') + (entry.error ? ' is-error' : '');
    var body = entry.url
        ? el('a', {class: 'notification-link', href: entry.url}, [leading, text, thumb])
        : el('div', {class: 'notification-link'}, [leading, text, thumb]);
    return el('li', {class: classes}, [
        el('span', {class: 'notification-unread-dot', 'aria-hidden': 'true'}),
        body,
    ]);
}

function renderPanel(list) {
    var body = panel.querySelector('.notifications-body');
    var clear = panel.querySelector('.notifications-clear');
    body.textContent = '';
    clear.hidden = list.length === 0;
    if (!list.length) {
        body.appendChild(el('div', {class: 'notifications-empty'}, [
            icon('bell'),
            el('p', {text: 'No notifications yet'}),
            el('p', {class: 'notifications-empty-help',
                     text: 'Messages that pop up at the bottom left of the page are kept here.'}),
        ]));
        return;
    }
    var seen = seenUpTo();
    var items = el('ul', {class: 'notifications-list'});
    list.forEach(function(entry) {
        items.appendChild(row(entry, entry.time > seen));
    });
    body.appendChild(items);
}

function update() {
    var list = load();
    if (panel) {
        renderPanel(list);
        markAllSeen(list);
    }
    updateBadge(list);
}

function onPointerDown(e) {
    if (panel && !panel.contains(e.target) && !button.contains(e.target))
        closePanel(false);
}
// Escape closes it, wherever the focus is: after a mouse click it's still
// on the bell
function onKeyDown(e) {
    if (e.key === 'Escape' && panel) {
        e.preventDefault();
        e.stopPropagation();
        closePanel(panel.contains(document.activeElement)
                   || document.activeElement === button);
    }
}
function place() {
    var rect = button.getBoundingClientRect();
    var viewWidth = document.documentElement.clientWidth;
    panel.style.top = (rect.bottom + 6) + 'px';
    panel.style.left = Math.max(8, Math.min(
        rect.right - panel.offsetWidth, viewWidth - panel.offsetWidth - 8)) + 'px';
}

function openPanel(keyboard) {
    var clear = el('button', {type: 'button', class: 'notifications-clear',
                              text: 'Clear all'});
    panel = el('div', {class: 'ytl-popover library-popover notifications-popover',
                       id: 'notifications-popover', role: 'dialog',
                       'aria-label': 'Notifications'}, [
        el('div', {class: 'library-popover-head'}, [
            el('h2', {text: 'Notifications'}),
            clear,
        ]),
        el('div', {class: 'library-popover-body notifications-body'}),
    ]);
    clear.addEventListener('click', function() {
        save([]);
        update();
        button.focus();
    });
    // Keys pressed in the list shouldn't reach the watch page's hotkeys
    panel.addEventListener('keydown', function(e) {
        e.stopPropagation();
    });
    document.body.appendChild(panel);
    update();
    place();
    document.addEventListener('pointerdown', onPointerDown, true);
    document.addEventListener('keydown', onKeyDown, true);
    window.addEventListener('resize', place);
    button.setAttribute('aria-expanded', 'true');
    if (keyboard) {
        var first = panel.querySelector('a, button:not([hidden])');
        if (first)
            first.focus();
    }
}

function closePanel(restoreFocus) {
    if (!panel)
        return;
    panel.remove();
    panel = null;
    document.removeEventListener('pointerdown', onPointerDown, true);
    document.removeEventListener('keydown', onKeyDown, true);
    window.removeEventListener('resize', place);
    button.setAttribute('aria-expanded', 'false');
    if (restoreFocus)
        button.focus();
}

if (button) {
    button.addEventListener('click', function(e) {
        if (panel)
            closePanel(e.detail === 0);
        else
            openPanel(e.detail === 0);
    });
    updateBadge(load());
}

// Keep the bell up to date with what other tabs add or read
window.addEventListener('storage', function(e) {
    if (e.key === LIST_KEY || e.key === SEEN_KEY || e.key === null)
        update();
});
// Coming back with the Back button shows the page as it was left
window.addEventListener('pageshow', function(e) {
    if (e.persisted)
        update();
});
})();
