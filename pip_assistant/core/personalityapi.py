"""The personality editor on the Settings page (step 8, plan §2), behind the core.personality request.

    {"action": "get"}                          → {"text", "builtin", "history": [{id, at, label, active}]}
    {"action": "save", "text": ...}            → the same, or {"error"} (empty, too long, stray curly brackets)
    {"action": "use", "id": ...}               → the same (undo: make a kept version active again)
    {"action": "preview", "text", "question"}  → {"answer"} or {"error"}: Pip's answer with that personality,
                                                 as text only (pip-thinker, thinker.ask kind "preview")

The personality text is the owner's own writing, not what people said, so it may be shown on the dashboard.
The preview question and answer are only returned to the page, never logged.
"""
from ..common import personality as store
from ..common.messages import new_id


class PersonalityAPI:
    def __init__(self, conv, store_module=store):
        self.conv = conv
        self.store = store_module

    def snapshot(self) -> dict:
        text, builtin = self.store.read()
        return {"text": text, "builtin": builtin, "history": self.store.history()}

    async def handle(self, data: dict) -> dict:
        action = (data or {}).get("action", "get")
        try:
            if action == "get":
                return self.snapshot()
            if action == "save":
                self.store.save(data.get("text", ""))
                return self.snapshot()
            if action == "use":
                self.store.use(str(data.get("id", "")))
                return self.snapshot()
            if action == "preview":
                return await self.preview(data.get("text", ""), data.get("question", ""))
        except ValueError as e:
            return {"error": str(e)}
        return {"error": "Unknown action"}

    async def preview(self, text: str, question: str) -> dict:
        text = self.store.validate(text)   # ValueError → {"error"} above
        question = (question or "").strip()
        if not question:
            return {"error": "Type a question to try it with."}
        try:
            reply = await self.conv.bus_request("thinker", "thinker.ask",
                                                {"answer_id": new_id(), "reason": "preview", "kind": "preview"},
                                                {"personality": text, "question": question[:500]}, timeout=60)
        except Exception as e:
            return {"error": f"Pip's thinking program didn't answer ({type(e).__name__})."}
        if reply.data.get("error"):
            return {"error": "Couldn't get an answer: " + str(reply.data["error"])[:160]}
        return {"answer": reply.private.get("text", "")}
