"""Where the boxes go on the monitor (step 7, D-111). Ported from Ziggletron's core/layout.py, which
worked well; only the kinds of box are Pip's.

The screen is a grid, COLS wide and ROWS tall. Pip's face is a box in the top left corner that can
shrink but never go (MIN_FACE), and every other thing on the screen is a box of its own. A box can be
given a place and size, just a size, or nothing: whatever isn't placed is fitted into the space that's
left, and then grows into any space around it nobody's using."""

COLS, ROWS = 12, 8
MIN_FACE = (3, 2)  # w, h: always at least this much of the top left is the face
MIN_BOX = (2, 2)
FACES = {"large": (7, 8), "medium": (5, 5), "small": (4, 4), "none": MIN_FACE, "tiny": MIN_FACE}
SIZES = {  # how big each kind of box would like to be, when it isn't told
    "clock": (4, 2), "weather": (4, 3), "timers": (4, 2), "calendar": (4, 3), "todos": (4, 3),
    "notes": (4, 3), "health": (4, 3), "conversation": (5, 4),
}
PLACE = ("x", "y", "w", "h")
ALIGN = ("top", "middle", "bottom")  # where the face sits in its box: the box itself stays in the corner


def clean_face(face) -> dict:
    """A face size from Claude: a word ("large", "small"...) or {"w", "h"}. Raises ValueError."""
    if isinstance(face, str):
        if face not in FACES:
            raise ValueError(f'the face can be {", ".join(FACES)}, or a size like {{"w": 5, "h": 4}}')
        w, h = FACES[face]
    elif isinstance(face, dict):
        w, h = _number(face.get("w"), "the face's w"), _number(face.get("h"), "the face's h")
        align = face.get("align") or "middle"
        if align not in ALIGN:
            raise ValueError(f"the face's align can be {', '.join(ALIGN)}")
    else:
        raise ValueError('the face should be a word like "small" or a size like {"w": 5, "h": 4}')
    if not (MIN_FACE[0] <= w <= COLS and MIN_FACE[1] <= h <= ROWS):
        raise ValueError(f"the face must be {MIN_FACE[0]}-{COLS} wide and {MIN_FACE[1]}-{ROWS} tall")
    if isinstance(face, dict) and align != "middle":
        return {"w": w, "h": h, "align": align}
    return {"w": w, "h": h}


def clean_place(section: dict) -> dict:
    """Just the x, y, w, h that were given, checked. Raises ValueError."""
    place = {}
    for key in PLACE:
        if section.get(key) is not None:
            place[key] = _number(section[key], f"the {section.get('type', 'box')} box's {key}")
    if "w" in place and not MIN_BOX[0] <= place["w"] <= COLS:
        raise ValueError(f"a box must be {MIN_BOX[0]}-{COLS} wide")
    if "h" in place and not MIN_BOX[1] <= place["h"] <= ROWS:
        raise ValueError(f"a box must be {MIN_BOX[1]}-{ROWS} tall")
    if "x" in place and not 0 <= place["x"] < COLS or "y" in place and not 0 <= place["y"] < ROWS:
        raise ValueError(f"x goes from 0 to {COLS - 1} and y from 0 to {ROWS - 1}")
    return place


def arrange(face: dict, sections: list[dict]) -> tuple[dict, list[dict]]:
    """The face, and every section with a place (x, y, w, h) that's on the screen, clear of the face
    and of each other. Sections given a place keep it exactly; the face grows down into rows under it
    that nothing uses. Raises ValueError, saying what's in the way."""
    face = {"w": face["w"], "h": face["h"]}
    sections = _boxes(face, [dict(s) for s in sections])
    grid = [[None] * COLS for _ in range(ROWS)]
    for section in sections:
        _fill(grid, section, _name(section))
    while face["h"] < ROWS and not _taken(grid, {"x": 0, "y": face["h"], "w": face["w"], "h": 1}):
        face["h"] += 1
    return face, sections


