"""MIDI time -> score position, from the model (not from the MIDI file).

LilyPond writes the preview MIDI with ``\\unfoldRepeats`` (wow/tech.md §3.3): repeats
and voltas are played out, D.C./D.S. jump back (inner repeats are repeated again on the
way back), Fine stops and To Coda jumps to the bar after the jump. ``unfold_bars``
replays the bar order with the same rules; ``PlayMap`` turns it into note onsets so the
canvas can follow the music and "play from bar N" knows where bar N first sounds.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

VOLTAS = ("volta.1", "volta.2", "volta.3", "volta.4")


def _structure(score):
    from .export import effective_bar_items
    items = effective_bar_items(score)
    repeats = []          # {"start", "end", "voltas": [[first, last], ...]}
    open_start = None
    volta_starts = []     # (bar, repeat) of the current volta group
    for bar, (starts, ends) in enumerate(items):
        if "bar.repeat_start" in starts:
            open_start = bar
        if any(v in starts for v in VOLTAS) and repeats:
            group = repeats[-1]
            if group["voltas"]:
                group["voltas"][-1][1] = bar - 1
            group["voltas"].append([bar, bar])
            volta_starts.append(bar)
        if "bar.repeat_end" in ends:
            repeats.append({"start": open_start if open_start is not None else 0, "end": bar, "voltas": []})
            open_start = None
        if "volta.end" in ends and repeats and repeats[-1]["voltas"]:
            repeats[-1]["voltas"][-1][1] = bar
    nav = {}
    for bar, (starts, ends) in enumerate(items):
        for item in (*starts, *ends):
            if item in ("nav.segno", "nav.tocoda", "nav.ds", "nav.dc", "nav.fine"):
                nav[item] = bar
    return len(items), repeats, nav


def unfold_bars(score) -> list[int]:
    """Bar indices in the order the preview MIDI plays them."""
    return unfold(score)[0]


def unfold(score) -> tuple[list[int], int | None]:
    """(``unfold_bars``, index in it where a D.C./D.S. return starts, or None)."""
    count, repeats, nav = _structure(score)
    if count == 0:
        return [], None
    back = None
    by_end = {r["end"]: r for r in repeats}
    volta_bars = {}
    for r in repeats:
        for index, (first, last) in enumerate(r["voltas"]):
            for bar in range(first, last + 1):
                volta_bars[bar] = (r, index)
    jump = nav.get("nav.ds", nav.get("nav.dc"))
    jump_target = nav.get("nav.segno", 0) if "nav.ds" in nav else 0
    order: list[int] = []
    passes: dict[int, int] = {}
    returning = False
    bar = 0
    guard = 0
    while bar < count and guard < count * 20:
        guard += 1
        if bar in volta_bars:
            r, index = volta_bars[bar]
            current = passes.get(r["end"], 1)
            if index != min(current, len(r["voltas"])) - 1:
                # Not this pass's ending: skip to the right one.
                target = r["voltas"][min(current, len(r["voltas"])) - 1][0]
                if target > bar:
                    bar = target
                    continue
        order.append(bar)
        r = by_end.get(bar)
        if r is not None and not r["voltas"]:
            done = passes.get(bar, 1)
            if done < 2:
                passes[bar] = done + 1
                bar = r["start"]
                continue
            passes[bar] = 1    # a D.C./D.S. return plays the repeat again
        # Fine/To Coda on a repeat's closing bar line act after its last pass (LilyPond).
        if returning and nav.get("nav.fine") == bar:
            break
        if returning and nav.get("nav.tocoda") == bar and jump is not None:
            bar = jump + 1
            continue
        if bar in volta_bars:
            r, index = volta_bars[bar]
            first, last = r["voltas"][index]
            total = max(2, len(r["voltas"]))
            current = passes.get(r["end"], 1)
            if bar == last and current < total:
                passes[r["end"]] = current + 1
                bar = r["start"]
                continue
            if bar == last and current >= total:
                passes[r["end"]] = 1
                if not (jump is not None and bar == jump and not returning):
                    bar = r["voltas"][-1][1] + 1
                    continue
                # A D.C./D.S. at the end of the last ending jumps after that ending.
        if jump is not None and bar == jump and not returning:
            returning = True
            bar = jump_target
            back = len(order)
            continue
        bar += 1
    return order, back


@dataclass(frozen=True)
class Onset:
    tick: int        # from the start of the MIDI (48 per quarter)
    bar: int
    row: int         # row in the mapped part


class PlayMap:
    """Note onsets of one part in performance order.

    ``order`` is the bar order actually played (``preview_midi.playing_order`` by default:
    an unfinished structure plays as written). When the first note has grace notes before
    it, the MIDI file starts with them (``lead_in``), so every time here is that much later."""

    def __init__(self, score, part: int = 0, order: list[int] | None = None):
        from .model import TICKS_PER_QUARTER
        from .preview_midi import lead_in, playing_order
        self.part = part
        self.tempo = score.tempo
        self.ticks_per_quarter = TICKS_PER_QUARTER
        ranges = score.part_bars(part)
        notes = score.parts[part].notes
        lengths = [sum(n.ticks for n in score.parts[0].notes[a:b]) for a, b in score.part_bars(0)]
        self.order = playing_order(score)[0] if order is None else list(order)
        self.lead_in = lead_in(score, self.order)[1]
        self.onsets: list[Onset] = []
        self.bar_starts: list[tuple[int, int]] = []   # (tick, bar) per visit
        tick = 0
        for bar in self.order:
            self.bar_starts.append((tick, bar))
            position = tick
            if bar < len(ranges):
                start, end = ranges[bar]
                for row in range(start, end):
                    self.onsets.append(Onset(position, bar, row))
                    position += notes[row].ticks
            tick += lengths[bar] if bar < len(lengths) else 0
        self.total_ticks = tick
        self._ticks = [o.tick for o in self.onsets]

    def seconds(self, ticks: int) -> float:
        """Time in the MIDI file of a tick of the music."""
        return self.lead_in + ticks / self.ticks_per_quarter * 60.0 / self.tempo

    def ticks(self, seconds: float) -> int:
        return int((seconds - self.lead_in) * self.tempo / 60.0 * self.ticks_per_quarter)

    @property
    def total_seconds(self) -> float:
        return self.seconds(self.total_ticks)

    def at(self, seconds: float) -> Onset | None:
        """The note sounding at ``seconds`` (None before the start or after the end)."""
        if seconds < self.lead_in or not self.onsets or seconds >= self.total_seconds:
            return None
        index = bisect_right(self._ticks, self.ticks(seconds)) - 1
        return self.onsets[index] if index >= 0 else None

    def first_time_of_bar(self, bar: int) -> float | None:
        for tick, visited in self.bar_starts:
            if visited == bar:
                return self.seconds(tick)
        return None

