'''Tests for the Classic/Modern/YouTube styles and the settings that only
apply to some of them'''
import re

import pytest

# youtube has to be imported before settings, like in test_util.py
import youtube
from youtube import yt_app
import settings


@pytest.fixture
def client():
    return yt_app.test_client()


def use_style(monkeypatch, ui_style, color_scheme=0):
    for name, value in (('ui_style', ui_style),
                        ('color_scheme', color_scheme)):
        monkeypatch.setattr(settings, name, value)
        monkeypatch.setitem(settings.current_settings_dict, name, value)


@pytest.mark.parametrize('theme, color_scheme', [
    (0, 1),     # Light
    (1, 1),     # Gray
    (2, 2),     # Dark
])
def test_upgrade_keeps_dark_mode(theme, color_scheme):
    upgraded = settings.upgrade_to_7({'theme': theme, 'settings_version': 6})
    assert upgraded['color_scheme'] == color_scheme
    assert upgraded['settings_version'] == 7


def test_upgrade_without_theme_keeps_default():
    upgraded = settings.upgrade_to_7({'settings_version': 6})
    assert 'color_scheme' not in upgraded


@pytest.mark.parametrize('ui_style, shown', [
    (0, {'theme', 'font'}),
    (1, {'color_scheme'}),
    (2, {'color_scheme'}),
])
def test_settings_shown_for_each_style(ui_style, shown):
    values = dict(settings.current_settings_dict, ui_style=ui_style)
    style_settings = {'theme', 'font', 'color_scheme'}
    assert {name for name in style_settings
            if settings.is_setting_shown(settings.SETTINGS_INFO[name],
                                         values)} == shown


def setting_items(html):
    '''{setting name: whether its row is hidden} from the settings page'''
    rows = re.findall(r'<li class="setting-item"(.*?)>.*?name="(\w+)"',
                      html, re.S)
    return {name: ' hidden' in attributes for attributes, name in rows}


@pytest.mark.parametrize('ui_style, hidden', [
    (0, {'color_scheme'}),
    (1, {'theme', 'font'}),
])
def test_settings_page_hides_settings_that_dont_apply(client, monkeypatch,
                                                      ui_style, hidden):
    use_style(monkeypatch, ui_style)
    html = client.get('/settings').get_data(as_text=True)
    items = setting_items(html)
    assert {name for name, is_hidden in items.items() if is_hidden} == hidden
    # hidden rows still send their value, so saving doesn't reset them
    assert 'theme' in items and 'color_scheme' in items
    assert 'data-shown-for="ui_style" data-shown-values="0"' in html


@pytest.mark.parametrize('ui_style, sheets, has_theme_css', [
    (0, [], True),
    (1, ['modern_ui.css'], False),
    (2, ['modern_ui.css', 'youtube_ui.css'], False),
])
def test_style_sheets(client, monkeypatch, ui_style, sheets, has_theme_css):
    use_style(monkeypatch, ui_style, color_scheme=2)
    html = client.get('/home').get_data(as_text=True)
    assert re.findall(r'/static/(\w+_ui\.css)', html) == sheets
    assert ('light_theme.css' in html) == has_theme_css
    assert 'data-color-scheme="dark"' in html
    # the new styles come after the page's own styles, to restyle them
    if sheets:
        assert html.index('</style>') < html.index(sheets[0])
