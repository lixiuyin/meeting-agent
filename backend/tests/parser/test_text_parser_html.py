import pytest

from src.services.parser.text_parsers import _strip_html_tags


@pytest.mark.parametrize("tag", ["script", "SCRIPT", "style"])
def test_plain_text_excludes_active_elements_with_spaced_end_tags(tag):
    assert (
        _strip_html_tags(f"Visible <{tag}>hidden instruction</{tag} \n> content")
        == "Visible content"
    )


def test_similarly_named_visible_elements_are_not_dropped():
    assert _strip_html_tags("<scripture>Visible text</scripture>") == "Visible text"


def test_html_end_tag_attributes_do_not_expose_script_text():
    text = _strip_html_tags("Visible <script>hidden</script\t\n bar> content")
    # Older supported stdlib parsers conservatively discard the remainder after
    # this malformed end tag. Neither behavior may expose the script contents.
    assert text in {"Visible", "Visible content"}
