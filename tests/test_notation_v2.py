"""Project file v2: symbol registry, export answer sheet (wow/tech.md §4), migration, rules, parts."""
import json
import re
import struct
from dataclasses import replace
from pathlib import Path

import pytest

from jianpu_studio import migrate, symbols, worker
from jianpu_studio.engine import find_tool, render, validate_source
from jianpu_studio.i18n_zh_cn import CATALOG
from jianpu_studio.measures import clean_pasted, delete_measures, duplicate_measures, insert_measures
from jianpu_studio.model import Note, Part, Project, Score, load_project, save_project
from jianpu_studio.notation_edit import add_part, apply_symbol, remove_part, symbol_problem

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/notation"
V1 = ROOT / "tests/fixtures/v1"
NAMES = sorted(p.stem for p in FIXTURES.glob("*.json"))
# tech.md §2.4: names added to the 0.3.x source whitelist (generated, never hand-copied).
SPEC_NEW_WORDS = set("""cresc decresc dim fp fz jsATempo jsAccel jsAccelSpan jsBowDownHook jsBowUpHook jsBreathV
jsBreathVBold jsCaesura jsCodaSign jsDCFineText jsDCText jsDSCodaText jsDSText jsDiziCircular jsDiziDa jsDiziDie
jsDiziDuo jsDiziHuashe jsDiziK jsDiziLiDown jsDiziLiUp jsDiziQizhen jsDiziTuOne jsDiziTuThree jsDiziTuTwo jsErhuDa
jsErhuDun jsErhuInner jsErhuLiandun jsErhuOuter jsErhuPao jsErhuPressVib jsErhuShift jsErhuSlideVib jsErhuTiao
jsErhuVibrato jsFineText jsFingerZero jsFp jsFz jsGzAn jsGzChan jsGzDacuo jsGzGou jsGzGuaDown jsGzGuaUp jsGzHuazhi
jsGzMo jsGzPi jsGzShuangtuo jsGzTi jsGzTiao jsGzTl jsGzTuo jsGzXiaocuo jsGzYao jsHarmArtificial jsHulusiClose
jsMordentFlat jsMordentNatural jsMordentSharp jsOttavaDown jsOttavaEnd jsOttavaUp jsPipaDaiqi jsPipaFu jsPipaGun
jsPipaLa jsPipaLun jsPipaRou jsPipaSao jsPipaTan jsPipaTiao jsPipaTui jsPipaYao jsPipaYin jsPizzLeft jsPizzRight
jsPrallFlat jsPrallNatural jsPrallSharp jsRf jsRfz jsRit jsRitSpan jsRuanLunFour jsRuanLunThree jsSanban
jsSegnoSign jsSf jsSfp jsSfpp jsSfz jsSfzz jsStaccatoHollow jsStringFive jsStringFour jsStringOne jsStringThree
jsStringTwo jsStrokeLun jsStrokeYao jsToCodaText jsTremFour jsTremOne jsTremTwo jsTrillFlat jsTrillNatural
jsTrillSharp jsVoltaJie jsWavySpan jsXuzhiSpan jsYangqinShuangtanlun prallprall reverseturn rfz sf sfp
startTrillSpan stopTextSpan stopTrillSpan sustainOff sustainOn""".split())


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def project(name):
    return Project.from_dict(fixture(name))


# ---------------------------------------------------------------- registry
def test_registry_is_complete_and_consistent():
    assert len(symbols.SYMBOLS) == 178          # tech.md's 175 + volta.3, volta.4, volta.end
    macros = set(symbols.macro_names())
    for s in symbols.SYMBOLS.values():
        assert s.name in CATALOG and CATALOG[s.name], s.id            # both languages
        assert s.kind in ("point", "pre", "span", "bar", "composite"), s.id
        assert s.confidence in ("high", "medium", "low"), s.id
        for word in re.findall(r"\\([A-Za-z]+)", s.export):
            assert word in symbols.source_words(), (s.id, word)        # export word whitelisted
            if word.startswith("js"):
                assert word in macros, (s.id, word)                    # and defined in the prelude
        if s.kind == "span" and s.storage == "spans":
            assert s.family and s.span_words(False)[0], s.id
    assert sum(s.confidence == "low" for s in symbols.SYMBOLS.values()) == 27


