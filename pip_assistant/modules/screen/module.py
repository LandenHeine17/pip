"""The screen module (step 7, D-111): what's on the monitor, changed by voice or on the dashboard.

The logic is in screen.py (and the grid in layout.py), both ported from Ziggletron. This file plugs
them into Pip: Claude's actions, the line Claude reads about the screen, the Screen page on the
dashboard, and keeping it all in pip.db. Each change publishes module.screen.changed (or .options for
"what can go on the screen?") with "layout": true, which pip-web passes on to the monitor; the monitor then
fetches what it shows (core/dashapi.py tv_snapshot: the layout from tv_layout() here, and each box's data
from the module that adds that kind of box). Other modules' kinds of box arrive in ctx.boxes (add_kinds).
"""
import asyncio
import json

from pip_assistant.core.modules import Action, Call, Invalid, Module

from .screen import KINDS, READY, Screen, ScreenError, add_kinds, box_name, describe, face_word, kind_of

SCHEMA = "CREATE TABLE IF NOT EXISTS screen_state (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL)"


class ScreenModule(Module):
    def __init__(self, ctx):
        self.ctx = ctx
        add_kinds(ctx.boxes)   # the calendar's, a to-do list's, … (each module's module.toml [monitor.<kind>])
        self.db = ctx.db()
        self.db.execute(SCHEMA)
        self.db.commit()
        row = self.db.execute("SELECT data FROM screen_state WHERE id = 1").fetchone()
        try:
            self.screen = Screen(json.loads(row["data"]) if row else None)
        except Exception:   # an unreadable saved state: start from the ready-made screens
            self.screen = Screen()
        self.actions = {verb: Action(self.check, self.do) for verb in
                        ("show_screen", "save_screen", "delete_screen", "add_box", "remove_box", "move_box",
                         "change_box", "arrange_screen", "undo_screen", "screen_fix", "screen_options")}

    # ---------------------------------------------------------------- keeping it
    def _keep(self) -> None:
        self.db.execute("INSERT OR REPLACE INTO screen_state (id, data) VALUES (1, ?)", (json.dumps(self.screen.state()),))
        self.db.commit()

    def _changed(self, kind: str = "changed") -> None:
        """Save, and tell pip-web: the monitor reloads what it shows. Only box kinds travel (no names or notes)."""
        self._keep()
        data = {"layout": True, "boxes": [s["type"] for s in self.screen.view["sections"]], "room": self.screen.room_talk()}
        try:
            asyncio.get_running_loop().create_task(self.ctx.publish(kind, data))
        except RuntimeError:   # no loop running (tests)
            pass

    # ---------------------------------------------------------------- what Claude reads
    def summary(self, call: Call) -> str:
        if call.kind == "join":   # join-in checks run every ~40 s: nothing about the screen (D-102)
            return ""
        return self.screen.summary()

    # ---------------------------------------------------------------- Claude's actions
    def check(self, a: dict, call: Call) -> dict:
        """Only that it makes sense; whether it fits is found out when it's done (and a fix offered)."""
        verb = a.get("do")
        if verb == "add_box" and kind_of(a.get("type")) is None:
            raise Invalid("unknown kind of box")
        if verb in ("remove_box", "move_box", "change_box") and not str(a.get("box") or "").strip():
            raise Invalid("which box")
        if verb in ("show_screen", "delete_screen", "save_screen") and not str(a.get("name") or "").strip():
            raise Invalid("which screen")
        if verb == "arrange_screen" and not isinstance(a.get("boxes"), list):
            raise Invalid("the boxes")
        return a

    def do(self, a: dict, call: Call) -> str:
        s = self.screen
        verb = a["do"]
        try:
            if verb == "show_screen":
                s.show(a["name"])
                said = f"Here's the {s.view['label']} screen."
            elif verb == "save_screen":
                boxes = a.get("boxes") if a.get("boxes") is not None else a.get("sections")
                if boxes is None:   # "save this as the study screen": what's showing, under a new name
                    boxes, face = s.view["sections"], a.get("face") or s.view["face"]
                else:
                    face = a.get("face")
                s.save(a["name"], boxes, face)
                said = f"The {s.view['label']} screen is saved and showing."
            elif verb == "delete_screen":
                ready = s.find_screen(a["name"]) in READY
                label = s.delete(a["name"])
                said = f"Put the {label} screen back the way it was." if ready else f"Deleted the {label} screen."
            elif verb == "add_box":
                section = {k: v for k, v in a.items() if k != "do"}
                s.add_box(section)
                said = f"Added the {box_name(s.view['sections'][-1])} box."
            elif verb == "remove_box":
                said = f"Took the {s.remove_box(a['box'])} box off."
            elif verb == "move_box":
                place = {k: a.get(k) for k in ("x", "y", "w", "h", "size", "align")}
                s.move_box(a["box"], place)
                face = str(a["box"]).strip().lower() in ("face", "the face", "your face", "you", "pip")
                said = f"Moved my face: it's {face_word(s.view['face'])} now." if face else f"Moved the {a['box']} box."
            elif verb == "change_box":
                changes = {k: v for k, v in a.items() if k not in ("do", "box")}
                s.change_box(a["box"], changes)
                said = f"Changed the {a['box']} box."
            elif verb == "arrange_screen":   # several changes at once: one step, one undo
                s.arrange(a["boxes"], a.get("face"))
                said = "Done: " + describe(s.view) + "."
            elif verb == "undo_screen":
                s.undo()
                said = f"Put it back: the {s.view['label']} screen, {describe(s.view).split(';')[0]}."
            elif verb == "screen_fix":
                s.apply_fix()
                said = "Done: " + describe(s.view) + "."
            elif verb == "screen_options":
                self._changed("options")   # the monitor lights up its list of boxes for a few seconds
                return "The screen can show: " + ", ".join(label for label, _ in KINDS.values()) + \
                    ". And these screens: " + ", ".join(v["label"] for v in s.views().values()) + "."
            else:
                raise Invalid(verb)
        except ScreenError as e:
            self._keep()   # a proposed fix waits for an okay (or nothing changed)
            if s.pending:
                return f"{_sentence(e)} I can {s.pending['words']}. Okay?"
            return _sentence(e)
        self._changed()
        return said

    # ---------------------------------------------------------------- the monitor and the dashboard
    def tv_layout(self) -> dict:
        return self.screen.for_tv()

    async def dashboard(self, action: str, args: dict) -> dict:
        s = self.screen
        try:
            if action == "show":
                s.show(str(args.get("name", "")))
            elif action == "undo":
                s.undo()
            elif action == "delete":
                s.delete(str(args.get("name", "")))
            elif action != "status":
                return {"error": f"unknown action {action}"}
        except ScreenError as e:
            return {"error": _sentence(e)}
        if action != "status":
            self._changed()
        return {"showing": s.name, "view": s.view, "screens": s.list_screens(), "can_undo": bool(s.history),
                "palette": s.for_tv()["palette"], "summary": describe(s.view)}


def _sentence(e: Exception) -> str:
    text = str(e).strip()
    text = text[:1].upper() + text[1:]
    return text if text.endswith((".", "?", "!")) else text + "."


def setup(ctx) -> Module:
    return ScreenModule(ctx)
