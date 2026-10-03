"""Automatic rolling backups of the app's settings (key maps, macros, scripts, profiles ...): a snapshot after every meaningful change,
at most one per `min_interval` seconds, the newest `keep` are kept. Pure file handling, no GUI."""
import hashlib
import json
import re
import time
from pathlib import Path

VOLATILE = {"usage", "counters", "pushed", "pushed_layers", "pushed_mode", "pushed_bright", "edit_layer", "twin_mode", "twin_bright", "wizard_done"}
NAME = re.compile(r"^config-\d{8}-\d{6}(-\d+)?\.json$")


def fingerprint(cfg):
    """Hash of the parts of the config that matter (counters, upload bookkeeping and window state do not)."""
    keep = {k: v for k, v in cfg.items() if k not in VOLATILE and k not in ("map",)}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()


class AutoBackup:
    def __init__(self, folder, keep=15, min_interval=120, clock=time.time):
        self.folder, self.keep, self.min_interval, self.clock = Path(folder), keep, min_interval, clock
        self._last_hash, self._last_at = None, 0.0

    def maybe(self, cfg, force=False):
        """Write a snapshot if the config changed (and the last one is old enough). Returns the path or None. Never raises."""
        try:
            h = fingerprint(cfg)
            now = self.clock()
            if self._last_hash is None:
                files = self.list()
                self._last_hash = self._hash_of(files[0][0]) if files else None
            if h == self._last_hash or (not force and now - self._last_at < self.min_interval and self._last_at):
                return None
            self.folder.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
            path, n = self.folder / f"config-{stamp}.json", 1
            while path.exists():
                n += 1
                path = self.folder / f"config-{stamp}-{n}.json"
            path.write_text(json.dumps(cfg, indent=1, default=str), encoding="utf-8")
            self._last_hash, self._last_at = h, now
            self._prune()
            return path
        except OSError:
            return None

    @staticmethod
    def _hash_of(path):
        try:
            return fingerprint(json.loads(Path(path).read_text(encoding="utf-8")))
        except (OSError, ValueError):
            return None

    def list(self):
        """[(path, mtime, size)] newest first."""
        if not self.folder.is_dir():
            return []
        out = [(p, p.stat().st_mtime, p.stat().st_size) for p in self.folder.iterdir() if NAME.match(p.name)]
        return sorted(out, key=lambda t: (t[1], t[0].name), reverse=True)

    def _prune(self):
        for p, _m, _s in self.list()[self.keep:]:
            try:
                p.unlink()
            except OSError:
                pass

    @staticmethod
    def read(path):
        """Load a snapshot; raises ValueError when it is not a usable config."""
        try:
            cfg = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ValueError(f"cannot read the backup: {e}") from None
        if not isinstance(cfg, dict) or not isinstance(cfg.get("layers") or cfg.get("map"), (list, dict)):
            raise ValueError("this file is not a Desk Companion settings backup")
        return cfg