def _boxes(face: dict, sections: list[dict]) -> list[dict]:
    grid: list[list[str | None]] = [[None] * COLS for _ in range(ROWS)]
    _fill(grid, {"x": 0, "y": 0, **face}, "the face")
    nothing_placed = sections and not any(k in section for section in sections for k in PLACE)
    if nothing_placed and (packed := _pack(face, sections)):  # tidy columns beside the face
        for section, box in zip(sections, packed, strict=True):
            _fill(grid, box, _name(section))
        for section, box in zip(sections, packed, strict=True):
            _grow(grid, box, _name(section))
        return [{**section, **box} for section, box in zip(sections, packed, strict=True)]
    placed: list[dict | None] = [None] * len(sections)
    for i, section in enumerate(sections):  # the ones told exactly where to go
        if all(k in section for k in PLACE):
            box = {k: section[k] for k in PLACE}
            if box["x"] + box["w"] > COLS or box["y"] + box["h"] > ROWS:
                raise ValueError(f"the {_name(section)} box goes off the screen ({_where(box)}; it's {COLS}x{ROWS})")
            if clash := _taken(grid, box):
                raise ValueError(f"the {_name(section)} box ({_where(box)}) overlaps {clash}")
            _fill(grid, box, _name(section))
            placed[i] = box
    grown = []
    sized_first = sorted(range(len(sections)), key=lambda i: not ("w" in sections[i] or "h" in sections[i]))
    for i in sized_first:  # then the rest, wherever there's room (the ones given a size first)
        section = sections[i]
        if placed[i] is None:
            want = (section.get("w") or SIZES[section["type"]][0], section.get("h") or SIZES[section["type"]][1])
            box = _find_room(grid, want, section.get("x"), section.get("y"))
            if box is None:
                raise ValueError(
                    f"there's no room left for the {_name(section)} box"
                )
            if any(k in section and section[k] != box[k] for k in ("w", "h")):
                raise ValueError(f"there's no room for the {_name(section)} box at that size ({section.get('w', box['w'])} wide, "
                                 f"{section.get('h', box['h'])} tall)")
            _fill(grid, box, _name(section))
            placed[i] = box
            if "w" not in section and "h" not in section:
                grown.append(i)
    for i in grown:  # boxes nobody sized spread into the space around them
        _grow(grid, placed[i], _name(sections[i]))
    return [{**section, **box} for section, box in zip(sections, placed, strict=True)]


def describe_place(box: dict) -> str:
    return f"x{box['x']} y{box['y']} w{box['w']} h{box['h']}"


def _find_room(grid, want: tuple[int, int], x=None, y=None) -> dict | None:
    """The first free spot, top to bottom then left to right, for the size wanted or the biggest
    smaller size that fits (and at x, y if those were given)."""
    sizes = sorted(
        {(w, h) for w in range(MIN_BOX[0], min(want[0], COLS) + 1) for h in range(MIN_BOX[1], min(want[1], ROWS) + 1)},
        key=lambda size: (-size[0] * size[1], -size[0]),
    )
    for w, h in sizes:
        for top in [y] if y is not None else range(ROWS - h + 1):
            for left in [x] if x is not None else range(COLS - w + 1):
                box = {"x": left, "y": top, "w": w, "h": h}
                if left + w <= COLS and top + h <= ROWS and not _taken(grid, box):
                    return box
    return None


