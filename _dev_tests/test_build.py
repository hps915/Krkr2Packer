# -*- coding: utf-8 -*-
"""Krkr2Packer 端到端自测：单元测试 + 真实模板完整管线 + 产物 apktool 验收。"""
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

PROJ = Path(r"C:\hps\GAME\Deepseek\gal\Krkr2Packer")
sys.path.insert(0, str(PROJ))

from core import config, utils                      # noqa: E402
from core import game as game_mod                   # noqa: E402
from core import manifest_edit as medit             # noqa: E402
from core import template as tpl_mod                # noqa: E402
from core import builder as builder_mod             # noqa: E402
from core import signer as signer_mod               # noqa: E402
from core import icon_gen                           # noqa: E402

utils.set_log_sink(lambda s: None)   # 静默：管线日志量太大，只打印测试结论

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)
    if not cond:
        FAILS.append(name)


# ================= 1) apktool.yml =================
YML = """!!brut.androlib.meta.MetaInfo
apkFileName: shell.apk
compressionType: false
isFrameworkApk: false
packageInfo:
  forcedPackageId: '127'
  renameManifestPackage: null
sdkInfo:
  minSdkVersion: '19'
  targetSdkVersion: '26'
sharedLibrary: false
sparseResources: false
unknownFiles: {}
usesFramework:
  ids:
  - 1
  tag: null
version: 2.9.3
versionInfo:
  versionCode: '1'
  versionName: '1.0'
"""
v = medit.parse_apktool_yaml(YML)
check("yml.parse", v.get("minSdkVersion") == "19" and v.get("targetSdkVersion") == "26"
      and v.get("versionCode") == "1" and v.get("versionName") == "1.0"
      and v.get("apktool_version") == "2.9.3", str(v))
y2 = medit.set_apktool_yaml(YML, version_code="42", version_name="9.9", target_sdk="30")
v2 = medit.parse_apktool_yaml(y2)
check("yml.rewrite", v2.get("versionCode") == "42" and v2.get("versionName") == "9.9"
      and v2.get("targetSdkVersion") == "30" and v2.get("minSdkVersion") == "19", str(v2))
check("yml.keep_other_lines", "forcedPackageId: '127'" in y2 and "version: 2.9.3" in y2)

# ================= 2) Manifest 文本操作 =================
MF = '''<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example.old"
    android:versionCode="1" android:versionName="1.0">
    <uses-permission android:name="android.permission.INTERNET"/>
    <uses-permission android:name="android.permission.WRITE_EXTERNAL_STORAGE"/>
    <uses-permission android:name="android.permission.READ_PHONE_STATE"/>
    <application
        android:allowBackup="true"
        android:label="@string/app_name"
        android:icon="@mipmap/ic_launcher" >
        <activity android:name="com.example.old.MainActivity">
            <intent-filter>
                <action android:name="android.intent.action.MAIN"/>
            </intent-filter>
        </activity>
        <service android:name=".MyService" >
            <intent-filter>
                <action android:name="x"/>
            </intent-filter>
        </service>
        <activity android:name="com.example.old.Plain" />
        <receiver android:name=".MyReceiver">
            <intent-filter>
                <action android:name="y"/>
            </intent-filter>
        </receiver>
    </application>
</manifest>
'''
check("mf.get_package", medit.get_package(MF) == "com.example.old")
t, old = medit.set_package(MF, "com.new.pkg")
check("mf.set_package", old == "com.example.old" and 'package="com.new.pkg"' in t)
t2, n = medit.qualify_relative_component_names(t, old)
check("mf.qualify", n == 2 and 'android:name="com.example.old.MyService"' in t2
      and 'android:name="com.example.old.MyReceiver"' in t2, f"n={n}")
