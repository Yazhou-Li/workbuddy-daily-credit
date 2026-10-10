#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
WorkBuddy 每日免费积分自动领取器 — 跨平台版 (macOS / Windows / Linux)

核心手法：**不碰你的凭据**。
请求由 WorkBuddy 主进程代发——脚本只跟本机 IPC 通道说话，鉴权 Header
（Bearer token + x-user-id）与图灵盾签名都在主进程内完成。因此：

  ✅ 不读取、不保存、不解密任何 accessToken
  ✅ 不伪造设备令牌，不触碰加密的本地凭据文件
  ✅ 唯一读取的文件是主进程公开的那份 endpoint.json

退出码
------
  0  成功领取 或 今日已领取（幂等成功）
  1  失败（已耗尽重试）
  2  活动未开启 / 已结束 / 账号未登录（非故障，无需重试）
  3  WorkBuddy 主进程不可用（等待超时）

用法
----
  python3 claim.py                  # 领取
  python3 claim.py --json           # 机器可读输出（供自动化解析）
  python3 claim.py --status         # 只查询，不领取
  python3 claim.py --doctor         # 环境自检（跨平台排错用）
  python3 claim.py --wait 120       # 最多等待主进程就绪 120 秒
  python3 claim.py --no-launch      # 不自动拉起 WorkBuddy
  python3 claim.py --no-self-update # 本次跳过自更新检查
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import wbplatform as plat  # noqa: E402

SKILL_DIR = os.path.dirname(SCRIPT_DIR)
STATE_FILE = os.path.join(SKILL_DIR, "state.json")
HISTORY_FILE = os.path.join(SKILL_DIR, "history.jsonl")
LOG_DIR = os.path.join(SKILL_DIR, "logs")

PIPE_NAME = "wb.request"
MAX_ATTEMPTS = 4
SOCKET_TIMEOUT = 20

VERSION = "2.0.0"


def log(msg, quiet=False):
    line = "[%s] %s" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    if not quiet:
        sys.stderr.write(line + "\n")
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, datetime.now().strftime("%Y-%m") + ".log"),
                  "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# wbipc 协议（平台无关：握手只用到 ticket 字符串做 HMAC，与传输方式无关）   #
# --------------------------------------------------------------------------- #
def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _ticket_id(ticket: str) -> str:
    return hashlib.sha256(ticket.encode("utf-8")).hexdigest()[:16]


def _transcript_bytes(role: str, tr: dict) -> bytes:
    label = "wbipc-s" if role == "server" else "wbipc-c"
    out = b""
    for item in (label, str(tr["protocol"]), tr["endpoint"],
                 tr["clientNonce"], tr["serverNonce"]):
        raw = item.encode("utf-8")
        out += len(raw).to_bytes(4, "big") + raw
    return out


def _proof(ticket: str, role: str, tr: dict) -> str:
    return _b64u(hmac.new(ticket.encode("utf-8"),
                          _transcript_bytes(role, tr), hashlib.sha256).digest())


class IpcClient:
    """JSON-RPC over IPC（换行分帧）。传输细节由 wbplatform 屏蔽。"""

    def __init__(self, endpoint: str, ticket: str, client_id="daily-credit"):
        self.endpoint = endpoint
        self.ticket = ticket
        self.client_id = client_id
        self._seq = 0
        self.sock = plat.connect_ipc(endpoint, SOCKET_TIMEOUT)

    def _send(self, obj):
        self.sock.sendall((json.dumps(obj) + "\n").encode("utf-8"))

    def _recv(self):
        line = self.sock.read_line()
        if line is None:
            return None
        return json.loads(line.decode("utf-8"))

    def handshake(self):
        client_nonce = _b64u(os.urandom(32))
        self._send({
            "type": "session_hello",
            "protocol_min": 1, "protocol_max": 1,
            "client_nonce": client_nonce,
            "ticket_id": _ticket_id(self.ticket),
            "client": {"kind": "skill", "id": self.client_id, "version": VERSION},
        })
        msg = self._recv()
        if not msg or msg.get("type") != "session_challenge":
            raise RuntimeError("握手失败：未收到 session_challenge (%s)"
                               % (msg.get("type") if msg else "empty"))
        tr = {"protocol": 1, "endpoint": self.endpoint,
              "clientNonce": client_nonce, "serverNonce": msg["server_nonce"]}
        self._send({"type": "session_prove",
                    "client_proof": _proof(self.ticket, "client", tr)})
        ack = self._recv()
        if not ack or ack.get("type") != "session_hello_ack":
            raise RuntimeError("握手失败：票据校验未通过 (auth_failed)")
        self.pipes = ack.get("pipes", [])
        return self.pipes

    def rpc(self, method, params=None, mode=None):
        self._seq += 1
        req_id = self._seq
        frame = {"jsonrpc": "2.0", "id": req_id, "method": method,
                 "params": params or {}}
        if mode:
            frame["mode"] = mode
        self._send(frame)
        while True:
            resp = self._recv()
            if resp is None:
                raise RuntimeError("连接已关闭，未收到响应")
            if resp.get("id") == req_id:
                if "error" in resp:
                    err = resp["error"]
                    raise RuntimeError("RPC 错误 %s: %s"
                                       % (err.get("code"), err.get("message")))
                return resp["result"]

    def open_pipe(self, pipe):
        return self.rpc("broker/GetPipe", {"pipe": pipe})["channel"]

    def fetch(self, channel, method, path, body=b"{}"):
        params = {"method": method, "path": path,
                  "headers": {"content-type": "application/json"},
                  "body_b64": base64.b64encode(body).decode("ascii")}
        res = self.rpc("%s/http.fetch" % channel, params, mode="call")
        raw = base64.b64decode(res.get("body_b64") or b"")
        return res.get("status"), raw.decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# 环境自愈                                                                     #
