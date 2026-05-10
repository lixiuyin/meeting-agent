import re

_GREETING_PATTERN = re.compile(
    r"^(hi|hello|hey|good\s*(morning|afternoon|evening))[\s!!.]*$",
    re.IGNORECASE,
)
_SMALLTALK_PATTERN = re.compile(
    r"^(thanks|thank\s*you|ok|okay|got\s*it)[\s!!.]*$",
    re.IGNORECASE,
)
# HIGH-7: CJK greetings and thanks patterns.
# fullwidth punctuation is intentional — CJK locales commonly use it.
_CJK_GREETING_PATTERN = re.compile(
    r"^(你好|您好|早上好|下午好|晚上好|嗨|哈[啰咯]|こんにちは|안녕|안녕하세요)[\s!!。！]*$",  # noqa: RUF001
)
_CJK_SMALLTALK_PATTERN = re.compile(
    r"^(谢谢|多谢|感谢|好的|明白了|知道了|收到|ありがとう|감사|감사합니다)[\s!!。！]*$",  # noqa: RUF001
)

_CJK_RANGES: tuple[tuple[str, str], ...] = (
    ("一", "鿿"),  # CJK Unified Ideographs (Han)
    ("぀", "ゟ"),  # Hiragana
    ("゠", "ヿ"),  # Katakana  # noqa: RUF001
    ("가", "힯"),  # Hangul Syllables
)
# ASCII "?" plus fullwidth U+FF1F (commonly typed in CJK locales).
_QUESTION_MARKERS: frozenset[str] = frozenset({"?", "？"})  # noqa: RUF001

_CASUAL_RESPONSES: dict[str, str] = {
    "greeting": "Hello! I'm your Meeting Agent. Ask me anything about your meetings.",
    "thanks": "You're welcome! Let me know if you have more questions.",
    "ack": "Got it! Feel free to ask about your meetings anytime.",
}


def _classify_intent(question: str) -> str:
    """Rule-based intent classifier. Returns 'casual' or 'rag'.

    - 'casual': greeting/thanks → skip retrieval, respond directly
    - 'rag': default → full pipeline
    """
    q = question.strip()
    # HIGH-7: English + CJK greeting/thanks patterns.
    if _GREETING_PATTERN.match(q) or _CJK_GREETING_PATTERN.match(q):
        return "casual"
    if _SMALLTALK_PATTERN.match(q) or _CJK_SMALLTALK_PATTERN.match(q):
        return "casual"
    # Safety net: mixed CJK+English queries with real content should not be
    # classified as casual. If the query is longer than a greeting and contains
    # question markers or is >6 CJK chars, route to RAG.
    if _is_cjk(q) and len(q) > 6:
        return "rag"
    return "rag"


def _is_cjk(text: str) -> bool:
    """Check if text contains CJK characters."""
    for ch in text:
        for lo, hi in _CJK_RANGES:
            if lo <= ch <= hi:
                return True
    return False


def _casual_response(question: str) -> str:
    """Return a canned response for casual inputs."""
    q = question.strip().lower()
    if _GREETING_PATTERN.match(q) or _CJK_GREETING_PATTERN.match(q):
        return _CASUAL_RESPONSES["greeting"]
    if _SMALLTALK_PATTERN.match(q) or _CJK_SMALLTALK_PATTERN.match(q):
        return _CASUAL_RESPONSES["thanks"]
    return _CASUAL_RESPONSES["ack"]


def _has_cjk(text: str) -> bool:
    """Return True if text contains any CJK character (Chinese/Japanese/Korean)."""
    return any(any(start <= ch <= end for start, end in _CJK_RANGES) for ch in text)


_TRIVIAL_TWO_WORD_WORDS: frozenset[str] = frozenset(
    {
        "ok",
        "okay",
        "thanks",
        "thank",
        "you",
        "got",
        "it",
        "yes",
        "yeah",
        "yep",
        "nope",
        "cool",
        "nice",
        "great",
        "fine",
        "sure",
        "right",
        "yo",
        "hey",
        "hi",
        "hello",
        "bye",
        "please",
        "sorry",
        "wow",
        "hmm",
        "lol",
        "haha",
    }
)


def _is_trivially_short(question: str) -> bool:
    """Return True if the question is trivially short small-talk that can skip RAG.

    Catches inputs that escaped the regex classifier (e.g. "yo", "thanks bro").
    Only flags exactly-2-word inputs where BOTH words are trivial small-talk
    tokens — this prevents blocking legitimate 2-word RAG queries like
    "summarize meeting" or "find budget".
    Returns False for:
    - Empty / slash-command inputs
    - Questions (contain ASCII or fullwidth question mark)
    - CJK text — whitespace tokenization under-counts tokens for languages
      without space delimiters, so we never short-circuit them here
    - Any 2-word input containing a non-trivial word (verb, noun, etc.)
    """
    text = question.strip()
    if not text or text.startswith("/"):
        return False
    if any(marker in text for marker in _QUESTION_MARKERS):
        return False
    if _has_cjk(text):
        return False
    words = text.split()
    return len(words) == 2 and all(w.lower() in _TRIVIAL_TWO_WORD_WORDS for w in words)
