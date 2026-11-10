"""What other modules may use of the to-dos: `ctx.module("todos")`, if their module.toml `needs` it.

A module only ever gets the to-dos the person asking (`call`, core/modules.Call) may hear about (each
person's sharing choice on the Google page). Anything more, add it here on purpose.

    todos = ctx.module("todos")
    todos.items(call)              # [{"id", "title", "due", "list"}]: open to-dos
    todos.add("milk", call)        # on the speaker's list, or the household's
"""


class TodosAPI:
    def __init__(self, todos):
        self._t = todos   # module.py's Todos

    def items(self, call) -> list[dict]:
        """Open to-dos the person asking may hear about."""
        t = self._t
        lists = {x["id"]: x for x in t.store.lists()}
        accounts = t._accounts()
        return [{"id": i["id"], "title": i["title"], "due": i.get("due", ""), "list": lists.get(i["list_id"], {}).get("name", "")}
                for i in t.store.items() if t.visible(i, call, lists, accounts)]

    def add(self, title: str, call, whose: str = "") -> dict:
        """Add a to-do: on `whose` list (a person's name), else the speaker's, else the household's."""
        item = self._t.store.add(title, self._t.target(whose, call))
        return {"id": item["id"], "title": item["title"]}