# --------------------------------------------------------------------------- #
def wait_for_endpoint(wait_seconds: int, allow_launch: bool, quiet: bool):
    deadline = time.time() + max(0, wait_seconds)
    launched = False
    while True:
        cfg = plat.read_endpoint()
        if cfg and plat.socket_alive(cfg[0]):
            return cfg
        if allow_launch and not launched and not plat.app_running():
            log("WorkBuddy 未运行，正在静默拉起…", quiet)
            if plat.launch_app():
                launched = True
        if time.time() >= deadline:
            return None
        time.sleep(3)


# --------------------------------------------------------------------------- #
# 业务                                                                         #
# --------------------------------------------------------------------------- #
def api_get_status(client):
    status, body = client.fetch(client.channel, "POST",
                                "/v2/billing/meter/checkin-activity-status", b"{}")
    if status != 200:
        raise RuntimeError("状态查询失败 HTTP %s" % status)
    payload = json.loads(body)
    if payload.get("code") != 0:
        raise RuntimeError("状态查询被拒绝: %s" % payload.get("msg"))
    return payload.get("data") or {}


def api_claim(client):
    status, body = client.fetch(client.channel, "POST",
                                "/v2/billing/meter/daily-checkin", b"{}")
    if status != 200:
        raise RuntimeError("领取失败 HTTP %s" % status)
    payload = json.loads(body)
    if payload.get("code") != 0:
        raise RuntimeError("领取被拒绝: %s" % payload.get("msg"))
    return payload.get("data") or {}


def run_once(cfg, quiet=False):
    endpoint, ticket = cfg
    client = IpcClient(endpoint, ticket)
    try:
        pipes = client.handshake()
        if PIPE_NAME not in pipes:
            return {"ok": False, "fatal": True, "exit": 2,
                    "reason": "账号未登录或服务不可用（可见服务: %s）" % ",".join(pipes)}
        client.channel = client.open_pipe(PIPE_NAME)

        before = api_get_status(client)
        if not before.get("active"):
            return {"ok": False, "fatal": True, "exit": 2,
                    "reason": "签到活动未开启或已结束", "data": before}

        if before.get("today_checked_in"):
            return {"ok": True, "skipped": True, "exit": 0,
                    "reason": "今日已领取，跳过", "credit": 0,
                    "total_credits": before.get("total_credits"),
                    "daily_credit": before.get("daily_credit"),
                    "streak_days": before.get("streak_days"), "data": before}

        claimed = api_claim(client)
        after = api_get_status(client)
        return {
            "ok": True, "skipped": False, "exit": 0,
            "credit": claimed.get("credit") or before.get("daily_credit"),
            "streak_days": after.get("streak_days", before.get("streak_days")),
            "total_credits": after.get("total_credits", before.get("total_credits")),
            "daily_credit": before.get("daily_credit"),
            "reason": "领取成功", "data": after,
        }
    finally:
        client.close()


