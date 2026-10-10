# CHANGELOG

格式参考 [Keep a Changelog](https://keepachangelog.com/)，版本号遵循语义化版本。

## [2.0.0] — 2026-10-10

首个开源版本。从 macOS-only 的一次性脚本，重构为跨平台、可自进化的 Skill。

### Added
- **跨平台支持**：新增 `scripts/wbplatform.py` 平台适配层，把全部 OS 差异收敛到单个文件
  - IPC 传输：Unix Domain Socket（macOS/Linux）↔ Named Pipe / AF_UNIX 自动探测（Windows）
  - 定时持久化：launchd（macOS）↔ schtasks（Windows）↔ crontab（Linux）
  - 进程检测、主程拉起、解释器发现均按平台分派
- **自进化更新**：新增 `scripts/updater.py`
  - 比对上游 `manifest.json` 的 sha256 决定是否升级
  - 七道安全闸：HTTPS / 仓库 pinning / 逐文件哈希 / 路径白名单 / 原子替换 / 备份回滚 / 可完全关闭
  - 24 小时节流，可通过 `WORKBUDDY_DAILY_CREDIT_NO_UPDATE=1` 或 `--no-self-update` 关闭
- **统一安装器**：新增 `scripts/install.py`，取代原先 macOS-only 的 `install.sh`
- **环境自检**：新增 `--doctor`，输出平台、解释器选择、端点探测、定时状态（已脱敏）
- **端到端探测**：`read_endpoint()` 扩展为多候选路径搜索 + 临时目录兜底扫描
- 新增 `tools/build_manifest.py`，由 CI 生成 manifest，并支持 `--check` 校验同步
- 新增 21 个测试用例，其中 11 个专测自更新的路径穿越防护
- 新增 CI：macOS / Windows / Ubuntu × Python 3.11 / 3.13 矩阵

### Changed
- **仓库布局**：`skill 目录 == 仓库根目录`。原先 `skill/scripts/` 下移一层，
  使 clone 到 `~/.workbuddy/skills/<name>` 后两边结构完全同构，自更新无需猜测安装位置
- `IpcClient` 改走 `wbplatform.connect_ipc()`，业务逻辑与传输实现解耦
- 握手协议保持逐字节兼容（同一 ticket、同一 transcript 构造），
  `client.version` 由 `1.0` 升为 `2.0.0`
- launchd plist 增加 `EnvironmentVariables.PYTHONUTF8=1`，
  避免 launchd 干净环境下中文汇报抛 `UnicodeEncodeError`

### Fixed
- **进程误判**：`pgrep -x WorkBuddy` 精确匹配拿不到 Electron 主进程，
  导致主进程在运行也被判定为未运行并触发多余的拉起。
  改为精确优先、`pgrep -f WorkBuddy.app` 模糊兜底（模糊串带 `.app`
  是为了避免误命中命令行里含本 skill 路径的 Python 进程）
- **解释器选择**：不再无条件硬编码 `/usr/bin/python3`。
  macOS 上未装 Xcode CLT 时它是个占位壳，放进 launchd 会静默失败。
  改为优先使用当前活动的 `sys.executable`，逐级回退
- `claim.py` 中一处 log 格式化参数顺序错误（`log("...%s", quiet)` 漏了取模）

### Known limitations
- Windows / Linux 分支**尚未在真机验证**。详见 README 的「平台支持与验证状态」一节
- 功能面只覆盖每日签到，不含「成长中心」的旅行礼物 / 派 Buddy / 开盲盒等玩法

## [1.0.0] — 2026-10-10

初始内部版本，macOS-only。
通过 wbipc IPC 通道代理签到请求，实现幂等领取与 launchd 每日 4 时点 + 登录补跑。
