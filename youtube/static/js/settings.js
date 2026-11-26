// Shows only the settings that apply to what's chosen right now, such as the
// Classic theme and font only when the Classic style is picked. The server
// renders the same thing from the saved values, so this only matters before
// saving.
(function() {
    var dependents = document.querySelectorAll('.setting-item[data-shown-for]');

    function update() {
        dependents.forEach(function(item) {
            var control = document.getElementsByName(item.dataset.shownFor)[0];
            if (!control)
                return;
            var values = item.dataset.shownValues.split(' ');
            var value = control.type === 'checkbox'
                ? (control.checked ? 'True' : 'False') : control.value;
            item.hidden = values.indexOf(value) === -1;
        });
    }

    var controlling = {};
    dependents.forEach(function(item) {
        controlling[item.dataset.shownFor] = true;
    });
    Object.keys(controlling).forEach(function(name) {
        var control = document.getElementsByName(name)[0];
        if (control)
            control.addEventListener('change', update);
    });
    update();
}());

(function() {
    var stop = document.querySelector('.server-stop');
    if (!stop)
        return;
    stop.addEventListener('click', function(e) {
        if (!confirm('Stop youtube-local? It will stay off until you start it again.'))
            e.preventDefault();
    });
}());

// "Sign in to YouTube" with a browser: says whether that browser is signed
// in, and checks again when coming back from signing in.
(function() {
    var browser = document.getElementsByName('youtube_sign_in_browser')[0];
    var mode = document.getElementsByName('youtube_sign_in')[0];
    var status = document.querySelector('.sign-in-status');
    var checkButton = document.querySelector('.sign-in-check');
    var signInLink = document.querySelector('.sign-in-controls a');
    if (!browser || !mode || !status)
        return;
    var waitingForSignIn = false;

    function usingBrowser() {
        return mode.value === '1';
    }

    function check() {
        if (!usingBrowser())
            return;
        status.classList.remove('is-signed-in');
        status.textContent = 'Checking…';
        fetch('/youtube.com/ytl-api/sign_in_status?browser=' + encodeURIComponent(browser.value),
              {cache: 'no-store'})
            .then(function(response) {
                if (!response.ok)
                    throw new Error('error ' + response.status);
                return response.json();
            })
            .then(function(result) {
                status.textContent = result.message;
                status.classList.toggle('is-signed-in', result.signed_in);
            })
            .catch(function(e) {
                status.textContent = 'Couldn\'t check: ' + e.message;
            });
    }

    checkButton.addEventListener('click', check);
    browser.addEventListener('change', check);
    mode.addEventListener('change', check);
    signInLink.addEventListener('click', function() {
        waitingForSignIn = true;
    });
    window.addEventListener('focus', function() {
        if (waitingForSignIn) {
            waitingForSignIn = false;
            check();
        }
    });
    check();
}());
