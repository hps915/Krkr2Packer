# -*- coding: utf-8 -*-
"""打包管线编排（规格书 第四节 13 步）。跑在后台线程，日志走 utils.log 全局槽。"""
import os
import re
import shutil
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import config, signer, utils
from . import game as game_mod
from . import manifest_edit as medit
from . import template as tpl

try:
    from . import icon_gen
except Exception:               # Pillow 未安装时其余功能仍可用
    icon_gen = None

STEPS = ["校验输入", "复制模板", "清理游戏区", "拷入游戏", "重写 recentpath",
         "修改包名", "修改应用名", "替换图标", "版本/SDK 设置", "exported 检查",
         "回编译 APK", "签名", "成品验收"]


@dataclass
class BuildParams:
    template_dir: Path
    game_dir: Path
    startup: str
    package: str
    app_name: str
    out_dir: Path
    out_name_tpl: str = config.DEFAULT_OUTPUT_NAME
    icon_path: str = ""
    icon_ref: str = ""
    version_code: str = ""
    version_name: str = ""
    min_sdk: str = ""             # 空 = 跟随模板
    target_sdk: str = ""          # 空 = 跟随模板
    template_target_sdk: str = "26"
    perm_keep: list = None        # None = 全部保留
    marker_replace: bool = False
    marker_new: str = ""
    template_marker: str = ""
    apktool_path: str = ""
    build_tools_dir: str = ""
    keystore_path: str = ""
    store_pass: str = ""
    key_pass: str = ""
    alias: str = "game"


@dataclass
class BuildResult:
    ok: bool = False
    apk_path: str = ""
    size: int = 0
    sha256: str = ""
    error: str = ""
    cancelled: bool = False
    warnings: list = field(default_factory=list)
    verify: list = field(default_factory=list)