def test_source_whitelist_is_generated_from_the_registry():
    words = symbols.source_words()
    assert words - symbols.V1_WORDS == SPEC_NEW_WORDS
    assert not any(w.endswith("Midi") for w in words)
    validate_source("1=D 4/4\nc5 \\jsErhuInner c6 \\jsSfz \\startTrillSpan c1' \\stopTrillSpan c2'")
    for bad in ("1=D 4/4\nc5 \\jsSfzMidi", "1=D 4/4\nslide=⌒ c5", "1=D 4/4\nFr=1 c5", "c5 \\jsUnknown"):
        with pytest.raises(ValueError):
            validate_source(bad)


def test_prelude_variants_and_braces():
    tc, sc = symbols.prelude("tc"), symbols.prelude("sc", parts=2)
    assert 'jsErhuInner = ^\\markup \\fontsize #-2 "內"' in tc
    assert 'jsErhuInner = ^\\markup \\fontsize #-2 "内"' in sc
    assert "SystemStartBrace" in sc and "SystemStartBrace" not in tc
    assert "Fingering.font-encoding = #'latin1" in tc            # tech.md P1
    for name in symbols.macro_names():
        assert f"\n{name} = " in tc
    assert "jsSfzMidi = " in tc


def test_low_confidence_symbols_are_hidden_by_default():
    hidden = {s.id for s in symbols.SYMBOLS.values() if s.confidence == "low"}
    assert hidden and not hidden & {s.id for s in symbols.symbols()}
    assert hidden <= {s.id for s in symbols.symbols(include_hidden=True)}
    assert "slide.back" not in {s.id for s in symbols.search("hua")}
    assert "slide.back" in {s.id for s in symbols.search("hua", include_hidden=True)}


@pytest.mark.parametrize("query, expected", [("滑", "slide.up"), ("hua", "slide.down"), ("渐强", "hairpin.cresc"),
                                             ("fanfu", "bar.repeat_start"), ("內弦", "erhu.inner")])
def test_search_in_both_scripts_and_pinyin(query, expected):
    assert expected in {s.id for s in symbols.search(query)}


# ---------------------------------------------------------------- answer sheet
@pytest.mark.parametrize("name", NAMES)
def test_fixture_exports_exact_jianpu_text_and_round_trips(name):
    data = fixture(name)
    p = Project.from_dict(data)
    assert p.score.to_jianpu() == (FIXTURES / f"{name}.jly").read_text(encoding="utf-8")
    expected = json.loads(json.dumps(data["score"]))       # ids are stored in registry order
    for part in expected["parts"]:
        for note in part["notes"]:
            for key in ("marks", "spans_start", "spans_end", "bar_start", "bar_end"):
                if key in note:
                    note[key] = list(symbols.sort_ids(note[key]))
    assert p.to_dict()["score"] == expected               # save omits note defaults, like the fixture
    assert Project.from_dict(json.loads(json.dumps(p.to_dict()))) == p


def test_print_options_travel_with_the_notation():
    assert project("f03_pickup_full_last").notation().startswith("% jianpu-studio: sloppy-bars\n")
    assert project("f12_folk_sc").notation().startswith("% jianpu-studio: print=sc\n")
    assert project("f01_dynamics").notation().startswith("title=")
    assert worker.read_options(project("f12_folk_sc").notation()) == {"variant": "sc", "sloppy": False}
    assert worker.part_count(project("f10_duet").notation()) == 2


def test_fix_fine_braces_the_rest_of_the_block():
    ly = 'a { b \\volta 2 \\fine \\volta 1 c d "}" % }\n e } f'
    assert worker.fix_fine(ly) == 'a { b \\volta 2 \\fine \\volta 1 { c d "}" % }\n e } } f'


