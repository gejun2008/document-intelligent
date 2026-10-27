"""LibreOffice 转换池。

soffice 多进程共用一个用户 profile 会互相锁死，所以每个槽位用独立的
-env:UserInstallation；超时就杀整个进程组，避免留下僵尸 soffice。
服务器上务必装中文字体（Noto CJK / 思源），否则渲染出来是方块，后面什么模型都救不回来。
"""

from __future__ import annotations

import asyncio
import os
import shutil
import signal
from pathlib import Path


class ConversionError(RuntimeError):
    pass


class LibreOfficePool:
    def __init__(self, binary: str, instances: int, timeout_s: int, profile_root: Path):
        self.binary = binary
        self.timeout_s = timeout_s
        self.profile_root = profile_root
        self._slots: asyncio.Queue[int] = asyncio.Queue()
        for i in range(max(instances, 1)):
            self._slots.put_nowait(i)

    @property
    def available(self) -> bool:
        return shutil.which(self.binary) is not None or Path(self.binary).exists()

    async def convert(self, src: Path, fmt: str, out_dir: Path) -> Path:
        """fmt 例如 "pdf"、"docx"、"xlsx"；返回转换后的文件路径。"""
        if not self.available:
            raise ConversionError(f"找不到 LibreOffice：{self.binary}")
        out_dir.mkdir(parents=True, exist_ok=True)
        slot = await self._slots.get()
        try:
            profile = (self.profile_root / f"lo_profile_{slot}").resolve()
            cmd = [self.binary, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore",
                   "--nologo", "--convert-to", fmt, "--outdir", str(out_dir), str(src)]
            proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE,
                                                        stderr=asyncio.subprocess.PIPE, start_new_session=True)
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), self.timeout_s)
            except asyncio.TimeoutError:
                os.killpg(proc.pid, signal.SIGKILL)
                await proc.wait()
                raise ConversionError(f"LibreOffice 超时（{self.timeout_s}s）：{src.name}") from None
        finally:
            self._slots.put_nowait(slot)

        target = out_dir / f"{src.stem}.{fmt.split(':')[0]}"
        if proc.returncode != 0 or not target.exists():
            raise ConversionError(f"LibreOffice 转换失败 {src.name}: {stderr.decode(errors='replace')[-500:]}")
        return target
