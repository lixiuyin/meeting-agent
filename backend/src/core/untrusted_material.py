"""High-signal poisoning indicators, not a complete prompt-injection detector.

Untrusted instructions may be discussed as RAG evidence, but must not become
durable user/project assertions. Authorization is enforced independently.
"""

import re
import unicodedata

_DIRECTIVE = re.compile(
    r"(?:ignore|override|disregard|bypass).{0,80}(?:instruction|system|policy|permission|scope|rule)"
    r"|(?:system|developer)\s*(?:message|prompt|override)\s*:"
    r"|</?(?:system|developer|meeting_context|user_memory)>|\[/?INST\]|<\|(?:im_start|system)\|>"
    r"|(?:save|store|remember|memorize|write).{0,60}(?:memory|memories|as a fact)"
    r"|(?:ignore|忽略|绕过|覆盖|无视).{0,40}(?:指令|规则|权限|系统|范围)"
    r"|(?:记住|写入|保存|修改).{0,40}(?:记忆|用户偏好|系统提示)"
    r"|(?:read|reveal|exfiltrate|send).{0,60}"
    r"(?:api.?key|secret|other user's|other users'|private files)",
    re.IGNORECASE | re.DOTALL,
)


def has_embedded_directive(text: object) -> bool:
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = "".join(c for c in normalized if unicodedata.category(c) != "Cf")
    return bool(_DIRECTIVE.search(normalized))


def is_separate_clean_quote(quote: str | None, source: str | None) -> bool:
    """Allow review of an exact standalone paragraph, never certify its truth."""
    if not quote or not source or has_embedded_directive(quote):
        return False

    def normalize(text: str) -> str:
        return " ".join(text.split())

    paragraphs = re.split(r"\n\s*\n", source)
    matches = [part for part in paragraphs if normalize(part) == normalize(quote)]
    return len(matches) == 1


def clean_review_paragraph(quote: str | None, source: str | None) -> str | None:
    """Restore the surrounding evidence of a unique quote for pending review.

    The whole paragraph must be free of detected directives. This never
    authorizes confirmation and rejects quotes repeated anywhere in the source.
    """
    if not quote or not source or has_embedded_directive(quote):
        return None
    normalized_quote = " ".join(quote.split())
    paragraphs = re.split(r"\n\s*\n", source)
    matches = [part for part in paragraphs if normalized_quote in " ".join(part.split())]
    if len(matches) != 1:
        return None
    paragraph = matches[0]
    if (
        has_embedded_directive(paragraph)
        or " ".join(paragraph.split()).count(normalized_quote) != 1
    ):
        return None
    return paragraph.strip()