def read_midi(path):
    data = path.read_bytes()
    count, division = struct.unpack(">HH", data[10:14])
    pos, tracks, tempos = 14, [], []
    for _ in range(count):
        length = struct.unpack(">I", data[pos + 4:pos + 8])[0]
        chunk, pos = data[pos + 8:pos + 8 + length], pos + 8 + length
        i, status, notes = 0, None, []
        while i < len(chunk):
            while chunk[i] & 0x80:
                i += 1
            i += 1
            b = chunk[i]
            if b == 0xFF:
                kind, size = chunk[i + 1], chunk[i + 2]
                if kind == 0x51:
                    tempos.append(round(60e6 / int.from_bytes(chunk[i + 3:i + 6], "big")))
                i += 3 + size
                continue
            if b & 0x80:
                status, i = b, i + 1
            if status & 0xF0 in (0xC0, 0xD0):
                i += 1
                continue
            a, v = chunk[i], chunk[i + 1]
            i += 2
            if status & 0xF0 == 0x90 and v:
                notes.append((a, v))
        if notes:
            tracks.append(notes)
    return tracks, tempos


MIDI_COUNTS = {"f01_dynamics": [12], "f02_pickup": [12], "f03_pickup_full_last": [6], "f04_volta": [25],
               "f05_volta_jie": [25], "f06_dc_al_fine": [21], "f07_ds_al_coda": [29], "f08_ornaments": [15],
               "f09_erhu": [14], "f10_duet": [24, 19], "f11_parts_pickup_lyrics": [8, 5], "f12_folk_sc": [16]}


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    tool = find_tool("lilypond")
    if not tool:
        pytest.skip("JIANPU_LILYPOND not configured")
    out = {}
    for name in NAMES:
        out[name] = render(project(name).notation(), tmp_path_factory.mktemp(name) / "輸出 目錄", tool.resolve())
    return out


@pytest.mark.integration
@pytest.mark.parametrize("name", NAMES)
def test_fixture_compiles_without_warnings_and_plays_the_expected_notes(rendered, name):
    result = rendered[name]
    assert result.pdf.is_file()
    assert not re.search(r"warning|error", result.log, re.I), result.log[-3000:]
    tracks, _ = read_midi(result.midi)
    assert [len(t) for t in tracks] == MIDI_COUNTS[name]


@pytest.mark.integration
def test_midi_dynamics_ottava_tempo_and_jump_order(rendered):
    # f01: p=69 ramps to f=95; sfz accents one note (+20); fp/sfpp set p=69 / pp=62 and accent
    # their own note; rf accents without changing pp; mf=86 ramps down to pp=62.
    tracks, tempos = read_midi(rendered["f01_dynamics"].midi)
    assert [v for _, v in tracks[0]] == [69, 78, 86, 95, 115, 89, 82, 82, 86, 78, 70, 62]
    assert tempos == [80]
    # f08: the four notes under 8va sound an octave higher.
    pitches = [p for p, _ in read_midi(rendered["f08_ornaments"].midi)[0][0]]
    assert pitches[8:12] == [69 + 12, 71 + 12, 74 + 12, 76 + 12] and pitches[:4] == [69, 71, 69, 66]
    # f05: 1234 5555 1234 6666 1234 1 after the 3 3 2 2 intro.
    degree = {62: 1, 64: 2, 66: 3, 67: 4, 69: 5, 71: 6}
    played = "".join(str(degree[p]) for p, _ in read_midi(rendered["f05_volta_jie"].midi)[0][0])
    assert played == "3322" + "1234" + "5555" + "1234" + "6666" + "1234" + "1"
    # f07: D.S. returns to the Segno bar and jumps from To Coda to the Coda.
    played = "".join(str(degree[p]) for p, _ in read_midi(rendered["f07_ds_al_coda"].midi)[0][0])
    assert played == "1111" "2222" "3333" "4444" "2222" "3333" "5555" "6"
    # f06: D.C. al Fine plays up to Fine, not past it (P2).
    played = "".join(str(degree[p]) for p, _ in read_midi(rendered["f06_dc_al_fine"].midi)[0][0])
    assert played == "12345654" "33221" "12345654"
    # a tempo written once applies to every part
    assert read_midi(rendered["f10_duet"].midi)[1] == [66]
    assert "SystemStartBrace" in rendered["f10_duet"].lilypond.read_text(encoding="utf-8")


