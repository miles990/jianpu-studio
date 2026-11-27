"""CLI worker also available in the frozen executables (Windows/macOS)."""

import os
import re
import sys
from pathlib import Path

from . import symbols
from .engine import validate_source
from .i18n import LANGUAGES, N_, TRADITIONAL, catalog
from .model import DIRECTIVE, atomic_write
from .musicxml import read_xml
from .vendor import jianpu_ly

# Pango/fontconfig family lists: the bundled Noto font comes first, then the
# usual system CJK fonts on Windows, macOS and Linux. The bundled Traditional
# Chinese Noto font contains the full CJK glyph set, so Simplified text never
# falls back to missing glyphs; system SC fonts are listed only as a last
# resort for developer installs without the bundled font.
SERIF_FONTS = ("C059, Noto Serif CJK TC, Noto Serif TC, PMingLiU, MingLiU, Songti TC, STSong, "
               "Microsoft JhengHei, PingFang TC, Noto Serif CJK SC, SimSun, Songti SC, "
               "Microsoft YaHei, serif")
SANS_FONTS = ("Noto Sans CJK TC, Noto Sans TC, Microsoft JhengHei, PingFang TC, Heiti TC, "
              "Noto Sans CJK SC, Microsoft YaHei, PingFang SC, Heiti SC, sans-serif")

# jianpu-ly adds this block when "/Library/Fonts/Arial Unicode.ttf" exists on
# the converting machine (typical macOS). set-global-fonts was removed in
# LilyPond 2.25, and our own font settings below replace it on every version.
_LEGACY_FONT_BLOCK = re.compile(r"#\(define fonts\s*\(set-global-fonts\b")
_LEGACY_TEMPO = "tempoWholesPerMinute = #(ly:make-moment 84 4)"


def _remove_legacy_fonts(text: str) -> str:
    while match := _LEGACY_FONT_BLOCK.search(text):
        depth, end = 0, None
        for index in range(match.start() + 1, len(text)):
            depth += {"(": 1, ")": -1}.get(text[index], 0)
            if depth == 0:
                end = index + 1
                break
        if end is None:
            raise ValueError("Unbalanced jianpu-ly font block")
        text = text[:match.start()] + text[end:]
    return text


def adapt_output(result: str, minor_version: int, paper_size: str, fonts: tuple[str, str] | None = None) -> str:
    """Make jianpu-ly's LilyPond text valid for the selected LilyPond version."""
    result = _remove_legacy_fonts(result)
    if minor_version >= 25:
        # 2.25+ expects an exact rational number of whole notes per minute
        # rather than a moment; 84 quarter notes = 21 whole notes. Without
        # this, LilyPond ignores the value (an explicit \tempo still applies).
        result = result.replace(_LEGACY_TEMPO, "tempoWholesPerMinute = #21 ")
    result += '\n\\paper { #(set-paper-size "' + paper_size + '") }\n'
    # property-defaults.fonts exists since 2.24 and replaces set-global-fonts.
    serif, sans = fonts or (SERIF_FONTS, SANS_FONTS)
    result += ('\\paper {\n  property-defaults.fonts.serif = "' + serif + '"\n'
               '  property-defaults.fonts.sans = "' + sans + '"\n}\n')
    # No "LilyPond v2.26.0" footer on the user's pages.
    result += '\\header { tagline = ##f }\n'
    return result


def read_options(source: str) -> dict:
    """Print options a visual score writes as its first line (``% jianpu-studio: print=sc sloppy-bars``)."""
    first = source.lstrip("\ufeff").split("\n", 1)[0]
    if not first.startswith(DIRECTIVE):
        return {}
    options = {}
    for word in first[len(DIRECTIVE):].split():
        key, _, value = word.partition("=")
        options[key] = value or True
    out = {"variant": "sc" if options.get("print") == "sc" else "tc", "sloppy": options.get("sloppy-bars") is True}
    if options.get("draft"):
        # a draft print, with the interface language its 「草稿」 mark is written in
        out["draft"] = options["draft"] if options["draft"] in LANGUAGES else TRADITIONAL
    return out


def part_count(source: str) -> int:
    """Number of parts, split exactly like upstream (``\\sNextPart\\s``), ignoring comments."""
    text = "\n".join(line.split("%", 1)[0] for line in source.splitlines())
    return len(re.findall(r"(?<=\s)NextPart(?=\s)", " " + text + " ")) + 1


