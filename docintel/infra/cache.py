"""页级结果缓存：一页一个 JSON 文件，原子写入，多进程/多实例共享目录也安全。

key 里包含文件内容哈希、页、路由、引擎版本、prompt 版本，任何一个变了都会自动失效。
任务失败后重跑，已完成的页直接命中缓存，相当于断点续跑。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def file_sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


class PageCache:
    def __init__(self, root: Path):
        self.root = root

    @staticmethod
    def key(*parts: object) -> str:
        return hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        try:
            return json.loads(self._path(key).read_text("utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def put(self, key: str, value: dict[str, Any]) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)
        os.replace(tmp, path)
