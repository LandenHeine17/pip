"""What's on the monitor, and changing it (step 7, D-111). Ported from Ziggletron's core/screen.py.

A *screen* (Ziggletron called them dashboards) is the face's size plus a list of boxes on a 12×8 grid
(layout.py). There are four ready-made screens (Default, Morning, Focus, Night) and up to MAX_SAVED
more. Changing the screen that's showing changes that screen and keeps it; a ready-made one that was
changed can be put back.

Every change is checked first. When something doesn't fit, nothing changes: Pip gets a proposed fix
("make the face smaller", "take the notes box off") to offer, and applies it if they say okay
(PIP-NEW-061). The last UNDO_KEEP screens are kept, so "undo that" puts the last one back exactly.
"""
import copy
import re

from .layout import COLS, FACES, LABELS, PLACE, ROWS, SIZES, arrange, clean_face, clean_place

# Each kind of box, by the name people would use (the monitor lists them), and what it can be told. These are
# the screen's own; other modules add theirs (the calendar, a to-do list) in their module.toml's
# [monitor.<kind>] (add_kinds below), and draw them in their static/monitor.js.
KINDS = {
    "clock": ("Clock", ()),
    "weather": ("Weather", ()),
    "timers": ("Timers & alarms", ()),
    "notes": ("Notes", ("title", "lines")),
    "health": ("Pi health", ()),
    "conversation": ("Conversation", ("room",)),
}
OWN_KINDS = frozenset(KINDS)
ONCE_ONLY = {"clock", "weather", "timers", "health", "conversation"}  # the same thing twice is never useful
ALIASES = {  # other words for a kind of box, so "put the forecast up" finds the weather
    **{label.lower(): kind for kind, (label, _) in KINDS.items()},
    "time": "clock", "forecast": "weather", "timer": "timers", "alarms": "timers",
    "note": "notes", "text": "notes", "pi": "health", "vitals": "health", "stats": "health", "diagnostics": "health",
    "chat": "conversation", "talk": "conversation", "captions": "conversation",
}
COLORS = {  # what a box can be coloured, each bright enough to read on the dark screen
    "red": "#ff6b6b", "orange": "#ffa04d", "yellow": "#ffd25e", "green": "#6fdc7a", "teal": "#4fd1c5",
    "blue": "#5aa9ff", "purple": "#a58cff", "pink": "#ff7fb0", "white": "#f1eee8", "gray": "#8c8c9a",
}
NO_COLOR = ("none", "normal", "default", "plain", "no color", "off")
MAX_BOXES = 8
MAX_SAVED = 20
MAX_LINES = 12
MAX_LINE = 200
MAX_NAME = 40
UNDO_KEEP = 10
REARRANGE = ("move the other boxes around to make room", "fit every box in afresh")   # done without asking


class ScreenError(ValueError):
    """A change that can't be made. `fix`, if there is one, is a change that would work instead."""

    def __init__(self, message: str, fix: dict | None = None):
        super().__init__(message)
        self.fix = fix


def _built_in(label: str, face, *boxes, dim: bool = False) -> dict:
    return {"label": label, "face": clean_face(face), "sections": [{"type": b} if isinstance(b, str) else b for b in boxes],
            **({"dim": True} if dim else {})}


BUILT_IN = {  # the ready-made screens (fitted once, below)
    "default": _built_in("Default", "large", {"type": "clock", "x": 7, "y": 0, "w": 5, "h": 2},
                         {"type": "weather", "x": 7, "y": 2, "w": 5, "h": 2}, {"type": "timers", "x": 7, "y": 4, "w": 5, "h": 2},
                         {"type": "calendar", "x": 7, "y": 6, "w": 5, "h": 2}),
    "morning": _built_in("Morning", "small", "clock", "weather", "calendar", "todos"),   # (a module's box is left out while it's off)
    "focus": _built_in("Focus", "small", {"type": "clock", "x": 4, "y": 0, "w": 8, "h": 4}, {"type": "timers", "x": 4, "y": 4, "w": 8, "h": 4}),
    "night": _built_in("Night", "small", {"type": "clock", "x": 4, "y": 0, "w": 8, "h": 3}, dim=True),
}


# ---------------------------------------------------------------- checking what Claude asked for
def kind_of(name) -> str | None:
    name = str(name or "").strip().lower()
    return name if name in KINDS else ALIASES.get(name) or ALIASES.get(name.removesuffix(" box"))