def insert_prelude(ly: str, variant: str = "tc", parts: int = 1) -> str:
    """App-owned macro definitions before the first \\score (tech.md §3.2a/d)."""
    index = ly.index("\\score {")
    return ly[:index] + symbols.prelude(variant, parts) + "\n" + ly[index:]


_FINE = "\\volta 2 \\fine \\volta 1"


def fix_fine(ly: str) -> str:
    """Brace the music after upstream's ``Fine`` (tech.md P2).

    jianpu-ly writes Fine as ``\\volta 2 \\fine \\volta 1`` followed by the rest of the
    block WITHOUT braces, so ``\\volta 1`` covers one note only: unfolded MIDI plays past
    Fine and LilyPond warns "found music after \\fine". Wrap the rest of the enclosing block,
    skipping strings and comments while counting braces.
    """
    position = 0
    while (index := ly.find(_FINE, position)) >= 0:
        start = index + len(_FINE)
        depth, k = 0, start
        while k < len(ly):
            if ly.startswith("%{", k):
                k = ly.index("%}", k) + 2
                continue
            c = ly[k]
            if c == '"':
                k = ly.index('"', k + 1) + 1
                continue
            if c == "%":
                k = ly.index("\n", k)
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                if depth == 0:
                    break
                depth -= 1
            k += 1
        ly = ly[:start] + " {" + ly[start:k] + "} " + ly[k:]
        position = start
    return ly


def _block_end(ly: str, k: int) -> int:
    """Index just past the ``{ ... }`` block opening at ``ly[k]``, skipping strings."""
    depth = 0
    while k < len(ly):
        c = ly[k]
        if c == '"':
            k = ly.index('"', k + 1)
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return k + 1
        k += 1
    raise ValueError("Unbalanced afterGrace block")


_AFTER_GRACE = "\\afterGrace {"
_TEXT_SCRIPTS = re.compile(r'(?:\s*[_^]"[^"]*")+')


def attach_after_grace_text(ly: str) -> str:
    """Move the text written on a note with an after-grace onto that note.

    Upstream wraps only the note in ``\\afterGrace { note } { graces }`` and leaves the
    note's ``^"text"`` after the block, where LilyPond finds nothing to attach it to
    ("Unattached TextScriptEvent") and prints it over the next note.
    """
    position = 0
    while (index := ly.find(_AFTER_GRACE, position)) >= 0:
        main_open = index + len(_AFTER_GRACE) - 1
        main_end = _block_end(ly, main_open)
        grace_open = main_end + len(ly[main_end:]) - len(ly[main_end:].lstrip())
        position = main_end
        if not ly.startswith("{", grace_open):
            continue
        grace_end = _block_end(ly, grace_open)
        texts = _TEXT_SCRIPTS.match(ly, grace_end)
        if texts:
            ly = (ly[:main_end - 1] + texts[0].strip() + " " + ly[main_end - 1:grace_end]
                  + ly[texts.end():])
    return ly


# 8va/8vb sound ±12 in MIDI. Upstream places the line words wherever the notes put them --
# inside a \times or \afterGrace block when the line starts or ends there -- so the rewrite
# must not open or close braces (that re-nests tuplets and grace blocks). \transposition is a
# context setting: it changes the sounding pitch from that moment on without any nesting.
_OTTAVA_MIDI = {"\\jsOttavaUp": "\\transposition c''", "\\jsOttavaDown": "\\transposition c",
                "\\jsOttavaEnd": "\\transposition c'"}


_SEGNO_BEFORE_KEY = re.compile(r"\\repeat segno (\d+) \{ (\\transpose \S+ \S+ \{)")
_STRINGS = re.compile(r'("(?:[^"\\]|\\.)*")')


def _without_bar_checks(text: str) -> str:
    """``text`` with its bar checks (`` | ``, outside strings) removed."""
    return "".join(piece if index % 2 else re.sub(r"(?<=\s)\|(?=\s)", " ", piece)
                   for index, piece in enumerate(_STRINGS.split(text)))


