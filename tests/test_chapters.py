'''Tests for reading chapters from a video's description'''
import pytest

# youtube has to be imported before settings, like in test_util.py
import youtube
from youtube.chapters import parse_chapters, parse_line


def starts_and_titles(chapters):
    return [(chapter['start'], chapter['title']) for chapter in chapters]


def test_chapters_from_description():
    description = '''This week in AI.

00:00 AI news intro
01:03 Budget3D
02:18 Reshoot 4D
03:15 SplatAnything
04:21 Open-H3-Regenerate-2K IC

Follow me: https://example.com'''
    chapters = parse_chapters(description, 600)
    assert starts_and_titles(chapters) == [
        (0, 'AI news intro'),
        (63, 'Budget3D'),
        (138, 'Reshoot 4D'),
        (195, 'SplatAnything'),
        (261, 'Open-H3-Regenerate-2K IC'),
    ]
    assert [chapter['time_text'] for chapter in chapters] == [
        '0:00', '1:03', '2:18', '3:15', '4:21']


@pytest.mark.parametrize('line, expected', [
    ('0:00 Intro', (0, 'Intro')),
    ('1:02:03 The end', (3723, 'The end')),
    ('90:15 Long one', (5415, 'Long one')),
    ('- 1:03 - Budget3D', (63, 'Budget3D')),
    ('[1:03] Budget3D', (63, 'Budget3D')),
    ('(1:03): Budget3D', (63, 'Budget3D')),
    ('2. 1:03 | Budget3D', (63, 'Budget3D')),
    ('1:03 - 2:18 Budget3D', (63, 'Budget3D')),
    ('Budget3D 1:03', (63, 'Budget3D')),
    ('Budget3D - (1:03)', (63, 'Budget3D')),
    ('Budget3D ..... 1:03', (63, 'Budget3D')),
    ('1:03 3D printing: a guide', (63, '3D printing: a guide')),
    ('1:03', None),                           # no title
    ('1:75 Not a time', None),
    ('See 1:03 for the best part', None),     # in the middle of a sentence
    ('Open 9:00am to 5:00pm', None),
    ('Just some text', None),
])
def test_parse_line(line, expected):
    assert parse_line(line) == expected


@pytest.mark.parametrize('description', [
    None,
    '',
    'No timestamps here',
    '0:00 Intro\n1:03 Only two',
    # highlights, not chapters: doesn't start at 0:00
    '0:30 Funny\n1:03 Funnier\n2:18 Funniest',
])
def test_no_chapters(description):
    assert parse_chapters(description, 600) == []


def test_skips_timestamps_that_dont_fit():
    description = '''0:00 Intro
1:03 First
0:45 Goes back
2:18 Second
30:00 After the end of the video'''
    assert starts_and_titles(parse_chapters(description, 600)) == [
        (0, 'Intro'), (63, 'First'), (138, 'Second')]
    # without a known length, nothing is after the end
    assert len(parse_chapters(description)) == 4
