#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""
wbplatform — 跨平台适配层（macOS / Windows / Linux）

把「操作系统差异」全部收敛到这一个文件里，让 claim.py 的业务逻辑保持平台无关。

收敛的差异共六类：
  1. IPC 传输       Unix Domain Socket  vs  Windows Named Pipe   vs  Win32 AF_UNIX
  2. 端点发现       endpoint.json 的候选搜索路径
  3. 进程检测       pgrep               vs  tasklist
  4. 主程拉起       open -j -g          vs  可执行文件多候选
  5. 解释器发现     python3             vs  python.exe / pythonw.exe
  6. 定时持久化     launchd             vs  schtasks

设计原则：**探测优先，回退兜底，绝不假设**。
任何探测失败都必须返回 None / False，由上层决定如何降级，而不是抛异常炸掉整个定时任务。

⚠️ 诚实的边界声明
-----------------
macOS 分支的每一项都经过真机实测（2026-10，macOS 26 arm64）。
Windows / Linux 分支是依据官方文档与协议对称性推导实现的（见 win_note 常量），
截至本项目发布时尚未在真机上验证过。若你在 Windows 上跑成功或跑失败，
请在仓库提 issue 附上下面 --doctor 的输出，让它真正跨起来。
"""

from __future__ import annotations

import glob
import json
import os
import socket
import subprocess
import sys

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

PLATFORM = "windows" if IS_WIN else ("macos" if IS_MAC else ("linux" if IS_LINUX else sys.platform))

win_note = (
    "Windows 分支依据 (1) WorkBuddy wbipc 协议栈 Electron 实现复用同一份 "
    "endpoint.json + ticket 结构、(2) Python 3.8+ 在 Windows 原生支持 AF_UNIX、"
    "(3) Electron IPC 在 Windows 上惯用 \\\\.\\pipe\\ 命名管道 三点推导。未真机验证。"
)

HOME = os.path.expanduser("~")

# S1: 端点候选路径 —— 逐个尝试，命中第一个可读出 endpoint+ticket 的
ENDPOINT_CANDIDATES = [
    os.path.join(HOME, ".workbuddy", "wbipc", "endpoint.json"),
    os.path.join(HOME, ".codebuddy", "wbipc", "endpoint.json"),
]
if IS_WIN:
    ENDPOINT_CANDIDATES.insert(
        1, os.path.join(os.environ.get("APPDATA", ""), "WorkBuddy", "wbipc", "endpoint.json")
    )

PIPE_PREFIX = "\\\\.\\pipe\\"


# --------------------------------------------------------------------------- #
# 1. IPC 传输层                                                                #
# --------------------------------------------------------------------------- #
class NamedPipeStream:
    """Windows 命名管道流。提供与 socket 一致的 sendall/recv/close/settimeout 子集。"""

    def __init__(self, path):
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self.path = path
        # FILE_FLAG_OVERLAPPED 会让同步 ReadFile 在管道上行为异常，这里不开
        GENERIC_READ_WRITE = 0x80000000 | 0x40000000
        OPEN_EXISTING = 3
        handle = ctypes.windll.kernel32.CreateFileW(
            ctypes.c_wchar_p(path),
            wintypes.DWORD(GENERIC_READ_WRITE),
            wintypes.DWORD(0),
            None,
            wintypes.DWORD(OPEN_EXISTING),
            wintypes.DWORD(0),
            None,
        )
        INVALID = ctypes.c_void_p(-1).value
        if handle in (INVALID, None):
            raise OSError("CreateFileW 打开命名管道失败: %s (err=%s)"
                          % (path, ctypes.windll.kernel32.GetLastError()))
        self.handle = handle
        self.timeout = 20

    def settimeout(self, t):
        self.timeout = t

    def sendall(self, data: bytes):
        ctypes = self._ctypes
        written = ctypes.c_ulong(0)
        ok = ctypes.windll.kernel32.WriteFile(
            ctypes.c_void_p(self.handle), data, len(data),
            ctypes.byref(written), None)
        if not ok:
            raise OSError("WriteFile 失败 (err=%s)" % ctypes.windll.kernel32.GetLastError())
        if written.value != len(data):
            raise OSError("WriteFile 短写: %s/%s" % (written.value, len(data)))

    def recv(self, bufsize=65536):
        ctypes = self._ctypes
        buf = ctypes.create_string_buffer(bufsize)
        read = ctypes.c_ulong(0)
        ok = ctypes.windll.kernel32.ReadFile(
            ctypes.c_void_p(self.handle), buf, bufsize,
            ctypes.byref(read), None)
        if not ok:
            err = ctypes.windll.kernel32.GetLastError()
            if err in (109, 232):      # ERROR_BROKEN_PIPE / ERROR_NO_DATA
                return b""
            raise OSError("ReadFile 失败 (err=%s)" % err)
        return buf.raw[: read.value]

    def close(self):
        try:
            self._ctypes.windll.kernel32.CloseHandle(self._ctypes.c_void_p(self.handle))
        except Exception:
            pass


class BaseTransport:
    """把「套接字」与「命名管道」统一为同一套 sendall/recv/readline/close 接口。"""

    def __init__(self, raw, is_socket: bool):
        self.raw = raw
        self.is_socket = is_socket
        self.buf = b""

    def settimeout(self, t):
        try:
            self.raw.settimeout(t)
        except Exception:
            self.raw.timeout = t

    def sendall(self, data: bytes):
        self.raw.sendall(data)

    def _pull(self) -> bool:
        chunk = self.raw.recv(65536)
        if not chunk:
            return False
        self.buf += chunk
        return True

    def read_line(self) -> bytes | None:
        """读到一行（不含换行符），连接关闭返回 None。"""
        while b"\n" not in self.buf:
            if not self._pull():
                return None
        line, _, self.buf = self.buf.partition(b"\n")
        return line

    def close(self):
        try:
            self.raw.close()
        except Exception:
            pass


def connect_ipc(endpoint: str, timeout: int = 20) -> BaseTransport:
    """按 endpoint 字符串的形式自动选择传输方式。"""
    if endpoint.startswith(PIPE_PREFIX) or endpoint.startswith("\\\\"):
        if not IS_WIN:
            raise OSError("检测到命名管道端点，但当前不是 Windows")
        stream = NamedPipeStream(endpoint)
        stream.settimeout(timeout)
        return BaseTransport(stream, is_socket=False)

    if not hasattr(socket, "AF_UNIX"):
        raise OSError("当前平台不支持 AF_UNIX")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(endpoint)
    return BaseTransport(sock, is_socket=True)


def socket_alive(endpoint: str) -> bool:
    """端点是否可用（存在性检查）。"""
    if endpoint.startswith(PIPE_PREFIX) or endpoint.startswith("\\\\"):
        try:
            NamedPipeStream(endpoint).close()
            return True
        except Exception:
            return False
    return os.path.exists(endpoint)


# --------------------------------------------------------------------------- #
# 2. 凭据端点发现                                                              #
# --------------------------------------------------------------------------- #
def read_endpoint():
    """读出 (endpoint, ticket)。每次运行都重读——主进程重启后路径会变。"""
    for path in ENDPOINT_CANDIDATES:
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            continue
        ep, tk = cfg.get("endpoint"), cfg.get("ticket")
        if ep and tk:
            return ep, tk
    # 兜底：主进程可能把 socket 放在临时目录，直接扫
    for pat in (
        os.path.join(os.environ.get("TMPDIR", "/tmp"), "**", "wbipc", "endpoint.json"),
        os.path.join(HOME, "Library", "**", "wbipc", "endpoint.json"),
    ):
        if IS_WIN and "Library" in pat:
            continue
        for hit in glob.glob(pat, recursive=True)[:5]:
            try:
                with open(hit, encoding="utf-8") as f:
                    cfg = json.load(f)
                if cfg.get("endpoint") and cfg.get("ticket"):
                    return cfg["endpoint"], cfg["ticket"]
            except Exception:
                continue
    return None


# --------------------------------------------------------------------------- #
# 3. 进程检测 / 4. 主程拉起                                                    #
# --------------------------------------------------------------------------- #
APP_NAME_WIN = "WorkBuddy.exe"
APP_PATH_MAC = "/Applications/WorkBuddy.app"


def app_candidates():
    if IS_WIN:
        base = os.environ.get("LOCALAPPDATA", "")
        return [
            os.path.join(base, "Programs", "WorkBuddy", APP_NAME_WIN),
            os.path.join(base, "WorkBuddy", APP_NAME_WIN),
            os.path.join(os.environ.get("PROGRAMFILES", ""), "WorkBuddy", APP_NAME_WIN),
        ]
    if IS_MAC:
        return [APP_PATH_MAC]
    return ["workbuddy", "WorkBuddy"]


def app_running() -> bool:
    """主进程是否在运行。

    ⚠️ 实测坑（2026-10-10, macOS 26）：`pgrep -x WorkBuddy` 精确匹配**拿不到**进程，
    但 `pgrep -f WorkBuddy.app` 能稳定命中。原因是 -x 要求 comm 与字符串完全相等，
    而 Electron 主进程的实际 comm 并非裸 "WorkBuddy"。
    旧实现只用 -x，于是在主进程明明活着时也判定为「未运行」，进而触发多余的 open 调用。
    这里改成两级：精确优先，模糊兜底。
    模糊串选 "WorkBuddy.app"（带 .app）而非 "WorkBuddy"，是为了避免误命中
    命令行里恰好含有本 skill 路径的 Python 进程。
    """
    try:
        if IS_WIN:
            out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + APP_NAME_WIN],
                                 capture_output=True, text=True).stdout
            return APP_NAME_WIN.lower() in out.lower()
        try:
            if subprocess.run(["pgrep", "-x", "WorkBuddy"],
                              capture_output=True, text=True).stdout.strip():
                return True
        except Exception:
            pass
        try:
            return bool(subprocess.run(["pgrep", "-f", "WorkBuddy.app"],
                                       capture_output=True, text=True).stdout.strip())
        except Exception:
            return False
    except Exception:
        return False


def launch_app() -> bool:
    """静默拉起 WorkBuddy，不抢前台。"""
    try:
        if IS_WIN:
            for cand in app_candidates():
                if cand and os.path.exists(cand):
                    subprocess.Popen([cand], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL,
                                     creationflags=0x08000000)  # CREATE_NO_WINDOW
                    return True
            return False
        if IS_MAC:
            if os.path.exists(APP_PATH_MAC):
                subprocess.run(["open", "-j", "-g", "-a", APP_PATH_MAC], capture_output=True)
                return True
            return False
        for cand in app_candidates():
            subprocess.Popen([cand], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    except Exception:
        return False
    return False


# --------------------------------------------------------------------------- #
# 5. 解释器发现                                                                #
# --------------------------------------------------------------------------- #
def find_python(windowed: bool = False) -> str:
    """为定时任务挑选一个「确定能跑」的 Python。

    优先级：当前解释器 > 同名无窗口版 > PATH > 常见安装目录 > py 启动器。
    顺序很重要：macOS 上 /usr/bin/python3 在未装 CLT 时只是个弹窗占位壳，
    放进 launchd 会静默失败，因此绝不能无条件硬编码它。
    """
    cur = sys.executable
    if cur and os.path.exists(cur) and not _is_stub(cur):
        if IS_WIN and windowed:
            alt = os.path.join(os.path.dirname(cur), "pythonw.exe")
            if os.path.exists(alt):
                return alt
        return cur

    names = ["pythonw.exe", "python.exe"] if IS_WIN else ["python3"]
    for name in names:
        hit = _which(name)
        if hit and not _is_stub(hit):
            return hit

    if IS_WIN:
        pats = [
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python", "*", "*.exe"),
            r"C:\Python3*\*.exe",
            os.path.join(os.environ.get("PROGRAMFILES", ""), "Python3*", "*.exe"),
        ]
        for pat in pats:
            hits = sorted(glob.glob(pat), reverse=True)
            prefer = [h for h in hits
                      if (h.endswith("pythonw.exe") if windowed
                          else h.endswith("python.exe"))]
            if prefer:
                return prefer[0]
        launcher = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "py.exe")
        if os.path.exists(launcher):
            try:
                out = subprocess.run([launcher, "-3", "-c", "import sys;print(sys.executable)"],
                                     capture_output=True, text=True, timeout=20).stdout.strip()
                hit = out.splitlines()[-1] if out else ""
                if hit and os.path.exists(hit) and not _is_stub(hit):
                    return hit
            except Exception:
                pass
    else:
        for pat in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"):
            if os.path.exists(pat) and not _is_stub(pat):
                return pat
    return sys.executable


def _which(name: str):
    from shutil import which
    return which(name)


def _is_stub(path: str) -> bool:
    """macOS 上未装 Xcode CLT 时 /usr/bin/python3 是占位壳，需排除。"""
    if not IS_MAC:
        return False
    try:
        if os.path.realpath(path).startswith("/usr/bin/python3"):
            out = subprocess.run(["xcode-select", "-p"], capture_output=True,
                                 text=True, timeout=10).stdout.strip()
            return not out
    except Exception:
        return False
    return False


# --------------------------------------------------------------------------- #
# 6. 定时持久化                                                                #
# --------------------------------------------------------------------------- #
TASK_LABEL = "cn.workbuddy.daily-credit"
SCHED_HOURS = [8, 12, 18, 22]
SCHED_MINUTE = 30


def install_scheduler(skill_dir: str, python: str, claim_py: str) -> tuple:
    """安装定时。返回 (ok: bool, message: str)。"""
    if IS_WIN:
        return _install_schtasks(python, claim_py)
    if IS_MAC:
        return _install_launchd(skill_dir, python, claim_py)
    return _install_cron(python, claim_py)


def uninstall_scheduler() -> tuple:
    if IS_WIN:
        r = subprocess.run(["schtasks", "/Delete", "/TN", TASK_LABEL, "/F"],
                           capture_output=True, text=True)
        return (r.returncode == 0, r.stdout.strip() or "已卸载")
    if IS_MAC:
        plist = _plist_path()
        if os.path.exists(plist):
            subprocess.run(["launchctl", "unload", plist], capture_output=True)
            os.remove(plist)
        return (True, "已卸载 launchd 任务")
    r = subprocess.run(["crontab", "-r"], capture_output=True, text=True)
    return (r.returncode == 0, r.stdout.strip() or "已卸载")


def scheduler_status() -> str:
    if IS_WIN:
        r = subprocess.run(["schtasks", "/Query", "/TN", TASK_LABEL, "/FO", "LIST"],
                           capture_output=True, text=True)
        return r.stdout.strip() or "未安装"
    if IS_MAC:
        plist = _plist_path()
        r = subprocess.run(["launchctl", "list", TASK_LABEL], capture_output=True, text=True)
        found = r.returncode == 0
        tail = "\nplist: %s (%s)" % (plist, "存在" if os.path.exists(plist) else "缺失")
        return (r.stdout.strip() or "未安装") + tail
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout.strip() or "未安装"


def _plist_path():
    return os.path.join(HOME, "Library", "LaunchAgents", TASK_LABEL + ".plist")


def _install_launchd(skill_dir, python, claim_py) -> tuple:
    plist = _plist_path()
    os.makedirs(os.path.dirname(plist), exist_ok=True)
    os.makedirs(os.path.join(skill_dir, "logs"), exist_ok=True)
    intervals = "".join(
        "    <dict><key>Hour</key><integer>%d</integer>"
        "<key>Minute</key><integer>%d</integer></dict>\n" % (h, SCHED_MINUTE)
        for h in SCHED_HOURS
    )
    content = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>%(label)s</string>
  <key>ProgramArguments</key>
  <array>
    <string>%(py)s</string>
    <string>%(claim)s</string>
    <string>--wait</string><string>180</string>
    <string>--quiet</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
%(iv)s  </array>
  <key>RunAtLoad</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>LowPriorityIO</key><true/>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONUTF8</key><string>1</string></dict>
  <key>StandardOutPath</key><string>%(out)s</string>
  <key>StandardErrorPath</key><string>%(err)s</string>
</dict>
</plist>
""" % {
        "label": TASK_LABEL, "py": python, "claim": claim_py, "iv": intervals,
        "out": os.path.join(skill_dir, "logs", "launchd.out.log"),
        "err": os.path.join(skill_dir, "logs", "launchd.err.log"),
    }
    with open(plist, "w", encoding="utf-8") as f:
        f.write(content)
    subprocess.run(["launchctl", "unload", plist], capture_output=True)
    subprocess.run(["launchctl", "load", plist], capture_output=True)
    r = subprocess.run(["launchctl", "list", TASK_LABEL], capture_output=True, text=True)
    if r.returncode == 0:
        return (True, "launchd 已注册并生效: " + TASK_LABEL)
    return (True, "plist 已写入 %s，但当前会话无法注册到 launchd（缺少 GUI session）；\n"
                  "下次登录或开机时会自动加载。" % plist)