def clean_color(color) -> str:
    """A colour name or "#rrggbb"; "" for plain. Raises ScreenError for anything else."""
    c = str(color or "").strip().lower().replace("grey", "gray")
    if not c or c in NO_COLOR:
        return ""
    if c in COLORS or re.fullmatch(r"#[0-9a-f]{6}", c):
        return c
    raise ScreenError(f"a box can be {', '.join(COLORS)}, a colour like #3399ff, or plain")


def clean_name(name) -> str:
    """A screen's name: what people call it, as its key ("Study time" → "study time")."""
    n = re.sub(r"\s+", " ", str(name or "")).strip().lower().removesuffix(" screen").strip()
    if not n:
        raise ScreenError("a screen needs a name")
    return n[:MAX_NAME]


def _field(kind: str, key: str, value):
    if key == "lines":
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list):
            raise ScreenError("a note's lines should be a list of text")
        return [str(x).strip()[:MAX_LINE] for x in value if str(x).strip()][:MAX_LINES]
    if key == "room":
        return bool(value)
    return str(value).strip()[:MAX_NAME]


def clean_sections(sections) -> list[dict]:
    """Boxes as Claude wrote them, made safe to keep and draw."""
    if not isinstance(sections, list):
        raise ScreenError("a screen needs a list of boxes")
    cleaned, seen = [], set()
    for section in sections:
        section = {"type": section} if isinstance(section, str) else section
        kind = kind_of(section.get("type")) if isinstance(section, dict) else None
        if kind is None:
            raise ScreenError(f"there's no {section.get('type') if isinstance(section, dict) else section!r} box "
                              f"(there's {', '.join(KINDS)})")
        if kind in ONCE_ONLY and kind in seen:
            continue
        seen.add(kind)
        clean = {"type": kind}
        for key in KINDS[kind][1]:
            if section.get(key) not in (None, ""):
                clean[key] = _field(kind, key, section[key])
        if color := clean_color(section.get("color") or section.get("colour")):
            clean["color"] = color
        try:
            clean.update(clean_place(section))
        except ValueError as e:
            raise ScreenError(str(e)) from None
        cleaned.append(clean)
    if len(cleaned) > MAX_BOXES:
        raise ScreenError(f"a screen can have at most {MAX_BOXES} boxes: take one off first")
    return cleaned


def box_name(section: dict) -> str:
    if section["type"] == "notes" and section.get("title"):
        return section["title"]
    if section.get("list"):
        return f"{section['list']} list"
    return KINDS[section["type"]][0]


def _unplaced(section: dict) -> dict:
    return {k: v for k, v in section.items() if k not in PLACE}


def _overlap(a: dict, b: dict) -> bool:
    if not all(k in a for k in PLACE) or not all(k in b for k in PLACE):
        return False
    return a["x"] < b["x"] + b["w"] and b["x"] < a["x"] + a["w"] and a["y"] < b["y"] + b["h"] and b["y"] < a["y"] + a["h"]


def face_word(face: dict) -> str:
    for word, (w, h) in FACES.items():
        if (face["w"], face["h"]) == (w, h) and word != "none":
            return word
    return f"{face['w']}×{face['h']}"


def make(label: str, face, sections, dim: bool = False, keep: int | None = None) -> dict:
    """A screen, fitted and checked: every box placed on the grid. Raises ScreenError with a fix if it
    doesn't fit (`keep`: the box just asked for, which the fix leaves as asked if it can)."""
    try:
        face = clean_face(face)
    except ValueError as e:
        raise ScreenError(str(e)) from None
    sections = clean_sections(sections)
    try:
        shown, placed = arrange(face, sections)
    except ValueError as e:
        raise ScreenError(f"That doesn't fit: {e}.", fix=propose_fix(label, face, sections, dim, keep)) from None
    view = {"label": label, "face": face, "face_shown": shown, "sections": placed}
    if dim:
        view["dim"] = True
    return view


