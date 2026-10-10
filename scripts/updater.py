#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
updater — 自进化更新器

让 skill 能够跟着仓库一起进化：当 upstream 修了协议兼容、加了新逻辑，
本机无需手动重新下载就会在一次常规签到里顺手完成升级。

安全模型（这一节是重点，别在改动时削弱）
-----------------------------------------
「自动从网上下载代码并执行」本质上是个 RCE 开关，所以这里设了七道闸：

  1. 强制 HTTPS，使用系统默认可信根证书库，不做任何 verify=False 的妥协
  2. 仓库身份 pinning：MANIFEST_URL 里的 owner/repo 写死，不接受配置覆盖。
     也就是说，只有你主动 fork 并改代码才能把更新源换掉——攻击者改不了
  3. 双重校验：manifest 里的每个文件都带 sha256，下载后逐字节比对，
     不符立刻丢弃磁盘上的临时文件，绝不落力的彩
  4. 路径白名单：只允许覆盖 UPDATABLE 前缀内的文件。即便 manifest 被人
     塞了 ../../ 也不会写出目录
  5. 先写 .tmp 再 os.replace 原子替换，中途断电不会留下半截文件
  6. 每份替换前留 .bak，并提供 --rollback 一键回退
  7. 可控关闭：环境变量 WORKBUDDY_DAILY_CREDIT_NO_UPDATE=1 或 --no-self-update

即便如此，仍然建议：**先用 git clone 看一眼代码再让它自动更新。**
信任应该是默认的加例外，不是相反。

用法
----
  python3 updater.py                 # 按需检查并更新（24h 节流）
  python3 updater.py --force         # 忽略节流
  python3 updater.py --check         # 只看有没有新版本，不落地
  python3 updater.py --rollback      # 回退到 .bak
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
# 布局约定：**skill 目录本身就是仓库根目录**。
# 于是 clone 到 ~/.workbuddy/skills/workbuddy-daily-credit 后，
# 这棵树的形状与 GitHub 仓库完全同构 —— manifest 里的相对路径两边都能直接对上，
# 不需要任何「安装位置」的猜测逻辑。
ROOT = SKILL_DIR

OWNER = "Yazhou-Li"
REPO = "workbuddy-daily-credit"
BRANCH = "main"
MANIFEST_URL = ("https://raw.githubusercontent.com/%s/%s/%s/manifest.json"
                % (OWNER, REPO, BRANCH))
RAW_PREFIX = ("https://raw.githubusercontent.com/%s/%s/%s/" % (OWNER, REPO, BRANCH))

# 白名单：仓库根目录下的相对路径前缀
UPDATABLE = ("scripts/", "references/", "SKILL.md", "VERSION")
CHECK_INTERVAL_SEC = 24 * 3600
STATE_FILE = os.path.join(SKILL_DIR, "state.json")
VERSION_FILE = os.path.join(ROOT, "VERSION")


