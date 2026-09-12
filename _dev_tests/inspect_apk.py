# -*- coding: utf-8 -*-
"""检查模板壳 APK 的内部结构（zip 层面），验证规格书假设。"""
import zipfile, re, sys
from pathlib import Path
from collections import Counter

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

APK = Path(r"C:\Users\Administrator\Documents\Default Project\传送门\直装包模板壳.apk")
print("exists:", APK.exists(), "size:", APK.stat().st_size if APK.exists() else None)

z = zipfile.ZipFile(APK)
names = z.namelist()
print("total entries:", len(names))

tops = Counter()
for n in names:
    parts = n.split("/")
    if parts[0] == "assets":
        tops["assets/" + parts[1] if len(parts) > 1 else "assets/"] += 1
    else:
        tops[parts[0]] += 1
print("\n--- 顶层结构统计 ---")
for k, v in sorted(tops.items()):
    print(f"{k:45s} {v}")

print("\n--- assets 完整清单 ---")
for n in sorted(names):
    if n.startswith("assets"):
        print("  ", n, z.getinfo(n).file_size)

print("\n--- lib ---")
for n in sorted(names):
    if n.startswith("lib/"):
        print("  ", n, z.getinfo(n).file_size)

print("\n--- 根文件 ---")
for n in sorted(names):
    if "/" not in n.rstrip("/"):
        print("  ", n, z.getinfo(n).file_size)

print("\n--- res/ 一级目录 ---")
resdirs = Counter()
for n in names:
    if n.startswith("res/"):
        resdirs[n.split("/")[1]] += 1
for k, v in sorted(resdirs.items()):
    print(f"  res/{k:24s} {v}")

try:
    data = z.read("assets/recentpath.xml")
    print("\n--- assets/recentpath.xml 原始内容 ---")
    print(repr(data))
except KeyError:
    print("\n!! 无 assets/recentpath.xml")

dexes = [n for n in names if n.endswith(".dex")]
print("\ndex 文件:", dexes)
for d in dexes:
    data = z.read(d)
    print(f"\n--- {d} 关键字符串探测 ---")
    for kw in [b"youlagou", b"ready", b"recentpath", b"startTransform",
               b"copyAsset2", b"isInstall2", b"preference", b"XP3",
               b"activity", b"Timer", b"100"]:
        idxs = []
        start = 0
        while True:
            i = data.find(kw, start)
            if i < 0: break
            idxs.append(i); start = i + 1
            if len(idxs) > 5: break
        print(f"  {kw!r}: {'FOUND x%d %s' % (len(idxs), idxs) if idxs else 'no'}")
    # 抽取 dex 中的可读字符串（粗略）
    strs = set(re.findall(rb"[\x20-\x7e]{5,60}", data))
    hits = [s.decode() for s in strs if re.search(rb"ready|youla|recentpath|preference|Transform|Install|qlwh|Kirikiri|Kirikiroid", s, re.I)]
    print(f"  相关字符串 {len(hits)} 个:")
    for s in sorted(hits)[:60]:
        print("   ", s)

mf = z.read("AndroidManifest.xml")

def utf16_strings(b, minlen=4):
    return [m.group().decode("utf-16le") for m in re.finditer(rb"(?:[\x20-\x7e]\x00){%d,}" % minlen, b)]

print("\n--- AndroidManifest.xml (AXML) UTF-16 字符串 ---")
seen = []
for s in utf16_strings(mf):
    if s not in seen:
        seen.append(s)
for s in seen:
    print("  ", s)