@pytest.mark.integration
def test_duet_example_renders_both_parts(tmp_path):
    tool = find_tool("lilypond")
    if not tool:
        pytest.skip("JIANPU_LILYPOND not configured")
    p = load_project(ROOT / "examples/duet.jps")
    assert len(p.score.parts) == 2
    result = render(p.notation(), tmp_path / "duet", tool.resolve())
    assert not re.search(r"warning|error", result.log, re.I), result.log[-3000:]
    assert [len(t) for t in read_midi(result.midi)[0]] == [53, 34]      # D.C. replays the inner repeat


# ---------------------------------------------------------------- v1 -> v2
@pytest.mark.parametrize("name", ["twinkle", "v1_marks", "v1_lines"])
def test_v1_projects_open_and_export_like_v1(name):
    p = load_project(V1 / f"{name}.jps")
    assert json.loads((V1 / f"{name}.jps").read_text(encoding="utf-8"))["version"] == 1
    v1_text = (V1 / f"{name}.v1.jly").read_text(encoding="utf-8")
    v2_text = p.score.to_jianpu()
    assert migrate.same_music_text(v1_text, v2_text)
    if name == "twinkle":
        assert v2_text == v1_text
    assert p.score.print_variant == "tc" and p.score.pickup == 0 and len(p.score.parts) == 1


def test_v1_migration_details_and_documented_differences():
    p = load_project(V1 / "v1_marks.jps")
    first = p.score.notes[0]
    assert first.marks == ("dyn.mp", "art.accent", "bow.down", "finger.0")
    assert (first.finger, first.bow, first.dynamic, first.articulation) == (0, "downbow", "mp", "accent")
    text = p.score.to_jianpu()
    assert "c5 \\jsFingerZero" in text and "\\jsSfz" in text and "Fr=0" not in text
    lines = load_project(V1 / "v1_lines.jps").score
    assert lines.notes[0].spans_start == ("hairpin.cresc", "line.slur")
    assert lines.notes[0].bar_start == ("bar.repeat_start",)
    assert lines.notes[9].spans_start == ("hairpin.dim",) and lines.notes[10].spans_end == ("hairpin.dim",)
    assert not migrate.same_music_text("1=C 4/4\nc1 c2 c3 c4", "1=C 4/4\nc1 c2 c4 c3")


def test_save_writes_compact_version_two_and_reloads(tmp_path):
    p = load_project(ROOT / "examples/duet.jps")
    path = tmp_path / "中文 路徑" / "duet.jps"
    save_project(path, p)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["version"] == 2 and "notes" not in data["score"]
    assert {"degree": 3, "duration": 2} in data["score"]["parts"][1]["notes"]
    assert load_project(path) == p


# ---------------------------------------------------------------- legacy edit paths
def test_legacy_field_edits_never_drop_new_marks():
    note = Note(5, marks=("finger.1", "erhu.inner", "art.accent", "art.fermata", "dyn.sf"))
    assert note.finger == 1 and note.articulation == "accent" and note.dynamic == ""
    changed = replace(note, finger=2, articulation="tenuto", bow="upbow", dynamic="p")
    assert set(changed.marks) == {"finger.2", "erhu.inner", "art.tenuto", "art.fermata", "dyn.p", "bow.up"}
    cleared = replace(changed, finger=-1, articulation="")
    assert "erhu.inner" in cleared.marks and "art.fermata" in cleared.marks
    assert "art.tenuto" not in cleared.marks and "finger.2" not in cleared.marks
    hooked = Note(1, marks=("bow.down_hook", "finger.5"))
    assert hooked.bow == "" and hooked.finger == -1
    assert replace(hooked, slur_start=True).marks == hooked.marks
    with pytest.raises(TypeError):
        Note(1, nonsense=True)


