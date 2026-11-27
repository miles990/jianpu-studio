"""The notation symbol registry: the single source of truth for every mark.

Palette, canvas, export words, the source whitelist, the LilyPond prelude and both
interface languages all read this table (data in ``symbols_data.py``). A symbol's
``kind`` decides where a note stores it:

* ``point`` / ``pre`` -> ``Note.marks`` (``pre`` words print before the note head)
* ``span``            -> ``Note.spans_start`` / ``Note.spans_end`` (paired by family)
* ``bar``             -> ``Note.bar_start`` / ``Note.bar_end`` (part 0, bar boundaries)

``line.tie`` and ``tuplet.3`` are structural note fields (``tie``, ``tuplet``) and the
two ``composite`` symbols combine grace notes with a slide; they are listed for the
palette but are not stored as ids.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .i18n import N_, tr
from .symbols_data import ROWS

# ``\name`` words accepted in 0.3.x sources; the registry adds to this set.
V1_WORDS = frozenset({
    "p", "pp", "ppp", "mp", "mf", "f", "ff", "fff", "sfz", "fermata", "bar", "break", "noBreak", "pageBreak",
    "noPageBreak", "breathe", "accent", "staccato", "tenuto", "marcato", "staccatissimo", "flageolet", "trill",
    "prall", "mordent", "turn", "upbow", "downbow"})
# LilyPond built-in dynamics that a hand-written source may use next to the registry words (tech.md §2.4).
SOURCE_BUILTINS = frozenset({"fp", "sf", "sfp", "fz", "rfz", "cresc", "dim", "decresc"})

CATEGORY_NAMES = {
    "dynamic": N_("力度"), "barline": N_("小節線與反覆"), "navigation": N_("跳躍記號"), "line": N_("線條"),
    "articulation": N_("演奏法"), "ornament": N_("裝飾音"), "tempo": N_("速度表情"), "pedal": N_("踏板"),
    "technique": N_("民樂技法"), "fingering": N_("指法、弓法與弦序"),
}  # catalog sources; show tr(value)
GROUP_NAMES = {
    "common": N_("通用"), "erhu": N_("二胡"), "dizi": N_("笛子"), "guzheng": N_("古箏"), "pipa": N_("琵琶"),
    "ruan": N_("中阮"), "hulusi": N_("葫蘆絲"), "yangqin": N_("揚琴"),
}  # catalog sources; show tr(value)

# Line families: a start and an end of the same family pair up; ids inside a family share an end word.
SPAN_FAMILIES = {
    "hairpin.cresc": "hairpin", "hairpin.dim": "hairpin", "hairpin.cresc_text": "hairpin", "hairpin.dim_text": "hairpin",
    "line.slur": "slur", "line.8va": "ottava", "line.8vb": "ottava", "orn.trill_span": "trill", "line.wavy": "trill",
    "hulusi.xuzhi": "trill", "tempo.rit_span": "text", "tempo.accel_span": "text", "slide.gliss": "gliss",
}
STRUCTURAL = {"line.tie": "tie", "tuplet.3": "tuplet"}
# Marks a 0.3.x project could put on a rest (v1 allowed any articulation there). They stay
# valid on rests so old files open unchanged, but the palette does not offer them for rests.
LEGACY_REST_MARKS = frozenset({"art.staccato", "art.tenuto", "art.accent", "art.marcato", "art.staccatissimo",
                               "orn.trill", "orn.mordent", "orn.turn", "harmonic"})
# Bar items that change the playback order (written in every part); the rest only print.
STRUCTURE_BAR_ITEMS = ("bar.repeat_start", "bar.repeat_end", "bar.repeat_both", "volta.1", "volta.2", "volta.3",
                       "volta.4", "volta.final", "volta.end", "nav.segno", "nav.tocoda", "nav.ds", "nav.dc", "nav.fine")
BAR_START_ITEMS = frozenset({"bar.repeat_start", "volta.1", "volta.2", "volta.3", "volta.4", "volta.final",
                             "nav.segno", "nav.coda_sign", "nav.segno_sign"})
LINE_STYLES = ("bar.dashed", "bar.double", "bar.final")
JUMPS = ("nav.ds", "nav.dc")


@dataclass(frozen=True)
class Symbol:
    id: str
    name: str             # Traditional catalog source; display with tr(name) or .display_name
    category: str
    group: str
    keys: str             # search keys (pinyin and English)
    kind: str             # point | pre | span | bar | composite
    place: str            # above | below | left | right | between | bar
    canvas: str           # "text:<tc>|<sc>" drawn as text, or "shape:<name>" drawn by the canvas
    export: str           # jianpu-ly words, mini-language documented in wow/tech.md §2
    slot: str             # conflict group: at most one symbol per slot on a note ("" = none)
    on_rest: bool
    play: str             # playback effect (documentation for tooltips)
    confidence: str       # high | medium | low (low = hidden by default, needs teacher confirmation)
    jpword: str
    note: str
    macros: dict = field(default_factory=dict, compare=False)   # app prelude: name -> def or (tc, sc)
    midi: dict = field(default_factory=dict, compare=False)     # MIDI-only twins (never user-writable)

    @property
    def display_name(self) -> str:
        return tr(self.name)

    @property
    def hidden(self) -> bool:
        """Low-confidence printed forms stay in the catalog but are not offered by default."""
        return self.confidence == "low"

    @property
    def storage(self) -> str:
        if self.id in STRUCTURAL:
            return STRUCTURAL[self.id]
        return {"point": "marks", "pre": "marks", "span": "spans", "bar": "bar"}.get(self.kind, "composite")

    @property
    def family(self) -> str:
        return SPAN_FAMILIES.get(self.id, "")

    @property
    def bar_edge(self) -> str:
        """'start' or 'end': the bar boundary a bar item belongs to."""
        if self.kind != "bar":
            return ""
        return "start" if self.id in BAR_START_ITEMS else "end"

    @property
    def print_only(self) -> bool:
        """Bar items that print but do not change the playback order."""
        return self.kind == "bar" and self.id not in STRUCTURE_BAR_ITEMS and self.id not in LINE_STYLES

    def canvas_text(self, variant: str = "tc") -> str:
        """Text a canvas draws for ``text:`` symbols in the score's print variant ("" for shapes)."""
        if not self.canvas.startswith("text:"):
            return ""
        tc, _, sc = self.canvas[5:].partition("|")
        return sc if variant == "sc" and sc else tc

    def words(self) -> tuple[str, str]:
        """(slot, word) for point/pre marks: slot is post, pre, after or tok."""
        slot, _, word = self.export.partition(":")
        return slot, word.split(" ")[0] if slot == "after" else word

    def span_words(self, end: bool) -> tuple[str, str]:
        """(slot, word) of a span's start or end; slot is post, pre or after ("" = no word)."""
        start_word, _, end_word = self.export[len("span:"):].partition("|")
        slot, _, word = (end_word if end else start_word).partition(":")
        return (slot, word) if slot in ("post", "pre", "after") else ("", "")

    def bar_word(self) -> str:
        """Literal word of a simple bar item (line style or print-only mark)."""
        return self.export.split(":", 2)[2] if self.export.startswith(("bar:start:", "bar:end:")) else ""