def propose_fix(label: str, face: dict, sections: list[dict], dim: bool = False, keep: int | None = None) -> dict | None:
    """A change that would fit, as close to what was asked as possible, in words and as a screen.
    In order: fit the other boxes in afresh (their places dropped), then the same with a smaller face,
    then without the biggest box that wasn't the one just asked for."""
    asked = sections[keep] if keep is not None and sections else None
    sized = {k: v for k, v in asked.items() if k not in ("x", "y")} if asked else None
    smaller = [w for w in ("medium", "small", "tiny") if FACES[w][0] * FACES[w][1] < face["w"] * face["h"]]
    everything = [_unplaced(s) for s in sections]

    def others(mine):
        return [mine if s is asked else _unplaced(s) for s in sections]

    def tries():
        if asked is not None:
            yield "move the other boxes around to make room", face, others(asked)
            if sized != asked:
                yield f"put the {box_name(asked)} box somewhere else at that size", face, others(sized)
            for word in smaller:
                yield f"make the face {word} to make room", clean_face(word), others(sized)
            if sized != _unplaced(asked):
                yield f"make the {box_name(asked)} box smaller so it fits", face, everything
        else:
            yield "fit every box in afresh", face, everything
        for word in smaller:
            yield f"make the face {word} and fit the boxes in around it", clean_face(word), everything
        for drop in sorted([s for s in sections if s is not asked], key=lambda s: -(s.get("w", 4) * s.get("h", 3))):
            yield f"take the {box_name(drop)} box off", clean_face("small"), [_unplaced(s) for s in sections if s is not drop]
    for words, f, s in tries():
        try:
            shown, placed = arrange(f, s)
        except ValueError:
            continue
        view = {"label": label, "face": f, "face_shown": shown, "sections": placed}
        if dim:
            view["dim"] = True
        return {"words": words, "view": view}
    return None


def describe(view: dict) -> str:
    """The screen in a few words, for Pip to say back and for Claude to know."""
    boxes = ", ".join(box_name(s) + (f" ({s['color']})" if s.get("color") else "") for s in view["sections"])
    face = view["face_shown"] if "face_shown" in view else view["face"]
    return f"face {face_word(view['face'])} ({face['w']}×{face['h']})" + (f"; {boxes}" if boxes else "; nothing else")


def places(view: dict) -> str:
    """Where each box is, on the grid (Claude needs this to move them exactly)."""
    return "; ".join(f"{box_name(s)} x{s['x']} y{s['y']} w{s['w']} h{s['h']}" for s in view["sections"])


# ---------------------------------------------------------------- the screen and its changes
def _ready() -> dict:
    """The ready-made screens, fitted, with only the kinds of box there are now."""
    return {n: make(v["label"], v["face"], [b for b in v["sections"] if b["type"] in KINDS], v.get("dim", False))
            for n, v in BUILT_IN.items()}


READY = _ready()


def add_kinds(boxes: dict) -> None:
    """The kinds of box other modules add (their module.toml's [monitor.<kind>], gathered by core/modules.py)."""
    for kind, b in boxes.items():
        if kind in OWN_KINDS:
            continue   # a module can't change the screen's own boxes
        KINDS[kind] = (b["label"], tuple(b["takes"]))
        (ONCE_ONLY.add if b["once"] else ONCE_ONLY.discard)(kind)
        ALIASES[b["label"].lower()] = kind
        for word in b["aliases"]:
            ALIASES.setdefault(word, kind)
        SIZES[kind], LABELS[kind] = tuple(b["size"]), b["name"]
    READY.clear()
    READY.update(_ready())