def midi_rewrite(ly: str) -> str:
    """Inside MIDI staves only: 8va/8vb sound transposed; sforzandi become velocity-only twins.

    A D.C. return starts at the first note, so upstream opens ``\\repeat segno`` before the
    MIDI staff's key wrapper ``\\transpose c X {``; the jump's closing brace then closes the
    key wrapper instead, and every bar after the D.C. joined the repeated body (played on
    both passes). Opening the key wrapper first restores the nesting the braces were written for.

    With a pickup, unfolding a repeat or Segno that starts on it replays the pickup where a bar
    begins, so every later bar check fails ("bar check failed at: 1/4") although the timing is
    right. Bar checks mean nothing to MIDI (the printed staff keeps them), so a MIDI staff with a
    pickup drops them.
    """
    twins = "|".join(sorted(symbols.midi_twins(), key=len, reverse=True))
    def fix(match):
        text = _SEGNO_BEFORE_KEY.sub(r"\2 \\repeat segno \1 {", match.group(0))
        if "\\partial" in text:
            text = _without_bar_checks(text)
        for word, replacement in _OTTAVA_MIDI.items():
            text = text.replace(word, replacement)
        return re.sub(r"\\(" + twins + r")(?![A-Za-z])", r"\\\1Midi", text) if twins else text
    return re.sub(r"(?s)% === BEGIN MIDI STAFF ===.*?% === END MIDI STAFF ===", fix, ly)


# Print engraving choices shared with the canvas (engraving.py): one-line italic jump texts
# ("D.S. al Coda", "D.C. al Fine"; LilyPond's default reads "D.S. al ⊕ e poi la Coda" on two
# lines), "To Coda ⊕" at the jump-off and "⊕ Coda" where the coda starts, the key mark clear
# of the tempo, dynamics that take horizontal room (no "mfsfz"), and a ragged last line.
#
# Where the signs sit, as on the canvas (checked by tests/test_screen_print_signs.py):
# * D.S./D.C./Fine texts (JumpScript) above the staff -- above the top part of a duet -- ending
#   at their bar line; LilyPond's default is below the bottom staff, where they ran into
#   hairpin ends.
# * "To Coda ⊕" (CodaMark) ends at its bar line instead of straddling it.
# * The metre is in the header line (1=D 4/4 ...), as jianpu prints it: no stacked time
#   signature on the number staff (a five-line staff printed with it keeps its own).
# * Ornaments (tr, mordents, turns) are set at the size of the other signs above a number
#   (the full-size tr was wider than its number and reached the bar line before its note);
#   octave dots, bowings and fermatas keep their size.
ENGRAVING = r"""
#(define (js-jump-text context return-count marks)
   (let* ((start (car marks)) (end (cadr marks)) (next (caddr marks))
          (head (if start "D.S." "D.C.")))
     (make-italic-markup (cond (next (string-append head " al Coda"))
                               (end (string-append head " al Fine"))
                               (else head)))))
#(for-each
  (lambda (name)
    (for-each
     (lambda (key)
       (let ((entry (assoc key default-script-alist)))
         (if (and entry (not (assq 'font-size (cdr entry))))
             (set-cdr! entry (acons 'font-size -2 (cdr entry))))))
     (list name (symbol->string name))))
  '(trill prall mordent prallprall prallmordent upprall downprall turn reverseturn))
#(define (js-coda-mark number context)
   (make-line-markup (list (make-italic-markup "To Coda") (make-coda-markup))))
#(define (js-wrap-lines layout props text width)
   "TEXT as lines no wider than WIDTH: broken after the last space that fits, or between two
characters when none does (a title of Han characters or one long word)."
   (let ((wide? (lambda (s) (> (interval-length (ly:stencil-extent (interpret-markup layout props s) X)) width)))
         (trim (lambda (s) (string-trim-both s #\space))))
     (let loop ((rest (trim text)) (lines '()))
       (if (or (string-null? rest) (not (wide? rest)))
           (reverse (if (string-null? rest) lines (cons rest lines)))
           (let find ((n 1))
             (if (and (< n (string-length rest)) (not (wide? (substring rest 0 (1+ n)))))
                 (find (1+ n))
                 (let* ((space (string-rindex rest #\space 0 (min (1+ n) (string-length rest))))
                        (cut (if (and space (> space 0)) space n)))
                   (loop (trim (substring rest cut)) (cons (trim (substring rest 0 cut)) lines)))))))))
#(define-markup-command (js-wrapped layout props align share text) (number? number? string?)
   "TEXT within SHARE of the page's line width, wrapped onto more lines; text that would need
more than three lines is set smaller (down to half size) to keep the title block compact.
ALIGN is the lines' alignment (-1 left, 0 centre, 1 right)."
   (let* ((width (* share (ly:output-def-lookup layout 'line-width)))
          (column (cond ((= align 0) make-center-column-markup) ((> align 0) make-right-column-markup)
                        (else make-left-column-markup))))
     (let try ((shrink 0))
       (let* ((sized (prepend-alist-chain 'font-size (- (chain-assoc-get 'font-size props 0) shrink) props))
              (lines (js-wrap-lines layout sized text width)))
         (if (and (> (length lines) 3) (< shrink 6))
             (try (+ shrink 1.5))
             (interpret-markup layout sized (column lines)))))))
\layout {
  \context { \Score
    dalSegnoTextFormatter = #js-jump-text
    codaMarkFormatter = #js-coda-mark
    \omit MetronomeMark
    \override RehearsalMark.padding = #1.6
    \override JumpScript.font-size = #0
    \override JumpScript.direction = #UP
    \override JumpScript.padding = #1.2
    \override CodaMark.self-alignment-X = #RIGHT
    \override CodaMark.font-size = #0
    \override CodaMark.padding = #1.2
  }
  \context { \RhythmicStaff
    \omit TimeSignature
  }
  \context { \Voice
    \override DynamicText.extra-spacing-width = #'(-0.4 . 0.6)
    \override DynamicLineSpanner.padding = #1.2
    \override Fingering.script-priority = #-100
    \override Script.padding = #0.8
  }
}
\paper { ragged-last = ##t }
"""
_BAR_NUMBERS = re.compile(r"\\override Score\.BarNumber(?:\.| #')break-visibility = #center-visible")
_BAR_EVERY = re.compile(r"\\set Score\.barNumberVisibility = #\(every-nth-bar-number-visible \d+\)")
_CODA_LABEL = '\\sectionLabel "Coda"'


