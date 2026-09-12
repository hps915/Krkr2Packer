# -*- coding: utf-8 -*-
"""模板解包 / 缓存 / 结构校验 / 内核自检（规格书 P0-1、2.2、2.3）。"""
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config, manifest_edit as medit, utils


@dataclass
class TemplateInfo:
    unpack_dir: Path
    package: str = ""
    label: str = ""
    icon_ref: str = ""
    round_icon_ref: str = ""
    version_code: str = ""
    version_name: str = ""
    min_sdk: str = ""
    target_sdk: str = ""
    permissions: list = field(default_factory=list)
    kernel_hashes: dict = field(default_factory=dict)    # abi -> sha256
    markers: list = field(default_factory=list)          # 安装标记候选
    apktool_version: str = ""
    hard_problems: list = field(default_factory=list)    # 缺失即“无效模板”
    warnings: list = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.hard_problems


def cache_key(apk_path: Path) -> str:
    st = Path(apk_path).stat()
    return f"{int(st.st_mtime * 1000)}:{st.st_size}"


def unpack(apk_path, unpack_dir=None, cfg=None, cancel=None, force=False,
           log=utils.log) -> TemplateInfo:
    """确保模板已解包到缓存目录；按 mtime+size 判断缓存可复用。"""
    apk_path = Path(apk_path)
    if not apk_path.is_file():
        raise utils.ToolError(f"模板 APK 不存在: {apk_path}")
    cfg = cfg if cfg is not None else config.load_config()
    unpack_dir = Path(unpack_dir) if unpack_dir else config.TEMPLATE_DIR
    key = cache_key(apk_path)

    if not force and (unpack_dir / "apktool.yml").exists() \
            and cfg.get("template_cache_key") == key:
        info = load_info(unpack_dir, log=log)
        if info.valid:
            log("模板缓存有效，跳过解包。")
            return info
        log("模板缓存结构校验失败，将重新解包…")

    java = utils.find_java()
    apktool = utils.find_apktool(cfg)
    if unpack_dir.exists():
        log("清理旧模板缓存…")
        shutil.rmtree(unpack_dir, ignore_errors=True)
    unpack_dir.parent.mkdir(parents=True, exist_ok=True)
    log(f"正在解包模板 → {unpack_dir}")
    t0 = time.time()
    utils.run_cmd([java, "-jar", apktool, "d", "-f", "-o", unpack_dir, apk_path],
                  cancel=cancel, log_fn=log, check=True)
    log(f"模板解包完成，用时 {time.time() - t0:.0f} 秒")
    cfg["template_cache_key"] = key
    config.save_config(cfg)
    return load_info(unpack_dir, log=log)


def validate_structure(unpack_dir):
    """返回 (致命缺失列表, 警告列表)。关键路径缺失 => 无效模板。"""
    u = Path(unpack_dir)
    hard, soft = [], []
    for rel in config.REQUIRED_PATHS:
        if not (u / rel).exists():
            hard.append(rel)
    sos = list(u.glob("lib/*/libgame.so"))
    if not sos:
        hard.append("lib/*/libgame.so")
    if not list(u.glob("smali*/**/MainActivity.smali")):
        hard.append("smali/**/MainActivity.smali")
    for rel in ("assets/img", "assets/locale", "assets/font", "assets/Default"):
        if rel not in hard and not (u / rel).exists() and rel != "assets/font":
            soft.append(f"缺少 {rel}（模板引擎资源，若原模板确有请检查解包完整性）")
    if not (u / "assets/DroidSansFallback.ttf").exists() and not (u / "assets/font").exists():
        soft.append("缺少引擎字体 assets/DroidSansFallback.ttf（若原模板确有请检查）")
    return hard, soft


def compute_kernel_hashes(unpack_dir) -> dict:
    u = Path(unpack_dir)
    return {so.parent.name: utils.sha256_file(so) for so in sorted(u.glob("lib/*/libgame.so"))}


_MARK_NAME_RE = re.compile(r"ready|mark|install", re.I)
# 边界匹配：避免 "al(ready)"、"(mark)etable" 这类普通单词误判成安装标记文件名
_READY_MARK_RE = re.compile(r"(?<![A-Za-z])(?:ready|mark)(?![A-Za-z])", re.I)
_CONST_STR = re.compile(r'const-string(?:/[jklprsv]+)?\s+[vp]\d+,\s*"([^"]*)"')