check("mf.label_ref", medit.get_app_label(MF) == "@string/app_name")
t3, ref = medit.set_app_label(MF, "测试游戏")
check("mf.label_ref_kept", ref == "@string/app_name" and 'android:label="@string/app_name"' in t3)
MF_LIT = MF.replace('android:label="@string/app_name"', 'android:label="Old &amp; Co"')
t4, ref2 = medit.set_app_label(MF_LIT, '新名字<>&"\'')
check("mf.label_literal", ref2 is None and 'android:label="新名字&lt;&gt;&amp;&quot;\'' in t4)
check("mf.icon_ref", medit.get_icon_ref(MF) == "@mipmap/ic_launcher")
check("mf.perms", len(medit.get_permissions(MF)) == 3)
t5, rm = medit.set_permissions(MF, {"android.permission.INTERNET"})
check("mf.perm_filter", rm == 2 and t5.count("<uses-permission") == 1, f"rm={rm}")
t6, n6 = medit.add_exported_to_components(MF)
check("mf.exported_count", n6 == 3, f"n={n6}")
svc = t6.split("<service")[1].split("</service>")[0]
check("mf.exported_service", 'android:exported="true"' in svc)
plain_seg = t6.split('<activity android:name="com.example.old.Plain"')[1].split("/>")[0]
check("mf.exported_selfclosing_untouched", "exported" not in plain_seg)
first_act_open = t6.split("<activity")[1].split(">")[0]
check("mf.exported_in_opening_tag", "exported" in first_act_open)

# ================= 3) recentpath / smali / marker =================
tmp = Path(tempfile.mkdtemp(prefix="krkr_unit_"))
assets = tmp / "assets"
assets.mkdir(parents=True)
p = medit.write_recentpath(assets, "com.krkr.test001", "data.xp3")
check("recentpath.write",
      '<Item Path="/storage/emulated/0/Android/data/com.krkr.test001/files/assets/data.xp3"/>'
      in p.read_text(encoding="utf-8"))

smali = tmp / "smali" / "sl" / "qlwh"
smali.mkdir(parents=True)
f_main = smali / "MainActivity.smali"
f_main.write_text(
    'const-string v0, "Android/data/com.example.old/files"\n'
    'const-string v1, "com.example.old.MainActivity"\n'
    'const-string v2, "Lsl/qlwh/MainActivity;"\n', encoding="utf-8")
n = medit.replace_old_package(tmp, "com.example.old", "com.krkr.test001")
body = f_main.read_text(encoding="utf-8")
check("smali.replace_count", n == 1, f"n={n}")
check("smali.path_replaced", '"Android/data/com.krkr.test001/files"' in body)
check("smali.classref_kept", '"com.example.old.MainActivity"' in body)
check("smali.slash_kept", '"Lsl/qlwh/MainActivity;"' in body)

f_main.write_text('const-string v3, "youlagou_ready_v2"\n'
                  'const-string v4, "youlagou_ready_v2"\n'
                  'invoke-virtual {v0}, Lcom/example/Mgr;->install(I)V\n', encoding="utf-8")
n3 = medit.replace_install_marker(tmp, "youlagou_ready_v2", "krkr_ready_ab12cd34")
body_after = f_main.read_text(encoding="utf-8")
check("marker.replace", n3 == 2 and "krkr_ready_ab12cd34" in body_after, f"n={n3}")
check("marker.safety_methodname_untouched", "->install(I)V" in body_after)
f_main.write_text('const-string v3, "install"\n'
                  'invoke-virtual {v0}, Lcom/example/Mgr;->install(I)V\n', encoding="utf-8")
n4 = medit.replace_install_marker(tmp, "install", "krkr_ready_ab12cd34")
check("marker.generic_word_only_in_literal",
      n4 == 1 and "->install(I)V" in f_main.read_text(encoding="utf-8"), f"n={n4}")

# ================= 4) 游戏扫描 =================
gdir = tmp / "fake_game"
gdir.mkdir()
(gdir / "data.xp3").write_bytes(b"XP3_\r\n\x00\x00\x00\x00\x00" + b"\x00" * (1200 * 1024))
(gdir / "plugin").mkdir()
(gdir / "plugin" / "foo.dll").write_bytes(b"\x00" * 64)
(gdir / "other.xp3").write_bytes(b"NOTMAGIC" + b"\x00" * 50)      # 小且无魔数 → 不算
(gdir / "big_enc.xp3").write_bytes(b"ZZZZ" + b"\x00" * (1100 * 1024))  # >1MB 无魔数 → 算
(gdir / "myfont.ttf").write_bytes(b"\x00" * 128)                  # 游戏自带字体 → 应照常打包
files, others, total = game_mod.scan_game_dir(gdir)
by_name = {f.name: f for f in files}
check("game.scan_all_listed", len(files) == 3 and others == 2,
      f"files={sorted(by_name)}, others={others}")
