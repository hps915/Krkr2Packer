# -*- coding: utf-8 -*-
"""小键盘点号（VK_DECIMAL）修复的行为测试。

Windows Tk 会把小键盘 . （真实硬件 keycode=110）映射为 Delete 键。
注：event_generate 无法伪造 keycode（Tk 会用 keysym 反推，Delete→46），
所以 handler 逻辑用伪造事件对象直接测；绑定路径用 keysym=KP_Separator 走真实分发。
"""
import sys
from pathlib import Path

PROJ = Path(r"C:\hps\GAME\Deepseek\gal\Krkr2Packer")
sys.path.insert(0, str(PROJ))

import tkinter as tk
from tkinter import ttk

import main  # noqa: E402  仅导入，不创建 App

FAILS = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail else ""))
    if not cond:
        FAILS.append(name)


def fake_event(widget, keycode, keysym, state=0):
    e = tk.Event()
    e.widget = widget
    e.keycode = keycode
    e.keysym = keysym
    e.state = state
    e.char = ""
    return e


root = tk.Tk()
root.geometry("260x60+100+100")

ent = ttk.Entry(root)
ent.pack(fill="x", padx=8, pady=8)
ent.bind("<Key>", main.App._numpad_dot_handler, add="+")   # 与 main.py 相同的绑定
ent.focus_force()
root.update()
H = main.App._numpad_dot_handler

# 1) 小键盘点（真实硬件 keycode=110；Tk 已把 keysym 映射成 Delete）→ 插入 '.' 不删字符
ent.delete(0, "end"); ent.insert(0, "abcdef"); ent.icursor(1)
r = H(fake_event(ent, 110, "Delete"))
check("numpad.dot_inserted", r == "break" and ent.get() == "a.bcdef",
      f"ret={r} text={ent.get()!r}")

# 2) 有选区时先替换选区
ent.delete(0, "end"); ent.insert(0, "abcdef")
ent.selection_range(1, 3); ent.icursor(1)
r = H(fake_event(ent, 110, "Delete"))
check("numpad.dot_replaces_selection", r == "break" and ent.get() == "a.def",
      f"ret={r} text={ent.get()!r}")

# 3) Ctrl+小键盘点 不拦截
ent.delete(0, "end"); ent.insert(0, "abcdef"); ent.icursor(1)
r = H(fake_event(ent, 110, "Delete", state=4))
check("numpad.ctrl_combo_passthrough", r is None and ent.get() == "abcdef",
      f"ret={r}")

# 4) 普通键放行
e2 = fake_event(ent, 67, "c")
check("numpad.normal_key_passthrough", H(e2) is None)

# 5) 真正的 Delete 键（keycode 46）行为不变：删除光标后一个字符
# （注：本 Tk 构建 keysym "KP_Separator" 无 keycode 映射、VK_DECIMAL 被映射为
#   Delete——正是要修的 bug 本体；event_generate 也无法伪造 keycode=110，
#   故绑定分发路径由普通键/真实 Delete 用例覆盖，小键盘路径由用例 1~3 覆盖。）
ent.delete(0, "end"); ent.insert(0, "acb"); ent.icursor(1)
ent.event_generate("<KeyPress>", keycode=46, keysym="Delete")
root.update()
check("numpad.real_delete_still_works", ent.get() == "ab", repr(ent.get()))

root.destroy()
print()
print("总结:", "ALL PASS ✔" if not FAILS else f"FAILED: {FAILS}")
sys.exit(0 if not FAILS else 1)
