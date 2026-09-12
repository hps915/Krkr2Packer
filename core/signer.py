# -*- coding: utf-8 -*-
"""keystore 生成/校验、v1(jarsigner) 与 v2(zipalign+apksigner) 签名（规格书 2.6）。

- targetSdk <= 29：jarsigner 的 v1 签名即可安装（安卓 11+ 对 targetSdk<=29 的
  v1-only 包有豁免）。官方推荐顺序 zipalign -> jarsigner。
- targetSdk >= 30：安卓 11+ 强制 v2 签名，v1-only 直接拒装。必须
  apktool b -> zipalign -f 4 -> apksigner sign，缺 build-tools 时给出明确报错。
"""
import os
import re
from dataclasses import dataclass
from pathlib import Path

from . import utils


@dataclass
class SignParams:
    keystore: Path
    store_pass: str
    alias: str
    key_pass: str = ""          # 默认同 store_pass
    build_tools_dir: str = ""
    v2: bool = False


def _exe(d: Path, name: str):
    for cand in (d / (name + ".exe"), d / (name + ".bat"), d / name):
        if cand.is_file():
            return cand
    return None


def _ver_key(d: Path):
    m = re.match(r"(\d+)(?:\.(\d+))?", d.name)
    return (int(m.group(1)), int(m.group(2) or 0)) if m else (-1, 0)


def find_build_tools(cfg_or_dir) -> tuple:
    """搜索 zipalign/apksigner：用户指定目录 → ANDROID_HOME → %LOCALAPPDATA%/Android/Sdk。

    返回 (zipalign, apksigner)，找不到返回 (None, None)。
    """
    bases = []
    if isinstance(cfg_or_dir, dict):
        d = cfg_or_dir.get("build_tools_dir")
        if d:
            bases.append(Path(d))
    elif cfg_or_dir:
        bases.append(Path(str(cfg_or_dir)))
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        v = os.environ.get(env)
        if v:
            bases.append(Path(v) / "build-tools")
            bases.append(Path(v))
    local = os.environ.get("LOCALAPPDATA")
    if local:
        sdk = Path(local) / "Android" / "Sdk"
        bases.append(sdk / "build-tools")
        bases.append(sdk)
    for base in bases:
        if not base.is_dir():
            continue
        z, a = _exe(base, "zipalign"), _exe(base, "apksigner")
        if z and a:
            return z, a
        subs = sorted((d for d in base.iterdir() if d.is_dir()),
                      key=_ver_key, reverse=True)
        for d in subs:
            z, a = _exe(d, "zipalign"), _exe(d, "apksigner")
            if z and a:
                return z, a
    return None, None


V2_HELP = (
    "targetSdk ≥ 30 的 APK 在安卓 11+ 强制要求 v2 签名（v1-only 直接拒装，报解析错误）。\n"
    "当前未找到 Android build-tools 中的 zipalign / apksigner。\n"
    "解决办法（任选其一）：\n"
    "  1) 「高级设置」里把 targetSdk 改回「跟随模板」（26，兼容性最好，推荐）；\n"
    "  2) 安装 Android SDK build-tools（Android Studio 或 cmdline-tools），\n"
    "     然后在「高级设置 → build-tools 目录」指定其路径。")


def ensure_keystore(sp: SignParams, allow_generate=True, log=utils.log) -> None:
    """keystore 不存在时自动生成（规格书 2.6 keytool 命令）；存在则校验口令与别名。"""
    ks = Path(sp.keystore)
    keytool = utils.find_jdk_tool("keytool")
    if not keytool:
        raise utils.ToolError("未找到 keytool（需要完整 JDK 17+，而非 JRE）")
    if ks.is_file():
        rc, _ = utils.run_cmd([keytool, "-list", "-keystore", ks,
                               "-storepass", sp.store_pass, "-alias", sp.alias],
                              log_fn=log)
        if rc != 0:
            raise utils.ToolError(
                f"keystore 校验失败（口令错误、别名不存在或文件损坏）: {ks}\n"
                "· 本工具自动生成的 keystore 默认口令为 krkr123456、别名为 game，"
                "请检查「高级设置 → 签名」里填写的是否一致；\n"
                "· 若是导入的 keystore，请核对密码与别名；\n"
                "· 若文件确已损坏，删除该文件后重新打包会自动重新生成"
                "（注意：换 keystore 后，同包名的旧版必须先卸载才能装新版）。")
        return
    if not allow_generate:
        raise utils.ToolError(f"keystore 不存在: {ks}")
    ks.parent.mkdir(parents=True, exist_ok=True)
    log(f"keystore 不存在，自动生成（一次生成后复用）: {ks}")
    utils.run_cmd([keytool, "-genkeypair", "-v", "-keystore", ks, "-alias", sp.alias,
                   "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000",
                   "-storepass", sp.store_pass, "-keypass", sp.key_pass,
                   "-dname", "CN=Game"], log_fn=log, check=True)


def sign_apk(in_apk, out_apk, sp: SignParams, cancel=None, log=utils.log) -> None:
    in_apk, out_apk = Path(in_apk), Path(out_apk)
    jarsigner = utils.find_jdk_tool("jarsigner")
    if not jarsigner:
        raise utils.ToolError("未找到 jarsigner（请安装完整 JDK 17+，而非 JRE）")

    if sp.v2:
        zipalign, apksigner = find_build_tools(sp.build_tools_dir)
        if not zipalign or not apksigner:
            raise utils.ToolError(V2_HELP)
        aligned = in_apk.parent / (in_apk.stem + "_aligned.apk")
        utils.run_cmd([zipalign, "-f", "4", in_apk, aligned],
                      cancel=cancel, log_fn=log, check=True)
        log("apksigner v2 签名…")
        utils.run_cmd([apksigner, "sign", "--ks", sp.keystore,
                       "--ks-pass", f"pass:{sp.store_pass}",
                       "--ks-key-alias", sp.alias,
                       "--key-pass", f"pass:{sp.key_pass}",
                       "--out", out_apk, aligned],
                      cancel=cancel, log_fn=log, check=True)
        aligned.unlink(missing_ok=True)
        rc, _ = utils.run_cmd([apksigner, "verify", "--print-certs", out_apk],
                              cancel=cancel, log_fn=log)
        if rc != 0:
            log("  ⚠ apksigner verify 未通过，请检查日志")
    else:
        # v1：先 zipalign（可选；未配置 build-tools 时直接签），后 jarsigner
        src = in_apk
        aligned = in_apk.parent / (in_apk.stem + "_aligned.apk")
        zipalign, _ = find_build_tools(sp.build_tools_dir)
        if zipalign:
            rc, _ = utils.run_cmd([zipalign, "-f", "4", in_apk, aligned],
                                  cancel=cancel, log_fn=log)
            if rc == 0:
                src = aligned
        log("jarsigner v1 签名（targetSdk<=29 可直接安装）…")
        rc, _ = utils.run_cmd(
            [jarsigner, "-sigalg", "SHA256withRSA", "-digestalg", "SHA-256",
             "-keystore", sp.keystore, "-storepass", sp.store_pass,
             "-keypass", sp.key_pass, "-signedjar", out_apk, src, sp.alias],
            cancel=cancel, log_fn=log)
        if src != in_apk:
            src.unlink(missing_ok=True)
        if rc != 0:
            raise utils.ToolError(f"jarsigner 签名失败（退出码 {rc}），详见日志")
        rc, out = utils.run_cmd([jarsigner, "-verify", out_apk],
                                cancel=cancel, log_fn=log)
        if rc != 0:
            log("  ⚠ jarsigner -verify 未通过，请检查日志")
