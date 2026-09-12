# -*- coding: utf-8 -*-
"""路径常量、默认值、配置持久化(JSON)。"""
import json
import os
import tempfile
from pathlib import Path

APP_NAME = "Krkr2Packer"

# %LOCALAPPDATA%/Krkr2Packer —— 模板解包缓存与配置所在
APP_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_NAME
TEMPLATE_DIR = APP_DIR / "template"           # 模板解包缓存目录
CONFIG_PATH = APP_DIR / "config.json"
KEYSTORE_DIR = APP_DIR / "keystore"
DEFAULT_KEYSTORE = KEYSTORE_DIR / "krkr2packer.jks"


def _win_temp() -> Path:
    """Windows 真实 %TEMP%（Git Bash 环境下 TEMP 可能是 /tmp，这里强制走 Windows 侧）。"""
    cand = Path(os.environ.get("LOCALAPPDATA") or "") / "Temp"
    if cand.is_dir():
        return cand
    try:
        return Path(tempfile.gettempdir())
    except Exception:
        return Path.home()


BUILD_ROOT = _win_temp() / APP_NAME            # %TEMP%/Krkr2Packer/build_<时间戳>

# 项目根目录（本文件位于 core/ 下）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = PROJECT_ROOT / "tools"
APKTOOL_JAR_LOCAL = TOOLS_DIR / "apktool.jar"

# ---- 模板白名单（瘦身与打包全程不可删/不可覆盖）----
# assets 根下视为“引擎运行时资源”的名字
ASSETS_WHITELIST = {
    "recentpath.xml", "ui", "img", "locale", "font", "Default", "others",
}
# 引擎字体/光标等运行时文件按“精确文件名”保护（游戏目录里其余所有文件
# ——包括游戏自带的 .ttf 字体——一律照常打包）
ASSETS_ENGINE_FILES = {"DroidSansFallback.ttf", "default.cur"}
# 清理游戏区时按扩展名兜底放行的引擎运行时文件类型
ASSETS_WHITELIST_EXTS = {".ttf", ".otf", ".ttc", ".cur"}

# 模板结构校验：缺失任一项 => “无效模板”
REQUIRED_PATHS = [
    "AndroidManifest.xml",
    "apktool.yml",
    "assets/recentpath.xml",
    "assets/ui",
]

# 启动图标五档：密度 -> 像素边长
ICON_DENSITY_SIZES = {"mdpi": 48, "hdpi": 72, "xhdpi": 96, "xxhdpi": 144, "xxxhdpi": 192}

DEFAULT_OUTPUT_NAME = "{appname}直装包.apk"

# 包名合法性（规格书正则）
PACKAGE_RE = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*){1,}$"

DEFAULTS = {
    "template_apk": "",
    "template_cache_key": "",        # 模板文件 mtime+size，判断是否需重新解包
    "kernel_accepted": {},           # {"arm64-v8a": "<SHA256>", ...} 用户已确认的内核指纹
    "apktool_path": "",              # 留空 => 项目 tools/apktool.jar => 自动下载
    "build_tools_dir": "",           # Android build-tools 目录（zipalign/apksigner）
    "sign_mode": "auto",             # auto=自动生成 / import=导入已有
    "keystore_path": "",
    "keystore_pass": "krkr123456",   # 自动生成 keystore 的默认口令（GUI 可改）
    "keystore_alias": "game",
    "out_dir": "",
    "out_name_tpl": DEFAULT_OUTPUT_NAME,
    "last_game_dir": "",
    "last_package": "",
    "last_app_name": "",
    "last_icon": "",
    "perm_keep": [],                 # 保留的权限列表；空 = 全部保留
    "marker_replace": True,          # 安装标记改名（同包名覆盖安装换游戏时强制重新解压）
    "marker_value": "",
    "template_marker": "",           # 模板中检测到的安装标记
    "follow_sdk": True,              # True = min/targetSdk 跟随模板（默认推荐）
    "min_sdk": "",
    "target_sdk": "",
    "version_code": "",
    "version_name": "",
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        if CONFIG_PATH.exists():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                cfg.update({k: v for k, v in data.items() if k in DEFAULTS})
    except Exception:
        pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        data = {k: cfg.get(k, DEFAULTS.get(k)) for k in DEFAULTS}
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    except Exception:
        pass
