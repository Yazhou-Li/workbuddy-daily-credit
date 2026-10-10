# 安全模型与自审结论

发布日期：2026-10-10 · 版本 2.0.0

这个文件回答一个问题：**你能放心让我这个脚本在你机器上常驻后台吗？**

先给结论，再给证据。

## 自审结论

| 检查项 | 结论 |
|---|---|
| 读取用户凭据（token / cookie / 密码） | ❌ **不读**，脚本中不存在任何 token 变量参与 request 构造 |
| 解密本地加密数据 | ❌ 不涉及 |
| 网络出站目标 | ✅ 仅两类：本机 IPC Unix Socket，以及自更新时的 raw.githubusercontent.com |
| 写盘范围 | ✅ 仅限 skill 目录自身（`state.json` / `history.jsonl` / `logs/`） |
| 权限提升 | ❌ 无 sudo / UAC 需求 |
| 隐蔽性与持久化 | ⚠️ **有持久化**（launchd / schtasks / crontab），但安装由用户显式执行、可 `--uninstall` 一键卸载，且有明确告知 |
| 依赖 | ✅ Python 标准库，零第三方包——没有供应链投毒面 |
| 代码量 | 约 1,100 行 Python，全部可人工审完 |

## 关于「零凭据」的可验证性

本方案的核心声明是「不接触你的 token」。这句话可以被独立验证，不需要信任我：

1. 全仓库搜 `accessToken`、`Bearer`、`authorization`，除协议文档的描述文字外，
   **业务代码里没有任何一处构造过鉴权头**。鉴权由 WorkBuddy 主进程在代理请求时注入
2. `scripts/claim.py` 只调用两个 IPC 方法：`broker/GetPipe` 和 `<channel>/http.fetch`，
   请求的 `path` 只有两个计费相关接口
3. 唯一读取的外部文件是 `~/.workbuddy/wbipc/endpoint.json`，里面是 socket 路径和握手票据，
   没有身份令牌

协议细节全部记录在 `references/PROTOCOL.md`，欢迎对照复核。

## 自进化（下载远端代码并执行）的风险与缓解

这是本项目中**唯一真正危险**的部分，因此单独说明。

威胁模型：假设有人控制了 manifest 的分发路径（例如仓库被接管、或者流量被中间人截获），
攻击者就有可能下发任意代码并在你的机器上有计划地执行。

七道缓解措施（实现在 `scripts/updater.py`）：

1. **强制 HTTPS**：使用默认可信证书体系，代码中不存在 `verify=False`
2. **仓库身份 pinning**：`OWNER` / `REPO` 常量写死，不接受环境变量或配置文件覆盖。
   攻击者无法通过「改个配置」来劫持更新源
3. **逐文件 sha256 校验**：下载内容与 manifest 不符则直接丢弃临时数据，不替换原有文件
4. **路径白名单**：`UPDATABLE` 限定四个前缀；任何含 `..`、绝对路径分隔符、盘符的路径都被 `_safe_join` 拒绝
5. **原子替换**：先写 `.tmp` 再 `os.replace`
6. **备份与回滚**：替换前留 `.bak`，`--rollback` 可还原
7. **可完全关闭**：环境变量 `WORKBUDDY_DAILY_CREDIT_NO_UPDATE=1` 或 `--no-self-update`

CI 中的 `tests/test_updater_safety.py` 覆盖了第 4 条的三类攻击面（`../` 穿越、绝对路径、白名单外路径）。

**残余风险（必须说清楚）**：

- 如果这个 GitHub 仓库本身被接管，攻击者可以构造一份合法的 manifest 并通过 sha256 校验。
  这是所有自更新机制的共同软肋（软件提供方的 TOTP 问题）。缓解手段只有自律：
  重要更新由 CI 自动生成 manifest，避免手改
- 更新只在签到时顺带检查一次，且有 24 小时节流，不存在常驻在后台的更新守护进程

如果你不接受这个残余风险：**请直接关闭自更新**，脚本功能完全不受影响：

```bash
export WORKBUDDY_DAILY_CREDIT_NO_UPDATE=1
```

## 已知的安全边界（不是缺陷，是设计取舍）

- **依赖本机主进程在线**：这是为「零凭据」付的代价。做不到关机期间还在云端签到
- **端点票据在本地明文**：`endpoint.json` 里握手用的 ticket 是明文文件。
  它只能连本机 socket，且目录权限 0700，但要意识到本机上的其他进程理论上也能读。
  这是 WorkBuddy 自身的设计，本项目按原样使用，不改变它的权限模型
- **更新源默认指向个人仓库**：这不是一个组织级项目。若 fork 出去用于生产环境，
  请务必改掉 `updater.py` 里的 `OWNER` / `REPO`

## 报告安全问题

发现可疑行为请不要直接开 public issue。用 GitHub 的 [Security Advisories](https://github.com/Yazhou-Li/workbuddy-daily-credit/security/advisories) 私下报告。
