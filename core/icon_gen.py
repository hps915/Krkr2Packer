# -*- coding: utf-8 -*-
"""Pillow 图标处理：RGBA 正方形补透明、48/72/96/144/192 五档生成、res 全分辨率
替换、自适应图标回退、圆角预览（规格书 P0-6、四-8）。"""
from pathlib import Path

from PIL import Image, ImageDraw

from . import config, utils

try:
    from PIL import ImageTk   # GUI 预览用，纯构建不依赖
except Exception:             # pragma: no cover
    ImageTk = None


def load_square(path) -> Image.Image:
    """任意 PNG/JPG/ICO → RGBA 正方形（不足处补透明，不裁切内容）。"""
    img = Image.open(Path(path))
    img.load()
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    w, h = img.size
    side = max(w, h, 1)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - w) // 2, (side - h) // 2), img)
    return canvas


def rounded(img: Image.Image, size: int = 96, radius_ratio: float = 0.22) -> Image.Image:
    """GUI 圆角预览。"""
    im = img.resize((size, size), Image.LANCZOS)
    scale = 4
    mask = Image.new("L", (size * scale, size * scale), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, size * scale - 1, size * scale - 1],
                        radius=int(size * scale * radius_ratio), fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    out = im.copy()
    out.putalpha(mask)
    return out


def _dir_density(dirname: str, rtype: str):
    """res 目录名 → 密度限定符（如 mipmap-xxhdpi-v4 → xxhdpi）。"""
    if dirname == rtype:
        return None
    for seg in dirname[len(rtype) + 1:].split("-"):
        if seg in config.ICON_DENSITY_SIZES:
            return seg
    return None


def replace_icons(res_dir, icon_ref: str, img: Image.Image,
                  round_ref: str = "", log=utils.log) -> dict:
    """按 Manifest android:icon 实际引用替换同名资源（全分辨率）。

    - @mipmap/xxx 或 @drawable/xxx：在 res 下该类型的所有密度目录写入 <name>.png
    - 存在 mipmap-anydpi-v26/<name>.xml（自适应图标）时删除，回退到 PNG
    - roundIcon 引用同名资源则一并处理
    """
    res_dir = Path(res_dir)
    if not icon_ref or not icon_ref.startswith("@"):
        raise utils.ToolError(f"无法识别 Manifest 的 android:icon 值: {icon_ref!r}")
    refs = {icon_ref[1:]}
    if round_ref and round_ref.startswith("@") and not round_ref.startswith("@android:"):
        refs.add(round_ref[1:])
    written = removed = 0

    for ref in sorted(refs):
        rtype, _, name = ref.partition("/")
        if rtype not in ("mipmap", "drawable") or not name:
            raise utils.ToolError(
                f"图标引用 {ref!r} 不是 @mipmap/@drawable 资源，无法自动替换")
        cand_dirs = [d for d in res_dir.iterdir() if d.is_dir()
                     and (d.name == rtype or d.name.startswith(rtype + "-"))]
        if not cand_dirs:
            raise utils.ToolError(f"res/ 下未找到 {rtype} 资源目录（引用 {ref}），模板异常")

        # 自适应图标 XML 一律删除，回退到 PNG
        for d in cand_dirs:
            if "-anydpi" in d.name:
                for f in list(d.iterdir()):
                    if f.is_file() and f.stem == name and f.suffix.lower() == ".xml":
                        f.unlink()
                        removed += 1
                        log(f"  已删除自适应图标 {f.relative_to(res_dir)}（回退 PNG）")

        # 先清理同名旧资源（任意扩展名：png/jpg/webp/xml）
        removed_before = removed
        for d in cand_dirs:
            if "-anydpi" in d.name:
                continue
            for f in list(d.iterdir()):
                if f.is_file() and f.stem == name:
                    f.unlink()
                    removed += 1

        # 写入五档 PNG；缺失的标准密度目录补齐
        density_dirs = {d: _dir_density(d.name, rtype) for d in cand_dirs
                        if "-anydpi" not in d.name}
        targets = {d: config.ICON_DENSITY_SIZES.get(den, 96)
                   for d, den in density_dirs.items()}
        for den, size in config.ICON_DENSITY_SIZES.items():
            if not any(den == _dir_density(d.name, rtype) for d in cand_dirs):
                nd = res_dir / f"{rtype}-{den}"
                nd.mkdir(parents=True, exist_ok=True)
                targets[nd] = size
        for d, size in sorted(targets.items()):
            out = d / f"{name}.png"
            img.resize((size, size), Image.LANCZOS).save(out, "PNG")
            written += 1
        log(f"  图标 {ref}: 写入 {len(targets)} 个密度目录 "
            f"(48/72/96/144/192), 清理旧文件 {removed - removed_before} 个")
    return {"written": written, "removed": removed}
