# WorkBuddy Daily Credit · 每日免费积分自动领取

[![CI](https://github.com/Yazhou-Li/workbuddy-daily-credit/actions/workflows/ci.yml/badge.svg)](https://github.com/Yazhou-Li/workbuddy-daily-credit/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Windows%20%7C%20Linux-lightgrey.svg)]()

一句话：**每天自动领掉 WorkBuddy「Buddy 加油站」的免费积分，全程不碰你的登录凭据。**

它同时是一个可被 AI Agent 调用的 Skill，也是一个能自己跟进版本进化的脚本。

---

## 它跟别的签到脚本有什么不一样

市面上做 WorkBuddy 签到的项目，主流做法是**从本地凭据文件里解出 accessToken，然后自己拼 HTTP 请求**。这条路能行，但有几个绕不开的代价：

- 要区分 token 的明文/加密两种形态，加密态还得调 Electron 的解密 helper
- 客户端改一次凭据存储格式，连签就断，得等人来修
- 脚本里出现过用户的 token

本方案换了条路：**让 WorkBuddy 主进程替你发请求。**

```
claim.py ──本机 IPC──> WorkBuddy 主进程 ──HTTP(带鉴权+签名)──> 服务端
              ↑                    ↑
        只跟本机通道说话      Bearer token / 图灵盾签名都在这里面完成
```

脚本从头到尾没接触过 token。它读的唯一一个文件，是主进程公开的那份 `~/.workbuddy/wbipc/endpoint.json`。

代价必须说清楚：**依赖本机主进程在线**。这是有意的选择——用一点点自主性，换掉了整类凭据处理和凭据泄露面。

### 同类项目横向对比

| 项目 | Stars | 覆盖玩法 | 凭据方式 | 形态 |
|---|---|---|---|---|
| [AlephAITech/WorkBuddyGuide](https://github.com/AlephAITech/WorkBuddyGuide) | 3.3k | 教程/Skills/MCP 聚合 | — | 蓝皮书 |
| [88lin/workbuddy-auto-signin](https://github.com/88lin/workbuddy-auto-signin) | 1.1k | 签到 + 成长中心全套 | 读本地 token | 脚本 |
| [ithtelab/workbuddy-manager](https://github.com/ithtelab/workbuddy-manager) | 900 | 账号池 + 签到 + 反代 | 纳管多账号 | 控制台 |
| [L0NE-6/WorkBuddy-Daily](https://github.com/L0NE-6/WorkBuddy-Daily) | 246 | 33 项自动化 | 读本地 token | 脚本 |
| **本项目** | — | **每日签到** | **零凭据（IPC 代理）** | **Skill + 自进化** |

**得承认的差距**：above two 个千星项目覆盖了「成长中心」的全部玩法（旅行礼物、派 Buddy、开盲盒、任务、断登补签……），本项目目前**只做每日签到这一件事**。功能面上我们更窄。选我们的理由只剩两个：不想让任何脚本读到自己的 token，以及希望它能被 AI Agent 直接调用并自动跟进更新。

如果哪天腾讯云把这个签到接口改了，本项目优势也就到这儿了——所以「自进化」才被设计进来：一次常规签到就能顺手完成升级。

---

## 平台支持与验证状态

**这一节请当真。**

| 平台 | IPC 传输 | 定时机制 | 验证状态 |
|---|---|---|---|
| macOS (arm64 / Intel) | Unix Domain Socket | launchd | ✅ **真机实测通过**（2026-10-10, macOS 26 arm64） |
| Windows 10/11 | Named Pipe / AF_UNIX 自动探测 | schtasks | ⚠️ **未真机验证**，推导实现 |
| Linux | Unix Domain Socket | crontab | ⚠️ **未真机验证**，推导实现 |

Windows 分支的推导依据写在 `scripts/wbplatform.py` 的 `win_note` 里，归结为三点：

1. WorkBuddy 的 `wbipc` 是 Electron 层实现，macOS/Windows 复用同一份 `endpoint.json + ticket` 结构的可能性很高
2. Python 3.8+ 在 Windows 上原生支持 `AF_UNIX`
3. Electron IPC 在 Windows 上惯用 `\\.\pipe\` 命名管道

**其中第 1 点是假设，不是事实。** 如果 WorkBuddy Windows 版压根不落地 `endpoint.json`，那么该分支需要从「凭据接收端约定」重新开始设计。

👉 如果你在 Windows 上跑通了（或跑挂了），请务必带着下面这行的输出来开 issue，让它真正跨起来：

```bash
python3 scripts/claim.py --doctor
```

---

## 快速开始

### 1. 放到 Agent 能看见的位置

```bash
git clone https://github.com/Yazhou-Li/workbuddy-daily-credit.git \
  ~/.workbuddy/skills/workbuddy-daily-credit
```

布局约定很关键：**这个目录自身就是仓库根目录**（里面直接是 `SKILL.md`、`scripts/`、`manifest.json`）。正因为两边同构，自更新才能在不用猜安装位置的情况下对齐路径。

### 2. 跑一次看看

```bash
python3 ~/.workbuddy/skills/workbuddy-daily-credit/scripts/claim.py
```

首次建议先看自检，它会打印平台、解释器选择、端点探测结果、定时状态：

```bash
python3 ~/.workbuddy/skills/workbuddy-daily-credit/scripts/claim.py --doctor
```

### 3. 装上定时（开机/登录会补跑）

```bash
python3 ~/.workbuddy/skills/workbuddy-daily-credit/scripts/install.py
```

| 平台 | 触发时点 |
|---|---|
| macOS | 每天 8:30 / 12:30 / 18:30 / 22:30 + 登录即跑 |
| Windows | 同上 4 个计划任务 + 1 个登录触发 |
| Linux | 同上 4 个时点（cron） |

**"关机了怎么办"**：三个平台都配了登录/开机兜底。只要当天开过一次机，当天就能领到。真正的"关机瞬间执行"做不到——那需要把设备令牌搬到云端，而图灵盾令牌由原生 SDK 生成，外部无法复现。这是硬边界，不是实现问题。

### 常用命令

```bash
python3 scripts/claim.py --json            # 机器可读输出（给自动化解析）
python3 scripts/claim.py --status          # 只看状态不领取
python3 scripts/install.py --status        # 查看定时状态与历史
python3 scripts/install.py --uninstall     # 卸载
python3 scripts/updater.py --check         # 看有没有新版，不落地
python3 scripts/updater.py --rollback      # 回退到更新前
```

退出码：`0` 成功或已领取 · `1` 失败 · `2` 活动未开启/未登录 · `3` 主进程不可用

---

## 自进化

每次领取前，会在 24 小时节流的前提下检查上游仓库的 `manifest.json`，比对每个文件的 sha256，不一致才下载。所以当接口变了的修复推到 main 之后，**你什么都不用做**，下次签到就带着修复回来了。

这个功能可以关：

```bash
export WORKBUDDY_DAILY_CREDIT_NO_UPDATE=1   # 永久关闭
python3 scripts/claim.py --no-self-update   # 单次跳过
```

### 安全模型

「自动下载代码并执行」本质上是个 RCE 开关，`scripts/updater.py` 为此设了七道闸：

| # | 措施 | 作用 |
|---|---|---|
| 1 | 强制 HTTPS + 系统可信根证书 | 杜绝中间人篡改（`urllib` 默认校验证书，代码里没有任何 `verify=False`） |
| 2 | 仓库身份 pinning | `owner/repo` 写死在常量里，不接受配置覆盖 |
| 3 | 逐文件 sha256 校验 | 下载后比对，不符直接丢弃，绝不落盘替换 |
| 4 | 路径白名单 | 只允许覆盖 `scripts/`、`references/`、`SKILL.md`、`VERSION`；manifest 塞 `../../` 也出不去 |
| 5 | 原子替换 | 先写 `.tmp` 再 `os.replace`，断电不会留半截文件 |
| 6 | 自动备份 + 回滚 | 每份替换前留 `.bak`，`--rollback` 一键还原 |
| 7 | 可完全关闭 | 环境变量 / 命令行都能跳过 |

CI 里对第 4 条有专门的测试（`tests/test_updater_safety.py`），包括 `../../` 穿越、绝对路径、白名单外路径三类攻击面。

即便如此，仍然建议：**先 clone 下来看一眼代码，再让它自动更新。**信任应当是默认加例外，而不是相反。

---

## 自愈机制

| 场景 | 处理 |
|---|---|
| 主进程未运行 | 静默后台拉起（不抢前台），轮询等待 IPC 就绪 |
| 端点失效 | 每次运行重新探测——主进程重启后 socket 路径会变 |
| IPC / 网络抖动 | 指数退避重试，最多 4 轮，每轮换用最新端点 |
| 今日已领取 | 直接跳过，绝不重复领取 |
| 活动结束 / 未登录 | 退出码 2 并说明原因，不做无谓重试 |

> 顺带修了一个旧实现的 bug：原先 `pgrep -x WorkBuddy` 精确匹配**拿不到** Electron 主进程，
> 导致主进程明明活着也被判定为未运行。现改为精确优先、模糊（`WorkBuddy.app`）兜底。见 `app_running()` 里的注释。

---

## 排错

第一步永远是 `--doctor`。

| 现象 | 原因与处理 |
|---|---|
| 退出码 3 | 主进程未运行且等待超时。手动打开 WorkBuddy 并登录后重试 |
| 退出码 2 且提示未登录 | IPC 仅暴露 `wb.entitlement`。在 WorkBuddy 内登录后重试 |
| 提示活动未开启 | 签到活动有有效期（`start_time`/`end_time`），过期属正常 |
| 定时未生效（macOS） | 要在**终端**里跑安装器，它需要 GUI session；确认 `~/Library/LaunchAgents/cn.workbuddy.daily-credit.plist` 存在 |
| macOS 上 python3 弹安装框 | `/usr/bin/python3` 在没装 CLT 时只是占位壳。安装器已自动避开，或改用 `which python3` 的真实路径 |
| macOS 上权限被拒 | 别把脚本放在 `~/Documents`、`~/Downloads`、`~/Desktop`——TCC 会拦后台进程读取 |

---

## 仓库结构

```
workbuddy-daily-credit/
├── SKILL.md                  # Agent 可读的技能描述（同时也是文档）
├── VERSION / manifest.json   # 自进化的信任根
├── references/PROTOCOL.md    # wbipc 协议细节（握手/帧格式/错误码）
├── scripts/
│   ├── claim.py              # 主逻辑
│   ├── wbplatform.py         # 平台适配层（所有 OS 差异都收敛在这里）
│   ├── updater.py            # 自进化更新器
│   └── install.py            # 跨平台定时安装器
├── tools/build_manifest.py   # 生成 manifest（CI 调用）
└── tests/                    # 21 个用例，其中 11 个专测自更新的路径穿越
```

想把 Windows 支持补起来的人，只需要改 `wbplatform.py` 一个文件——业务逻辑是平台无关的，改完跑 `--doctor` 贴输出即可。

---

## 免责说明

本项目是个人学习与技术交流用途的第三方工具，与 WorkBuddy / 腾讯官方无关。它通过 WorkBuddy 桌面端**自行暴露的本地 IPC 通道**发起请求，没有破解、绕过任何付费或鉴权机制，只是把你在客户端里手动点一下「领取」的动作自动化了。

所获积分均为官方公开发放的免费额度。如你发现有越界之处，欢迎提 issue 指出。

---

## License

[MIT](LICENSE) © 2026 Yazhou Li