check("game.small_no_magic_listed", "other.xp3" in by_name
      and not by_name["other.xp3"].has_magic
      and "仍可选用" in by_name["other.xp3"].detect_text,
      by_name.get("other.xp3").detect_text if "other.xp3" in by_name else "missing")
check("game.detect_flags", by_name["data.xp3"].has_magic
      and not by_name["big_enc.xp3"].has_magic
      and "加密" in by_name["big_enc.xp3"].detect_text)
check("game.krkr_verdict", game_mod.is_krkr_game(files))
check("game.suggest", game_mod.suggest_startup(files).name == "data.xp3")

# ================= 4.5) 包名输入归一化（中文输入法兼容） =================
cases = [
    ("com.example.game", "com.example.game"),          # 正常输入不变
    ("com。example。game", "com.example.game"),          # 中文句号 → 点
    ("com．example．game", "com.example.game"),          # 全角点 → 点
    ("COM.Example.GAME", "com.example.game"),          # 大写 → 小写
    ("ｃｏｍ．ｅｘａｍｐｌｅ", "com.example"),            # 全角字母数字 → 半角
    (" com . test . 01 ", "com.test.01"),              # 去空白
]
for raw, want in cases:
    got = utils.normalize_package_input(raw)
    check(f"normalize[{raw}]", got == want, f"{got!r}")

# ================= 5) 图标 =================
from PIL import Image  # noqa: E402
icon = tmp / "icon.png"
Image.new("RGBA", (300, 200), (200, 30, 30, 255)).save(icon)
img = icon_gen.load_square(icon)
check("icon.square_pad", img.size == (300, 300))
rnd = icon_gen.rounded(img, 96)
check("icon.rounded", rnd.size == (96, 96))

# ================= 6) 真实模板解包 =================
APK = Path(r"C:\Users\Administrator\Documents\Default Project\传送门\直装包模板壳.apk")
try:
    info = tpl_mod.unpack(APK)
except Exception as e:
    traceback.print_exc()
    print("FAIL template.unpack", e)
    sys.exit(1)
check("tpl.package", info.package == "com.example.youlagoumarriage", info.package)
check("tpl.icon", bool(info.icon_ref), f"icon={info.icon_ref} label={info.label}")
check("tpl.markers", "youlagou_ready_v2" in info.markers, str(info.markers[:5]))
check("tpl.kernel", len(info.kernel_hashes) == 2,
      " ".join(f"{k}:{h[:12]}" for k, h in sorted(info.kernel_hashes.items())))
hard, soft = tpl_mod.validate_structure(info.unpack_dir)
check("tpl.structure", not hard, f"hard={hard} soft={soft}")
check("tpl.sdk", info.min_sdk == "19" and info.target_sdk == "26",
      f"min={info.min_sdk} target={info.target_sdk} ver={info.version_name}/{info.version_code}")
check("tpl.perms", len(info.permissions) > 0, str(info.permissions))
status, _ = tpl_mod.check_kernel(info, {"kernel_accepted": info.kernel_hashes})
check("tpl.kernel_check", status == "ok")

# uses-sdk 是否存在于解码后的 Manifest（决定 set_uses_sdk 行为）
mtxt = medit.read_manifest(info.unpack_dir)
print("INFO uses-sdk in manifest:", "<uses-sdk" in mtxt, "| label:", info.label,
      "| icon:", info.icon_ref, "| roundIcon:", info.round_icon_ref)

# ================= 7) 完整管线（假游戏 → 成品） =================
cfg = config.load_config()
cfg["kernel_accepted"] = dict(info.kernel_hashes)
cfg["apktool_path"] = ""
config.save_config(cfg)

out = config.BUILD_ROOT / "out"
out.mkdir(parents=True, exist_ok=True)
params = builder_mod.BuildParams(
    template_dir=info.unpack_dir, game_dir=gdir, startup="data.xp3",
    package="com.krkr.test001", app_name="测试游戏Test", out_dir=out,
    icon_path=str(icon), icon_ref=info.icon_ref,
    marker_replace=True, marker_new="krkr_ready_ab12cd34",
    template_marker=info.markers[0] if info.markers else "",
    template_target_sdk=info.target_sdk,
    keystore_path=str(config.DEFAULT_KEYSTORE), store_pass="krkr123456", alias="game",
)
b = builder_mod.Builder(params)
b.on_step = lambda i, name: print(f"  step {i+1}/13 {name}", flush=True)
b.start()
b.join()
res = b.result
check("build.ok", res.ok, res.error[:800] if res.error else "")
if not res.ok:
    sys.exit(1)