_HEADER_FIELD = re.compile(r'^(title|composer)="([^"\n]*)"$', re.M)
# (alignment, share of the line width): the title is centred on the whole line; the composer
# is right-aligned in the right half (the title page's centre slot is the instrument's).
_HEADER_ALIGN = {"title": "#0 #0.98", "composer": "#1 #0.48"}


def engraving_rewrite(ly: str) -> str:
    """Bar numbers only at the start of a line (mid-line numbers read like stray notes in
    jianpu), the coda start labelled "⊕ Coda" (the canvas draws the same), and a title or
    composer too long for one line wrapped onto more lines (LilyPond would run it off the page)."""
    ly = _HEADER_FIELD.sub(lambda m: f'{m[1]} = \\markup \\js-wrapped {_HEADER_ALIGN[m[1]]} "{m[2]}"'
                           if m[2] else m[0], ly)
    ly = _BAR_NUMBERS.sub(lambda m: "\\override Score.BarNumber.break-visibility = #begin-of-line-visible", ly)
    ly = _BAR_EVERY.sub(lambda m: "\\set Score.barNumberVisibility = #first-bar-number-invisible-and-no-parenthesized-bar-numbers", ly)
    return ly.replace(_CODA_LABEL, '\\sectionLabel \\markup { \\musicglyph "scripts.coda" "Coda" }')


# Printed on every page of a draft (an unfinished repeat/jump printed as plain signs), so the
# paper cannot be taken for the finished score.
DRAFT_MARK = N_("反覆／跳躍尚未完成：記號照印，但不反覆、不跳躍")
DRAFT_WORD = N_("草稿")


def draft_mark(ly: str, language: str = TRADITIONAL) -> str:
    """Mark a draft print: a boxed 「草稿」 with the reason under the title, and 「草稿」 in the
    foot of every page. The texts are app constants in the interface language."""
    def text(source):
        return source if language == TRADITIONAL else catalog().get(source, source)
    line = ('subtitle = \\markup \\fontsize #-2 \\normal-text \\line { \\box \\pad-markup #0.3 "'
            + text(DRAFT_WORD) + '" \\hspace #0.6 "' + text(DRAFT_MARK) + '" }\n')
    ly = re.sub(r"\\header\{\n(?=meter = |title=|title = )", lambda m: m[0] + line, ly)
    foot = '\\markup \\fill-line { \\null \\fontsize #-1 \\box \\pad-markup #0.3 "' + text(DRAFT_WORD) + '" }'
    return ly + "\\paper { oddFooterMarkup = " + foot + " evenFooterMarkup = " + foot + " }\n"


