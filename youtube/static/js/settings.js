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
            item.hidden = values.indexOf(control.value) === -1;
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
