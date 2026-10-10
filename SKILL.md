---
name: workbuddy-daily-credit
description: 自动领取 WorkBuddy 每日免费积分（Buddy 加油站每日签到）。当用户说"领积分""签到""每日积分""免费额度""积分不够了""帮我自动领积分"，或需要定时/开机自动领取 WorkBuddy 积分时使用。跨平台支持 macOS / Windows / Linux；具备幂等领取、自动重试、主进程未运行时自动拉起、开机/登录补领，以及跟随上游仓库进化的自更新能力。
---

# WorkBuddy 每日免费积分自动领取（跨平台）

每天自动领取 WorkBuddy「Buddy 加油站」的免费积分，领过不重复领。
macOS / Windows / Linux 均可运行。

## 核心事实（已实测验证）

| 项目 | 值 | 验证状态 |
|---|---|---|
| 领取接口 | `POST /v2/billing/meter/daily-checkin` | ✅ macOS 真机实测 |
| 状态接口 | `POST /v2/billing/meter/checkin-activity-status` | ✅ macOS 真机实测 |
| 每日积分 | 动态读取，不硬编码 | 实测 100/天，由服务端下发 |
| 领取方式 | 经 WorkBuddy 主进程 IPC 代发 | 无需 token，无需伪造签名 |
| macOS 传输 | Unix Domain Socket | ✅ 实测 |
| Windows 传输 | Named Pipe / AF_UNIX 自动探测 | ⚠️ 未真机验证，见下 |

> **诚实边界**：macOS 全流程经过真机实测。Windows / Linux 分支是依据协议对称性
> 与 Electron 惯例推导实现的，**尚未在真机上跑过**。若你在 Windows 上验证成功或失败，
> 请务必到仓库提 issue（附 `--doctor` 输出），让跨平台真正闭环。

## 为什么不用「读 token」路线

同类方案（如 88lin/workbuddy-auto-signin）会先从本地凭据文件里解出 accessToken，
再自行拼 HTTP 请求。本 skill 刻意避开这条路：

- 不需要处理 token 的明文/加密两种形态，不需要调 Electron 解密 helper
- 读取的配置文件更少，客户端改一次格式不至于连签就断
- 鉴权由主进程完成，脚本里从头到尾没有出现过用户的 token

代价是必须依赖本机主进程在线。这是有意的选择。

## 快速使用

```bash
SK=~/.workbuddy/skills/workbuddy-daily-credit

python3 $SK/scripts/claim.py                    # 立即领取（幂等）
python3 $SK/scripts/claim.py --status --json    # 只看状态
python3 $SK/scripts/claim.py --json             # 机器可读（供自动化解析）
python3 $SK/scripts/claim.py --no-launch        # 不自动拉起 WorkBuddy
python3 $SK/scripts/claim.py --doctor           # 环境自检（跨平台排错第一步）
python3 $SK/scripts/claim.py --update           # 立即执行一次自更新检查
```

退出码：`0` 成功或已领取 · `1` 失败 · `2` 活动未开启/未登录 · `3` 主进程不可用。

## 定时安装

```bash
python3 $SK/scripts/install.py             # 安装并立即试跑
python3 $SK/scripts/install.py --status    # 查看状态与历史
python3 $SK/scripts/install.py --uninstall # 卸载
```

| 平台 | 机制 | 触发时点 |
|---|---|---|
| macOS | launchd | 每天 8:30 / 12:30 / 18:30 / 22:30 + 登录即跑 |
| Windows | schtasks | 同上 4 个 DAILY 任务 + 1 个 ONLOGON |
| Linux | crontab | 同上 4 个时点 |

错过的时点会在下次开机/登录后补跑 —— 只要当天开过一次机，当天就能领到。

## 自进化

每次领取前会（按 24h 节流）检查上游仓库的 `manifest.json`，比对 sha256 后
决定是否升级。这是可选的：

```bash
export WORKBUDDY_DAILY_CREDIT_NO_UPDATE=1     # 永久关闭
python3 scripts/claim.py --no-self-update     # 单次跳过
python3 scripts/updater.py --rollback         # 回退到更新前
```

安全约束见 `SECURITY.md`：强制 HTTPS + 仓库 pinning + 逐文件 sha256 + 路径白名单
+ 原子替换 + 自动备份。

## 自愈机制

1. 主进程未运行 → 静默后台拉起（不抢前台），轮询等待 IPC 就绪
2. 端点失效 → 每次运行重新探测（主进程重启后 socket 路径会变）
3. IPC/网络抖动 → 指数退避重试，最多 4 轮，每轮换用最新端点
4. 今日已领取 → 直接跳过，绝不重复领取
5. 活动结束/未登录 → 退出码 2 并说明原因，不做无谓重试

## 排错

先跑 `--doctor`，它会打印平台、解释器选择、端点探测结果、定时状态。

| 现象 | 原因与处理 |
|---|---|
| 退出码 3 | 主进程未运行且等待超时。手动打开 WorkBuddy 并登录后重试 |
| 退出码 2 且提示未登录 | IPC 仅暴露 `wb.entitlement`。在 WorkBuddy 内登录后重试 |
| 提示活动未开启 | 签到活动有有效期（`start_time`/`end_time`），过期属正常 |
| 定时未生效 | macOS 要在「终端」里跑安装（需 GUI session）；Windows 需允许创建计划任务 |
| macOS 上 python3 弹3安装框 | `/usr/bin/python3` 是 CLT 占位壳。安装器已自动避开，或用 `which python3` 的真实路径 |
| Windows 上找不到管道 | 跑 `--doctor`，把 `endpoint_resolved`/`endpoint_alive` 贴到 issue |

日志与状态：`logs/YYYY-MM.log`、`state.json`、`history.jsonl`（均在 skill 目录下）。

## 实现原理

```
claim.py ──IPC──> WorkBuddy 主进程 ──HTTP(带鉴权+签名)──> 服务端
```

调用主进程暴露的 `wb.request / http.fetch`，由已登录的主进程注入
`authorization: Bearer <token>` 与 `x-user-id` 后代理发出。
协议细节见 `references/PROTOCOL.md`，平台适配全部收敛在 `scripts/wbplatform.py`。

## 已知边界

- 依赖本机 WorkBuddy 处于已登录状态（IPC 通道只存在于本机）
- 严格意义上「关机瞬间执行」做不到：真正的云端执行需要把设备令牌搬到云端，
  而图灵盾令牌由原生 SDK 生成，无法在外部复现
- 实际效果等价：开机即补领 + 每日 4 个时点，保证当天 24 小时内领到
