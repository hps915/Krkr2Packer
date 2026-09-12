# -*- coding: utf-8 -*-
"""通用工具：子进程实时日志、java/apktool 检测、sha256、apktool 自动下载。"""
import hashlib
import os
import re
import shutil
import subprocess
import time
import unicodedata
import urllib.request
from pathlib import Path

from . import config

APKTOOL_VERSION = "2.9.3"
APKTOOL_URL = ("https://github.com/iBotPeaches/Apktool/releases/download/"
               f"v{APKTOOL_VERSION}/apktool_{APKTOOL_VERSION}.jar")


class ToolError(Exception):
    """用户可读的工具/环境错误。"""


class CancelledError(Exception):
    """用户取消打包。"""


# ---------------- 日志 ----------------
# 全局日志槽：GUI 启动后把回调挂进来（GUI 负责线程安全投递）；无 GUI 时打印
_log_sink = None


def set_log_sink(fn):
    global _log_sink
    _log_sink = fn


def log(msg=""):
    if _log_sink is not None:
        try:
            _log_sink(str(msg))
        except Exception:
            pass
    else:
        try:
            print(str(msg), flush=True)
        except Exception:
            pass


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def human_size(n) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{int(n)} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def read_text_retry(path, attempts=4, delay=0.25, encoding="utf-8") -> str:
    """读文本文件；Windows 下刚写完的文件可能被杀软/搜索索引器短暂锁定
    （OSError: permission/sharing violation），静默跳过会造成“时有时无”的
    随机 bug（如安装标记漏检），故带退避重试。"""
    last = None
    for i in range(attempts):
        try:
            return Path(path).read_text(encoding=encoding, errors="replace")
        except OSError as e:
            last = e
            time.sleep(delay * (i + 1))
    raise last


_PKG_PERIOD_MAP = {ord("。"): ".", ord("．"): ".", ord("，"): "."}


def normalize_package_input(s: str) -> str:
    """包名输入规范化，兼容中文输入法：

    - 全角句号“。”/“．”→ 半角“.”（解决中文标点模式下“点号打不进去”）
    - 全角字母/数字/下划线 → 半角（NFKC），大写 → 小写
    - 去除空白字符
    正常输入经此函数不变，可直接在变量 trace 里写回。
    """
    s = unicodedata.normalize("NFKC", s or "").translate(_PKG_PERIOD_MAP)
    s = s.lower()
    return "".join(ch for ch in s if not ch.isspace())


# ---------------- 子进程 ----------------
def run_cmd(args, cwd=None, cancel=None, check=False, log_fn=None):
    """运行外部命令，逐行转发 stdout/stderr（utf-8，非法字节替换，兼容中文路径）。

    返回 (returncode, 全部输出文本)。check=True 时非 0 退出码抛 ToolError。
    """
    args = [str(a) for a in args]
    lg = log_fn or log
    lg("$ " + " ".join(args))
    extra = {}
    if os.name == "nt":
        extra["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(
            args, cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            bufsize=1, **extra)
    except FileNotFoundError:
        raise ToolError(f"找不到可执行文件: {args[0]}")
    except OSError as e:
        raise ToolError(f"无法启动 {args[0]}: {e}")
    lines = []
    for raw in proc.stdout:
        line = raw.rstrip("\r\n")
        lines.append(line)
        if line.strip():
            lg(line)
        if cancel is not None and cancel.is_set():
            try:
                proc.kill()
            except Exception:
                pass
            raise CancelledError("操作已取消")
    rc = proc.wait()
    if cancel is not None and cancel.is_set():
        raise CancelledError("操作已取消")
    text = "\n".join(lines)
    if check and rc != 0:
        raise ToolError(f"命令失败（退出码 {rc}）: {args[0]}\n--- 输出末尾 ---\n{text[-1500:]}")
    return rc, text


# ---------------- JDK ----------------
def find_java() -> str:
    exe = shutil.which("java")
    if exe:
        return exe
    raise ToolError(
        "未检测到 java（需要 JDK 17+）。\n"
        "请安装 JDK 17 或更高版本并加入 PATH 后重试。\n"
        "推荐 Adoptium Temurin：https://adoptium.net")


def find_jdk_tool(name: str):
    """keytool / jarsigner：优先 java 同目录（PATH 里可能只有 java）。"""
    try:
        jdir = Path(find_java()).resolve().parent
    except ToolError:
        return shutil.which(name)
    exe = jdir / (name + (".exe" if os.name == "nt" else ""))
    if exe.is_file():
        return str(exe)
    return shutil.which(name)


# ---------------- apktool ----------------
_apktool_ver_cache = {}


def apktool_prefers_aapt1_flag(jar: Path) -> bool:
    """apktool >= 2.7 默认用 aapt2 回编（老模板会报 layouts.xml 错误），需加 --use-aapt1。"""
    key = f"{jar}:{int(jar.stat().st_mtime)}"
    if key in _apktool_ver_cache:
        return _apktool_ver_cache[key]
    ver = None
    try:
        rc, out = run_cmd([find_java(), "-jar", jar, "--version"],
                          log_fn=lambda s: None)
        m = re.search(r"(\d+)\.(\d+)", out)
        if rc == 0 and m:
            ver = (int(m.group(1)), int(m.group(2)))
    except Exception:
        ver = None
    prefer = bool(ver and ver >= (2, 7))
    _apktool_ver_cache[key] = prefer
    return prefer


def find_apktool(cfg: dict, auto_download=True) -> Path:
    p = cfg.get("apktool_path")
    if p and Path(p).is_file():
        return Path(p)
    if config.APKTOOL_JAR_LOCAL.is_file():
        return config.APKTOOL_JAR_LOCAL
    for cand in sorted(config.TOOLS_DIR.glob("apktool*.jar")):
        return cand
    if auto_download:
        return download_apktool()
    raise ToolError(
        "未找到 apktool.jar。请在「高级设置」指定路径，\n"
        f"或把 apktool.jar 放到: {config.APKTOOL_JAR_LOCAL}")


def download_apktool(dest: Path = None) -> Path:
    dest = Path(dest) if dest else config.APKTOOL_JAR_LOCAL
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"正在下载 apktool {APKTOOL_VERSION}（约 22MB）…")
    log(f"  {APKTOOL_URL}")
    try:
        req = urllib.request.Request(APKTOOL_URL, headers={"User-Agent": "Krkr2Packer/1.0"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as f:
            total = int(resp.headers.get("Content-Length") or 0)
            done, last_pct = 0, -100
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    if pct - last_pct >= 10:
                        last_pct = pct
                        log(f"  下载进度 {pct}%  ({human_size(done)} / {human_size(total)})")
    except Exception as e:
        try:
            dest.unlink()
        except Exception:
            pass
        raise ToolError(
            "apktool.jar 自动下载失败（请检查网络）。\n"
            "手动方案：到 https://github.com/iBotPeaches/Apktool/releases 下载 apktool_x.x.jar，\n"
            f"放到 {dest}，或在「高级设置」里指定路径。\n{e}")
    if dest.stat().st_size < 1024 * 1024:
        raise ToolError(f"下载的 apktool.jar 体积异常（{human_size(dest.stat().st_size)}），请手动下载。")
    log(f"apktool 下载完成: {dest}")
    return dest
