#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Krkr2Packer —— Kirikiri2（吉里吉里2）游戏一键转安卓直装包（Windows 桌面工具）。

用法:  python main.py
依赖:  Python 3.10+、Pillow（pip install -r requirements.txt）、JDK 17+
       （java/keytool/jarsigner 在 PATH；apktool.jar 首次使用自动下载）
"""
import json
import os
import queue
import re
import sys
import threading
import traceback
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core import config, utils
from core import builder as builder_mod
from core import game as game_mod
from core import signer as signer_mod
from core import template as tpl_mod

try:
    from core import icon_gen
    from PIL import ImageTk
except Exception as _e:          # Pillow 缺失时仍可启动，仅图标功能不可用
    icon_gen = None
    ImageTk = None
    _pillow_error = str(_e)
else:
    _pillow_error = ""

PKG_PREVIEW = "/storage/emulated/0/Android/data/{pkg}/files/assets/"
SDK_CHOICES = ["19", "21", "23", "24", "25", "26", "27", "28", "29",
               "30", "31", "32", "33", "34"]
STEP_COUNT = len(builder_mod.STEPS)

SDK_WARNING = ("targetSdk 说明：不写=跟随模板 26（默认推荐，兼容性最好，安卓 14 只弹一次"
               "「为旧版安卓打造」提示）。23~29 启用运行时权限且出现老安卓兼容行为"
               "（如点击屏幕边缘拉伸全屏）；≥30 需 v2 签名（zipalign+apksigner）且带 "
               "intent-filter 的组件必须 exported，否则拒装。")


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.q = queue.Queue()
        self.cfg = config.load_config()
        self.info = None                  # TemplateInfo
        self.kernel_ok = False
        self.builder = None
        self.cancel_event = threading.Event()
        self.xp3_files = []
        self._perm_vars = {}
        self._icon_photo = None

        utils.set_log_sink(lambda s: self.q.put(("log", s)))

        root.title("Krkr2Packer —— Kirikiri2 游戏一键转安卓直装包")
        root.geometry("1060x820")
        root.minsize(980, 720)
        style = ttk.Style()
        for t in ("vista", "xpnative", "clam"):
            if t in style.theme_names():
                style.theme_use(t)
                break

        self._build_vars()
        self._build_ui()
        self._make_ctx_menu()
        self._bind_entry_enhancements()
        self._load_cfg_to_ui()
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll()

        utils.log("Krkr2Packer 启动。流程：加载模板 → 选游戏目录 → 填包名/应用名 → 开始打包。")
        if _pillow_error:
            utils.log(f"⚠ Pillow 未加载，图标替换/预览不可用（pip install -r requirements.txt）: {_pillow_error}")
        if self.cfg.get("template_apk") and Path(self.cfg["template_apk"]).is_file():
            self._start_unpack(force=False)

    # ================= 变量 =================
    def _build_vars(self):
        self.var_template = tk.StringVar()
        self.var_gamedir = tk.StringVar()
        self.var_startup = tk.StringVar()
        self.var_pkg = tk.StringVar()
        self.var_app = tk.StringVar()
        self.var_icon = tk.StringVar()
        self.var_outdir = tk.StringVar()
        self.var_outname = tk.StringVar(value=config.DEFAULT_OUTPUT_NAME)
        self.var_follow_sdk = tk.BooleanVar(value=True)
        self.var_minsdk = tk.StringVar(value="19")
        self.var_targetsdk = tk.StringVar(value="26")
        self.var_vercode = tk.StringVar()
        self.var_vername = tk.StringVar()
        self.var_signmode = tk.StringVar(value="auto")
        self.var_ks_path = tk.StringVar()
        self.var_ks_pass = tk.StringVar()
        self.var_ks_show = tk.BooleanVar(value=False)
        self.var_ks_alias = tk.StringVar(value="game")
        self.var_buildtools = tk.StringVar()
        self.var_apktool = tk.StringVar()
        self.var_marker = tk.BooleanVar(value=True)
        self.var_template_marker = tk.StringVar()
        self.var_marker_val = tk.StringVar()
        self.var_adv_on = tk.BooleanVar(value=False)
        self.var_step = tk.StringVar(value="就绪")

    # ================= 界面 =================
    def _build_ui(self):
        pad = {"padx": 6, "pady": 4}
        outer = ttk.Frame(self.root, padding=8)
        outer.grid(row=0, column=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1, uniform="cols")
        outer.columnconfigure(1, weight=1, uniform="cols")
        outer.rowconfigure(3, weight=1)

        # ---- 模板设置 ----
        lf_t = ttk.LabelFrame(outer, text=" 模板设置（瘦身壳 APK） ")
        lf_t.grid(row=0, column=0, sticky="nsew", **pad)
        row = ttk.Frame(lf_t)
        row.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Entry(row, textvariable=self.var_template).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="浏览…", width=8, command=self._browse_template).pack(side="left", padx=4)
        self.btn_reunpack = ttk.Button(row, text="重新解包", width=10,
                                       command=lambda: self._start_unpack(True))
        self.btn_reunpack.pack(side="left")
        self.txt_template_info = tk.Text(lf_t, height=11, wrap="none", state="disabled",
                                         font=("Consolas", 9), background="#f6f6f6")
        self.txt_template_info.pack(fill="x", padx=6, pady=(2, 6))

        # ---- 游戏设置 ----
        lf_g = ttk.LabelFrame(outer, text=" 游戏设置（KRKR 游戏目录） ")
        lf_g.grid(row=0, column=1, sticky="nsew", **pad)
        row = ttk.Frame(lf_g)
        row.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Entry(row, textvariable=self.var_gamedir).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="浏览…", width=8, command=self._browse_game).pack(side="left", padx=4)
        self.btn_scan = ttk.Button(row, text="重新扫描", width=10, command=self._scan_game)
        self.btn_scan.pack(side="left")
        self.tree_game = ttk.Treeview(lf_g, columns=("file", "size", "detect"),
                                      show="headings", height=6)
        for cid, text, width, anchor in (("file", "xp3 文件（双击设为启动文件）", 230, "w"),
                                         ("size", "大小", 80, "e"),
                                         ("detect", "引擎检测", 130, "w")):
            self.tree_game.heading(cid, text=text)
            self.tree_game.column(cid, width=width, anchor=anchor)
        self.tree_game.pack(fill="x", padx=6, pady=2)
        self.tree_game.bind("<Double-1>", self._on_tree_dbl)
        row = ttk.Frame(lf_g)
        row.pack(fill="x", padx=6, pady=2)
        ttk.Label(row, text="启动文件:").pack(side="left")
        self.cmb_startup = ttk.Combobox(row, textvariable=self.var_startup,
                                        values=[""], width=26, state="readonly")
        self.cmb_startup.pack(side="left", padx=4)
        self.lbl_game_warn = ttk.Label(row, text="", foreground="#c00000", wraplength=200)
        self.lbl_game_warn.pack(side="left", fill="x", expand=True)

        # ---- 应用信息 ----
        lf_a = ttk.LabelFrame(outer, text=" 应用信息 ")
        lf_a.grid(row=1, column=0, sticky="nsew", **pad)
        row = ttk.Frame(lf_a)
        row.pack(fill="x", padx=6, pady=(6, 0))
        ttk.Label(row, text="包名:").pack(side="left")
        self.ent_pkg = ttk.Entry(row, textvariable=self.var_pkg)
        self.ent_pkg.pack(side="left", padx=4, fill="x", expand=True)
        self.var_pkg.trace_add("write", self._on_pkg_changed)
        self.lbl_pkg_preview = tk.Label(lf_a, text="", anchor="w", fg="#0a7d32",
                                        font=("Consolas", 8))
        self.lbl_pkg_preview.pack(fill="x", padx=8)
        row = ttk.Frame(lf_a)
        row.pack(fill="x", padx=6, pady=2)
        ttk.Label(row, text="应用名:").pack(side="left")
        ttk.Entry(row, textvariable=self.var_app).pack(side="left", padx=4, fill="x", expand=True)
        row = ttk.Frame(lf_a)
        row.pack(fill="x", padx=6, pady=(2, 6))
        ttk.Label(row, text="图标:").pack(side="left")
        ttk.Entry(row, textvariable=self.var_icon, width=26).pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(row, text="浏览…", width=8, command=self._pick_icon).pack(side="left")
        self.lbl_icon_preview = tk.Label(row, text="无图标", width=14, relief="groove")
        self.lbl_icon_preview.pack(side="right", padx=4)
        self.var_icon.trace_add("write", lambda *a: self._refresh_icon_preview())
        ttk.Label(lf_a, text="图标支持 PNG/JPG/ICO，自动生成 48/72/96/144/192 五档；\n"
                             "替换位置由 Manifest android:icon 实际引用决定，自适应图标自动回退 PNG。",
                  foreground="#666", wraplength=430, justify="left").pack(fill="x", padx=8, pady=(0, 6))

        # ---- 高级设置 ----
        lf_v = ttk.LabelFrame(outer, text=" 高级设置 ")
        lf_v.grid(row=1, column=1, sticky="nsew", **pad)
        ttk.Checkbutton(lf_v, text="显示高级设置", variable=self.var_adv_on,
                        command=self._toggle_adv).pack(anchor="w", padx=6, pady=2)
        self.adv = ttk.Frame(lf_v)
        self._build_advanced(self.adv)

        # ---- 输出 ----
        lf_o = ttk.LabelFrame(outer, text=" 输出 ")
        lf_o.grid(row=2, column=0, columnspan=2, sticky="ew", **pad)
        row = ttk.Frame(lf_o)
        row.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Label(row, text="输出目录:").pack(side="left")
        ttk.Entry(row, textvariable=self.var_outdir).pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(row, text="浏览…", width=8, command=self._browse_outdir).pack(side="left", padx=4)
        ttk.Label(row, text="文件名:").pack(side="left")
        ttk.Entry(row, textvariable=self.var_outname, width=24).pack(side="left", padx=4)
        row = ttk.Frame(lf_o)
        row.pack(fill="x", padx=6, pady=(2, 6))
        self.btn_build = tk.Button(row, text="开 始 打 包", command=self._start_build,
                                   font=("Microsoft YaHei UI", 12, "bold"),
                                   bg="#1f6feb", fg="white", activebackground="#1858c4",
                                   activeforeground="white", relief="flat",
                                   padx=24, pady=6, cursor="hand2")
        self.btn_build.pack(side="left")
        self.btn_cancel = ttk.Button(row, text="取消", command=self._cancel_build, state="disabled")
        self.btn_cancel.pack(side="left", padx=6)
        self.pb = ttk.Progressbar(row, mode="determinate", maximum=STEP_COUNT, value=0)
        self.pb.pack(side="left", fill="x", expand=True, padx=10)
        ttk.Label(row, textvariable=self.var_step, width=34, anchor="w").pack(side="left")

        # ---- 日志 ----
        lf_l = ttk.LabelFrame(outer, text=" 日志 ")
        lf_l.grid(row=3, column=0, columnspan=2, sticky="nsew", **pad)
        self.txt_log = tk.Text(lf_l, height=12, state="disabled", wrap="none",
                               font=("Consolas", 9), background="#101418",
                               foreground="#d8dee9", insertbackground="white")
        vs = ttk.Scrollbar(lf_l, orient="vertical", command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=vs.set)
        self.txt_log.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        vs.pack(side="right", fill="y", padx=(0, 6), pady=6)

    def _build_advanced(self, adv: ttk.Frame):
        adv.columnconfigure(1, weight=1)
        r = 0
        ttk.Label(adv, text="SDK 版本:").grid(row=r, column=0, sticky="w", pady=2)
        fr = ttk.Frame(adv)
        fr.grid(row=r, column=1, sticky="ew")
        ttk.Checkbutton(fr, text="跟随模板（推荐）", variable=self.var_follow_sdk,
                        command=self._toggle_sdk_follow).pack(side="left")
        self.cmb_minsdk = ttk.Combobox(fr, textvariable=self.var_minsdk,
                                       values=SDK_CHOICES, width=5, state="disabled")
        self.cmb_minsdk.pack(side="left", padx=2)
        ttk.Label(fr, text="min").pack(side="left")
        self.cmb_targetsdk = ttk.Combobox(fr, textvariable=self.var_targetsdk,
                                          values=SDK_CHOICES, width=5, state="disabled")
        self.cmb_targetsdk.pack(side="left", padx=2)
        ttk.Label(fr, text="target").pack(side="left")
        r += 1
        ttk.Label(adv, text=SDK_WARNING, foreground="#8a6d00", wraplength=440,
                  justify="left").grid(row=r, column=0, columnspan=2, sticky="ew", pady=2)
        r += 1
        ttk.Label(adv, text="版本号:").grid(row=r, column=0, sticky="w", pady=2)
        fr = ttk.Frame(adv)
        fr.grid(row=r, column=1, sticky="ew")
        ttk.Label(fr, text="code").pack(side="left")
        ttk.Entry(fr, textvariable=self.var_vercode, width=6).pack(side="left", padx=2)
        ttk.Label(fr, text="name（留空跟随模板）").pack(side="left", padx=(6, 2))
        ttk.Entry(fr, textvariable=self.var_vername, width=12).pack(side="left")
        r += 1
        ttk.Label(adv, text="签名:").grid(row=r, column=0, sticky="nw", pady=2)
        fr = ttk.Frame(adv)
        fr.grid(row=r, column=1, sticky="ew")
        ttk.Radiobutton(fr, text="自动生成 keystore", value="auto",
                        variable=self.var_signmode).pack(side="left")
        ttk.Radiobutton(fr, text="导入已有", value="import",
                        variable=self.var_signmode).pack(side="left", padx=6)
        r += 1
        fr = ttk.Frame(adv)
        fr.grid(row=r, column=0, columnspan=2, sticky="ew", pady=2)
        ttk.Label(fr, text="keystore:").pack(side="left")
        ttk.Entry(fr, textvariable=self.var_ks_path).pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(fr, text="浏览…", width=7, command=self._browse_keystore).pack(side="left")
        ttk.Label(fr, text="密码:").pack(side="left", padx=(8, 0))
        self.ent_ks_pass = ttk.Entry(fr, textvariable=self.var_ks_pass,
                                     width=12, show="•")
        self.ent_ks_pass.pack(side="left", padx=2)
        ttk.Checkbutton(fr, text="显示", variable=self.var_ks_show,
                        command=self._toggle_ks_show).pack(side="left")
        ttk.Label(fr, text="别名:").pack(side="left", padx=(8, 0))
        ttk.Entry(fr, textvariable=self.var_ks_alias, width=8).pack(side="left", padx=2)
        r += 1
        ttk.Label(adv, text="build-tools:").grid(row=r, column=0, sticky="w", pady=2)
        fr = ttk.Frame(adv)
        fr.grid(row=r, column=1, sticky="ew")
        ttk.Entry(fr, textvariable=self.var_buildtools).pack(side="left", padx=(0, 4), fill="x", expand=True)
        ttk.Button(fr, text="浏览…", width=7, command=self._browse_buildtools).pack(side="left")
        r += 1
        ttk.Label(adv, text="（targetSdk≥30 时需要其中的 zipalign/apksigner）",
                  foreground="#666").grid(row=r, column=0, columnspan=2, sticky="w")
        r += 1
        ttk.Label(adv, text="apktool.jar:").grid(row=r, column=0, sticky="w", pady=2)
        fr = ttk.Frame(adv)
        fr.grid(row=r, column=1, sticky="ew")
        ttk.Entry(fr, textvariable=self.var_apktool).pack(side="left", padx=(0, 4), fill="x", expand=True)
        ttk.Button(fr, text="浏览…", width=7, command=self._browse_apktool).pack(side="left")
        ttk.Button(fr, text="自动下载", width=9, command=self._download_apktool).pack(side="left", padx=4)
        r += 1
        ttk.Label(adv, text="安装标记:").grid(row=r, column=0, sticky="w", pady=2)
        fr = ttk.Frame(adv)
        fr.grid(row=r, column=1, sticky="ew")
        ttk.Checkbutton(fr, text="改名（覆盖安装强制重解压）", variable=self.var_marker).pack(side="left")
        self.cmb_marker = ttk.Combobox(fr, textvariable=self.var_template_marker,
                                       values=[""], width=18, state="readonly")
        self.cmb_marker.pack(side="left", padx=4)
        ttk.Entry(fr, textvariable=self.var_marker_val, width=16).pack(side="left", padx=2)
        ttk.Button(fr, text="换一个", width=7,
                   command=lambda: self.var_marker_val.set("krkr_ready_" + os.urandom(4).hex())).pack(side="left")
        r += 1
        ttk.Label(adv, text="权限保留:").grid(row=r, column=0, sticky="nw", pady=2)
        fr = ttk.Frame(adv)
        fr.grid(row=r, column=1, sticky="ew")
        btns = ttk.Frame(fr)
        btns.pack(anchor="e")
        ttk.Button(btns, text="全选", width=5, command=lambda: self._perm_set_all(True)).pack(side="left", padx=2)
        ttk.Button(btns, text="全不选", width=7, command=lambda: self._perm_set_all(False)).pack(side="left", padx=2)
        self.perm_inner = self._scroll_frame(fr, height=72)
        self.perm_inner.pack(fill="both", expand=True, pady=(2, 0))
        r += 1
        ttk.Label(adv, text="默认全保留。删除 READ_PHONE_STATE / WRITE_EXTERNAL_STORAGE 可能导致"
                            "广告 SDK getDeviceId 崩溃 / 引擎写入失败。",
                  foreground="#8a6d00", wraplength=440, justify="left").grid(row=r, column=0, columnspan=2, sticky="ew")
        r += 1
        fr = ttk.Frame(adv)
        fr.grid(row=r, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Button(fr, text="保存配置(JSON)", command=self._save_cfg_file).pack(side="left")
        ttk.Button(fr, text="载入配置(JSON)", command=self._load_cfg_file).pack(side="left", padx=6)

    def _scroll_frame(self, parent, height=72):
        canvas = tk.Canvas(parent, height=height, highlightthickness=0)
        vs = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vs.set)
        canvas.pack(side="left", fill="both", expand=True)
        vs.pack(side="right", fill="y")
        return inner

    # ================= 配置 =================
    def _load_cfg_to_ui(self):
        c = self.cfg
        self.var_template.set(c.get("template_apk", ""))
        self.var_gamedir.set(c.get("last_game_dir", ""))
        self.var_pkg.set(c.get("last_package", ""))
        self.var_app.set(c.get("last_app_name", ""))
        self.var_icon.set(c.get("last_icon", ""))
        self.var_outdir.set(c.get("out_dir") or str(Path.home() / "Desktop"))
        self.var_outname.set(c.get("out_name_tpl") or config.DEFAULT_OUTPUT_NAME)
        self.var_follow_sdk.set(c.get("follow_sdk", True))
        self.var_minsdk.set(c.get("min_sdk") or "19")
        self.var_targetsdk.set(c.get("target_sdk") or "26")
        self.var_vercode.set(c.get("version_code", ""))
        self.var_vername.set(c.get("version_name", ""))
        self.var_signmode.set(c.get("sign_mode") or "auto")
        self.var_ks_path.set(c.get("keystore_path", ""))
        self.var_ks_pass.set(c.get("keystore_pass") or "krkr123456")
        self.var_ks_alias.set(c.get("keystore_alias") or "game")
        self.var_buildtools.set(c.get("build_tools_dir", ""))
        self.var_apktool.set(c.get("apktool_path", ""))
        self.var_marker.set(c.get("marker_replace", True))
        self.var_template_marker.set(c.get("template_marker", ""))
        self.var_marker_val.set(c.get("marker_value") or ("krkr_ready_" + os.urandom(4).hex()))
        self._toggle_sdk_follow()
        self._on_pkg_changed()
        if self.var_gamedir.get() and Path(self.var_gamedir.get()).is_dir():
            self._scan_game()

    def _persist_cfg(self):
        c = self.cfg
        c.update({
            "template_apk": self.var_template.get().strip(),
            "last_game_dir": self.var_gamedir.get().strip(),
            "last_package": self.var_pkg.get().strip(),
            "last_app_name": self.var_app.get().strip(),
            "last_icon": self.var_icon.get().strip(),
            "out_dir": self.var_outdir.get().strip(),
            "out_name_tpl": self.var_outname.get().strip(),
            "follow_sdk": bool(self.var_follow_sdk.get()),
            "min_sdk": self.var_minsdk.get(),
            "target_sdk": self.var_targetsdk.get(),
            "version_code": self.var_vercode.get().strip(),
            "version_name": self.var_vername.get().strip(),
            "sign_mode": self.var_signmode.get(),
            "keystore_path": self.var_ks_path.get().strip(),
            "keystore_pass": self.var_ks_pass.get(),
            "keystore_alias": self.var_ks_alias.get().strip(),
            "build_tools_dir": self.var_buildtools.get().strip(),
            "apktool_path": self.var_apktool.get().strip(),
            "marker_replace": bool(self.var_marker.get()),
            "marker_value": self.var_marker_val.get().strip(),
            "template_marker": self.var_template_marker.get().strip(),
            "perm_keep": [n for n, v in self._perm_vars.items() if v.get()],
        })
        config.save_config(c)

    def _state_dict(self):
        return {
            "template": self.var_template.get().strip(),
            "game_dir": self.var_gamedir.get().strip(),
            "package": self.var_pkg.get().strip(),
            "app_name": self.var_app.get().strip(),
            "icon": self.var_icon.get().strip(),
            "out_dir": self.var_outdir.get().strip(),
            "out_name": self.var_outname.get().strip(),
            "follow_sdk": bool(self.var_follow_sdk.get()),
            "min_sdk": self.var_minsdk.get(),
            "target_sdk": self.var_targetsdk.get(),
            "version_code": self.var_vercode.get().strip(),
            "version_name": self.var_vername.get().strip(),
            "sign_mode": self.var_signmode.get(),
            "keystore": self.var_ks_path.get().strip(),
            "ks_pass": self.var_ks_pass.get(),
            "ks_alias": self.var_ks_alias.get().strip(),
            "build_tools": self.var_buildtools.get().strip(),
            "apktool": self.var_apktool.get().strip(),
            "marker_replace": bool(self.var_marker.get()),
            "marker_value": self.var_marker_val.get().strip(),
            "perm_keep": [n for n, v in self._perm_vars.items() if v.get()],
        }

    def _apply_state(self, d: dict):
        self.var_template.set(d.get("template", self.var_template.get()))
        self.var_gamedir.set(d.get("game_dir", self.var_gamedir.get()))
        self.var_pkg.set(d.get("package", ""))
        self.var_app.set(d.get("app_name", ""))
        self.var_icon.set(d.get("icon", ""))
        self.var_outdir.set(d.get("out_dir", self.var_outdir.get()))
        self.var_outname.set(d.get("out_name") or config.DEFAULT_OUTPUT_NAME)
        self.var_follow_sdk.set(d.get("follow_sdk", True))
        self.var_minsdk.set(d.get("min_sdk") or "19")
        self.var_targetsdk.set(d.get("target_sdk") or "26")
        self.var_vercode.set(d.get("version_code", ""))
        self.var_vername.set(d.get("version_name", ""))
        self.var_signmode.set(d.get("sign_mode") or "auto")
        self.var_ks_path.set(d.get("keystore", ""))
        self.var_ks_pass.set(d.get("ks_pass", ""))
        self.var_ks_alias.set(d.get("ks_alias") or "game")
        self.var_buildtools.set(d.get("build_tools", ""))
        self.var_apktool.set(d.get("apktool", ""))
        self.var_marker.set(d.get("marker_replace", True))
        self.var_marker_val.set(d.get("marker_value", self.var_marker_val.get()))
        keep = set(d.get("perm_keep") or [])
        if keep and self._perm_vars:
            for n, v in self._perm_vars.items():
                v.set(n in keep)
        self._toggle_sdk_follow()
        self._on_pkg_changed()
        self._refresh_icon_preview()

    def _save_cfg_file(self):
        f = filedialog.asksaveasfilename(defaultextension=".json",
                                         initialfile="krkr2packer配置.json",
                                         filetypes=[("JSON", "*.json")])
        if not f:
            return
        Path(f).write_text(json.dumps(self._state_dict(), ensure_ascii=False, indent=2),
                           encoding="utf-8")
        utils.log(f"配置已保存: {f}")

    def _load_cfg_file(self):
        f = filedialog.askopenfilename(filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if not f:
            return
        try:
            d = json.loads(Path(f).read_text(encoding="utf-8"))
            self._apply_state(d)
            utils.log(f"配置已载入: {f}")
        except Exception as e:
            messagebox.showerror("载入失败", str(e))

    # ================= 模板 =================
    def _browse_template(self):
        f = filedialog.askopenfilename(title="选择瘦身壳模板 APK",
                                       filetypes=[("APK", "*.apk"), ("所有文件", "*.*")])
        if f:
            self.var_template.set(f)
            self._start_unpack(False)

    def _start_unpack(self, force: bool):
        if self._template_busy():
            return
        apk = self.var_template.get().strip()
        if not apk or not Path(apk).is_file():
            messagebox.showerror("错误", "请先选择模板 APK 文件")
            return
        self.btn_reunpack.configure(state="disabled")
        self.var_step.set("模板解包中…")
        apktool_path = self.var_apktool.get().strip()

        def worker():
            try:
                cfg = dict(self.cfg)
                cfg["apktool_path"] = apktool_path
                info = tpl_mod.unpack(apk, cfg=cfg, force=force)
                self.q.put(("template_done", info))
            except Exception as e:
                self.q.put(("template_err", f"{e}\n{traceback.format_exc(limit=3)}"))

        threading.Thread(target=worker, daemon=True).start()

    def _template_busy(self):
        return str(self.btn_reunpack["state"]) == "disabled"

    def _on_template_done(self, info):
        self.info = info
        self.kernel_ok = False
        self.btn_reunpack.configure(state="normal")
        self.var_step.set("就绪")
        self.txt_template_info.configure(state="normal")
        self.txt_template_info.delete("1.0", "end")
        self.txt_template_info.insert("1.0", tpl_mod.info_text(info))
        self.txt_template_info.configure(state="disabled")
        self.var_template_marker.set(info.markers[0] if info.markers else "")
        self.cmb_marker.configure(values=info.markers or [""])
        # 权限勾选列表
        for w in self.perm_inner.winfo_children():
            w.destroy()
        self._perm_vars = {}
        if info.permissions:
            keep = set(self.cfg.get("perm_keep") or [])
            for name in info.permissions:
                v = tk.BooleanVar(value=(name in keep) if keep else True)
                self._perm_vars[name] = v
                ttk.Checkbutton(self.perm_inner, text=name, variable=v).pack(anchor="w")
        else:
            ttk.Label(self.perm_inner, text="（模板未声明 uses-permission）").pack(anchor="w")
        self.cfg["template_apk"] = self.var_template.get().strip()
        config.save_config(self.cfg)
        if info.hard_problems:
            messagebox.showerror("无效模板",
                                 "模板结构校验失败，缺失关键内容:\n- "
                                 + "\n- ".join(info.hard_problems)
                                 + "\n\n请确认这是按《使用说明》瘦身的 Kirikiroid2 壳 APK。")
            return
        self._kernel_check(info)

    def _kernel_check(self, info):
        status, detail = tpl_mod.check_kernel(info, self.cfg)
        hashes_txt = "\n".join(f"  [{abi}]  SHA256: {h}"
                               for abi, h in sorted(info.kernel_hashes.items()))
        if status == "ok":
            self.kernel_ok = True
            utils.log("内核自检通过（libgame.so SHA256 与已接受指纹一致）。")
            return
        if status == "mismatch":
            ok = messagebox.askyesno(
                "内核指纹变化",
                f"警告：模板内核 libgame.so 的 SHA256 与已接受值不一致，模板可能被篡改！\n\n"
                f"{detail}\n\n{hashes_txt}\n\n"
                "内核应为 Kirikiroid2 1.3.9 官方最终版。\n确定接受当前内核并继续吗？")
        else:
            ok = messagebox.askyesno(
                "确认内核",
                "首次使用该模板，请确认内核指纹（接受后存入配置，之后自动校验）：\n\n"
                f"{hashes_txt}\n\n内核版本：Kirikiroid2 1.3.9（官方最终版，无需也不得更换）\n\n"
                "是否接受此内核？")
        if ok:
            self.cfg["kernel_accepted"] = dict(info.kernel_hashes)
            config.save_config(self.cfg)
            self.kernel_ok = True
            utils.log("已接受当前内核指纹并写入配置。")
        else:
            utils.log("!! 未接受内核：模板不可用，请更换可信模板后重新加载。")

    # ================= 游戏 =================
    def _browse_game(self):
        d = filedialog.askdirectory(title="选择 KRKR 游戏目录（含 .xp3）")
        if d:
            self.var_gamedir.set(d)
            self._scan_game()

    def _scan_game(self):
        d = self.var_gamedir.get().strip()
        if not d or not Path(d).is_dir():
            self.lbl_game_warn.configure(text="目录无效")
            return
        self.btn_scan.configure(state="disabled")

        def worker():
            try:
                files, others, total = game_mod.scan_game_dir(d)
                self.q.put(("scan_done", files, others, total))
            except Exception as e:
                self.q.put(("scan_err", f"{e}\n{traceback.format_exc(limit=3)}"))

        threading.Thread(target=worker, daemon=True).start()

    def _on_scan_done(self, files, others, total):
        self.btn_scan.configure(state="normal")
        self.xp3_files = files
        self.tree_game.delete(*self.tree_game.get_children())
        for f in files:
            self.tree_game.insert("", "end", values=(f.name, utils.human_size(f.size),
                                                     f.detect_text))
        names = [f.name for f in files]
        self.cmb_startup.configure(values=names or [""])
        sugg = game_mod.suggest_startup(files)
        if sugg:
            self.var_startup.set(sugg.name)
        if not files:
            self.lbl_game_warn.configure(
                text=f"⚠ 未发现 .xp3（其他文件 {others} 个）——可能不是 KRKR 游戏")
        elif not game_mod.is_krkr_game(files):
            self.lbl_game_warn.configure(
                text=f"⚠ {len(files)} 个 xp3 均无魔数且 <1MB，可能不是 KRKR 游戏，"
                     "请自行确认启动文件", foreground="#8a6d00")
        else:
            self.lbl_game_warn.configure(
                text=f"✓ {len(files)} 个 xp3 / {utils.human_size(total)}，其他文件 {others} 个",
                foreground="#0a7d32")

    def _on_tree_dbl(self, _event):
        sel = self.tree_game.selection()
        if sel:
            vals = self.tree_game.item(sel[0], "values")
            if vals:
                self.var_startup.set(vals[0])

    # ================= 应用信息 =================
    def _on_pkg_changed(self, *_a):
        raw = self.var_pkg.get()
        pkg = utils.normalize_package_input(raw)
        if pkg != raw:
            # 兼容中文输入法：全角句号“。”自动转“.”、大写转小写、去空格。
            # 写回会再次触发本 trace，此时值已一致，不会死循环。
            self.var_pkg.set(pkg)
            return
        ok = bool(re.fullmatch(config.PACKAGE_RE, pkg))
        self.lbl_pkg_preview.configure(
            text=("✓ " if ok else "✗ 格式示例 com.example.game —— ") + PKG_PREVIEW.format(pkg=pkg or "<包名>"),
            fg=("#0a7d32" if ok else "#c00000"))

    def _toggle_ks_show(self):
        self.ent_ks_pass.configure(show="" if self.var_ks_show.get() else "•")

    # ---- 输入框增强：右键菜单 / 小键盘点号 / IME 重绘 / 禁用 IME ----
    @staticmethod
    def _numpad_dot_handler(event):
        """Windows Tk 的老 bug：小键盘点号（VK_DECIMAL，真实硬件 keycode=110）
        会被当作 Delete 处理（表现为删掉光标后一个字符）。拦截后手动插入 '.'
        并吞掉事件。多种事件形态都兜住。"""
        if event.keycode != 110 and event.keysym not in ("KP_Separator", "KP_Decimal") \
                and not (event.keysym == "Delete" and event.char == "."):
            return None
        if event.state & 0x0004 or event.state & 0x20000:   # Ctrl / Alt 组合不拦
            return None
        w = event.widget
        try:
            if w.selection_present():
                w.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        try:
            w.insert(w.index("insert"), ".")
            w.update_idletasks()
        except tk.TclError:
            pass
        return "break"

    @staticmethod
    def _repaint_after_ime(event):
        """TSF 输入法（微软拼音等）提交的文本可能不立即重绘——表现为“输入的
        英文要点一下输入框才显示出来”。KeyRelease 后强制排一次重绘。"""
        try:
            event.widget.after_idle(event.widget.update_idletasks)
        except Exception:
            pass

    @staticmethod
    def _disable_ime(widget):
        """对只需 ASCII 的输入框禁用输入法：绕过 IME 后按键必以原生 VK 到达
        （小键盘点号 keycode=110 拦截必生效），也不会再出现组合词浮窗。"""
        if os.name != "nt":
            return
        try:
            import ctypes
            ctypes.windll.imm32.ImmAssociateContext(widget.winfo_id(), None)
        except Exception:
            pass

    def _bind_entry_enhancements(self, widget=None):
        for child in (widget or self.root).winfo_children():
            if isinstance(child, (ttk.Entry, tk.Entry)):
                child.bind("<Key>", self._numpad_dot_handler, add="+")
                child.bind("<KeyRelease>", self._repaint_after_ime, add="+")
                child.bind("<Button-3>", self._show_ctx_menu, add="+")
            elif isinstance(child, tk.Text):
                child.bind("<Button-3>", self._show_ctx_menu, add="+")
            self._bind_entry_enhancements(child)
        # 包名 / keystore 密码永远只需要 ASCII：禁用输入法，直打即进
        for ent in (getattr(self, "ent_pkg", None), getattr(self, "ent_ks_pass", None)):
            if ent is not None:
                self._disable_ime(ent)

    def _make_ctx_menu(self):
        self._ctx_widget = None
        m = tk.Menu(self.root, tearoff=0)
        self._ctx_menu = m
        for label, virt in (("剪切", "<<Cut>>"), ("复制", "<<Copy>>"), ("粘贴", "<<Paste>>")):
            m.add_command(label=label, command=lambda a=virt: self._ctx_do(a))
        m.add_separator()
        m.add_command(label="删除", command=self._ctx_delete)
        m.add_command(label="全选", command=self._ctx_select_all)

    def _ctx_do(self, virt):
        w = getattr(self, "_ctx_widget", None)
        if w is None:
            return
        try:
            w.event_generate(virt)
        except tk.TclError:
            pass

    def _ctx_delete(self):
        w = getattr(self, "_ctx_widget", None)
        try:
            w.delete("sel.first", "sel.last")
        except tk.TclError:
            pass

    def _ctx_select_all(self):
        w = getattr(self, "_ctx_widget", None)
        try:
            if isinstance(w, tk.Text):
                w.tag_add("sel", "1.0", "end")
            else:
                w.selection_range(0, "end")
        except tk.TclError:
            pass

    def _show_ctx_menu(self, event):
        self._ctx_widget = event.widget
        try:
            self._ctx_menu.tk_popup(event.x_root, event.y_root)
        finally:
            try:
                self._ctx_menu.grab_release()
            except Exception:
                pass
        return "break"

    def _pick_icon(self):
        f = filedialog.askopenfilename(title="选择图标图片",
                                       filetypes=[("图片", "*.png *.jpg *.jpeg *.ico *.bmp"),
                                                  ("所有文件", "*.*")])
        if f:
            self.var_icon.set(f)
            self._refresh_icon_preview()

    def _refresh_icon_preview(self):
        if icon_gen is None or ImageTk is None:
            self.lbl_icon_preview.configure(text="Pillow\n未安装")
            return
        p = self.var_icon.get().strip()
        if not p or not Path(p).is_file():
            self.lbl_icon_preview.configure(image="", text="无图标")
            self._icon_photo = None
            return
        try:
            img = icon_gen.rounded(icon_gen.load_square(p), 96)
            self._icon_photo = ImageTk.PhotoImage(img)
            self.lbl_icon_preview.configure(image=self._icon_photo, text="", width=96, height=96)
        except Exception as e:
            self.lbl_icon_preview.configure(image="", text=f"无法读取\n{e}")
            self._icon_photo = None

    # ================= 高级 =================
    def _toggle_adv(self):
        if self.var_adv_on.get():
            self.adv.pack(fill="both", expand=True, padx=6, pady=(0, 6))
        else:
            self.adv.pack_forget()

    def _toggle_sdk_follow(self):
        state = "disabled" if self.var_follow_sdk.get() else "readonly"
        self.cmb_minsdk.configure(state=state)
        self.cmb_targetsdk.configure(state=state)

    def _perm_set_all(self, on: bool):
        for v in self._perm_vars.values():
            v.set(on)

    def _browse_outdir(self):
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.var_outdir.set(d)

    def _browse_keystore(self):
        f = filedialog.askopenfilename(filetypes=[("keystore", "*.jks *.keystore"), ("所有文件", "*.*")])
        if f:
            self.var_ks_path.set(f)

    def _browse_buildtools(self):
        d = filedialog.askdirectory(title="选择 Android build-tools 目录（含 zipalign/apksigner）")
        if d:
            self.var_buildtools.set(d)

    def _browse_apktool(self):
        f = filedialog.askopenfilename(filetypes=[("jar", "*.jar"), ("所有文件", "*.*")])
        if f:
            self.var_apktool.set(f)

    def _download_apktool(self):
        def worker():
            try:
                utils.download_apktool()
                self.q.put(("log", "apktool 就绪。"))
            except Exception as e:
                self.q.put(("log", f"!! {e}"))
        threading.Thread(target=worker, daemon=True).start()

    # ================= 打包 =================
    def _collect_params(self) -> builder_mod.BuildParams:
        return builder_mod.BuildParams(
            template_dir=Path(self.info.unpack_dir),
            game_dir=Path(self.var_gamedir.get().strip()),
            startup=self.var_startup.get().strip(),
            package=self.var_pkg.get().strip(),
            app_name=self.var_app.get().strip() or "KirikiriGame",
            out_dir=Path(self.var_outdir.get().strip() or str(Path.home() / "Desktop")),
            out_name_tpl=self.var_outname.get().strip() or config.DEFAULT_OUTPUT_NAME,
            icon_path=self.var_icon.get().strip(),
            icon_ref=self.info.icon_ref if self.info else "",
            version_code=self.var_vercode.get().strip(),
            version_name=self.var_vername.get().strip(),
            min_sdk="" if self.var_follow_sdk.get() else str(self.var_minsdk.get()),
            target_sdk="" if self.var_follow_sdk.get() else str(self.var_targetsdk.get()),
            template_target_sdk=self.info.target_sdk if self.info else "26",
            perm_keep=None if self._perm_all_on() else [n for n, v in self._perm_vars.items() if v.get()],
            marker_replace=bool(self.var_marker.get()),
            marker_new=self.var_marker_val.get().strip(),
            template_marker=self.var_template_marker.get().strip(),
            apktool_path=self.var_apktool.get().strip(),
            build_tools_dir=self.var_buildtools.get().strip(),
            keystore_path=self.var_ks_path.get().strip() or str(config.DEFAULT_KEYSTORE),
            store_pass=self.var_ks_pass.get() or "krkr123456",
            alias=self.var_ks_alias.get().strip() or "game",
        )

    def _perm_all_on(self):
        return all(v.get() for v in self._perm_vars.values())

    def _start_build(self):
        if self.builder and self.builder.is_alive():
            return
        if not self.info:
            messagebox.showerror("错误", "请先在「模板设置」加载模板 APK")
            return
        if not self.kernel_ok:
            messagebox.showerror("错误", "内核未确认（SHA256 自检未通过或未接受），无法打包。")
            return
        p = self._collect_params()
        if not re.fullmatch(config.PACKAGE_RE, p.package):
            messagebox.showerror("包名不合法",
                                 "包名格式: com.example.game\n"
                                 "（小写字母开头，至少两段，仅小写字母/数字/下划线）")
            return
        if not p.game_dir.is_dir():
            messagebox.showerror("错误", "请选择游戏目录")
            return
        if not p.startup or not (p.game_dir / p.startup).is_file():
            messagebox.showerror("错误", "请选择启动 xp3 文件")
            return
        if not p.out_name_tpl.lower().endswith(".apk"):
            messagebox.showerror("错误", "输出文件名模板必须以 .apk 结尾")
            return
        # targetSdk>=30 提前检查 v2 签名工具（验收标准 4）
        try:
            tsdk = int(p.target_sdk or p.template_target_sdk or 26)
        except ValueError:
            tsdk = 26
        if tsdk >= 30:
            z, a = signer_mod.find_build_tools(p.build_tools_dir)
            if not (z and a):
                messagebox.showerror("需要 v2 签名工具", signer_mod.V2_HELP)
                return
        if p.marker_replace and not p.template_marker:
            utils.log("提示：模板中未发现安装标记，本次跳过标记改名。")
        self._persist_cfg()
        self.cancel_event = threading.Event()
        self.builder = builder_mod.Builder(p, cancel_event=self.cancel_event)
        self.builder.on_step = lambda i, name: self.q.put(("step", i, name))
        self.builder.on_done = lambda res: self.q.put(("done", res))
        self._set_build_busy(True)
        self.builder.start()

    def _cancel_build(self):
        self.cancel_event.set()
        self.var_step.set("取消中…")

    def _set_build_busy(self, busy: bool):
        self.btn_build.configure(state="disabled" if busy else "normal")
        self.btn_cancel.configure(state="normal" if busy else "disabled")
        if busy:
            self.pb.configure(value=0)

    def _on_build_done(self, res):
        self._set_build_busy(False)
        self.var_step.set("完成")
        if res.cancelled:
            messagebox.showwarning("已取消", "打包已取消。")
            return
        if not res.ok:
            messagebox.showerror("打包失败", res.error or "未知错误")
            return
        msg = (f"打包完成！\n\n输出: {res.apk_path}\n体积: {utils.human_size(res.size)}\n"
               f"SHA256: {res.sha256}")
        if res.warnings:
            msg += "\n\n警告:\n- " + "\n- ".join(res.warnings)
        if res.verify:
            msg += "\n\n验收:\n- " + "\n- ".join(res.verify)
        if messagebox.askyesno("打包完成", msg + "\n\n是否打开输出目录？"):
            try:
                os.startfile(str(Path(res.apk_path).parent))   # Windows
            except Exception:
                pass

    # ================= 日志 / 轮询 =================
    def _append_log(self, s: str):
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", s + "\n")
        line_count = int(self.txt_log.index("end-1c").split(".")[0])
        if line_count > 4000:
            self.txt_log.delete("1.0", f"{line_count - 3000}.0")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                ev = self.q.get_nowait()
                kind = ev[0]
                if kind == "log":
                    self._append_log(ev[1])
                elif kind == "step":
                    self.pb.configure(value=ev[1] + 1)
                    self.var_step.set(f"第 {ev[1] + 1}/{STEP_COUNT} 步：{ev[2]}")
                elif kind == "done":
                    self._on_build_done(ev[1])
                elif kind == "template_done":
                    self._on_template_done(ev[1])
                elif kind == "template_err":
                    self.btn_reunpack.configure(state="normal")
                    self.var_step.set("就绪")
                    self._append_log(ev[1])
                    messagebox.showerror("模板解包失败", ev[1].splitlines()[0])
                elif kind == "scan_done":
                    self._on_scan_done(*ev[1:])
                elif kind == "scan_err":
                    self.btn_scan.configure(state="normal")
                    self._append_log(ev[1])
                    messagebox.showerror("扫描失败", ev[1].splitlines()[0])
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    def _on_close(self):
        try:
            self._persist_cfg()
        finally:
            self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
