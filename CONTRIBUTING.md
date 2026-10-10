# CONTRIBUTING

最缺的贡献：**Windows / Linux 的真机验证**。

## 你最可能帮上忙的三件事

1. **在 Windows 上跑一遍**，不管成功失败，把 `--doctor` 的输出贴到 issue
2. 补 macOS / Windows 上 WorkBuddy 主进程的 IPC 端点确切断位置与形态
3. 修正文档里任何与实际不符的描述

## 环境准备

零依赖，标准库就够：

```bash
git clone https://github.com/Yazhou-Li/workbuddy-daily-credit.git
cd workbuddy-daily-credit
python3 tests/test_updater_safety.py
python3 tests/test_core.py
```

需要 Python 3.8+。

## 改代码前先读这两处约束

### 1. 布局约定：这个目录本身就是 skill 根目录

`SKILL.md`、`scripts/`、`VERSION`、`manifest.json` 都在仓库根一层。
克隆到 `~/.workbuddy/skills/workbuddy-daily-credit/` 之后，目录结构与仓库**完全同构**——
自更新才能在不猜安装位置的情况下对齐路径。

**不要**为了「整理」把这些挪进 `src/` 或 `skill/` 子目录，那会让 `updater.py` 里的
`ROOT` 计算全部错位。

### 2. 平台差异只准写在 `scripts/wbplatform.py`

业务逻辑（`claim.py`）必须是平台无关的。需要判断操作系统的地方，
一律走 `wbplatform` 暴露的函数。这是为了保证 Windows 支持可以被独立贡献，
不必改动业务代码。

## 提交前 checklist

- [ ] `python3 tests/test_core.py` 和 `test_updater_safety.py` 全绿
- [ ] **改了 `scripts/` 下任何文件 → 必须重跑 `python3 tools/build_manifest.py`**
      否则线上用户拿到的 manifest 哈希对不上磁盘，自更新会反复失败
- [ ] 版本号更新：改 `VERSION`，语义化版本
- [ ] 没有引入第三方依赖（这条是硬约束，零依赖是刻意选择的）
- [ ] 没有新增对用户凭据文件的读取

## 关于「零凭据」这条底线

本项目相对同类方案的核心价值就是**不读 token**。任何引入凭证读取、解密、
或构造 `authorization` 头的 PR 都会被拒绝——不是为了道德洁癖，
而是那条路会把「客户端改一次格式就连签断开」的维护负担重新背回来。

## 报告跨平台问题请附带

```
python3 scripts/claim.py --doctor
```

输出已经做了脱敏（不含 ticket 明文），可以放心贴。