def persist(result):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"updated_at": datetime.now().isoformat(timespec="seconds"),
                       **{k: v for k, v in result.items() if k != "data"}},
                      f, ensure_ascii=False, indent=2)
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"),
                                "ok": result.get("ok"), "skipped": result.get("skipped"),
                                "credit": result.get("credit"),
                                "total": result.get("total_credits"),
                                "reason": result.get("reason")},
                               ensure_ascii=False) + "\n")
    except Exception as e:
        log("状态写入失败: %s" % e)


def maybe_self_update(quiet: bool, wait_seconds: int):
    """低频自更新：默认每 24h 检查一次，可被避开。任何异常都不影响主流程。"""
    if os.environ.get("WORKBUDDY_DAILY_CREDIT_NO_UPDATE") == "1":
        return None
    try:
        import updater
        return updater.maybe_update(quiet=quiet, timeout=min(20, max(5, wait_seconds // 2)))
    except Exception as e:
        log("自更新跳过: %s" % e, quiet)
        return {"ok": False, "reason": str(e)}


def main():
    # Windows 控制台默认非 UTF-8，打印中文会直接崩。必须在任何输出之前修好。
    plat.ensure_utf8_streams()
    ap = argparse.ArgumentParser(description="WorkBuddy 每日免费积分自动领取（跨平台）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--status", action="store_true", help="仅查询不领取")
    ap.add_argument("--wait", type=int, default=90, help="等待主进程就绪秒数")
    ap.add_argument("--no-launch", action="store_true", help="不自动拉起 WorkBuddy")
    ap.add_argument("--quiet", action="store_true", help="静默（仅写日志）")
    ap.add_argument("--no-self-update", action="store_true", help="跳过自更新检查")
    ap.add_argument("--update", action="store_true", help="强制执行一次自更新检查")
    ap.add_argument("--doctor", action="store_true", help="环境自检并退出")
    args = ap.parse_args()

    if args.doctor:
        print(json.dumps(plat.doctor(), ensure_ascii=False, indent=2))
        sys.exit(0)

    if args.update:
        import updater
        res = updater.maybe_update(force=True, quiet=False)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        sys.exit(0 if res.get("ok") else 1)

    quiet = args.quiet or args.json
    if not args.no_self_update:
        maybe_self_update(quiet, args.wait)

    cfg = wait_for_endpoint(args.wait, not args.no_launch, quiet)
    if not cfg:
        msg = "WorkBuddy 主进程不可用（等待 %ss 超时）" % args.wait
        log(msg, quiet)
        out = {"ok": False, "exit": 3, "reason": msg}
        print(json.dumps(out, ensure_ascii=False) if args.json else msg)
        sys.exit(3)

    last_err = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            if args.status:
                c = IpcClient(cfg[0], cfg[1])
                try:
                    pipes = c.handshake()
                    if PIPE_NAME not in pipes:
                        raise RuntimeError("未登录或服务不可用: %s" % pipes)
                    c.channel = c.open_pipe(PIPE_NAME)
                    d = api_get_status(c)
                finally:
                    c.close()
                result = {"ok": True, "exit": 0, "status_only": True,
                          "reason": "状态查询完成",
                          "total_credits": d.get("total_credits"),
                          "daily_credit": d.get("daily_credit"),
                          "streak_days": d.get("streak_days"),
                          "checked_in": d.get("today_checked_in"), "data": d}
            else:
                result = run_once(cfg, quiet)
            persist(result)
            log("结果 ok=%s skipped=%s credit=%s total=%s | %s"
                % (result.get("ok"), result.get("skipped"), result.get("credit"),
                   result.get("total_credits"), result.get("reason")), quiet=True)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, default=str))
            else:
                print(result.get("reason"))
                if result.get("ok") and not result.get("status_only"):
                    print("本次积分: %s | 累计: %s | 连续签到: %s 天"
                          % (result.get("credit"), result.get("total_credits"),
                             result.get("streak_days")))
            sys.exit(result.get("exit", 0))
        except Exception as e:
            last_err = e
            log("第 %d/%d 次尝试失败: %s" % (attempt, MAX_ATTEMPTS, e), quiet)
            if attempt < MAX_ATTEMPTS:
                time.sleep(min(2 ** attempt, 15))
                fresh = plat.read_endpoint()
                if fresh and plat.socket_alive(fresh[0]):
                    cfg = fresh

    out = {"ok": False, "exit": 1, "reason": str(last_err)}
    log("最终失败: %s" % last_err, quiet)
    print(json.dumps(out, ensure_ascii=False) if args.json else ("失败: %s" % last_err))
    sys.exit(1)


if __name__ == "__main__":
    main()
