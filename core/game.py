# -*- coding: utf-8 -*-
"""游戏目录扫描、XP3 魔数检测、启动文件推断。

检测规则（规格书 2.8）：后缀 .xp3 且（前 11 字节含 "XP3_" 或文件 > 1MB）即认定为
游戏数据——KrkrZ/加密版头部可能变体，故放宽。
"""
import time
from dataclasses import dataclass
from pathlib import Path

XP3_MAGIC = b"XP3_"


@dataclass
class Xp3File:
    path: Path
    size: int
    has_magic: bool      # 头 11 字节内含 XP3_

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def detect_text(self) -> str:
        if self.has_magic:
            return "XP3 魔数 OK"
        if self.size > 1024 * 1024:
            return ">1MB 视为加密/变体包"
        return "无魔数<1MB，仍可选用"


def is_game_data(f: "Xp3File") -> bool:
    """规格书 2.8 的 KRKR 游戏数据判定（仅用于“是否 KRKR 游戏”的提醒，
    不用于隐藏文件——启动文件可能恰好是小体积加密变体）。"""
    return f.has_magic or f.size > 1024 * 1024


def _head_has_magic(p: Path) -> bool:
    for i in range(3):        # 新写入文件可能被杀软短暂锁定，重试
        try:
            with open(p, "rb") as f:
                return XP3_MAGIC in f.read(11)
        except OSError:
            time.sleep(0.2 * (i + 1))
    return False


def is_xp3(p: Path) -> bool:
    try:
        size = p.stat().st_size
    except OSError:
        return False
    if p.suffix.lower() != ".xp3":
        return False
    if size > 1024 * 1024:
        return True
    return _head_has_magic(p)


def scan_game_dir(game_dir):
    """递归扫描目录，列出【所有】.xp3（无论体积/魔数，避免漏掉小体积启动文件）。
    返回 (xp3列表, 其他文件数, xp3总体积)。"""
    game_dir = Path(game_dir)
    files, others, total = [], 0, 0
    for p in game_dir.rglob("*"):
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        if p.suffix.lower() == ".xp3":
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            files.append(Xp3File(p, size, _head_has_magic(p)))
            total += size
        else:
            others += 1
    files.sort(key=lambda f: f.name.lower())
    return files, others, total


def is_krkr_game(files) -> bool:
    return any(is_game_data(f) for f in files)


def suggest_startup(files):
    """启动文件推断：data.xp3 优先 → 文件名含 data/init/start → 体积最大。"""
    if not files:
        return None
    by_name = {f.name.lower(): f for f in files}
    if "data.xp3" in by_name:
        return by_name["data.xp3"]
    for kw in ("data", "init", "start"):
        cands = [f for f in files if kw in f.name.lower()]
        if cands:
            return max(cands, key=lambda f: f.size)
    return max(files, key=lambda f: f.size)
