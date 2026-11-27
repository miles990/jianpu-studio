"""Standard MIDI files: a small writer (format 1) and a reader for checks.

Only what the preview needs: tempo, program change, notes with velocities and the sustain
pedal (CC64), one track per part.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class NoteEvent:
    start: int          # ticks
    end: int
    pitch: int
    velocity: int


@dataclass
class Track:
    notes: list[NoteEvent]
    channel: int = 0
    program: int = 0
    controls: list[tuple[int, int, int]] | None = None    # (tick, controller, value)
    name: str = ""


def _vlq(value: int) -> bytes:
    out = [value & 0x7F]
    value >>= 7
    while value:
        out.append(0x80 | (value & 0x7F))
        value >>= 7
    return bytes(reversed(out))


def _chunk(kind: bytes, payload: bytes) -> bytes:
    return kind + struct.pack(">I", len(payload)) + payload


def write(tracks: list[Track], *, division: int, tempo_us: int, numerator=4, denominator=4) -> bytes:
    """Format-1 SMF: a tempo track, then one track per part."""
    log2 = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4}.get(denominator, 2)
    head = (b"\x00\xff\x51\x03" + tempo_us.to_bytes(3, "big")
            + b"\x00\xff\x58\x04" + bytes([numerator, log2, 24, 8]) + b"\x00\xff\x2f\x00")
    chunks = [_chunk(b"MTrk", head)]
    for track in tracks:
        channel = track.channel & 0x0F
        events = []          # (tick, order, bytes): note-offs before note-ons at the same tick
        name = track.name.encode("utf-8")
        events.append((0, -2, b"\xff\x03" + _vlq(len(name)) + name))
        events.append((0, -1, bytes([0xC0 | channel, track.program & 0x7F])))
        for tick, controller, value in track.controls or ():
            events.append((tick, 1, bytes([0xB0 | channel, controller & 0x7F, value & 0x7F])))
        for note in track.notes:
            events.append((note.start, 2, bytes([0x90 | channel, note.pitch & 0x7F, max(1, min(127, note.velocity))])))
            events.append((note.end, 0, bytes([0x80 | channel, note.pitch & 0x7F, 0])))
        events.sort(key=lambda e: (e[0], e[1]))
        payload, last = bytearray(), 0
        for tick, _, data in events:
            payload += _vlq(tick - last) + data
            last = tick
        payload += b"\x00\xff\x2f\x00"
        chunks.append(_chunk(b"MTrk", bytes(payload)))
    header = _chunk(b"MThd", struct.pack(">HHH", 1, len(chunks), division))
    return header + b"".join(chunks)


@dataclass(frozen=True)
class ReadNote:
    track: int
    start: float        # seconds
    end: float
    pitch: int
    velocity: int


def read(data: bytes) -> tuple[list[ReadNote], list[tuple[float, int, int, int]], list[int]]:
    """(notes, controls (seconds, track, controller, value), programs) with times in seconds."""
    if data[:4] != b"MThd":
        raise ValueError("not a MIDI file")
    _, _, count, division = struct.unpack(">IHHH", data[4:14])
    offset, raw, tempos, controls, programs = 14, [], [(0, 500000)], [], []

    def vlq(payload, position):
        value = 0
        while True:
            byte = payload[position]
            position += 1
            value = (value << 7) | (byte & 0x7F)
            if not byte & 0x80:
                return value, position

    for index in range(count):
        size = struct.unpack(">I", data[offset + 4:offset + 8])[0]
        track = data[offset + 8:offset + 8 + size]
        offset += 8 + size
        position, tick, running, open_notes = 0, 0, None, {}
        while position < len(track):
            delta, position = vlq(track, position)
            tick += delta
            status = track[position]
            if status & 0x80:
                position += 1
            else:
                status = running
            if status == 0xFF:
                kind = track[position]
                length, position = vlq(track, position + 1)
                if kind == 0x51:
                    tempos.append((tick, int.from_bytes(track[position:position + 3], "big")))
                position += length
                continue
            if status in (0xF0, 0xF7):
                length, position = vlq(track, position)
                position += length
                continue
            running = status
            high = status & 0xF0
            if high in (0xC0, 0xD0):
                if high == 0xC0:
                    programs.append(track[position])
                position += 1
                continue
            a, b = track[position], track[position + 1]
            position += 2
            if high == 0x90 and b:
                open_notes.setdefault(a, []).append((tick, b))
            elif high in (0x80, 0x90) and open_notes.get(a):
                start, velocity = open_notes[a].pop(0)
                raw.append((index, start, tick, a, velocity))
            elif high == 0xB0:
                controls.append((tick, index, a, b))
    tempos.sort()

    def seconds(tick):
        out, last_tick, last_tempo = 0.0, 0, 500000
        for at, tempo in tempos:
            if at >= tick:
                break
            out += (at - last_tick) * last_tempo / division / 1e6
            last_tick, last_tempo = at, tempo
        return out + (tick - last_tick) * last_tempo / division / 1e6

    notes = sorted((ReadNote(t, seconds(s), seconds(e), p, v) for t, s, e, p, v in raw),
                   key=lambda n: (n.start, n.track, n.pitch))
    return notes, [(seconds(t), i, c, v) for t, i, c, v in controls], programs
