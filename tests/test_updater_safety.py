#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""自更新安全属性测试 —— 本项目最重要的一组测试。

updater 会下载远端文件并写盘，一旦路径白名单失守就是任意文件覆盖。
这里逐条验证「不该写的地方绝对写不进去」。

跑法： python3 tests/test_updater_safety.py
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import updater  # noqa: E402


class TestSafeJoin(unittest.TestCase):
    """路径白名单：越界一律拒绝。"""

    def test_rejects_parent_traversal(self):
        for bad in [
            "../evil.py",
            "../../etc/passwd",
            "scripts/../../evil.py",
            "scripts/sub/../../../evil.py",
        ]:
            with self.subTest(path=bad):
                self.assertIsNone(updater._safe_join(bad), msg=bad)

    def test_rejects_absolute_paths(self):
        for bad in ["/etc/passwd", "/tmp/evil.py", "C:\\Windows\\evil.py"]:
            with self.subTest(path=bad):
                self.assertIsNone(updater._safe_join(bad), msg=bad)

    def test_rejects_outside_whitelist(self):
        for bad in ["README.md", "tests/test_updater_safety.py", "tools/build_manifest.py"]:
            with self.subTest(path=bad):
                self.assertIsNone(updater._safe_join(bad), msg=bad)

    def test_accepts_whitelisted(self):
        for good in ["scripts/claim.py", "references/PROTOCOL.md", "SKILL.md", "VERSION"]:
            with self.subTest(path=good):
                resolved = updater._safe_join(good)
                self.assertIsNotNone(resolved, msg=good)
                self.assertTrue(resolved.startswith(ROOT), msg=good)

    def test_resolved_path_never_escapes_root(self):
        for good in ["scripts/claim.py", "SKILL.md"]:
            resolved = updater._safe_join(good)
            self.assertEqual(os.path.commonpath([resolved, ROOT]), ROOT)


class TestPlanChanges(unittest.TestCase):
    def test_unknown_path_is_ignored(self):
        manifest = {"version": "9.9.9",
                    "files": {"../../.zshrc": {"sha256": "deadbeef", "size": 1}}}
        self.assertEqual(updater._plan_changes(manifest), [])

    def test_missing_sha_is_ignored(self):
        manifest = {"files": {"scripts/claim.py": {"size": 1}}}
        self.assertEqual(updater._plan_changes(manifest), [])

    def test_manifest_respects_https_and_pinning(self):
        # manifest/base URL 必须 pin 到 HTTPS + 固定仓库，不能出现可被外部覆盖的口子
        self.assertTrue(updater.MANIFEST_URL.startswith("https://"))
        self.assertTrue(updater.RAW_PREFIX.startswith("https://"))
        self.assertIn("raw.githubusercontent.com", updater.MANIFEST_URL)
        self.assertIn(updater.OWNER, updater.MANIFEST_URL)
        self.assertIn(updater.REPO, updater.MANIFEST_URL)


class TestRollback(unittest.TestCase):
    def test_rollback_reports_even_without_baks(self):
        # 没有 .bak 时不应炸掉，应返回干净结果
        res = updater.rollback()
        self.assertIn("ok", res)
        self.assertIn("restored", res)


class TestLayoutAssumption(unittest.TestCase):
    """布局约定：skill 目录 == 仓库根目录。破坏这个约定自更新就会错位。"""

    def test_root_contains_version_and_manifest(self):
        self.assertTrue(os.path.exists(os.path.join(ROOT, "VERSION")))
        self.assertTrue(os.path.exists(os.path.join(ROOT, "SKILL.md")))
        self.assertTrue(os.path.exists(os.path.join(ROOT, "scripts", "claim.py")))

    def test_version_matches_manifest(self):
        import json
        with open(os.path.join(ROOT, "manifest.json"), encoding="utf-8") as f:
            ver = json.load(f)["version"]
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), ver)


if __name__ == "__main__":
    unittest.main(verbosity=2)