SYMBOLS: dict[str, Symbol] = {row["id"]: Symbol(**row) for row in ROWS}
ORDER = {sid: index for index, sid in enumerate(SYMBOLS)}


def get(symbol_id: str) -> Symbol:
    return SYMBOLS[symbol_id]


def symbols(*, include_hidden: bool = False, category: str | None = None, group: str | None = None) -> list[Symbol]:
    """Catalog in registry order; low-confidence symbols only when asked for."""
    return [s for s in SYMBOLS.values() if (include_hidden or not s.hidden)
            and (category is None or s.category == category) and (group is None or s.group == group)]


def search(query: str, *, include_hidden: bool = False, instrument: str = "all") -> list[Symbol]:
    """Ranked palette search: names in both scripts, pinyin by whole syllable, English id/keys.

    Pinyin matches whole syllables only (``hua`` finds 上滑音 but not 雙小節線 *shuang*);
    see ``symbols_ui.search`` for the ranking (wow/design.md §2.4).
    """
    from .symbols_ui import search as ranked
    return ranked(query, instrument=instrument, include_hidden=include_hidden)


def sort_ids(ids) -> tuple[str, ...]:
    """Registry order, duplicates removed; unknown ids keep their place at the end (validate rejects them)."""
    unique = list(dict.fromkeys(ids))
    return tuple(sorted(unique, key=lambda sid: ORDER.get(sid, len(ORDER))))