def test_legacy_hairpin_end_is_paired_by_family_and_saved_canonically():
    score = Score(notes=[Note(1, hairpin="diminuendo"), Note(2), Note(3), Note(4, hairpin_end=True)])
    assert score.notation_error() is None
    assert score.to_dict()["parts"][0]["notes"][3]["spans_end"] == ["hairpin.dim"]
    assert "c4 \\!" in score.to_jianpu()


def test_paste_keeps_complete_lines_and_drops_half_lines_and_bar_items():
    notes = [Note(1, spans_start=("line.slur", "hairpin.cresc"), bar_start=("bar.repeat_start",)),
             Note(2, spans_end=("line.slur",), marks=("finger.1",)), Note(3, bar_end=("bar.double",))]
    cleaned, removed = clean_pasted(notes)
    assert cleaned[0].spans_start == ("line.slur",) and cleaned[1].spans_end == ("line.slur",)
    assert cleaned[1].marks == ("finger.1",)
    assert not any(n.bar_start or n.bar_end for n in cleaned)
    assert removed == 3


# ---------------------------------------------------------------- rules (§1.5), one per row
def bar(*degrees, **first):
    notes = [Note(d) for d in degrees]
    notes[0] = replace(notes[0], **first) if first else notes[0]
    return notes


@pytest.mark.parametrize("build, message", [
    (lambda: Score(notes=[Note(1, marks=("no.such",))] + bar(2, 3, 4)), "不支援的記號"),            # exists
    (lambda: Score(notes=[Note(1, spans_start=("dyn.p",))] + bar(2, 3, 4)), "放錯位置"),             # kind/field
    (lambda: Score(notes=[Note(1, marks=("dyn.p", "dyn.f"))] + bar(2, 3, 4)), "不能同時"),            # slot
    (lambda: Score(notes=[Note(0, marks=("erhu.inner",))] + bar(2, 3, 4)), "休止符"),                # rest
    (lambda: Score(notes=[Note(1, grace_after="2", marks=("dyn.p",))] + bar(2, 3, 4)), "後倚音"),    # after grace
    (lambda: Score(parts=[Part("a", bar(1, 2, 3, 4)), Part("b", bar(1, 2, 3, 4, bar_end=("bar.double",)))]),
     "第一聲部"),                                                                                    # bar items part 0
    (lambda: Score(pickup=5 * 12, notes=bar(1, 2, 3)), "弱起"),                                     # pickup value
    (lambda: Score(parts=[Part()] * 5), "1 到 4"),                                                  # parts
    (lambda: Score(parts=[Part(name="x" * 41)]), "聲部名稱"),                                       # text
])
def test_validate_rejects(build, message):
    with pytest.raises(ValueError, match=message):
        build().validate()


def notes_of(*bars):
    return [n for b in bars for n in b]


