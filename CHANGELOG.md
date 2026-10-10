# CHANGELOG

格式参考 [Keep a Changelog](https://keepachangelog.com/)，版本号遵循语义化版本。

## [2.0.4] — 2026-10-10

### Fixed
- **修复 Windows CRLF 导致自更新永远更新不完**（三个平台 bug 里唯一真的影响功能的那个）
  Git 在 Windows 上默认 `core.autocrlf=true`，checkout 时把 LF 换成 CRLF → 文件字节变
  → sha256 变 → manifest 判定「全部 7 个文件都不符」。后果不只是 CI 变红，
  而是真实用户场景里每次自更新都会以为有新版本，下载后本地仍是 CRLF、
  哈希对不上，陷入永远更新不完的循环。
  新增 `.gitattributes` 把文本文件钉死 `eol=lf`。

## [2.0.3] — 2026-10-10

### Fixed
- `tools/build_manifest.py` 同样补上 Windows UTF-8 处理。
  上一版只修了 `scripts/` 下三个脚本，漏了独立运行的 tools 脚本——
  它在 CI 的 `manifest --check` 步骤里打印中文又一次触发同一个 UnicodeEncodeError
- `.github/workflows/ci.yml` 增加全局 `PYTHONUTF8: "1"` 作为 CI 侧兜底，
  避免今后新增的任何工具脚本再次掉进同一个坑

> 这三次连续修复（2.0.1 → 2.0.3）全部由 GitHub Actions 的 `windows-latest`
> runner 暴露。这是把 CI 矩阵覆盖到 Windows 的直接收益——
> 如果只在 macOS 上验证，这三个问题会一直留给第一位 Windows 用户去撞。

## [2.0.2] — 2026-10-10

### Fixed
- **修复 Windows 上输出中文直接崩溃**（真正的跨平台 bug，由 GitHub Actions
  `windows-latest` runner 首次暴露）：
  Windows 控制台默认走系统 ANSI 代码页（简中环境 cp936、CI 上 cp1252），
  打印中文抛 `UnicodeEncodeError: 'charmap' codec can't encode characters`。
  本项目汇报文案全是中文，因此这台算性问题会让 Windows 上连 `--doctor` 都跑不完。
  在 `wbplatform.ensure_utf8_streams()` 中统一强制 UTF-8，
  并在 `claim.py` / `install.py` / `updater.py` 的入口最早处调用。
  （macOS 分支里同类问题靠 launchd plist 的 `PYTHONUTF8=1` 环境变量解决，
  但那只覆盖了定时任务，覆盖不到命令行直接执行，所以这次改为在代码层根治）

## [2.0.1] — 2026-10-10

### Fixed
- 修正 `tests/test_core.py` 中 `test_doctor_smoke` 在 Windows CI 上的误报。
  原断言检查「输出全文不含 ticket 字样」，但 Windows 分支的说明文本 `windows_note`
  里恰好包含 "...endpoint.json + ticket 结构..." 这一句解释性文字。
  判据改为「ticket 不得作为**键名**出现」，并补充检查 socket 路径必须抽象化。
  （这是测试自身的问题，平台层代码在 Windows 上工作正常）
- 顺带消除一处 `ResourceWarning: unclosed file`

> 值得记录的是：这条误报恰恰带来了好消息 —— Windows runner 上的 `doctor()`
> 已验证能正确识别 `platform=windows`、选出存在的 `python.exe`、
> 生成 3 个 Windows 候选路径，并能正常调用 `schtasks /Query`。
> 平台层在 Windows 上不崩，剩下的只是「能不能真的连上 IPC」这个真机问题。

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
