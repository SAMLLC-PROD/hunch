"""A Hunch project is just a folder of documents plus a hidden .hunch/ directory.

    my-notes/
      hunch.json          <- your settings (safe to edit by hand, safe to commit)
      .hunch/             <- generated index + manifold (safe to delete; rebuilt automatically)
      car/ ...            <- each top-level folder becomes a domain
      house/ ...
      loose-note.md       <- loose files are grouped into automatic domains
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .index import Index
from .ingest import load_documents, snapshot
from .manifold import AUTHORITY_MODES, Domain, build_auto_domains, merge

CONFIG_NAME = "hunch.json"
STATE_DIR = ".hunch"
DEFAULT_CONFIG = {
    "domains": {},
    "settings": {"max_passages": 8, "provider": "", "model": ""},
}


class HunchError(Exception):
    pass


class Project:
    def __init__(self, root: str | os.PathLike):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise HunchError(f"Folder not found: {self.root}")
        self.state = self.root / STATE_DIR
        self.config_path = self.root / CONFIG_NAME
        self._index: Index | None = None
        self._auto: list[dict] | None = None
        self._domains: list[Domain] | None = None
        self._last_fresh_check = 0.0

    # ---------------------------------------------------------------- config
    def load_config(self) -> dict:
        if not self.config_path.exists():
            return json.loads(json.dumps(DEFAULT_CONFIG))
        try:
            cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise HunchError(f"{CONFIG_NAME} is not valid JSON: {e}") from e
        cfg.setdefault("domains", {})
        cfg.setdefault("settings", {})
        for k, v in DEFAULT_CONFIG["settings"].items():
            cfg["settings"].setdefault(k, v)
        return cfg

    def save_config(self, cfg: dict):
        self.config_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self._domains = None

    def setting(self, key: str, default=None):
        return self.load_config()["settings"].get(key, default)

    # ----------------------------------------------------------------- build
    def build(self) -> dict:
        t0 = time.time()
        self.state.mkdir(exist_ok=True)
        gitignore = self.state / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n")
        docs = load_documents(self.root)
        index = Index.build(docs)
        previous = self._read_json(self.state / "manifold.json", {}).get("domains", [])
        auto = build_auto_domains(index, previous)
        index.save(self.state / "index.json")
        self._write_json(self.state / "manifold.json", {"domains": auto})
        self._write_json(self.state / "snapshot.json", snapshot(self.root))
        if not self.config_path.exists():
            self.save_config(json.loads(json.dumps(DEFAULT_CONFIG)))
        self._index, self._auto, self._domains = index, auto, None
        return {
            "documents": len(index.docs),
            "chunks": index.n,
            "domains": len(auto),
            "seconds": round(time.time() - t0, 2),
        }

    def is_stale(self) -> bool:
        snap = self._read_json(self.state / "snapshot.json", None)
        if snap is None:
            return True
        current = {k: list(v) for k, v in snapshot(self.root).items()}
        return current != snap

    def ensure_fresh(self, min_interval: float = 20.0):
        """Rebuild if documents changed. Checks at most once per `min_interval` seconds."""
        now = time.time()
        if self._index is not None and now - self._last_fresh_check < min_interval:
            return
        self._last_fresh_check = now
        if not (self.state / "index.json").exists() or self.is_stale():
            self.build()

    # ------------------------------------------------------------- accessors
    @property
    def index(self) -> Index:
        if self._index is None:
            path = self.state / "index.json"
            if not path.exists():
                self.build()
            else:
                try:
                    self._index = Index.load(path)
                except (ValueError, KeyError, json.JSONDecodeError):
                    self.build()
        return self._index  # type: ignore[return-value]

    @property
    def auto_domains(self) -> list[dict]:
        if self._auto is None:
            _ = self.index
            if self._auto is None:
                self._auto = self._read_json(self.state / "manifold.json", {}).get("domains", [])
        return self._auto

    @property
    def domains(self) -> list[Domain]:
        if self._domains is None:
            self._domains = merge(self.auto_domains, self.load_config())
        return self._domains

    def domain(self, key: str) -> Domain:
        key_l = key.strip().lower()
        for d in self.domains:
            if d.id.lower() == key_l or d.label.lower() == key_l:
                return d
        matches = [d for d in self.domains if key_l in d.id.lower() or key_l in d.label.lower()]
        if len(matches) == 1:
            return matches[0]
        names = ", ".join(d.id for d in self.domains)
        raise HunchError(f"No single domain matches '{key}'. Domains: {names}")

    # ----------------------------------------------------------- user edits
    def _edit_domain(self, key: str) -> tuple[dict, dict, Domain]:
        d = self.domain(key)
        cfg = self.load_config()
        return cfg, cfg["domains"].setdefault(d.id, {}), d

    def set_authority(self, key: str, mode: str, watch_url: str | None = None) -> Domain:
        if mode not in AUTHORITY_MODES:
            raise HunchError(f"Authority must be one of: {', '.join(AUTHORITY_MODES)}")
        if mode == "watch" and not watch_url:
            raise HunchError("'watch' needs a URL to watch (--url https://...)")
        cfg, entry, d = self._edit_domain(key)
        entry["authority"] = mode
        if watch_url:
            entry["watch_url"] = watch_url
        self.save_config(cfg)
        return self.domain(d.id)

    def rename(self, key: str, label: str, description: str | None = None) -> Domain:
        cfg, entry, d = self._edit_domain(key)
        entry["label"] = label
        if description is not None:
            entry["description"] = description
        self.save_config(cfg)
        return self.domain(d.id)

    def add_signal(self, key: str, phrase: str) -> Domain:
        cfg, entry, d = self._edit_domain(key)
        entry.setdefault("signals", [])
        if phrase not in entry["signals"]:
            entry["signals"].append(phrase)
        self.save_config(cfg)
        return self.domain(d.id)

    def add_alias(self, key: str, word: str) -> Domain:
        cfg, entry, d = self._edit_domain(key)
        entry.setdefault("aliases", [])
        if word not in entry["aliases"]:
            entry["aliases"].append(word)
        self.save_config(cfg)
        return self.domain(d.id)

    def store_ai_enrichment(self, domain_id: str, label: str, description: str, signals: list[str]):
        auto = self.auto_domains
        for a in auto:
            if a["id"] == domain_id:
                a["label_ai"] = label
                a["description_auto"] = description
                a["signals_auto"] = signals
        self._write_json(self.state / "manifold.json", {"domains": auto})
        self._domains = None

    # --------------------------------------------------------------- helpers
    def read_document(self, rel: str, max_chars: int = 20000) -> str:
        from .ingest import read_text
        p = (self.root / rel).resolve()
        if self.root not in p.parents and p != self.root:
            raise HunchError("Path is outside the project folder.")
        if not p.is_file():
            raise HunchError(f"No such document: {rel}")
        text = read_text(p)
        return text if len(text) <= max_chars else text[:max_chars] + "\n\n[... truncated ...]"

    @staticmethod
    def _read_json(path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    @staticmethod
    def _write_json(path: Path, data):
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
