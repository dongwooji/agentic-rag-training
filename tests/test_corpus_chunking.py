"""Source text conservation and existing word-based chunking contracts."""
import pytest

from src.literature.corpus import _chunk_section
from src.literature.jats import SectionText

PARAMS = dict(target_words=350, minimum_words=80, maximum_words=550, overlap_words=60)


def words(count, prefix='word'):
    return ' '.join(f'{prefix}{i:04d}' for i in range(count))


def chunk(paragraphs, **changes):
    return _chunk_section(SectionText('Results', tuple(paragraphs)), **(PARAMS | changes))


def without_overlap(chunks, overlap=60):
    result = []
    previous = []
    for item in chunks:
        values = item['text'].split()
        shared = next((n for n in range(min(overlap, len(previous), len(values)), 0, -1)
                       if previous[-n:] == values[:n]), 0)
        result.extend(values[shared:])
        previous = values
    return result


@pytest.mark.parametrize('length', [1, 40, 59, 60, 61, 79, 80, 349, 350, 351, 549, 550, 551, 1100])
def test_single_section_preserves_every_source_word(length):
    source = words(length)
    actual = chunk([source])
    assert actual
    assert without_overlap(actual) == source.split()
    assert all(0 < len(c['text'].split()) <= 610 for c in actual)
    assert all(c['paragraph_start'] == c['paragraph_end'] == 1 for c in actual)


@pytest.mark.parametrize('first_length', [1, 40, 60])
def test_short_initial_paragraph_survives_large_following_paragraph(first_length):
    # The initial buffer must flush even though it is at most overlap_words.
    source = [words(first_length, 'initial'), words(550, 'following')]
    actual = chunk(source)
    assert without_overlap(actual) == ' '.join(source).split()
    assert actual[0]['text'].split() == source[0].split()
    assert actual[0]['paragraph_start'] == actual[0]['paragraph_end'] == 1
    assert max(c['paragraph_end'] for c in actual) == 2
    assert max(len(c['text'].split()) for c in actual) <= 610


def test_audit_discussion_path_preserves_initial_sixty_words():
    source = [words(60, 'initial'), words(500, 'following'), words(25, 'tail')]
    actual = chunk(source)
    assert without_overlap(actual) == ' '.join(source).split()
    assert actual[0]['text'].split() == source[0].split()


def test_several_short_paragraphs_form_one_short_section():
    source = [words(10, 'a'), words(15, 'b'), words(20, 'c')]
    assert chunk(source) == [{'text': '\n\n'.join(source), 'paragraph_start': 1, 'paragraph_end': 3}]


def test_overlap_only_tail_does_not_create_a_duplicate_chunk():
    actual = chunk([words(350)])
    assert actual == [{'text': words(350), 'paragraph_start': 1, 'paragraph_end': 1}]


def test_full_sized_next_unit_discards_overlap_only_buffer_not_source():
    source = [words(350, 'a'), words(550, 'b')]
    actual = chunk(source)
    assert [c['text'] for c in actual] == source
    assert without_overlap(actual) == ' '.join(source).split()


def test_normal_long_section_keeps_existing_overlap_and_paragraph_ranges():
    a, b, c = words(200, 'a'), words(200, 'b'), words(100, 'c')
    assert chunk([a, b, c]) == [
        {'text': a + '\n\n' + b, 'paragraph_start': 1, 'paragraph_end': 2},
        {'text': ' '.join(b.split()[-60:]) + '\n\n' + c, 'paragraph_start': 2, 'paragraph_end': 3},
    ]


def test_small_new_tail_merges_without_repeating_overlap():
    source = [words(400, 'a'), words(30, 'b')]
    actual = chunk(source)
    assert actual == [{'text': '\n\n'.join(source), 'paragraph_start': 1, 'paragraph_end': 2}]


def test_maximum_plus_overlap_tail_merge_policy_is_unchanged():
    source = [words(550, 'a'), words(60, 'b')]
    actual = chunk(source)
    assert len(actual) == 1 and len(actual[0]['text'].split()) == 610
    assert without_overlap(actual) == ' '.join(source).split()


def test_new_tail_over_merge_limit_is_not_discarded():
    source = [words(550, 'a'), words(61, 'b')]
    actual = chunk(source)
    assert len(actual) == 2 and len(actual[-1]['text'].split()) == 121
    assert without_overlap(actual) == ' '.join(source).split()


def test_zero_overlap_still_preserves_short_source():
    assert chunk([words(1)], overlap_words=0)[0]['text'] == words(1)


def test_actual_overlap_prefix_can_be_shorter_than_configured_overlap():
    # Exercise the generic function with a high target so the short prefix is
    # still present at final flush; only the carried 40 words may be stripped.
    source = [words(40, 'a'), words(90, 'b')]
    actual = chunk(source, target_words=150, minimum_words=100, maximum_words=100)
    assert without_overlap(actual) == ' '.join(source).split()


def test_empty_section_does_not_generate_an_empty_chunk():
    assert chunk([]) == []
    assert chunk(['', '  ']) == []