check("build.name", Path(res.apk_path).name == "测试游戏Test直装包.apk", Path(res.apk_path).name)
check("build.verify_all_ok", all(x.startswith("✓") for x in res.verify), str(res.verify))
print("INFO build size:", utils.human_size(res.size), "sha256:", res.sha256[:16], "…")

# ================= 8) 验收标准1：产物可 apktool d 反编译 =================
java = utils.find_java()
apktool = utils.find_apktool(cfg)
vd = config.BUILD_ROOT / "verify_manual"
shutil.rmtree(vd, ignore_errors=True)
utils.set_log_sink(None)
utils.run_cmd([java, "-jar", apktool, "d", "-f", "-o", vd, res.apk_path], check=True)
mtxt = (vd / "AndroidManifest.xml").read_text(encoding="utf-8")
check("verify.redecode_pkg", medit.get_package(mtxt) == "com.krkr.test001",
      medit.get_package(mtxt))
rt = (vd / "assets" / "recentpath.xml").read_text(encoding="utf-8")
check("verify.redecode_recentpath",
      "com.krkr.test001" in rt and "data.xp3" in rt, rt.strip().replace("\n", " "))
sm = list((vd / "smali").rglob("MainActivity.smali"))
check("verify.smali_intact", len(sm) == 1 and "smali/sl/qlwh" in str(sm[0]).replace("\\", "/"),
      str(sm))
all_smali = "\n".join(f.read_text(encoding="utf-8", errors="replace")
                      for f in (vd / "smali").rglob("*.smali"))
check("verify.marker_replaced", "krkr_ready_ab12cd34" in all_smali
      and "youlagou_ready_v2" not in all_smali)
mtxt2 = medit.read_manifest(vd)
check("verify.app_name_manifest", f'android:label="测试游戏Test"' in mtxt2,
      medit.get_app_label(mtxt2))
strings_xmls = list((vd / "res").glob("values*/strings.xml"))
has_app_name = any("<string name=\"app_name\"" in f.read_text(encoding="utf-8", errors="replace")
                   for f in strings_xmls)
if has_app_name:
    check("verify.app_name_strings", any("测试游戏Test" in f.read_text(encoding="utf-8", errors="replace")
                                         for f in strings_xmls))
else:
    print("INFO template strings.xml 无 app_name（label 为字面量形态），跳过 strings 断言")
icon_pngs = list((vd / "res").glob("**/ic_launcher*.png"))
check("verify.icon_png", len(icon_pngs) >= 3, str([p.parent.name for p in icon_pngs]))
game_in_assets = (vd / "assets" / "data.xp3").is_file() and (vd / "assets" / "plugin" / "foo.dll").is_file()
check("verify.game_files", game_in_assets)
check("verify.game_font_copied", (vd / "assets" / "myfont.ttf").is_file(),
      "游戏自带 ttf 应照常打包")
whitelist_ok = (vd / "assets" / "ui" / "RecentListItem.csb").is_file() \
    and (vd / "assets" / "DroidSansFallback.ttf").is_file()
check("verify.whitelist_kept", whitelist_ok)
all_xp3_copied = (vd / "assets" / "other.xp3").is_file()  # 规格书：所有 .xp3 与目录均拷入
check("verify.all_xp3_copied", all_xp3_copied)

# ================= 9) 签名工具发现（无 SDK 时应返回 None） =================
z, a = signer_mod.find_build_tools({"build_tools_dir": ""})
print("INFO build-tools found:", z, a)
check("sign.nobuildtools_graceful", (z is None) == (a is None))

print()
print("用时可忽略。总结:", "ALL PASS ✔" if not FAILS else f"FAILED {len(FAILS)}: {FAILS}")
shutil.rmtree(tmp, ignore_errors=True)
shutil.rmtree(vd, ignore_errors=True)
sys.exit(0 if not FAILS else 1)
