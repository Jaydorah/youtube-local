// Subscriptions page:
// - the Check buttons check in the background without leaving the page,
//   and a status box shows how far along checking is
// - changing a "Hide" option applies it right away
// - the sidebar can be hidden, and stays hidden on later visits
(function() {
'use strict';

var STATUS_URL = '/youtube.com/subscriptions/check_status';
var POLL_INTERVAL_MS = 1000;

var statusBox = document.getElementById('check-status');
var statusText = document.getElementById('check-status-text');
var progressFill = document.getElementById('check-progress-fill');
var spinner = document.getElementById('check-spinner');
var reloadButton = document.getElementById('check-status-reload');
var closeButton = document.getElementById('check-status-close');
var refreshForms = Array.prototype.filter.call(
    document.querySelectorAll('form'),
    function(form) {
        var action = form.querySelector('input[name="action"]');
        return action && action.value === 'refresh';
    }
);

var polling = false;
// Whether checking was seen running, so its end can be reported
var sawChecking = false;
// Whether its end went under the bell in the header already
var reported = false;

function formatNumber(number) {
    return Number(number).toLocaleString();
}

function setButtonChecking(form, checking) {
    var button = form.querySelector('input[type="submit"]');
    if (!button.dataset.label)
        button.dataset.label = button.value;
    button.value = checking ? 'Checking…' : button.dataset.label;
    button.disabled = checking;
}

// Keeps the result under the bell too, for when the status box was missed
function report(status) {
    if (reported || !window.ytlNotifications)
        return;
    reported = true;
    var text = 'Checked ' + formatNumber(status.total)
        + (status.total === 1 ? ' channel: ' : ' channels: ');
    if (status.videos_added)
        text += formatNumber(status.videos_added)
            + (status.videos_added === 1 ? ' new video' : ' new videos');
    else
        text += 'no new videos';
    window.ytlNotifications.add(text, {url: '/youtube.com/subscriptions'});
}

function render(status) {
    var checkingIds = {};
    status.channel_ids.forEach(function(id) { checkingIds[id] = true; });

    refreshForms.forEach(function(form) {
        var type = form.querySelector('input[name="type"]').value;
        var channelInput = form.querySelector('input[name="channel_id"]');
        var checking;
        if (type === 'channel')
            checking = Boolean(checkingIds[channelInput.value]);
        else
            checking = status.checking > 0 && form.dataset.pressed === '1';
        setButtonChecking(form, checking);
        if (!checking)
            form.dataset.pressed = '';
    });

    if (status.checking > 0) {
        reported = false;
        statusBox.hidden = false;
        spinner.hidden = false;
        reloadButton.hidden = true;
        closeButton.hidden = true;
        var text = 'Checking for new videos… ' + formatNumber(status.done)
            + ' of ' + formatNumber(status.total)
            + (status.total === 1 ? ' channel' : ' channels');
        if (status.videos_added)
            text += ' · ' + formatNumber(status.videos_added) + ' added';
        statusText.textContent = text;
        progressFill.style.width =
            (status.total ? 100*status.done/status.total : 0) + '%';
    } else if (sawChecking) {
        spinner.hidden = true;
        closeButton.hidden = false;
        progressFill.style.width = '100%';
        report(status);
        if (status.videos_added) {
            statusText.textContent = 'Done. '
                + formatNumber(status.videos_added)
                + (status.videos_added === 1 ? ' video' : ' videos')
                + ' added.';
            reloadButton.hidden = false;
        } else {
            statusText.textContent = 'Done. No new videos.';
        }
    } else {
        statusBox.hidden = true;
    }
}

function poll() {
    fetch(STATUS_URL, {cache: 'no-store'})
    .then(function(response) { return response.json(); })
    .then(function(status) {
        if (status.checking > 0)
            sawChecking = true;
        render(status);
        if (status.checking > 0)
            setTimeout(poll, POLL_INTERVAL_MS);
        else
            polling = false;
    })
    .catch(function() { polling = false; });
}

function startPolling() {
    if (!polling) {
        polling = true;
        poll();
    }
}

refreshForms.forEach(function(form) {
    form.addEventListener('submit', function(e) {
        e.preventDefault();
        form.dataset.pressed = '1';
        setButtonChecking(form, true);
        fetch(form.action || window.location.href, {
            method: 'POST',
            body: new URLSearchParams(new FormData(form)),
        })
        .then(function() {
            sawChecking = true;
            reported = false;
            startPolling();
        })
        .catch(function() {
            form.dataset.pressed = '';
            setButtonChecking(form, false);
        });
    });
});

reloadButton.addEventListener('click', function() {
    window.location.reload();
});
closeButton.addEventListener('click', function() {
    statusBox.hidden = true;
    sawChecking = false;
});

var filterForm = document.getElementById('feed-filters');
if (filterForm) {
    filterForm.addEventListener('change', function() {
        filterForm.submit();
    });
}

// Remember whether the sidebar is hidden
var sidebarToggle = document.getElementById('sidebar-collapse-cbox');
if (sidebarToggle) {
    sidebarToggle.addEventListener('change', function() {
        document.cookie = 'subscriptions_sidebar_hidden='
            + (sidebarToggle.checked ? '1; max-age=315360000' : '; max-age=0')
            + '; path=/; samesite=lax';
    });
}

// Checking may already be running, e.g. after reloading the page
startPolling();
})();
