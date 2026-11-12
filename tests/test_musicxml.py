import zipfile

import pytest

from jianpu_studio.musicxml import read_xml, visual_import

XML = '''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" "http://www.musicxml.org/dtds/partwise.dtd">
<score-partwise version="4.0"><work><work-title>四個音符</work-title></work>
<part-list><score-part id="P1"><part-name>Piano</part-name></score-part></part-list>
<part id="P1"><measure number="1"><attributes><divisions>1</divisions><key><fifths>0</fifths></key><time><beats>4</beats><beat-type>4</beat-type></time><clef><sign>G</sign><line>2</line></clef></attributes>
<note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration><type>quarter</type></note>
<note><pitch><step>D</step><octave>4</octave></pitch><duration>1</duration><type>quarter</type></note>
<note><pitch><step>E</step><octave>4</octave></pitch><duration>1</duration><type>quarter</type></note>
<note><rest/><duration>1</duration><type>quarter</type></note>
</measure></part></score-partwise>'''


def test_read_musicxml_and_visual_import(tmp_path):
    path = tmp_path / "score.musicxml"
    path.write_text(XML, encoding="utf-8")
    text = read_xml(path)
    project, message = visual_import(text)
    assert project.score.title == "四個音符"
    assert [n.degree for n in project.score.notes] == [1, 2, 3, 0]
    assert project.score.rhythm_error() is None
    assert "DOCTYPE" not in text


def test_complex_score_falls_back():
    project, message = visual_import(XML.replace("<note><pitch>", "<note><chord/><pitch>", 1))
    assert project is None
    assert "避免遺失" in message


def test_compressed_musicxml(tmp_path):
    path = tmp_path / "score.mxl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="score.xml"/></rootfiles></container>')
        archive.writestr("score.xml", XML)
    assert "四個音符" in read_xml(path)


def test_mxl_traversal_rejected(tmp_path):
    path = tmp_path / "bad.mxl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", '<container><rootfiles><rootfile full-path="../score.xml"/></rootfiles></container>')
        archive.writestr("../score.xml", XML)
    with pytest.raises(ValueError, match="不安全"):
        read_xml(path)


def test_xml_entities_rejected(tmp_path):
    path = tmp_path / "bad.xml"
    path.write_text('<!DOCTYPE score-partwise [<!ENTITY secret SYSTEM "file:///etc/passwd">]><score-partwise>&secret;</score-partwise>')
    with pytest.raises(Exception):
        read_xml(path)
