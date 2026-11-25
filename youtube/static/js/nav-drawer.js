// The ☰ button in the header opens the side menu. It is a link to the menu
// page so it still works without JavaScript; here it becomes a button that
// opens the <dialog> instead. The dialog closes with Escape by itself and
// gives focus back to the button.
(function() {
    let drawer = document.getElementById('nav-drawer');
    let opener = document.getElementById('menu-link');
    if (!drawer || !opener || typeof drawer.showModal !== 'function')
        return;

    opener.setAttribute('role', 'button');
    opener.setAttribute('aria-haspopup', 'dialog');
    opener.setAttribute('aria-controls', 'nav-drawer');
    opener.setAttribute('aria-expanded', 'false');

    function open() {
        drawer.showModal();
        opener.setAttribute('aria-expanded', 'true');
    }
    opener.addEventListener('click', function(e) {
        e.preventDefault();
        open();
    });
    // a link only reacts to Enter, a button also to Space
    opener.addEventListener('keydown', function(e) {
        if (e.key === ' ') {
            e.preventDefault();
            open();
        }
    });

    drawer.addEventListener('close', function() {
        opener.setAttribute('aria-expanded', 'false');
    });
    drawer.querySelector('.drawer-close').addEventListener('click', function() {
        drawer.close();
    });
    // The panel fills the dialog, so a click landing on the dialog itself
    // was on the dimmed backdrop beside it
    drawer.addEventListener('click', function(e) {
        if (e.target === drawer)
            drawer.close();
    });

    // The filter toggle is a <label> for a hidden checkbox, which the
    // keyboard can't reach by itself
    let filterToggle = document.querySelector(
        '#site-search .filter-dropdown-toggle-button[role="button"]');
    let filterBox = document.getElementById('filter-dropdown-toggle-cbox');
    if (filterToggle && filterBox) {
        let sync = function() {
            filterToggle.setAttribute('aria-expanded', String(filterBox.checked));
        };
        sync();
        filterBox.addEventListener('change', sync);
        filterToggle.addEventListener('keydown', function(e) {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                filterBox.checked = !filterBox.checked;
                sync();
            }
            else if (e.key === 'Escape' && filterBox.checked) {
                filterBox.checked = false;
                sync();
            }
        });
    }
})();
