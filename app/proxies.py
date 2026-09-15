"""Optional proxy rotation.

Sources, merged: the PROXIES env var and PROXY_FILE (a Coolify persistent-storage
path, so the list survives redeploys and can be edited without rebuilding). The
file is re-read whenever its mtime changes.

With no proxies configured the pool yields None and every request goes out
directly — the default, supported path.
"""

from __future__ import annotations

import os
import re
import threading
import time

# host:port:user:pass — the layout most residential providers hand out.
_QUAD_RE = re.compile(r"^([^:@\s]+):(\d{1,5}):([^:@\s]+):([^:@\s]+)$")
_PAIR_RE = re.compile(r"^([^:@\s]+):(\d{1,5})$")


def normalize_proxy(raw: str) -> str | None:
    """Coerce the common provider formats into a URL curl understands."""
    value = raw.strip()
    if not value or value.startswith("#"):
        return None
    if "://" in value:
        return value
    quad = _QUAD_RE.match(value)
    if quad:
        host, port, user, password = quad.groups()
        return f"http://{user}:{password}@{host}:{port}"
    if _PAIR_RE.match(value):
        return f"http://{value}"
    return None


def parse_proxy_list(blob: str) -> list[str]:
    out: list[str] = []
    for chunk in re.split(r"[,\n\r]+", blob or ""):
        proxy = normalize_proxy(chunk)
        if proxy and proxy not in out:
            out.append(proxy)
    return out


class ProxyPool:
    def __init__(self, env_value: str = "", file_path: str = "", cooldown_s: float = 120.0):
        self._env_proxies = parse_proxy_list(env_value)
        self._file_path = file_path
        self._file_proxies: list[str] = []
        self._file_mtime: float | None = None
        self.cooldown_s = cooldown_s
        self._cooldowns: dict[str, float] = {}
        self._cursor = 0
        self._lock = threading.Lock()
        self._reload_file()

    def _reload_file(self) -> None:
        path = self._file_path
        if not path:
            return
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            if self._file_proxies:
                self._file_proxies = []
                self._file_mtime = None
            return
        if mtime == self._file_mtime:
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                self._file_proxies = parse_proxy_list(fh.read())
            self._file_mtime = mtime
        except OSError:
            pass

    @property
    def all(self) -> list[str]:
        merged = list(self._env_proxies)
        for proxy in self._file_proxies:
            if proxy not in merged:
                merged.append(proxy)
        return merged

    def __len__(self) -> int:
        return len(self.all)

    def acquire(self) -> str | None:
        """Next usable proxy, or None for a direct connection."""
        with self._lock:
            self._reload_file()
            candidates = self.all
            if not candidates:
                return None
            now = time.monotonic()
            for _ in range(len(candidates)):
                proxy = candidates[self._cursor % len(candidates)]
                self._cursor += 1
                if self._cooldowns.get(proxy, 0.0) <= now:
                    return proxy
            # Every proxy is cooling down — fall back to direct rather than fail.
            return None

    def penalise(self, proxy: str | None) -> None:
        if not proxy:
            return
        with self._lock:
            self._cooldowns[proxy] = time.monotonic() + self.cooldown_s

    def reward(self, proxy: str | None) -> None:
        if not proxy:
            return
        with self._lock:
            self._cooldowns.pop(proxy, None)

    def stats(self) -> dict:
        with self._lock:
            now = time.monotonic()
            total = len(self.all)
            cooling = sum(1 for t in self._cooldowns.values() if t > now)
            return {"configured": total, "cooling_down": cooling, "available": total - cooling}
