# -*- coding: utf-8 -*-
"""AndroidManifest.xml / apktool.yml / smali / assets 的文本级修改。

注意（规格书 八）：
- smali 不参与包名重命名——Manifest 里 Activity 用全限定名，package 属性变了
  类名也不变；只替换 const-string 中点分形式的旧包名字符串，绝不移动 smali 目录。
- assets 下的文本文件直接做字节级替换，避免编码回写损伤非 UTF-8 文件。
"""
import re
from pathlib import Path

from . import utils

# 组件标签（activity-alias 必须排在 activity 之前，否则 \b 会误配）
_COMP_OPEN = r"(activity-alias|activity|service|receiver|provider)"
_BLOCK_RE = re.compile(
    r"<" + _COMP_OPEN + r"\b[^>]*?/>|<" + _COMP_OPEN + r"\b.*?</\2>", re.S)
_CONST_STR_RE = re.compile(r'(const-string(?:/[jklprsv]+)?\s+[vp]\d+,\s*)"([^"]*)"')
_ASSETS_TEXT_EXTS = {".xml", ".txt", ".ini", ".json", ".cfg", ".conf",
                     ".csv", ".js", ".html", ".properties", ".tjs"}


# ---------------- XML 工具 ----------------
def xml_escape_attr(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def xml_escape_text(s: str) -> str:
    s = str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s.replace("'", "\\'")     # aapt 要求字符串资源里的单引号转义


# ---------------- Manifest ----------------
def read_manifest(work_dir) -> str:
    return (Path(work_dir) / "AndroidManifest.xml").read_text(
        encoding="utf-8", errors="replace")


def write_manifest(work_dir, text: str) -> None:
    (Path(work_dir) / "AndroidManifest.xml").write_text(text, encoding="utf-8")


def get_package(text: str):
    m = re.search(r'<manifest\b[^>]*?\bpackage="([^"]+)"', text)
    return m.group(1) if m else None


def set_package(text: str, new_pkg: str):
    """替换 <manifest package="...">；返回 (新文本, 旧包名)。"""
    m = re.search(r'(<manifest\b[^>]*?)\bpackage="([^"]+)"', text)
    if not m:
        raise utils.ToolError("AndroidManifest.xml 中未找到 package 属性，模板无效")
    old = m.group(2)
    s, e = m.span(2)
    return text[:s] + new_pkg + text[e:], old


def qualify_relative_component_names(text: str, pkg: str):
    """把 android:name=".XXX" 相对类名补成 pkg.XXX（改包名后相对名会指向不存在的类）。"""
    if not pkg:
        return text, 0
    return re.subn(r'(android:name=")\.', rf"\g<1>{pkg}.", text)


def get_app_label(text: str):
    m = re.search(r'<application\b[^>]*?android:label="([^"]*)"', text)
    return m.group(1) if m else None


def set_app_label(text: str, app_name: str):
    """改应用名。返回 (新文本, 字符串资源引用或 None)。

    - label 为字面量：直接替换该字面量
    - label 为 @string/xxx：保留引用，返回引用名，由调用方改 strings.xml
    - application 无 label：插入 android:label
    """
    m = re.search(r'(<application\b[^>]*?)android:label="([^"]*)"', text)
    if m:
        ref = m.group(2)
        if ref.startswith("@"):
            return text, ref
        s, e = m.span(2)
        return text[:s] + xml_escape_attr(app_name) + text[e:], None
    m2 = re.search(r"<application\b", text)
    if not m2:
        raise utils.ToolError("Manifest 中未找到 <application> 标签")
    ins = m2.end()
    return (text[:ins] + f' android:label="{xml_escape_attr(app_name)}"' + text[ins:], None)


def get_icon_ref(text: str):
    m = re.search(r'<application\b[^>]*?android:icon="([^"]*)"', text)
    return m.group(1) if m else None


def get_round_icon_ref(text: str):
    m = re.search(r'<application\b[^>]*?android:roundIcon="([^"]*)"', text)
    return m.group(1) if m else None


def get_permissions(text: str):
    return re.findall(r'<uses-permission\b[^>]*android:name="([^"]+)"', text)


def set_permissions(text: str, keep):
    """只保留 keep 集合中的 uses-permission。返回 (新文本, 删除数)。"""
    keep = set(keep or [])
    removed = 0

    def repl(m):
        nonlocal removed
        mm = re.search(r'android:name="([^"]+)"', m.group(0))
        if mm and mm.group(1) in keep:
            return m.group(0)
        removed += 1
        return ""

    text = re.sub(r"[ \t]*<uses-permission\b[^>]*/>\r?\n?", repl, text)
    return text, removed


def set_uses_sdk(text: str, min_sdk=None, target_sdk=None):
    """若 Manifest 存在 <uses-sdk> 标签则同步修改（优先级高于 apktool.yml）。"""
    m = re.search(r"<uses-sdk\b[^>]*/?>", text)
    if not m:
        return text, False
    tag = m.group(0)
    if min_sdk:
        if re.search(r'android:minSdkVersion="[^"]*"', tag):
            tag = re.sub(r'android:minSdkVersion="[^"]*"',
                         f'android:minSdkVersion="{min_sdk}"', tag)
        else:
            tag = tag.replace("<uses-sdk", f'<uses-sdk android:minSdkVersion="{min_sdk}"', 1)
    if target_sdk:
        if re.search(r'android:targetSdkVersion="[^"]*"', tag):
            tag = re.sub(r'android:targetSdkVersion="[^"]*"',
                         f'android:targetSdkVersion="{target_sdk}"', tag)
        else:
            tag = tag.replace("<uses-sdk", f'<uses-sdk android:targetSdkVersion="{target_sdk}"', 1)
    return text[:m.start()] + tag + text[m.end():], True


def add_exported_to_components(text: str):
    """targetSdk>=30 时，给所有带 intent-filter 的组件补 android:exported="true"。"""
    count = 0

    def repl(m):
        nonlocal count
        if m.group(1):          # 自闭合组件不可能有 intent-filter
            return m.group(0)
        block = m.group(0)
        if "<intent-filter" not in block:
            return block
        gt = block.find(">")
        if gt < 0 or "android:exported" in block[:gt]:
            return block
        count += 1
        return block[:gt] + ' android:exported="true"' + block[gt:]

    return _BLOCK_RE.sub(repl, text), count


# ---------------- strings.xml ----------------
def update_string_resource(res_dir, name: str, value: str) -> int:
    """更新 res/values*/strings.xml 中指定 string；返回更新的文件数。"""
    n = 0
    res_dir = Path(res_dir)
    if not res_dir.is_dir():
        return 0
    pat = re.compile(r'(<string name="%s">).*?(</string>)' % re.escape(name), re.S)
    for strings in sorted(res_dir.glob("values*/strings.xml")):
        try:
            t = strings.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        new, cnt = pat.subn(
            lambda m: m.group(1) + xml_escape_text(value) + m.group(2), t, count=1)
        if cnt:
            strings.write_text(new, encoding="utf-8")
            n += 1
    return n


def upsert_string_resource(res_dir, name: str, value: str) -> int:
    """update 的兜底：对应资源不存在时追加到 res/values/strings.xml。"""
    n = update_string_resource(res_dir, name, value)
    if n:
        return n
    res_dir = Path(res_dir)
    base = res_dir / "values" / "strings.xml"
    entry = f'    <string name="{name}">{xml_escape_text(value)}</string>\n'
    if base.exists():
        t = base.read_text(encoding="utf-8", errors="replace")
        if "</resources>" in t:
            t = t.replace("</resources>", entry + "</resources>")
        else:
            t += entry
        base.write_text(t, encoding="utf-8")
        return 1
    base.parent.mkdir(parents=True, exist_ok=True)
    base.write_text('<?xml version="1.0" encoding="utf-8"?>\n<resources>\n'
                    + entry + "</resources>\n", encoding="utf-8")
    return 1


# ---------------- apktool.yml ----------------
def parse_apktool_yaml(text: str) -> dict:
    """极简行级解析，只取 sdkInfo / versionInfo / 顶层 version。"""
    vals, section = {}, None
    for raw in text.splitlines():
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        if indent == 0:
            section = None
            if ":" in line:
                k, _, v = line.partition(":")
                v = v.strip()
                if k == "version":
                    vals["apktool_version"] = v.strip("'\"")
                elif v == "":
                    section = k
            continue
        if section in ("sdkInfo", "versionInfo") and ":" in line:
            k, _, v = line.partition(":")
            vals[k.strip()] = v.strip().strip("'\"")
    return vals


def set_apktool_yaml(text: str, version_code=None, version_name=None,
                     min_sdk=None, target_sdk=None) -> str:
    """行级改写 apktool.yml 的 versionInfo / sdkInfo 段（规格书 2.5-2）。"""
    want = {
        "sdkInfo": {"minSdkVersion": min_sdk, "targetSdkVersion": target_sdk},
        "versionInfo": {"versionCode": version_code, "versionName": version_name},
    }
    out, section = [], None
    for raw in text.splitlines():
        stripped = raw.strip()
        indent = len(raw) - len(raw.lstrip(" "))
        if indent == 0 and stripped.endswith(":") and stripped[:-1] in want:
            section = stripped[:-1]
            out.append(raw)
            continue
        if indent > 0 and section and ":" in stripped and section in want:
            k = stripped.split(":", 1)[0].strip()
            if k in want[section] and want[section][k]:
                out.append(f"  {k}: '{want[section][k]}'")
                continue
        out.append(raw)
    body = "\n".join(out)
    if text.endswith("\n"):
        body += "\n"
    return body


# ---------------- smali / assets 字符串替换 ----------------
def _is_class_ref(s: str, pkg: str) -> bool:
    """形如 "pkg.MainActivity"（pkg 后跟 . + 大写）视作类名字符串，跳过不替换。"""
    i = s.find(pkg)
    while i >= 0:
        rest = s[i + len(pkg):]
        if rest.startswith(".") and len(rest) > 1 and rest[1].isupper():
            return True
        i = s.find(pkg, i + 1)
    return False


def replace_old_package(work_dir, old_pkg: str, new_pkg: str, log=utils.log) -> int:
    """全局搜索 smali 与 assets 文本文件中的旧包名（点分）并替换。

    类引用在 smali 里是斜线形式（Lsl/qlwh/MainActivity;），不含点，不受影响；
    "sl.qlwh.MainActivity" 这种类名字符串被 _is_class_ref 跳过，防止破坏跳转。
    """
    if not old_pkg or old_pkg == new_pkg:
        return 0
    total = 0
    work_dir = Path(work_dir)
    old_b, new_b = old_pkg.encode("ascii"), new_pkg.encode("ascii")

    for smali_dir in sorted(work_dir.glob("smali*")):
        for f in smali_dir.rglob("*.smali"):
            try:
                t = utils.read_text_retry(f)
            except OSError:
                continue
            if old_pkg not in t:
                continue
            hits = []

            def repl(m):
                s = m.group(2)
                if old_pkg in s and not _is_class_ref(s, old_pkg):
                    hits.append(s)
                    return m.group(1) + '"' + s.replace(old_pkg, new_pkg) + '"'
                return m.group(0)

            t2 = _CONST_STR_RE.sub(repl, t)
            if hits:
                f.write_text(t2, encoding="utf-8")
                total += len(hits)
                rel = f.relative_to(work_dir)
                log(f"  smali 替换 {rel}: {len(hits)} 处 -> {hits[0][:60]}")

    assets = work_dir / "assets"
    if assets.is_dir():
        for f in sorted(assets.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in _ASSETS_TEXT_EXTS:
                continue
            try:
                raw = f.read_bytes()
            except OSError:
                continue
            if old_b not in raw or b"\x00" in raw[:1024]:
                continue
            f.write_bytes(raw.replace(old_b, new_b))
            total += 1
            log(f"  assets 替换 {f.relative_to(work_dir)}")
    return total


def replace_install_marker(work_dir, old_marker: str, new_marker: str,
                           log=utils.log) -> int:
    """把安装标记字符串（如 youlagou_ready_v2）替换为新随机串。

    只替换 const-string 字面量内部的匹配（避免误伤同名的方法名/字段），
    全部 smali 文件全局进行，保证写标记与查标记两处一致。
    """
    if not old_marker or old_marker == new_marker:
        return 0
    total = 0
    pat = re.compile(r'(const-string(?:/[jklprsv]+)?\s+[vp]\d+,\s*)"([^"]*)"')

    for smali_dir in sorted(Path(work_dir).glob("smali*")):
        for f in smali_dir.rglob("*.smali"):
            try:
                t = utils.read_text_retry(f)
            except OSError:
                continue
            if old_marker not in t:
                continue
            n = 0

            def repl(m):
                nonlocal n
                s = m.group(2)
                if old_marker in s:
                    n += s.count(old_marker)
                    return m.group(1) + '"' + s.replace(old_marker, new_marker) + '"'
                return m.group(0)

            t2 = pat.sub(repl, t)
            if n:
                f.write_text(t2, encoding="utf-8")
                total += n
                log(f"  安装标记替换 {f.name}: {n} 处")
    return total


# ---------------- recentpath.xml ----------------
def write_recentpath(assets_dir, package: str, startup_name: str) -> Path:
    """整体重写游戏入口钥匙（规格书 四-5：不做子串替换，整文件重写最稳）。

    路径必须与 MainActivity 解压目标一致：
    /storage/emulated/0/Android/data/<包名>/files/assets/<启动xp3名>
    """
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<RecentPathList>\n"
        f"    <Item Path=\"/storage/emulated/0/Android/data/{package}"
        f"/files/assets/{startup_name}\"/>\n"
        "</RecentPathList>\n"
    )
    p = Path(assets_dir) / "recentpath.xml"
    p.write_text(content, encoding="utf-8")
    return p