def _local_version():
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "0.0.0"


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_json(path, obj):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _get(url, timeout=20) -> bytes:
    """HTTPS GET，证书校验交给默认 context（不做任何弱化）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "%s-updater" % REPO})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        if r.status != 200:
            raise RuntimeError("HTTP %s" % r.status)
        return r.read()


def _throttled(force: bool) -> bool:
    if force:
        return False
    st = _read_json(STATE_FILE, {}) or {}
    last = st.get("last_update_check_ts")
    if not last:
        return False
    try:
        elapsed = datetime.now().timestamp() - datetime.fromisoformat(last).timestamp()
    except Exception:
        return False
    return elapsed < CHECK_INTERVAL_SEC


def _touch_check_ts():
    st = _read_json(STATE_FILE, {}) or {}
    st["last_update_check_ts"] = datetime.now().isoformat(timespec="seconds")
    _write_json(STATE_FILE, st)


def _safe_join(relpath: str) -> str | None:
    """把 manifest 里的相对路径映射到本地绝对路径，越界返回 None。"""
    if not relpath or ".." in relpath.replace("\\", "/").split("/"):
        return None
    if relpath.startswith("/") or relpath.startswith("\\") or ":" in relpath[:2]:
        return None
    if not relpath.startswith(UPDATABLE):
        return None
    return os.path.join(ROOT, relpath.replace("/", os.sep))


def _plan_changes(manifest: dict) -> list:
    """比对本地 sha256，返回需要更新的 [(relpath, expected_sha256, size)]。"""
    changes = []
    for rel, meta in (manifest.get("files") or {}).items():
        target = _safe_join(rel)
        if target is None:
            continue
        want = meta.get("sha256")
        if not want:
            continue
        try:
            with open(target, "rb") as f:
                have = _sha256(f.read())
        except Exception:
            have = None
        if have != want:
            changes.append((rel, want, meta.get("size", 0)))
    return changes


def _apply(rel: str, want: str, timeout: int) -> tuple:
    """下载→校验→备份→原子替换。任何一步失败都必须不破坏现场。"""
    target = _safe_join(rel)
    if target is None:
        return (False, "路径越界，拒绝: " + rel)
    url = RAW_PREFIX + rel
    try:
        data = _get(url, timeout)
    except Exception as e:
        return (False, "下载失败 %s (%s)" % (rel, e))
    if _sha256(data) != want:
        return (False, "校验和不符，已丢弃: " + rel)
    os.makedirs(os.path.dirname(target), exist_ok=True)
    if os.path.exists(target):
        try:
            os.replace(target, target + ".bak")
        except Exception as e:
            return (False, "备份失败 %s (%s)" % (rel, e))
    tmp = target + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, target)
    except Exception as e:
        try:
            if os.path.exists(target + ".bak") and not os.path.exists(target):
                os.replace(target + ".bak", target)
        except Exception:
            pass
        return (False, "写入失败 %s (%s)" % (rel, e))
    return (True, "已更新 " + rel)


def maybe_update(quiet=False, timeout=20, force=False, check_only=False) -> dict:
    out = {"ok": True, "updated": [], "failures": [],
           "current_version": _local_version(), "remote_version": None}
    try:
        if _throttled(force):
            out["reason"] = "距上次检查不足 24h，跳过"
            return out

        try:
            raw = _get(MANIFEST_URL, timeout)
        except Exception as e:
            out.update(ok=False, reason="manifest 获取失败: %s" % e)
            _touch_check_ts()
            return out

        manifest = json.loads(raw.decode("utf-8"))
        remote = manifest.get("version", "0.0.0")
        out["remote_version"] = remote
        _touch_check_ts()

        changes = _plan_changes(manifest)
        if not changes:
            out["reason"] = "已是最新版本 %s" % out["current_version"]
            return out
        if check_only:
            out["pending"] = [c[0] for c in changes]
            out["reason"] = "有 %d 个文件可更新" % len(changes)
            return out

        for rel, want, _ in changes:
            ok, msg = _apply(rel, want, timeout)
            (out["updated"] if ok else out["failures"]).append(msg)
            if not quiet:
                sys.stderr.write(("  ✓ " if ok else "  ✗ ") + msg + "\n")
        if out["failures"]:
            out["ok"] = False
            out["reason"] = "%d 个文件更新失败，已保留 .bak 可用 --rollback 回退" % len(out["failures"])
        else:
            out["reason"] = "已升级至 %s，共 %d 个文件" % (remote, len(out["updated"]))
    except Exception as e:
        out.update(ok=False, reason="自更新异常: %s" % e)
    return out


def rollback() -> dict:
    restored, failed = [], []
    for root, _dirs, files in os.walk(ROOT):
        for name in files:
            if not name.endswith(".bak"):
                continue
            bak = os.path.join(root, name)
            target = bak[:-4]
            try:
                os.replace(bak, target)
                restored.append(os.path.relpath(target, ROOT))
            except Exception as e:
                failed.append("%s (%s)" % (name, e))
    return {"ok": not failed, "restored": restored, "failures": failed}


def main():
    try:
        import wbplatform
        wbplatform.ensure_utf8_streams()
    except Exception:
        pass
    ap = argparse.ArgumentParser(description="自进化更新器")
    ap.add_argument("--force", action="store_true", help="忽略 24h 节流")
    ap.add_argument("--check", action="store_true", help="只检查不落地")
    ap.add_argument("--rollback", action="store_true", help="回退到备份")
    ap.add_argument("--timeout", type=int, default=20)
    args = ap.parse_args()

    if args.rollback:
        print(json.dumps(rollback(), ensure_ascii=False, indent=2))
        sys.exit(0)
    res = maybe_update(force=args.force, check_only=args.check, timeout=args.timeout)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0 if res.get("ok") else 1)


if __name__ == "__main__":
    main()