def _pack(face: dict, sections: list[dict]) -> list[dict] | None:
    """Boxes stacked in columns: to the right of the face, and under it once those are full. Each
    column's height is shared out by how tall each box would like to be. As few columns as fit (two
    to start with, if there's lots of room). None if they don't fit that way."""
    right = COLS - face["w"]
    fewest = 2 if right >= 8 and len(sections) > 2 else 1
    for count in range(fewest, max(fewest, right // MIN_BOX[0]) + 1):
        if packed := _pack_in(face, sections, count):
            return packed
    return None


def _pack_in(face: dict, sections: list[dict], count: int) -> list[dict] | None:
    right = COLS - face["w"]
    columns = []
    if right >= MIN_BOX[0]:
        x = face["w"]
        for i in range(count):
            width = right // count + (1 if i < right % count else 0)
            columns.append({"x": x, "y": 0, "w": width, "h": ROWS, "boxes": []})
            x += width
    if ROWS - face["h"] >= MIN_BOX[1]:
        columns.append({"x": 0, "y": face["h"], "w": face["w"], "h": ROWS - face["h"], "boxes": [], "spare": True})
    tall = {i: SIZES[s["type"]][1] for i, s in enumerate(sections)}
    for i in range(len(sections)):
        roomy = [c for c in columns if (len(c["boxes"]) + 1) * MIN_BOX[1] <= c["h"]]
        if not roomy:
            return None
        # the least full column, keeping the one under the face for when the others are full
        column = min(roomy, key=lambda c: (c.get("spare", False), sum(tall[j] for j in c["boxes"]) / c["h"]))
        column["boxes"].append(i)
    placed: list[dict | None] = [None] * len(sections)
    for column in columns:
        if column["boxes"]:
            y = column["y"]
            for i, height in zip(column["boxes"], _share(column["h"], [tall[i] for i in column["boxes"]]), strict=True):
                placed[i] = {"x": column["x"], "y": y, "w": column["w"], "h": height}
                y += height
    return placed


def _share(total: int, wants: list[int]) -> list[int]:
    """`total` rows shared out in proportion to `wants`, each at least MIN_BOX tall."""
    spare = total - MIN_BOX[1] * len(wants)
    extra = [spare * w / sum(wants) for w in wants]
    heights = [MIN_BOX[1] + int(e) for e in extra]
    for i in sorted(range(len(wants)), key=lambda i: extra[i] - int(extra[i]), reverse=True)[: total - sum(heights)]:
        heights[i] += 1
    return heights


def _grow(grid, box: dict, name: str):
    """Spread a box into free space on any side, a whole edge at a time."""
    grew = True
    while grew:
        grew = False
        for side in ("left", "right", "up", "down"):
            edge = {
                "left": {**box, "x": box["x"] - 1, "w": 1},
                "right": {**box, "x": box["x"] + box["w"], "w": 1},
                "up": {**box, "y": box["y"] - 1, "h": 1},
                "down": {**box, "y": box["y"] + box["h"], "h": 1},
            }[side]
            if edge["x"] < 0 or edge["y"] < 0 or edge["x"] + edge["w"] > COLS or edge["y"] + edge["h"] > ROWS:
                continue
            if _taken(grid, edge):
                continue
            _fill(grid, edge, name)
            if side in ("left", "right"):
                box["w"] += 1
                box["x"] = min(box["x"], edge["x"])
            else:
                box["h"] += 1
                box["y"] = min(box["y"], edge["y"])
            grew = True


def _taken(grid, box: dict) -> str | None:
    for row in range(box["y"], min(box["y"] + box["h"], ROWS)):
        for col in range(box["x"], min(box["x"] + box["w"], COLS)):
            if grid[row][col]:
                return grid[row][col] if grid[row][col] == "the face" else f"the {grid[row][col]} box"
    return None


def _fill(grid, box: dict, name: str):
    for row in range(box["y"], box["y"] + box["h"]):
        for col in range(box["x"], box["x"] + box["w"]):
            grid[row][col] = name


LABELS = {"timers": "timers", "todos": "to-do list", "health": "Pi health"}


def _name(section: dict) -> str:
    if section.get("title"):
        return section["title"]
    if section.get("list"):
        return f"{section['list']} list"
    return LABELS.get(section["type"], section["type"])


def _where(box: dict) -> str:
    return describe_place(box)


def _number(value, what: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{what} should be a whole number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{what} should be a whole number") from None
    if not number.is_integer():
        raise ValueError(f"{what} should be a whole number")
    return int(number)
