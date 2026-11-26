// Loads a comment's replies in place when "View replies" is opened. Uses
// events on the document because the watch page adds its comments after
// loading.
function repliesSummary(e) {
    return e.target.closest && e.target.closest('details.replies > summary');
}

document.addEventListener('click', function(e) {
    var summary = repliesSummary(e);
    if (!summary)
        return;
    var details = summary.parentElement;
    if (details.dataset.loaded)
        return;
    details.dataset.loaded = '1';
    doXhr(details.getAttribute('src') + '&slim=1', function(html) {
        details.querySelector('.comment_page').innerHTML = html;
    });
});

// Middle-click opens the replies on their own page
document.addEventListener('auxclick', function(e) {
    var summary = repliesSummary(e);
    if (summary && e.button === 1)
        window.open(summary.parentElement.getAttribute('src'));
});