def _install_schtasks(python, claim_py) -> tuple:
    """Windows：一个每日触发 + 一个登录触发；两者幂等共用一套脚本。"""
    base = [python, claim_py, "--wait", "180", "--quiet"]
    results = []
    ok_all = True
    # 每日多点：schtasks 单任务只能一个时间，这里按小时建多个后缀任务
    for h in SCHED_HOURS:
        tn = "%s-%02d%02d" % (TASK_LABEL, h, SCHED_MINUTE)
        cmd = ["schtasks", "/Create", "/F", "/TN", tn,
               "/TR", subprocess.list2cmdline(base),
               "/SC", "DAILY", "/ST", "%02d:%02d" % (h, SCHED_MINUTE)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        results.append("%s -> %s" % (tn, "OK" if r.returncode == 0 else (r.stderr or r.stdout).strip()))
        ok_all = ok_all and r.returncode == 0
    # 登录补跑：覆盖「当天开机较晚」的情况
    tn_logon = TASK_LABEL + "-onlogon"
    cmd = ["schtasks", "/Create", "/F", "/TN", tn_logon,
           "/TR", subprocess.list2cmdline(base), "/SC", "ONLOGON"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    results.append("%s -> %s" % (tn_logon, "OK" if r.returncode == 0 else (r.stderr or r.stdout).strip()))
    return (ok_all, "\n".join(results))


def _install_cron(python, claim_py) -> tuple:
    line = "0 %s * * * %s %s --wait 180 --quiet\n" % (
        ",".join(str(h) for h in SCHED_HOURS), python, claim_py)
    try:
        cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    except Exception:
        cur = ""
    kept = "\n".join(l for l in cur.splitlines() if TASK_LABEL not in l)
    new = (kept + "\n" + line) if kept.strip() else line
    p = subprocess.run(["crontab", "-"], input=new, capture_output=True, text=True)
    return (p.returncode == 0, p.stderr.strip() or "crontab 已安装: " + line.strip())


# --------------------------------------------------------------------------- #
# 自检                                                                         #
# --------------------------------------------------------------------------- #
def ensure_utf8_streams():
    """强制 stdout/stderr 用 UTF-8 输出。

    ⚠️ 实测坑（2026-10-10, GitHub Actions windows-latest）：
    Windows 控制台默认走系统 ANSI 代码页（简体中文环境是 cp936，CI 上是 cp1252），
    一打印中文就抛 UnicodeEncodeError: 'charmap' codec can't encode characters。
    本项目的汇报文案全是中文，所以不修这一处，Windows 上连 --doctor 都跑不完。

    Python 3.7+ 用 reconfigure 就地改；再包一层 try 是因为某些重定向场景
    （比如被别的进程接管 stdout）下 reconfigure 会失败，那就退回原样，不致命。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def doctor() -> dict:
    """收集诊断信息。提 issue 时贴这个（自动脱敏，不含 ticket 明文）。"""
    cfg = read_endpoint()
    ep, _ = cfg if cfg else (None, None)
    return {
        "platform": PLATFORM,
        "python_version": sys.version.split()[0],
        "python_selected": find_python(),
        "app_running": app_running(),
        "app_candidates": [c for c in app_candidates() if c],
        "endpoint_file_exists": any(os.path.exists(p) for p in ENDPOINT_CANDIDATES if p),
        "endpoint_resolved": ("<unix-sock>" if ep and not ep.startswith("\\\\")
                              else ("<named-pipe>" if ep else None)),
        "endpoint_alive": socket_alive(ep) if ep else False,
        "scheduler": scheduler_status()[:500],
        "windows_note": win_note if IS_WIN else "",
    }
