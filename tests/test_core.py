#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""协议与平台层单元测试（纯逻辑，不需要 WorkBuddy 在线）。

跑法： python3 tests/test_core.py
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import wbplatform as plat  # noqa: E402
import updater  # noqa: E402


class TestTicketId(unittest.TestCase):
    """ticket_id = sha256(ticket).hexdigest()[:16] —— 与原版实现必须逐字节一致。"""

    def test_known_vector(self):
        import hashlib
        ticket = "abc123"
        want = hashlib.sha256(ticket.encode("utf-8")).hexdigest()[:16]
        # 直接复用 claim.py 里的实现
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        import claim
        self.assertEqual(claim._ticket_id(ticket), want)
        self.assertEqual(len(want), 16)


class TestTranscript(unittest.TestCase):
    """握手 transcript 的拼接必须带 4 字节大端长度前缀，且 client/server 标签不同。"""

    def test_length_prefixed(self):
        import claim
        tr = {"protocol": 1, "endpoint": "/tmp/x.sock",
              "clientNonce": "c" * 43, "serverNonce": "s" * 22}
        raw_c = claim._transcript_bytes("client", tr)
        raw_s = claim._transcript_bytes("server", tr)
        self.assertNotEqual(raw_c, raw_s)
        # 第一段长度前缀应为 len("wbipc-c") == 7
        self.assertEqual(int.from_bytes(raw_c[:4], "big"), 7)
        self.assertTrue(raw_c[4:].startswith(b"wbipc-c"))
        self.assertTrue(raw_s[4:].startswith(b"wbipc-s"))

    def test_proof_is_stable_and_distinct(self):
        import claim
        tr = {"protocol": 1, "endpoint": "/tmp/x.sock",
              "clientNonce": "c" * 43, "serverNonce": "s" * 22}
        p1 = claim._proof("tick", "client", tr)
        p2 = claim._proof("tick", "client", tr)
        p3 = claim._proof("tick", "server", tr)
        self.assertEqual(p1, p2)
        self.assertNotEqual(p1, p3)


class TestPlatform(unittest.TestCase):
    """平台层必须能在当前系统上完成自检而不抛异常。"""

    def test_platform_is_recognised(self):
        self.assertIn(plat.PLATFORM, ("macos", "windows", "linux"))

    def test_find_python_returns_existing_path(self):
        py = plat.find_python()
        self.assertTrue(py, "未能选出任何解释器")
        self.assertTrue(os.path.exists(py), "选出的解释器不存在: %s" % py)

    def test_endpoint_candidates_are_absolute(self):
        for p in plat.ENDPOINT_CANDIDATES:
            if p:
                self.assertTrue(os.path.isabs(p), p)

    def test_socket_alive_on_bogus_returns_false(self):
        self.assertFalse(plat.socket_alive("/nonexistent/should/not/exist.sock"))

    def test_doctor_smoke(self):
        d = plat.doctor()
        self.assertIn("platform", d)
        self.assertIn("endpoint_resolved", d)
        # doctor 会被贴到 issue 里，因此不得泄露 ticket 明文。
        # 判据是「ticket 不能作为键名出现」，而不是「全文不能出现 ticket 字样」——
        # 后者会误伤 windows_note 里那句解释性的 "...endpoint.json + ticket 结构..."。
        # （这条断言曾经在 Windows CI 上误报失败，是测试自身的问题，不是平台层的问题。）
        self.assertFalse(any("ticket" in str(k).lower() for k in d.keys()))
        # socket 路径也必须抽象化，不能直接吐原文
        self.assertNotIn("/Users/", str(d.get("endpoint_resolved")))

    def test_version_present(self):
        with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as f:
            self.assertRegex(f.read().strip(), r"^\d+\.\d+\.\d+$")


class TestCliWiring(unittest.TestCase):
    """命令行骨架：脚本语法正确、参数可解析。"""

    def test_scripts_parse(self):
        for f in ("claim.py", "updater.py", "install.py", "wbplatform.py"):
            with self.subTest(file=f):
                path = os.path.join(ROOT, "scripts", f)
                self.assertTrue(os.path.exists(path))
                with open(path, encoding="utf-8") as fh:
                    compile(fh.read(), path, "exec")


if __name__ == "__main__":
    unittest.main(verbosity=2)