def macro_names() -> list[str]:
    return sorted({name for s in SYMBOLS.values() for name in s.macros})


# A draft print of an unfinished repeat/ending/jump (export ``draft``): the signs are printed as
# they stand -- bar lines, ending brackets, texts -- without any repeat or jump structure, so
# the score typesets while it is being built (and MIDI plays it as written).
_DRAFT_LABELS = {"One": ("1.", "1."), "Two": ("2.", "2."), "Three": ("3.", "3."), "Four": ("4.", "4."),
                 "Jie": ("結", "结")}
# ``jsDraftVoltaX`` opens a bracket; ``jsDraftVoltaXNext`` closes the one before it at the same
# moment (two separate \\set at one moment would overwrite each other).
DRAFT_MACROS = {
    **{f"jsDraftVolta{name}": tuple(f"\\set Score.repeatCommands = #'((volta \"{label}\"))" for label in labels)
       for name, labels in _DRAFT_LABELS.items()},
    **{f"jsDraftVolta{name}Next": tuple(f"\\set Score.repeatCommands = #'((volta #f) (volta \"{label}\"))"
                                        for label in labels) for name, labels in _DRAFT_LABELS.items()},
    "jsDraftVoltaEnd": "\\set Score.repeatCommands = #'((volta #f))",
}


def source_words() -> frozenset[str]:
    """``\\name`` words a jianpu source may contain: 0.3.x words, built-in dynamics and every
    word the registry exports or defines. MIDI-only twins are not included."""
    names = set(V1_WORDS) | SOURCE_BUILTINS | set(macro_names()) | set(DRAFT_MACROS)
    for s in SYMBOLS.values():
        names |= set(re.findall(r"\\([A-Za-z]+)", s.export))
    return frozenset(names)


def prelude(variant: str = "tc", parts: int = 1) -> str:
    """App-generated LilyPond definitions, inserted by the worker before the first \\score.

    User text can only reference these names (whitelisted single words); it never defines
    LilyPond or Scheme itself. ``variant`` selects Traditional or Simplified technique text.
    """
    out = ["%% ---- Jianpu Studio prelude (app-generated; not user input) ----",
           r"\layout { \context { \RhythmicStaff \consists Ottava_spanner_engraver "
           r"\consists Piano_pedal_engraver \consists Piano_pedal_align_engraver "
           r"ottavationMarkups = #ottavation-ordinals \override SustainPedalLineSpanner.staff-padding = #3.5 } }"]
    # jianpu-ly writes erhu fingering (一二三四) and ▼ as \finger \markup. Fingering uses the
    # music font (fetaText encoding) and LilyPond 2.25+ no longer falls back to a text font,
    # so these print nothing without this; size 2 matches the other technique text (P1).
    out.append(r"\layout { \context { \Voice \override Fingering.font-encoding = #'latin1 "
               r"\override Fingering.font-size = #2 } }")
    if parts > 1:
        out.append(r"\layout { \context { \Score systemStartDelimiter = #'SystemStartBrace "
                   r"\consists Span_bar_engraver \override SpanBar.bar-extent = #'(-2 . 2) } }")
    # MIDI levels: sforzandi keep the running level; the fp family leaves p/pp.
    out.append("#(set! absolute-volume-alist (append '((\"sf\" . #f) (\"fp\" . 0.55) (\"sfp\" . 0.55) "
               "(\"sfpp\" . 0.49)) absolute-volume-alist))")
    seen = set()
    for s in SYMBOLS.values():
        for name, definition in s.macros.items():
            if name not in seen:
                seen.add(name)
                text = definition if isinstance(definition, str) else definition[variant == "sc"]
                out.append(f"{name} = {text}")
    for s in SYMBOLS.values():
        out.extend(f"{name} = {definition}" for name, definition in s.midi.items())
    for name, definition in DRAFT_MACROS.items():
        out.append(f"{name} = {definition if isinstance(definition, str) else definition[variant == 'sc']}")
    out.append("%% ---- end prelude ----")
    return "\n".join(out) + "\n"


def midi_twins() -> tuple[str, ...]:
    """Macro names that the worker swaps for their ``...Midi`` twin inside MIDI staves."""
    return tuple(name[:-4] for s in SYMBOLS.values() for name in s.midi)
