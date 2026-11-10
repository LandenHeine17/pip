"""The calendar module (D-98, D-99, D-101): Pip's calendar, synced both ways with Google.

    store.py       the events on the Pi and their heads-ups
    sync.py        two-way sync with each connected person's Google calendars
    visibility.py  whose calendar it is, and who may hear about it (private events, "only me" accounts)
    actions.py     what Claude reads about the calendar, and Claude's calendar actions
    dashboard.py   the calendar page's requests, the front page's two-week card, the monitor's box
    offline.py     Pip's own calendar phrases, for when he's offline (core/commands.py runs them)
    api.py         what other modules may use (ctx.module("calendar"))
    module.py      this file: puts the parts together (one Calendar), and the sync loop
    static/        the calendar page; monitor.js (the monitor's box); account.js (its part of a Google account's page)
"""
import asyncio
import logging

from pip_assistant.core.modules import Action, Module

from .actions import ClaudeActions
from .api import CalendarAPI
from .dashboard import DashboardPage
from .offline import CalendarOffline
from .store import CalendarStore
from .sync import CalendarSync
from .visibility import Visibility

log = logging.getLogger(__name__)


class Calendar(Visibility, ClaudeActions, DashboardPage, Module):
    """The calendar: its parts (visibility.py, actions.py, dashboard.py) share this one object (self.store, …)."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.google = ctx.module("google")
        self.store = CalendarStore(ctx.db(), ctx.alerts, ctx.clock)
        self.api = CalendarAPI(self)   # api.py: what other modules may use
        self.sync = CalendarSync(self.store, self.google, ctx.settings, ctx.clock)
        self._wake = asyncio.Event()
        self.store.on_change.append(lambda _eid: self._wake.set())
        if self.google is not None:
            self.google_hooks()
        self.actions = {"add:event": Action(self.check_add, self.do_add),
                        "change:event": Action(self.check_change, self.do_change),
                        "cancel:event": Action(self.check_cancel, self.do_cancel),
                        "look:calendar": Action(self.check_look, self.do_look),
                        "add:calendar": Action(self.check_new_calendar, self.do_new_calendar),
                        "change:calendar": Action(self.check_edit_calendar, self.do_edit_calendar),
                        "cancel:calendar": Action(self.check_delete_calendar, self.do_delete_calendar)}
        self._offline = CalendarOffline(self.store, self.may_hear)   # offline.py: Pip's own calendar phrases

    def offline(self) -> CalendarOffline:
        return self._offline

    def google_hooks(self) -> None:
        hooks = getattr(self.google, "on_connect", None)
        if hooks is not None:
            hooks.append(lambda _aid: self._wake.set())
        self.google.on_disconnect.append(self.forget_account)

    def forget_account(self, aid: str) -> None:
        """Someone disconnected Google: drop Pip's copy of their calendars (nothing is deleted in Google)."""
        for c in self.store.calendars(ignored=True):
            if c["account_id"] == aid:
                self.store.remove_calendar(c["id"])

    # ---------------------------------------------------------------- background
    def tasks(self) -> list:
        return [self._loop()]

    async def _loop(self) -> None:
        last_tidy = 0.0
        while True:
            seen = self.store.version
            if self.ctx.online:
                await self.sync.run_once()
            if self.store.version != seen:
                await self.ctx.publish("changed")
            if self.ctx.clock() - last_tidy > 3600:
                self.store.prune()
                self.store.refresh_alerts()
                last_tidy = self.ctx.clock()
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), self.ctx.settings["sync_minutes"] * 60)
                await asyncio.sleep(1.0)   # let a burst of changes settle
            except asyncio.TimeoutError:
                pass


def setup(ctx) -> Module:
    return Calendar(ctx)
