"""Small, deterministic safeguards for untrusted prompt data."""

from html import escape


def escape_prompt_data(value: object) -> str:
    """Escape structural markup without changing the underlying user text.

    Prompt guards still tell the model to treat retrieved content as data. This
    function supplies the complementary structural guarantee: untrusted text
    cannot close the XML-like section that contains it and open a fake section.
    """
    return escape("" if value is None else str(value), quote=False)
