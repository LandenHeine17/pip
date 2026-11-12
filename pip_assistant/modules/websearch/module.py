"""Web search (D-125): a look-up Claude can ask for, answered by the Brave Search API.

Before D-125 Pip used Claude's own built-in web search: Claude read whole result pages before answering, and
answers took 10-15 s (trace, 2026-11-12). Now Claude asks for a look-up instead,

    ACTION {"do": "look", "kind": "web", "query": "huskers football score"}

and pip-core (core/acting.py, Acting.look_up; the same path as the calendar's look-ups) calls `do_look` here,
then asks Claude again with what it returned: a few results' titles and descriptions, about 700 letters.

Nothing is kept: no tables, and the query is never logged (the trace records only that a look-up happened).
The key is the module's own section of ~/pip-data/secrets.toml ([websearch] api_key). The module may reach
only api.search.brave.com (module.toml permissions, enforced by ctx.http).
"""
import html
import re

from pip_assistant.core.modules import Action, Call, Invalid, Module

URL = "https://api.search.brave.com/res/v1/web/search"
MAX_LETTERS = 700
TAG = re.compile(r"<[^>]+>")


def plain(text: str) -> str:
    """Brave marks the matching words with <strong>: just the words."""
    return re.sub(r"\s+", " ", html.unescape(TAG.sub("", text or ""))).strip()


class WebSearch(Module):
    def __init__(self, ctx):
        self.ctx = ctx
        self.actions = {"look:web": Action(self.check_look, self.do_look)}

    def check_look(self, a: dict, call: Call) -> str:
        query = re.sub(r"\s+", " ", str(a.get("query") or "")).strip()
        if not 1 <= len(query) <= 200:
            raise Invalid("a web search needs a short query")
        if not self.ctx.secrets.get("api_key"):
            raise Invalid("no Brave Search API key in secrets.toml [websearch]")
        return query

    async def do_look(self, query: str, call: Call) -> str:
        n = max(1, min(10, int(self.ctx.settings.get("results", 3))))
        async with self.ctx.http(timeout=float(self.ctx.settings.get("timeout_seconds", 4.0))) as http:
            r = await http.get(URL, params={"q": query, "count": n},
                               headers={"Accept": "application/json", "X-Subscription-Token": self.ctx.secrets.get("api_key")})
            r.raise_for_status()
            results = (r.json().get("web") or {}).get("results") or []
        lines, used = [], 0
        for i, x in enumerate(results[:n], 1):
            line = f"{i}. {plain(x.get('title', ''))}: {plain(x.get('description', ''))}"
            if x.get("age"):
                line += f" ({plain(x['age'])})"
            line = line[:max(0, MAX_LETTERS - used)]
            if not line:
                break
            lines.append(line)
            used += len(line)
        if not lines:
            raise LookupError("no results")
        return f"Web results for '{query}': " + " ".join(lines)


def setup(ctx) -> WebSearch:
    return WebSearch(ctx)