def finish_output(ly: str, variant: str = "tc", parts: int = 1, draft: str = "") -> str:
    """tech.md §3.2 a–d, after ``adapt_output``; ``draft`` is the language of a draft's mark."""
    ly = insert_prelude(ly, variant, parts)
    index = ly.index("\\score {")
    ly = ly[:index] + ENGRAVING + ly[index:]
    ly = midi_rewrite(fix_fine(attach_after_grace_text(engraving_rewrite(header_line(ly)))))
    return draft_mark(ly, draft) if draft else ly


_KEY_MARK = re.compile(r"\\mark \\markup\{1=([A-G])(\\flat|\\sharp)?\}")
_TEMPO = re.compile(r"\\tempo 4=(\d+)")
_TIME = re.compile(r"\\time (\d+/\d+)")
HEAD_TEXT = "\u2060"          # export marks free text of the first note for the header line
_HEAD_TEXT = re.compile(r'\s*\^"' + HEAD_TEXT + r'([^"]*)"')


def header_line(ly: str) -> str:
    """The line printed jianpu puts at the top left, under the title: 「1=♭B  2/4  中速 稍自由  ♩=72」.

    It becomes the header's ``meter`` line (left-aligned above the first system, so nothing of
    the first bar is crammed under it) instead of upstream's rehearsal mark. The key is written
    with its accidental before the letter, the metre follows it (the number staff prints no
    time signature), the first note's free text (tempo words) joins the line, and the metronome
    mark is part of it (MetronomeMark is omitted from the layout; the \\tempo stays for MIDI)."""
    tempo = _TEMPO.search(ly)
    # Upstream echoes its input in a comment before the first score: the note's own text is the
    # first one inside the score (removing the echo left the word printed twice).
    start = max(ly.find("\\score {"), 0)
    text = _HEAD_TEXT.search(ly, start)
    words = text[1] if text else ""
    if text:
        ly = ly[:text.start()] + ly[text.end():]          # only the first note's text moves
    key = _KEY_MARK.search(ly)
    if key is None:
        return ly
    time = _TIME.search(ly, start)
    sign = {"\\flat": "\\flat", "\\sharp": "\\sharp"}.get(key[2] or "")
    parts = [f'\\concat {{ "1=" \\raise #0.75 \\fontsize #-3 {sign} "{key[1]}" }}' if sign else f'"1={key[1]}"']
    if time:
        parts.append(f'\\hspace #1.5 "{time[1]}"')
    if words:
        parts.append(f'\\hspace #2 "{words}"')
    if tempo:
        parts.append('\\hspace #1.5 \\concat { \\fontsize #-2 \\general-align #Y #DOWN \\note {4} #1 '
                     f'" = {tempo[1]}" }}')
    line = "meter = \\markup { \\line { " + " ".join(parts) + " } }\n"
    ly = _KEY_MARK.sub("", ly)
    return re.sub(r"\\header\{\n(?=title=)", lambda m: m[0] + line, ly)


def run(args: list[str]) -> int:
    try:
        operation, source, target, *extra = args
        if operation == "convert":
            data = Path(source).read_text(encoding="utf-8-sig")
            validate_source(data)
            minor = int(extra[0]) if extra else 26
            jianpu_ly._lilypond_minor_version = minor
            paper_size = extra[1] if len(extra) > 1 else "a4"
            staff_size = int(extra[2]) if len(extra) > 2 else 20
            if paper_size not in ("a4", "letter") or not 16 <= staff_size <= 30:
                raise ValueError("Unsupported page or staff size")
            jianpu_ly.staff_size = jianpu_ly.lyric_size = staff_size
            options = read_options(data)
            # Upstream reads this variable while converting: a pickup with a full last bar (§3.2e).
            previous = os.environ.pop("j2ly_sloppy_bars", None)
            if options.get("sloppy"):
                os.environ["j2ly_sloppy_bars"] = "1"
            try:
                result = adapt_output(jianpu_ly.process_input(data), minor, paper_size)
            finally:
                os.environ.pop("j2ly_sloppy_bars", None)
                if previous is not None:
                    os.environ["j2ly_sloppy_bars"] = previous
            result = finish_output(result, options.get("variant", "tc"), part_count(data),
                                   options.get("draft", ""))
        elif operation == "import":
            result = jianpu_ly.xml2jianpu(read_xml(Path(source)))
        else:
            raise ValueError("Unknown worker operation")
        atomic_write(Path(target), result)
        return 0
    except Exception as exc:
        # windowed PyInstaller builds may have no standard output stream.
        message = str(exc)
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        if len(args) >= 3:
            atomic_write(Path(args[2]).with_suffix(".error.txt"), message)
        return 1