@pytest.mark.parametrize("score, message", [
    (Score(notes=notes_of([Note(1, spans_start=("line.slur",))], bar(2, 3, 4))), "連弓線"),              # pairing
    (Score(notes=notes_of(bar(1, 2, 3, 4, spans_start=("orn.trill_span",)),
                          [Note(1, spans_start=("line.wavy",)), Note(2), Note(3), Note(4, spans_end=("line.wavy",))])),
     "重疊"),                                                                                            # family overlap
    (Score(notes=[Note(1, spans_start=("hairpin.cresc",)), Note(2, marks=("dyn.f",)), Note(3),
                  Note(4, spans_end=("hairpin.cresc",))]), "中斷"),                                      # dyn in hairpin
    (Score(notes=notes_of([Note(1, spans_start=("line.8va",), bar_start=("bar.repeat_start",)), Note(2), Note(3),
                           Note(4, bar_end=("bar.repeat_end",))],
                          [Note(5, spans_end=("line.8va",)), Note(5), Note(5), Note(5)],
                          )), "八度"),                                                                    # 8va block
    (Score(notes=[Note(1), Note(2, bar_start=("nav.segno",)), Note(3), Note(4)]), "第一個音符"),           # boundary
    (Score(notes=notes_of(bar(1, 2, 3, 4, bar_start=("bar.repeat_start",)),
                          bar(1, 2, 3, 4, bar_start=("bar.repeat_start",)))), "巢狀"),                    # repeats
    (Score(notes=notes_of(bar(1, 2, 3, 4, bar_start=("bar.repeat_start",)), [Note(1), Note(2), Note(3),
                          Note(4, bar_end=("bar.repeat_end",))], bar(5, 5, 5, 5, bar_start=("volta.2",)))),
     "房號"),                                                                                           # voltas
    (Score(notes=notes_of(bar(1, 2, 3, 4), [Note(1), Note(2), Note(3), Note(4, bar_end=("nav.ds",))])), "D.S."),
    (Score(notes=notes_of(bar(1, 2, 3, 4, bar_start=("bar.repeat_start",)),
                          [Note(1), Note(2), Note(3), Note(4, bar_end=("nav.fine",))],
                          [Note(1), Note(2), Note(3), Note(4, bar_end=("bar.repeat_end",))],
                          [Note(1), Note(2), Note(3), Note(4, bar_end=("nav.dc",))])), "反覆段落"),       # nav in repeat
])
def test_notation_error_rules(score, message):
    score.validate()
    assert message in (score.notation_error() or "")


def test_pickup_rhythm_rules():
    short = Score(pickup=48, notes=[Note(5), Note(1), Note(2), Note(3), Note(4), Note(5, duration=2, dotted=True)])
    assert short.rhythm_error() is None and not short.needs_sloppy_final_bar()
    full = Score(pickup=48, notes=[Note(5), Note(1), Note(2), Note(3), Note(4), Note(5, duration=1)])
    assert full.rhythm_error() is None and full.needs_sloppy_final_bar()
    missing = Score(pickup=48, notes=[Note(5), Note(1), Note(2), Note(3), Note(4), Note(5)])
    assert "還缺 2 拍" in missing.rhythm_error()
    missing.fill_last_bar()
    assert missing.rhythm_error() is None and len(missing.notes) == 7
    assert Score(pickup=48, notes=[Note(5, duration=2)]).rhythm_error().startswith("第 1 個音符超出第 1 小節")


def test_parts_must_have_equal_length():
    score = Score(parts=[Part("甲", bar(1, 2, 3, 4)), Part("乙", bar(1, 2, 3, 4) + bar(5, 6, 7, 1))])
    assert "總拍數" in score.rhythm_error()


# ---------------------------------------------------------------- multi-part measure edits and palette API
def duet():
    return load_project(ROOT / "examples/duet.jps")


@pytest.mark.parametrize("operation, bars", [
    (lambda s: insert_measures(s, 2), 7), (lambda s: delete_measures(s, [4]), 5),
    (lambda s: duplicate_measures(s, [2]), 7),
])
def test_measure_edits_change_every_part_together(operation, bars):
    p = duet()
    before = p.snapshot()
    result = operation(p.score)
    edited = p.snapshot()
    result.apply(edited.score)
    assert [len(edited.score.part_bars(i)) for i in range(2)] == [bars, bars]
    assert edited.score.rhythm_error() is None and edited.score.notation_error() is None
    assert edited.score.to_jianpu()
    assert p == before                                    # the original project (undo state) is untouched


def test_duplicate_never_copies_navigation_and_keeps_bar_items_in_place():
    p = duet()
    result = duplicate_measures(p.score, [3])               # the Fine bar
    edited = p.snapshot()
    result.apply(edited.score)
    text = edited.score.to_jianpu()
    assert text.count("Fine") == 2 and "nav.fine" in edited.score.to_dict()["parts"][0]["notes"][14]["bar_end"]
    assert sum("nav.fine" in n.bar_end for n in edited.score.notes) == 1
    assert "1" in result.notice                             # the copy's Fine is removed, with a notice


