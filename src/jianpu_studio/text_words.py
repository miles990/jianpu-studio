"""Ready-made words a user writes above notes (data, in both scripts; round 6).

Tempo/expression words, erhu tunings (定弦: on the first note they print in the header line
next to 1=D) and bow sections, as (traditional, simplified, pinyin keys). The palette search
offers them as 「加上文字「…」」; they are the user's own text, not interface text, so they are
not translated: a user typing Simplified gets the Simplified word.
"""
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
)
# Bow sections qualify the note they stand on: never moved into the header line.
BOW_WORDS = (
    ("全弓", "全弓", "quangong 弓段"), ("上半弓", "上半弓", "shangbangong 弓段"), ("下半弓", "下半弓", "xiabangong 弓段"),
    ("弓尖", "弓尖", "gongjian 弓段"), ("弓根", "弓根", "gonggen 弓段"),
)
TEXT_WORDS = TEMPO_WORDS + TUNING_WORDS + BOW_WORDS
# Words that may print in the header line when they stand on the first note.
HEADER_PHRASES = frozenset(w for tc, sc, _ in TEMPO_WORDS + TUNING_WORDS for w in (tc, sc))


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