def find_install_markers(unpack_dir) -> list:
    """在 smali 全局搜 const-string 中含 ready/mark/install 的短字符串（规格书 P1）。

    排序：ready/mark 类（真正的安装标记文件名，如 youlagou_ready_v2）优先，
    纯 install 字样的通用词靠后；含点/斜线的包名与路径一律排除。
    """
    hits = {}
    for smali_dir in Path(unpack_dir).glob("smali*"):
        for f in smali_dir.rglob("*.smali"):
            try:
                t = utils.read_text_retry(f)
            except OSError:
                continue
            for m in _CONST_STR.finditer(t):
                s = m.group(1)
                if 4 <= len(s) <= 48 and _MARK_NAME_RE.search(s) \
                        and "." not in s and "/" not in s and "\\" not in s \
                        and " " not in s and ":" not in s:
                    hits[s] = hits.get(s, 0) + 1

    def rank(kv):
        s, c = kv
        return (0 if _READY_MARK_RE.search(s) else 1, -c, s)

    return [k for k, _ in sorted(hits.items(), key=rank)]


def load_info(unpack_dir, log=utils.log) -> TemplateInfo:
    u = Path(unpack_dir)
    info = TemplateInfo(unpack_dir=u)
    mpath = u / "AndroidManifest.xml"
    if not mpath.exists():
        info.hard_problems.append("AndroidManifest.xml")
        return info
    text = medit.read_manifest(u)
    info.package = medit.get_package(text) or ""
    info.label = medit.get_app_label(text) or ""
    info.icon_ref = medit.get_icon_ref(text) or ""
    info.round_icon_ref = medit.get_round_icon_ref(text) or ""
    info.permissions = medit.get_permissions(text)

    yml = u / "apktool.yml"
    if yml.exists():
        vals = medit.parse_apktool_yaml(yml.read_text(encoding="utf-8", errors="replace"))
        info.version_code = vals.get("versionCode", "")
        info.version_name = vals.get("versionName", "")
        info.min_sdk = vals.get("minSdkVersion", "")
        info.target_sdk = vals.get("targetSdkVersion", "")
        info.apktool_version = vals.get("apktool_version", "")
    else:
        info.hard_problems.append("apktool.yml")

    hard, soft = validate_structure(u)
    info.hard_problems = hard
    info.warnings = soft
    info.kernel_hashes = compute_kernel_hashes(u)
    info.markers = find_install_markers(u)
    return info


def check_kernel(info: TemplateInfo, cfg: dict):
    """内核自检。返回 (status, detail)，status ∈ ok / missing / mismatch。"""
    accepted = cfg.get("kernel_accepted") or {}
    if not info.kernel_hashes:
        return "missing", "模板中未找到 libgame.so"
    if not accepted:
        return "missing", "尚未确认过内核指纹（首次使用）"
    bad = [abi for abi, h in info.kernel_hashes.items() if accepted.get(abi) != h]
    if bad:
        return "mismatch", "以下 ABI 的 libgame.so 与已接受指纹不一致: " + ", ".join(bad)
    return "ok", ""


def info_text(info: TemplateInfo) -> str:
    """GUI 只读信息面板文本。"""
    lines = []
    lines.append(f"原包名:   {info.package or '?'}")
    lines.append(f"版本:     {info.version_name or '?'}  (versionCode {info.version_code or '?'})")
    lines.append(f"sdkInfo:  minSdk {info.min_sdk or '?'} / targetSdk {info.target_sdk or '?'}")
    lines.append(f"label:    {info.label or '(无)'}")
    lines.append(f"图标:     {info.icon_ref or '(无)'}")
    if info.round_icon_ref:
        lines.append(f"圆图标:   {info.round_icon_ref}")
    lines.append("内核:     Kirikiroid2 1.3.9（官方最终版，无需也不得更换）")
    for abi, h in sorted(info.kernel_hashes.items()):
        lines.append(f"  libgame.so [{abi}]  SHA256: {h[:24]}…")
    lines.append(f"安装标记: {', '.join(info.markers[:3]) or '(未发现)'}")
    lines.append(f"权限:     {len(info.permissions)} 项")
    if info.hard_problems:
        lines.append(f"!! 结构缺失: {', '.join(info.hard_problems)}（无效模板）")
    for w in info.warnings:
        lines.append(f"警告: {w}")
    return "\n".join(lines)