class Screen:
    """What's showing and what's kept. `state` is plain data (module.py keeps it as JSON in pip.db)."""

    def __init__(self, state: dict | None = None):
        s = state or {}
        self.saved: dict = s.get("saved", {})          # name → screen (changed ready-made ones too)
        self.name: str = s.get("name", "default")
        self.history: list = s.get("history", [])      # earlier [name, screen] pairs, newest last (undo)
        self.pending: dict | None = s.get("pending")  # a fix waiting for an okay: {"words", "name", "view"}
        if self.name not in self.views():
            self.name = "default"

    def state(self) -> dict:
        return {"saved": self.saved, "name": self.name, "history": self.history, "pending": self.pending}

    def views(self) -> dict:
        return {**READY, **self.saved}

    @property
    def view(self) -> dict:
        return self.views()[self.name]

    def find_screen(self, name) -> str:
        n = clean_name(name)
        views = self.views()
        if n in views:
            return n
        for key, v in views.items():
            if v["label"].lower() == n:
                return key
        raise ScreenError(f"there's no {name!r} screen (there's {', '.join(v['label'] for v in views.values())})")

    def _remember(self) -> None:
        self.history = [*self.history, [self.name, copy.deepcopy(self.view)]][-UNDO_KEEP:]

    def _set(self, name: str, view: dict) -> None:
        self._remember()
        if name in READY and view == READY[name]:
            self.saved.pop(name, None)
        else:
            self.saved[name] = view
        self.name = name
        self.pending = None

    # -- what Claude (or the dashboard) asks for
    def show(self, name) -> dict:
        key = self.find_screen(name)
        if key != self.name:
            self._remember()
            self.name = key
        self.pending = None
        return self.view

    def save(self, name, sections, face=None) -> dict:
        key = clean_name(name)
        views = self.views()
        if key not in views and len([n for n in self.saved if n not in BUILT_IN]) >= MAX_SAVED:
            raise ScreenError(f"there can be at most {MAX_SAVED} screens of your own: delete one first")
        old = views.get(key)
        label = old["label"] if old else str(name).strip()[:MAX_NAME].removesuffix(" screen").strip().capitalize()
        face = face if face not in (None, "") else (old["face"] if old else "small")
        view = self._try(key, lambda: make(label, face, sections, bool(old and old.get("dim"))))
        self._set(key, view)
        return view

    def delete(self, name) -> str:
        key = self.find_screen(name)
        if key in BUILT_IN and key not in self.saved:
            raise ScreenError(f"the {BUILT_IN[key]['label']} screen is one of the ready-made ones and hasn't been changed")
        label = self.views()[key]["label"]
        self.history = [*self.history, [key, copy.deepcopy(self.saved.pop(key))]][-UNDO_KEEP:]
        if self.name == key and key not in BUILT_IN:
            self.name = "default"
        self.pending = None
        return label

    def add_box(self, section: dict) -> dict:
        new = clean_sections([section])[0]
        sections = list(self.view["sections"])
        if new["type"] in ONCE_ONLY and any(s["type"] == new["type"] for s in sections):
            raise ScreenError(f"the {KINDS[new['type']][0]} box is already on the screen: move it instead")
        others = [_unplaced(s) if _overlap(s, new) else s for s in sections]
        return self._edit(self.view["face"], [*others, new], -1)

    def remove_box(self, box) -> str:
        sections = list(self.view["sections"])
        gone = sections.pop(self._find(box))
        self._edit(self.view["face"], sections)
        return box_name(gone)

    def move_box(self, box, place: dict) -> dict:
        """Move or resize one box ("face" for the face: its size, or align top/middle/bottom)."""
        place = {k: v for k, v in place.items() if v not in (None, "")}
        if not place:
            raise ScreenError("say where it should go or how big it should be")
        if str(box).strip().lower() in ("face", "the face", "your face", "you", "pip"):
            now = self.view["face"]
            size = place.get("size")
            if size:
                try:
                    w, h = FACES[str(size).lower()]
                except KeyError:
                    raise ScreenError(f"the face can be {', '.join(w for w in FACES if w != 'none')}") from None
            else:
                w, h = place.get("w", now["w"]), place.get("h", now["h"])
            align = place.get("align", now.get("align", "middle"))
            face = {"w": w, "h": h, "align": align}
            if (w, h) == (now["w"], now["h"]):    # just moving it in its box: nothing else moves
                return self._edit(face, self.view["sections"])
            # Everything is fitted in afresh around it, rather than leaving gaps where the face was.
            return self._edit(face, [_unplaced(s) for s in self.view["sections"]])
        sections = list(self.view["sections"])
        i = self._find(box)
        try:
            wanted = clean_place({**place, "type": sections[i]["type"]})
        except ValueError as e:
            raise ScreenError(str(e)) from None
        target = {**sections[i], **wanted}
        if not all(k in place for k in ("x", "y")):    # a new size where it is, unless that's off the screen
            target["x"] = min(target["x"], COLS - target["w"])
            target["y"] = min(target["y"], ROWS - target["h"])
            face = self.view["face_shown"]
            if _overlap(target, {"x": 0, "y": 0, **face}):   # or on the face: then wherever it fits at that size
                target = {k: v for k, v in target.items() if k not in ("x", "y")}
        others = [_unplaced(s) if _overlap(s, target) else s for s in sections[:i] + sections[i + 1:]]
        return self._edit(self.view["face"], others[:i] + [target] + others[i:], i)

    def change_box(self, box, changes: dict) -> dict:
        """What a box shows or its colour: a note's title and lines, which to-do list, room talk on or off."""
        sections = [dict(s) for s in self.view["sections"]]
        i = self._find(box)
        s = sections[i]
        allowed = KINDS[s["type"]][1]
        for key, value in changes.items():
            if key in ("color", "colour"):
                s.pop("color", None)
                if c := clean_color(value):
                    s["color"] = c
            elif key in allowed:
                s[key] = _field(s["type"], key, value)
            elif value not in (None, ""):
                raise ScreenError(f"the {box_name(s)} box can't be given a {key}")
        return self._edit(self.view["face"], sections, i)

    def arrange(self, sections, face=None) -> dict:
        """Several changes at once (owner's feedback, 2026-11-10): the whole new layout of the screen that's showing,
        boxes moved, resized, added and taken off together, as one change (one undo). A box Claude lists that's
        already on the screen keeps what it shows (a note's lines, which list, its colour) unless it's changed;
        a box left out comes off. Everything is checked first: if it doesn't fit, a fix is offered as usual."""
        if not isinstance(sections, list):
            raise ScreenError("a screen needs a list of boxes")
        old, used, merged = self.view["sections"], set(), []
        for s in sections:
            if not isinstance(s, (str, dict)):
                raise ScreenError("each box is a kind of box, like {\"type\": \"clock\"}")
            s = {"type": s} if isinstance(s, str) else dict(s)
            kind = kind_of(s.get("type"))
            match = next((i for i, o in enumerate(old) if i not in used and o["type"] == kind and
                          all(o.get(k) == s[k] for k in ("title", "list") if s.get(k))), None)
            if match is not None:
                used.add(match)
                s = {**{k: v for k, v in old[match].items() if k not in PLACE}, **s}
            merged.append(s)
        return self._edit(face if face not in (None, "") else self.view["face"], merged)

    def undo(self) -> dict:
        if not self.history:
            raise ScreenError("there's nothing to undo")
        name, view = self.history.pop()
        if name in READY and view == READY[name]:
            self.saved.pop(name, None)         # back to the ready-made one, unchanged
        else:
            self.saved[name] = view
        self.name = name
        self.pending = None
        return view

    def apply_fix(self) -> dict:
        if not self.pending:
            raise ScreenError("there's no change waiting")
        fix = self.pending
        self._set(fix["name"], fix["view"])
        return fix["view"]

    # -- helpers
    def _find(self, box) -> int:
        wanted = str(box or "").strip().lower()
        kind = kind_of(wanted)
        sections = self.view["sections"]
        for i, s in enumerate(sections):
            if wanted in (box_name(s).lower(), str(s.get("title", "")).lower(), str(s.get("list", "")).lower()):
                return i
        for i, s in enumerate(sections):
            if s["type"] == kind:
                return i
        names = ", ".join(box_name(s) for s in sections) or "nothing"
        raise ScreenError(f"there's no {box!r} box on the screen (there's {names}, and the face)")

    def _try(self, name: str, build):
        try:
            return build()
        except ScreenError as e:
            if e.fix:
                self.pending = {"words": e.fix["words"], "name": name, "view": e.fix["view"]}
            raise

    def _edit(self, face, sections, index: int | None = None) -> dict:
        v = self.view
        keep = None if index is None else index % len(sections)
        try:
            view = make(v["label"], face, sections, v.get("dim", False), keep)
        except ScreenError as e:
            # Moving the other boxes about is what anyone would expect, so that's just done. Only a
            # smaller face or a box coming off is asked about first.
            if not (e.fix and e.fix["words"] in REARRANGE):
                if e.fix:
                    self.pending = {"words": e.fix["words"], "name": self.name, "view": e.fix["view"]}
                raise
            view = e.fix["view"]
        self._set(self.name, view)
        return view["face_shown"] if index is None else view["sections"][index]

    # -- for the monitor, the dashboard and Claude
    def for_tv(self) -> dict:
        v = self.view
        on = {s["type"] for s in v["sections"]}
        return {"name": self.name, "view": v, "palette": COLORS,
                "kinds": [{"type": k, "label": label, "on": k in on} for k, (label, _) in KINDS.items()]}

    def room_talk(self) -> bool:
        """Room talk goes to the monitor only while a conversation box showing it is up (D-111)."""
        return any(s["type"] == "conversation" and s.get("room", True) for s in self.view["sections"])

    def list_screens(self) -> list[dict]:
        return [{"name": n, "label": v["label"], "built_in": n in BUILT_IN, "changed": n in BUILT_IN and n in self.saved,
                 "showing": n == self.name} for n, v in self.views().items()]

    def summary(self) -> str:
        v = self.view
        out = f"Screen (the monitor): {v['label']}: {describe(v)}. Places: {places(v) or 'none'}."
        out += " Screens: " + ", ".join(v["label"] for v in self.views().values()) + "."
        if self.pending:
            out += f" Waiting for an okay to: {self.pending['words']} (if they agree: {{\"do\": \"screen_fix\"}})."
        return out