def test_pickup_bar_is_protected_from_structural_measure_edits():
    score = Project.from_dict(fixture("f02_pickup")).score
    for operation in (lambda: delete_measures(score, [0]), lambda: insert_measures(score, 0),
                      lambda: duplicate_measures(score, [0])):
        with pytest.raises(ValueError, match="弱起"):
            operation()
    result = insert_measures(score, 2)
    edited = replace(score, parts=[replace(score.parts[0], notes=result.notes)])
    assert edited.rhythm_error() is None and edited.part_bars(0)[0] == (0, 1)


def test_palette_api_applicability_apply_and_parts():
    score = Score(notes=bar(1, 2, 0, 4) + bar(5, 6, 7, 1))
    assert symbol_problem(score, 0, [], "erhu.inner")
    assert "休止符" in symbol_problem(score, 0, [2], "erhu.inner")
    assert symbol_problem(score, 0, [2], "art.fermata") is None
    assert symbol_problem(score, 0, [0], "line.slur")                       # needs two notes
    apply_symbol(score, 0, [0, 1], "erhu.inner")
    apply_symbol(score, 0, [0, 1], "line.slur")
    apply_symbol(score, 0, [0, 7], "bar.repeat_start")                     # a selection spanning bars: a pair
    apply_symbol(score, 0, [7], "nav.fine")
    assert score.notation_error() is None
    assert score.notes[0].marks == ("erhu.inner",) and score.notes[1].spans_end == ("line.slur",)
    assert score.notes[0].bar_start == ("bar.repeat_start",) and score.notes[7].bar_end == ("bar.repeat_end", "nav.fine")
    index = add_part(score, "二胡二")
    assert index == 1 and score.rhythm_error() is None
    apply_symbol(score, 1, [1], "bar.double")                              # bar items go to part 0
    assert not any(n.bar_end for n in score.parts[1].notes) and "bar.double" in score.notes[7].bar_end
    assert "NextPart" in score.to_jianpu()
    with pytest.raises(ValueError):
        remove_part(score, 0)
    remove_part(score, 1)
    assert len(score.parts) == 1


def test_measure_edit_with_multi_part_score_is_one_undo_step(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    from jianpu_studio.app import MainWindow
    monkeypatch.setattr(MainWindow, "render_score", lambda *a, **k: None)
    monkeypatch.setattr(MainWindow, "error", lambda self, title, error: pytest.fail(f"{title}: {error}"))
    window = MainWindow(recovery=False)
    window.recovery_path = tmp_path / "recovery.jps"
    window.autosave.stop()
    qtbot.addWidget(window, before_close_func=lambda w: setattr(w, "dirty", False))
    original = duet()
    window.reset(duet())
    window.notes.select_bar(1)
    QApplication.processEvents()
    window.measure_action("delete")
    assert [len(window.project.score.part_bars(i)) for i in range(2)] == [5, 5]
    assert len(window.history) == 1
    window.undo()
    assert window.project == original
    window.redo()
    assert len(window.project.score.parts[1].notes) < len(original.score.parts[1].notes)


def test_v1_recovery_file_is_restored(qtbot, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from jianpu_studio import app as app_module
    from jianpu_studio.app import MainWindow
    monkeypatch.setattr(MainWindow, "render_score", lambda *a, **k: None)
    monkeypatch.setattr(MainWindow, "error", lambda self, title, error: pytest.fail(f"{title}: {error}"))
    monkeypatch.setattr(app_module.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    window = MainWindow(recovery=False)
    window.recovery_path = tmp_path / "recovery.jps"
    window.recovery_path.write_bytes((V1 / "v1_marks.jps").read_bytes())
    qtbot.addWidget(window, before_close_func=lambda w: setattr(w, "dirty", False))
    window.restore_recovery()
    assert window.project.score.title == "v1 記號"
    assert window.project.score.notes[0].finger == 0
