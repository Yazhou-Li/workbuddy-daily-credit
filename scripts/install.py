#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
install.py — 跨平台定时安装器（替代原先 macOS-only 的 install.sh）

  python3 install.py               安装并立即试跑一次
  python3 install.py --install     同上
  python3 install.py --status      查看定时状态与最近结果
  python3 install.py --uninstall   卸载定时
  python3 install.py --doctor      环境自检

覆盖三平台：
  macOS    launchd     LaunchAgents + StartCalendarInterval（错过的时点开机补跑）
  Windows  schtasks    多个 DAILY 任务 + 一个 ONLOGON 补跑
  Linux    crontab     schedule+%s

「关机了怎么办」的三平台共同答案：
  三者都配了「登录/开机即触发」的兜底任务 —— launchd 的 RunAtLoad、
  schtasks 的 ONLOGON、cron 的 @reboot 语义（此处用多点 + 手动才算稳，
  故 Linux 额外依赖每日多点触发）。只要当天开过机，当天就能领到。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import wbplatform as plat  # noqa: E402

SKILL_DIR = os.path.dirname(SCRIPT_DIR)
CLAIM = os.path.join(SCRIPT_DIR, "claim.py")


def do_install(windowed: bool = False):
    py = plat.find_python(windowed=windowed)
    print("平台: %s" % plat.PLATFORM)
    print("解释器: %s" % py)
    ok, msg = plat.install_scheduler(SKILL_DIR, py, CLAIM)
    print(msg)
    print("\n立即试跑一次…")
    try:
        r = subprocess.run([py, CLAIM, "--wait", "60", "--quiet", "--json"],
                           capture_output=True, text=True, timeout=180)
        print((r.stdout or "").strip()[:500])
        if r.returncode != 0:
            print("[退出码 %s] %s" % (r.returncode, (r.stderr or "").strip()[:300]))
    except Exception as e:
        print("试跑失败: %s" % e)
    return 0 if ok else 1


def do_status():
    print("=== 定时任务 ===")
    print(plat.scheduler_status())
    print("\n=== 最近状态 ===")
    st = os.path.join(SKILL_DIR, "state.json")
    if os.path.exists(st):
        print(open(st, encoding="utf-8").read().strip())
    print("\n=== 最近 8 次记录 ===")
    hs = os.path.join(SKILL_DIR, "history.jsonl")
    if os.path.exists(hs):
        print("".join(open(hs, encoding="utf-8").readlines()[-8:]).strip())
    return 0


def do_doctor():
    print(json.dumps(plat.doctor(), ensure_ascii=False, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser(description="WorkBuddy 每日积分 · 跨平台定时安装器")
    ap.add_argument("--install", action="store_true", help="安装（默认动作）")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--doctor", action="store_true")
    ap.add_argument("--windowed", action="store_true",
                    help="Windows 下优先选 pythonw.exe（无控制台窗口）")
    args = ap.parse_args()

    if args.uninstall:
        ok, msg = plat.uninstall_scheduler()
        print(msg)
        return 0 if ok else 1
    if args.status:
        return do_status()
    if args.doctor:
        return do_doctor()
    return do_install(windowed=args.windowed)


if __name__ == "__main__":
    sys.exit(main())
