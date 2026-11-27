"""Ready-made words a user writes above notes (data, in both scripts; round 6).

Tempo/expression words, erhu tunings (定弦: on the first note they print in the header line
next to 1=D) and bow sections, as (traditional, simplified, pinyin keys). The palette search
offers them as 「加上文字「…」」; they are the user's own text, not interface text, so they are
not translated: a user typing Simplified gets the Simplified word.
"""
import re

# Tempo/expression words, erhu tunings (定弦; on the first note they print in the header line
# next to 1=D) and bow sections. Each is (traditional, simplified, pinyin keys). Search offers
# 「加上文字「…」」 for them; any other query that finds nothing can still be added as text.
TEMPO_WORDS = (
    ("中速", "中速", "zhongsu"), ("稍慢", "稍慢", "shaoman"), ("稍快", "稍快", "shaokuai"),
    ("慢板", "慢板", "manban"), ("中板", "中板", "zhongban"), ("快板", "快板", "kuaiban"),
    ("行板", "行板", "xingban"), ("柔板", "柔板", "rouban"), ("急板", "急板", "jiban"),
    ("散板", "散板", "sanban"), ("稍自由", "稍自由", "shaoziyou"), ("自由地", "自由地", "ziyoude"),
    ("深情地", "深情地", "shenqingde"), ("抒情地", "抒情地", "shuqingde"), ("優美地", "优美地", "youmeide"),
    ("歡快地", "欢快地", "huankuaide"), ("熱情地", "热情地", "reqingde"), ("如歌地", "如歌地", "rugede"),
    ("漸慢", "渐慢", "jianman"), ("漸快", "渐快", "jiankuai"), ("原速", "原速", "yuansu"),
    ("引子", "引子", "yinzi"), ("尾聲", "尾声", "weisheng"),
)
TUNING_WORDS = (
    ("定弦 1–5", "定弦 1–5", "dingxian 15 定弦"), ("15弦", "15弦", "dingxian 15 xian 定弦"),
    ("52弦", "52弦", "dingxian 52 xian 定弦"), ("63弦", "63弦", "dingxian 63 xian 定弦"),
    ("26弦", "26弦", "dingxian 26 xian 定弦"), ("41弦", "41弦", "dingxian 41 xian 定弦"),
    ("37弦", "37弦", "dingxian 37 xian 定弦"),
)
# Bow sections qualify the note they stand on: never moved into the header line.
BOW_WORDS = (
    ("全弓", "全弓", "quangong 弓段"), ("上半弓", "上半弓", "shangbangong 弓段"), ("下半弓", "下半弓", "xiabangong 弓段"),
    ("弓尖", "弓尖", "gongjian 弓段"), ("弓根", "弓根", "gonggen 弓段"),
)
TEXT_WORDS = TEMPO_WORDS + TUNING_WORDS + BOW_WORDS
# Words that may print in the header line when they stand on the first note.
HEADER_PHRASES = frozenset(w for tc, sc, _ in TEMPO_WORDS + TUNING_WORDS for w in (tc, sc))


_TUNING = re.compile(r"[1-7][1-7]弦")              # 15弦, 37弦: inner and outer open string
_TUNING_PAIR = re.compile(r"[1-7][–—~-][1-7]")      # the 1–5 of 「定弦 1–5」
_TEMPO_PHRASES = frozenset(w for tc, sc, _ in TEMPO_WORDS for w in (tc, sc))
# Tonic pitch classes of the keys the app offers, and the major scale: the erhu's open strings
# are D (inner) and A (outer), whatever the key.
_TONIC = {"C": 0, "G": 7, "D": 2, "A": 9, "E": 4, "B": 11, "F#": 6, "F": 5, "Bb": 10, "Eb": 3, "Ab": 8, "Db": 1,
          "Gb": 6}
_DEGREE = {0: 1, 2: 2, 4: 3, 5: 4, 7: 5, 9: 6, 11: 7}


def header_parts(text: str) -> tuple[list[str], list[str]] | None:
    """(tempo/expression words, tunings) when ``text`` is made only of such whole phrases --
    it then prints in the header line; None when any part is something else (a bow section,
    a section title: those stay on their note). Any tuning is recognised, not only the
    ready-made ones: 「37弦」, 「定弦 3–7」."""
    words = text.split()
    tempo, tunings = [], []
    k = 0
    while k < len(words):
        if words[k] == "定弦" and k + 1 < len(words) and _TUNING_PAIR.fullmatch(words[k + 1]):
            tunings.append(words[k] + " " + words[k + 1])
            k += 2
        elif _TUNING.fullmatch(words[k]):
            tunings.append(words[k])
            k += 1
        elif words[k] in _TEMPO_PHRASES:
            tempo.append(words[k])
            k += 1
        else:
            return None
    return (tempo, tunings) if tempo or tunings else None


def open_string_degrees(key: str, first_text: str = "") -> tuple[int, int] | None:
    """(inner, outer) open-string degrees: from a tuning written on the first note, else the
    standard D–A tuning in ``key`` (None when those are not plain degrees of the key)."""
    parts = header_parts(first_text) if first_text else None
    if parts and parts[1]:
        digits = [int(c) for c in parts[1][-1] if c.isdigit()]
        return digits[0], digits[1]
    tonic = _TONIC.get(key)
    if tonic is None:
        return None
    inner, outer = _DEGREE.get((2 - tonic) % 12), _DEGREE.get((9 - tonic) % 12)
    return (inner, outer) if inner and outer else None


def asks_for_tuning(query: str) -> bool:
    """A search (already case-folded) for the tuning: 定弦, 弦, dingxian, xian."""
    compact = query.replace(" ", "")
    return bool(compact) and (compact in "定弦" or "dingxian".startswith(compact) or compact == "xian")


def key_tunings(key: str) -> list[str]:
    """The tuning words for ``key``: 1=♭B → 「定弦 3–7」「37弦」."""
    degrees = open_string_degrees(key)
    return [f"定弦 {degrees[0]}–{degrees[1]}", f"{degrees[0]}{degrees[1]}弦"] if degrees else []


def phrases(text: str, known) -> list[str] | None:
    """``text`` split into whole phrases from ``known`` (a phrase may contain spaces, like
    「定弦 1–5」); None when some part of it is not one of them."""
    words = text.split()
    table = [tuple(p.split()) for p in known]
    out: list[str] = []
    k = 0
    while k < len(words):
        match = max((p for p in table if tuple(words[k:k + len(p)]) == p), key=len, default=None)
        if match is None:
            return None
        out.append(" ".join(match))
        k += len(match)
    return out


def contains_phrase(text: str, phrase: str) -> bool:
    """True when ``phrase`` is already in ``text`` as whole words (not just as a substring)."""
    words, target = text.split(), phrase.split()
    return bool(target) and any(words[k:k + len(target)] == target for k in range(len(words)))
