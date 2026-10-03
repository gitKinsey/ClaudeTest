"""Undo / redo for editors that change a JSON-able state: snapshots, bounded, de-duplicated."""
import copy
import json


class EditHistory:
    def __init__(self, limit=50):
        self.limit, self._undo, self._redo = limit, [], []

    @staticmethod
    def _key(state):
        return json.dumps(state, sort_keys=True, default=str)

    def record(self, state):
        """Call with the state AFTER every change (and once with the starting state)."""
        if self._undo and self._key(self._undo[-1]) == self._key(state):
            return
        self._undo.append(copy.deepcopy(state))
        del self._undo[:-self.limit]
        self._redo.clear()

    def can_undo(self):
        return len(self._undo) > 1

    def can_redo(self):
        return bool(self._redo)

    def undo(self):
        if not self.can_undo():
            return None
        self._redo.append(self._undo.pop())
        return copy.deepcopy(self._undo[-1])

    def redo(self):
        if not self._redo:
            return None
        st = self._redo.pop()
        self._undo.append(st)
        return copy.deepcopy(st)