class Builder(threading.Thread):
    def __init__(self, params: BuildParams, cancel_event=None):
        super().__init__(daemon=True, name="Krkr2PackerBuilder")
        self.params = params
        self.cancel_event = cancel_event or threading.Event()
        self.result = BuildResult()
        self.on_step = None      # fn(step_index, step_name)
        self.on_done = None      # fn(BuildResult)
        self._work = None

    # ---------------- 骨架 ----------------
    def _step(self, idx: int):
        utils.log(f"\n========== 第 {idx + 1}/{len(STEPS)} 步：{STEPS[idx]} ==========")
        if self.on_step:
            try:
                self.on_step(idx, STEPS[idx])
            except Exception:
                pass

    def _check_cancel(self):
        if self.cancel_event.is_set():
            raise utils.CancelledError("操作已取消")

    def run(self):
        res = self.result
        work = None
        try:
            self._check_cancel()
            self._step(0)
            self._validate()
            self._step(1)
            work = self._work = self._copy_template()
            self._step(2)
            self._clean_assets()
            self._step(3)
            self._copy_game()
            self._step(4)
            self._rewrite_recentpath()
            self._step(5)
            self._rename_package()
            self._step(6)
            self._set_app_name()
            self._step(7)
            self._replace_icon()
            self._step(8)
            self._apply_version_sdk()
            self._step(9)
            tsdk = self._maybe_exported()
            self._step(10)
            unsigned = self._apktool_build(work)
            self._step(11)
            final = self._sign(unsigned, tsdk)
            self._step(12)
            self._verify(final)
            res.ok = True
            res.apk_path = str(final)
            res.size = final.stat().st_size
            res.sha256 = utils.sha256_file(final)
            shutil.rmtree(work, ignore_errors=True)
            utils.log(f"\n✔ 打包完成: {final}")
            utils.log(f"  体积: {utils.human_size(res.size)}")
            utils.log(f"  SHA256: {res.sha256}")
        except utils.CancelledError:
            res.cancelled = True
            res.error = "已取消"
            utils.log("!! 用户取消打包")
        except utils.ToolError as e:
            res.error = str(e)
            utils.log(f"!! 打包失败: {e}")
        except Exception as e:
            import traceback
            res.error = f"{e}"
            utils.log(f"!! 未预期异常: {e}")
            utils.log(traceback.format_exc(limit=5))
        if self._work and not res.ok:
            utils.log(f"（工作目录保留供排查: {self._work}）")
        if self.on_done:
            try:
                self.on_done(res)
            except Exception:
                pass

    # ---------------- 各步骤 ----------------
    def _validate(self):
        p = self.params
        if not re.fullmatch(config.PACKAGE_RE, p.package or ""):
            raise utils.ToolError(
                f"包名不合法: {p.package!r}\n"
                "格式示例: com.example.game（小写字母开头，至少两段，"
                "仅小写字母/数字/下划线）")
        if not Path(p.template_dir).is_dir():
            raise utils.ToolError("模板未解包，请先在「模板设置」加载模板 APK")
        hard, _ = tpl.validate_structure(p.template_dir)
        if hard:
            raise utils.ToolError("无效模板，结构缺失: " + ", ".join(hard))
        gdir = Path(p.game_dir)
        if not gdir.is_dir():
            raise utils.ToolError(f"游戏目录不存在: {gdir}")
        if not (gdir / p.startup).is_file():
            raise utils.ToolError(f"启动文件不存在: {gdir / p.startup}")
        name = self._final_name()
        if not name.lower().endswith(".apk"):
            raise utils.ToolError(f"输出文件名必须以 .apk 结尾: {name}")
        Path(p.out_dir).mkdir(parents=True, exist_ok=True)
        utils.log(f"输入校验通过：包名 {p.package}，启动文件 {p.startup}")

    def _final_name(self) -> str:
        p = self.params
        name = (p.out_name_tpl or config.DEFAULT_OUTPUT_NAME).format(appname=p.app_name)
        name = re.sub(r'[\\/:*?"<>|]', "_", name).strip()
        return name or "output.apk"

    def _copy_template(self) -> Path:
        work = config.BUILD_ROOT / f"build_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}"
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)
        work.parent.mkdir(parents=True, exist_ok=True)
        utils.log(f"复制模板到工作目录: {work}")
        shutil.copytree(self.params.template_dir, work)
        return work

    def _clean_assets(self):
        """assets/ 下删除白名单之外的一切（瘦身壳模板中本应为空，此步为兜底）。"""
        assets = self._work / "assets"
        removed = []
        for child in sorted(assets.iterdir()):
            if child.name in config.ASSETS_WHITELIST:
                continue
            if child.is_file() and child.suffix.lower() in config.ASSETS_WHITELIST_EXTS:
                continue    # 引擎字体/光标等运行时文件
            removed.append(child.name)
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        if removed:
            utils.log("已清理游戏区残留（白名单之外）: " + ", ".join(removed))
        else:
            utils.log("游戏区已为空（瘦身壳模板），无需清理")

    def _copy_game(self):
        """游戏目录里的【所有文件与子目录】全部拷入 assets（含 plugin/savedata/
        游戏自带字体等），仅与引擎白名单精确同名/同名的引擎运行时文件冲突时跳过。"""
        p = self.params
        assets = self._work / "assets"
        gdir = Path(p.game_dir)
        total = 0
        for child in sorted(gdir.iterdir()):
            conflict = (child.name in config.ASSETS_WHITELIST
                        or child.name in config.ASSETS_ENGINE_FILES)
            if conflict:
                msg = f"游戏文件与引擎白名单冲突，已跳过（白名单优先）: {child.name}"
                self.result.warnings.append(msg)
                utils.log(f"  ⚠ {msg}")
                continue
            dest = assets / child.name
            if child.is_dir():
                shutil.copytree(child, dest, dirs_exist_ok=True)
                size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file())
            else:
                shutil.copy2(child, dest)
                size = child.stat().st_size
            total += size
            utils.log(f"  拷入 {child.name} ({utils.human_size(size)})")
        utils.log(f"游戏资源拷入完成，共 {utils.human_size(total)}")

    def _rewrite_recentpath(self):
        p = medit.write_recentpath(self._work / "assets", self.params.package,
                                   self.params.startup)
        utils.log(f"已重写 {p.name}（游戏入口钥匙）:")
        for line in p.read_text(encoding="utf-8").rstrip().splitlines():
            utils.log("  " + line)

    def _rename_package(self):
        p = self.params
        text = medit.read_manifest(self._work)
        text, old_pkg = medit.set_package(text, p.package)
        utils.log(f"包名: {old_pkg} -> {p.package}")
        if old_pkg != p.package:
            text, n = medit.qualify_relative_component_names(text, old_pkg)
            if n:
                utils.log(f"  相对类名补充旧包名前缀: {n} 处")
        medit.write_manifest(self._work, text)
        n = medit.replace_old_package(self._work, old_pkg, p.package)
        utils.log(f"  smali/assets 旧包名字符串替换: {n} 处")
        # 安装标记改名：同包名覆盖安装换游戏时强制重新解压
        if p.marker_replace:
            if p.template_marker:
                marker = p.marker_new or ("krkr_ready_" + os.urandom(4).hex())
                n2 = medit.replace_install_marker(self._work, p.template_marker, marker)
                utils.log(f"  安装标记 {p.template_marker} -> {marker}（{n2} 处）")
            else:
                utils.log("  未在模板中发现安装标记，跳过改名")

    def _set_app_name(self):
        p = self.params
        text = medit.read_manifest(self._work)
        text, ref = medit.set_app_label(text, p.app_name)
        medit.write_manifest(self._work, text)
        if ref:
            name = ref.split("/", 1)[1]
            n = medit.update_string_resource(self._work / "res", name, p.app_name)
            if not n:
                n = medit.upsert_string_resource(self._work / "res", name, p.app_name)
            utils.log(f"应用名 -> {p.app_name}（更新 {ref}，{n} 个 values 文件）")
        else:
            utils.log(f"应用名 -> {p.app_name}（Manifest 字面量）")
        # 两种形态都处理：strings.xml 里若存在 app_name 一并更新（规格书 P0-5）
        n2 = medit.update_string_resource(self._work / "res", "app_name", p.app_name)
        if n2:
            utils.log(f"  同步更新 res/values*/strings.xml 的 app_name（{n2} 个文件）")

    def _replace_icon(self):
        p = self.params
        if not p.icon_path:
            utils.log("未选择图标，沿用模板图标")
            return
        if icon_gen is None:
            raise utils.ToolError("Pillow 未安装（pip install -r requirements.txt），无法替换图标")
        text = medit.read_manifest(self._work)
        icon_ref = medit.get_icon_ref(text)
        if not icon_ref:
            msg = "Manifest 无 android:icon，图标未替换"
            self.result.warnings.append(msg)
            utils.log(f"  ⚠ {msg}")
            return
        img = icon_gen.load_square(p.icon_path)
        icon_gen.replace_icons(self._work / "res", icon_ref, img,
                               round_ref=medit.get_round_icon_ref(text))
        utils.log(f"图标已替换为 {Path(p.icon_path).name}（48/72/96/144/192 五档）")

    @staticmethod
    def _mbcs_roundtrip_ok(s: str) -> bool:
        """Windows ANSI 代码页能否无损表示该字符串（aapt 命令行参数以代码页传递）。"""
        if os.name != "nt":
            return True
        try:
            return s.encode("mbcs").decode("mbcs") == s
        except Exception:
            return False

    def _apply_version_sdk(self):
        p = self.params
        yml = self._work / "apktool.yml"
        ytext = yml.read_text(encoding="utf-8", errors="replace")
        changed = False
        if any((p.version_code, p.version_name, p.min_sdk, p.target_sdk)):
            ytext = medit.set_apktool_yaml(
                ytext, version_code=p.version_code or None,
                version_name=p.version_name or None,
                min_sdk=p.min_sdk or None, target_sdk=p.target_sdk or None)
            changed = True
            utils.log(f"apktool.yml 已更新: versionCode={p.version_code or '跟随'} "
                      f"versionName={p.version_name or '跟随'} "
                      f"minSdk={p.min_sdk or '跟随'} targetSdk={p.target_sdk or '跟随'}")
            mtext = medit.read_manifest(self._work)
            mtext2, ch = medit.set_uses_sdk(mtext, p.min_sdk or None, p.target_sdk or None)
            if ch:
                medit.write_manifest(self._work, mtext2)
                utils.log("  Manifest 内 <uses-sdk> 已同步修改（优先级高于 yml）")
        # 版本名净化：部分模板 versionName 含 ANSI 代码页无法表示的字符（如盲文空格
        # U+2800），apktool 传给 aapt 的命令行参数会变成 '?'，回编译直接失败。
        cur = medit.parse_apktool_yaml(ytext).get("versionName", "")
        if cur and not self._mbcs_roundtrip_ok(cur):
            ytext = medit.set_apktool_yaml(ytext, version_name="1.0")
            changed = True
            utils.log(f"  ⚠ versionName 含代码页无法表示的字符（{cur!r}），已改为 1.0")
        if changed:
            yml.write_text(ytext, encoding="utf-8")
        else:
            utils.log("版本/SDK 跟随模板（未修改）")

    def _maybe_exported(self) -> int:
        p = self.params
        tsdk = int(p.target_sdk or p.template_target_sdk or 26)
        if tsdk >= 30:
            text = medit.read_manifest(self._work)
            text, n = medit.add_exported_to_components(text)
            medit.write_manifest(self._work, text)
            utils.log(f"targetSdk={tsdk}（>=30）：已为 {n} 个带 intent-filter 的组件"
                      f"补 android:exported=\"true\"")
            utils.log("  注意：targetSdk>=30 还需 v2 签名（zipalign + apksigner）")
        else:
            utils.log(f"targetSdk={tsdk}（<30），无需处理 exported；将使用 v1 签名")
        return tsdk

    def _apktool_build(self, work: Path) -> Path:
        java = utils.find_java()
        apktool = utils.find_apktool({"apktool_path": self.params.apktool_path})
        unsigned = config.BUILD_ROOT / f"unsigned_{int(time.time())}.apk"
        utils.log("apktool 回编译中（assets 量大时需数分钟，请耐心等待）…")
        t0 = time.time()
        # 老模板的 res/values/layouts.xml 含 APKTOOL_DUMMY 占位符，aapt2（apktool
        # 2.7+ 默认）会报 "invalid value for type 'layout'"，故回编用 aapt1。
        args = [java, "-jar", apktool, "b"]
        if utils.apktool_prefers_aapt1_flag(apktool):
            args.append("--use-aapt1")
        args += [work, "-o", unsigned]
        utils.run_cmd(args, cancel=self.cancel_event, check=True)
        if not unsigned.is_file() or unsigned.stat().st_size == 0:
            raise utils.ToolError("apktool 未生成产物 APK")
        utils.log(f"回编译完成，用时 {time.time() - t0:.0f} 秒")
        return unsigned

    def _sign(self, unsigned: Path, tsdk: int) -> Path:
        p = self.params
        if not p.keystore_path:
            raise utils.ToolError("未配置 keystore（高级设置 → 签名）")
        sp = signer.SignParams(
            keystore=Path(p.keystore_path), store_pass=p.store_pass,
            key_pass=p.key_pass or p.store_pass, alias=p.alias or "game",
            build_tools_dir=p.build_tools_dir, v2=tsdk >= 30)
        signer.ensure_keystore(sp, allow_generate=True)
        out_dir = Path(p.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        final = out_dir / self._final_name()
        if final.exists():
            final.unlink()
        signer.sign_apk(unsigned, final, sp, cancel=self.cancel_event)
        try:
            unsigned.unlink()
        except OSError:
            pass
        return final

    def _verify(self, final: Path):
        """成品自动验收（P2）：直接读产物 zip 抽查，几秒内完成，兼容超大包。"""
        p = self.params
        utils.log("成品验收（抽查包内关键内容）…")
        checks = []
        with zipfile.ZipFile(final) as z:
            names = z.namelist()
            mf = z.read("AndroidManifest.xml")
            pkg_ok = (p.package.encode("utf-16le") in mf
                      or p.package.encode("utf-8") in mf)
            checks.append(("Manifest 包名", pkg_ok, p.package))
            try:
                recent = z.read("assets/recentpath.xml").decode("utf-8", "replace")
                rp_ok = p.package in recent and p.startup in recent
                checks.append(("recentpath.xml 入口", rp_ok,
                               f"…/Android/data/{p.package}/files/assets/{p.startup}"))
            except KeyError:
                checks.append(("recentpath.xml 入口", False, "assets/recentpath.xml 缺失"))
            startup_ok = f"assets/{p.startup}" in names
            checks.append(("启动 xp3 已打入", startup_ok, f"assets/{p.startup}"))
            n_xp3 = sum(1 for n in names if n.lower().endswith(".xp3"))
            checks.append(("assets 内 xp3 数量", n_xp3 > 0, f"{n_xp3} 个"))
            if p.icon_ref and p.icon_path:
                rtype, _, name = p.icon_ref[1:].partition("/")
                icon_ok = any(n.startswith(f"res/{rtype}")
                              and Path(n).name == f"{name}.png" for n in names)
                checks.append(("图标资源存在", icon_ok, f"res…/{name}.png"))
        ok_all = True
        for title, ok, detail in checks:
            utils.log(f"  {'✓' if ok else '✗'} {title}: {detail}")
            if not ok:
                ok_all = False
        self.result.verify = [f"{'✓' if ok else '✗'} {t} ({d})" for t, ok, d in checks]
        if not ok_all:
            raise utils.ToolError("成品验收未通过（见上方 ✗ 项）")
        size = final.stat().st_size
        if size > 4 * 1024 ** 3:
            self.result.warnings.append(
                "成品超过 4GB：zip64 与部分签名/安装工具可能不兼容，强烈建议精简游戏资源")
            utils.log("  ⚠ 严重警告：成品超过 4GB，zip64/签名/安装兼容性风险高")
        elif size > 2 * 1024 ** 3:
            self.result.warnings.append("成品超过 2GB：部分机型或工具可能出现兼容问题")
            utils.log("  ⚠ 警告：成品超过 2GB")
