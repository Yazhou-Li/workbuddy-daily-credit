#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
生成 manifest.json —— 自进化更新的信任根。

CI 在每次推 push 到 main 时跑它，产出一份「版本号 + 每个文件的 sha256」清单。
客户端 updater 拿到这份清单后，逐文件比对哈希，只有完全吻合才会落地。

用法：
  python3 tools/build_manifest.py            # 输出 manifest.json
  python3 tools/build_manifest.py --check    # 校验现存的 manifest 是否与磁盘一致
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

# 与 wbplatform.ensure_utf8_streams() 同源的问题：Windows 控制台默认 cp936/cp1252，
# 打印中文会抛 UnicodeEncodeError。本脚本独立运行（不 import scripts/），故内联一份。
for _stream in (sys.stdout, sys.stderr):
    try:
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "manifest.json")

# 这些文件参与自更新（相对仓库根）
TRACKED = [
    "VERSION",
    "SKILL.md",
    "references/PROTOCOL.md",
    "scripts/claim.py",
    "scripts/wbplatform.py",
    "scripts/updater.py",
    "scripts/install.py",
]

# 这些不会被自动下载，仅计入版本号一致性
EXCLUDED_DIRS = {".git", ".github", "logs", "__pycache__", ".pytest_cache"}


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def version() -> str:
    p = os.path.join(ROOT, "VERSION")
    try:
        with open(p, encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return "0.0.0"


def build() -> dict:
    files = {}
    missing = []
    for rel in TRACKED:
        abs_path = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.exists(abs_path):
            missing.append(rel)
            continue
        files[rel] = {
            "sha256": sha256_of(abs_path),
            "size": os.path.getsize(abs_path),
        }
    return {
        "version": version(),
        "generated_at": None,
        "files": files,
        "missing": missing,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="校验现有 manifest 是否与磁盘一致")
    args = ap.parse_args()

    current = build()
    if args.check:
        old = {}
        try:
            with open(OUT, encoding="utf-8") as f:
                old = json.load(f).get("files", {})
        except Exception as e:
            print("无法读取现有 manifest: %s" % e)
            return 1
        mism = [k for k, v in current["files"].items()
                if old.get(k, {}).get("sha256") != v["sha256"]]
        if mism:
            print("以下文件已变更但 manifest 未更新：" + ", ".join(mism))
            return 1
        print("manifest 与磁盘一致 ✓")
        return 0

    import datetime
    current["generated_at"] = datetime.datetime.now(
        datetime.timezone.utc).isoformat(timespec="seconds")
    if current["missing"]:
        print("警告：以下文件缺失，已从 manifest 剔除：%s" % current["missing"])
    del current["missing"]
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("已生成 %s（version=%s, %d 个文件）"
          % (OUT, current["version"], len(current["files"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
