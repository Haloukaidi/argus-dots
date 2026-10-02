# Argus dots 便携模块 2.1.0

分发包修订：2026-10-02 bootstrap 网络兼容修复。受管模块版本仍为 2.1.0，32 个业务 payload 与升级链不变；已安装的 2.1.0 不需要重新迁移，重复安装仍幂等。

把这份 ZIP 解压后交给另一台机器上的 Dot，就能复用同一套模块，不需要重写适配器。安装程序只使用 Python 标准库，不联网；专用协调子 agent 由接收端 Dot 在有真实工具、有限任务授权时启动。

## 已有 Argus：一条命令

先停止这个目录中正在运行的 Argus、producer 和协调任务。请使用 Python 3.11+；部分机器命令名是 `python3`。

```sh
python install_dots.py --argus-dir /absolute/path/to/Argus
```

目标必须是源码目录，兼容官方 `lbx154/Argus` 的固定基准：
`9cfe9129fd90511c3a1865844ec7dfda1b5d1008`。仅版本号 `0.1.8` 相同不够；若目录带 Git 元数据，还须 HEAD 精确指向该 commit（支持 detached、refs 和 worktree）。

安装前验证 727 个基准 Python/配置/schema 文件的 SHA256，拒绝未知新增 core Python 文件，并检查全部 32 个 payload 文件。本版本替换 8 个原文件、增加 24 个文件（包括已授权的原版工作流修复和回归测试），默认 provider、凭据、全局插件、用户任务和运行状态不变。已有源码里的非目标文档/运行数据可以保留；基准 core 有用户改动时安全拒绝，不擅自合并或覆盖。无 `.git` 的固定源码快照也可用。

成功 JSON 的 `self_check=hashes_and_python_syntax` 仅代表本地源码哈希/语法通过。`native_tools=not_verified` 表示没有验证这台机器/这个 Dot 的 native 能力，不能解释成完整 Argus 五角色生产流程已可运行。

运行依赖已经在当前 Argus 环境中时，继续使用原解释器。模块不默认创建 venv、不运行模型 setup/doctor，也不安装常驻服务。

## 已装 portable 2.0.0：显式升级

```sh
python install_dots.py --argus-dir /absolute/path/to/Argus --upgrade
```

先停止使用此源码目录的所有任务。2.1.0 会核对旧版完整 20 文件清单/已知哈希、实际文件、原始备份及事务元数据；任一用户后改、缺项或损坏都会在写入前拒绝。只改 release 字符串不能通过升级。`--check` 可先做不修改源码的升级预检；普通安装命令不会擅自升级已有版本。

这次新增 6 个原版修复文件纳入同一备份/恢复事务，实际源码和 5 个新回归测试一起交付，详见 `CHANGES-2.1.0.zh-CN.md`。旧 2.0.0 ZIP 仍可作为历史版本保留。

升级后，用本 2.1.0 安装器的 `--rollback` 恢复完整 2.0.0；`--uninstall` 恢复首次安装之前的原基准。若首次是从最初 8 文件补丁迁入，则卸载恢复那个旧补丁状态。新增修复文件也逐一恢复。保留两代备份，不影响 venv 和用户运行数据。

手工拷过完整 20 文件却没有合法安装元数据的目录不属于可升级安装；保留原目录，另用固定干净基准。不会把未知手工安装自动登记为可信旧版。

## 没有 Argus：可选联网 bootstrap

```sh
python bootstrap_argus.py --target /absolute/new/Argus --allow-network --venv --install-deps
```

这一步明确联网：从固定官方 GitHub 仓库抓取并验证上面的 commit，在新目录装 dots，建立该目录独立 `.venv-dots`，再由该 venv 的 pip 安装本地 Argus 源码及其声明的依赖。不覆盖已有目录，不读已有 Git 凭据，不改全局 Python/插件配置。仅继承明确允许的既有 proxy/CA 网络配置，保证当前平台路由和信任根可用；不继承模型 API key、Git token、pip index、Git 配置注入或关闭 TLS 验证的开关。proxy/CA 配置值不写入分发包；安装器不主动记录这些值，错误输出会过滤配置值及 proxy 认证片段。依赖可能下载并执行标准构建流程；Argus 上游依赖没有完整锁定，不能宣称传递依赖可重复构建。

只要源码时去掉 `--venv --install-deps`；只建空 venv 时保留 `--venv`、去掉 `--install-deps`。需要系统 `git`。bootstrap 的真实在线 GitHub/pip 路径已在本轮 Linux 验收执行：含空格的新目录、固定来源/commit、独立 venv 和依赖安装完成，随后通过 pip check、目录外导入和 bridge 帮助命令；精确记录见 `VALIDATION.json`。缺少显式网络开关仍会拒绝。下载受限时，不改来源，可先取得相同固定源码后用离线安装命令。

## 交给新 Dot

把 `NEW_DOT.zh-CN.md` 与 ZIP 一起交给它，并明确本次要完成的任务、允许的文件/工具、停止条件。手册包含有限协调任务、续接、typed-tool 回包、取消和恢复流程。

新 Dot 必须读取它当前实际工具清单，写出 JSON 字符串数组后检查：

```sh
python check_host.py --tools-file /path/to/actual-current-tools.json
```

缺少真实 spawn/followup/status/interrupt/命令执行工具就非零退出。这个清单检查不能证明工具实际执行成功；每台新主机仍须做一次获授权的真实有限 probe。模块不能给 Dot 增加原本没有的 native 工具，更不能用 Python 伪造它们。

普通 Argus CLI 没有 `--backend dots`。运行集成使用公开源码工厂 `build_dots_life_runner` 或文档中的 bridge/host 命令。完整五角色生产运行仍受接收 host 实际能力限制，尤其 Reviewer 的只读隔离、工具约束和 provider 用量/预算控制并未由此包补齐。

## 检查、重复安装、恢复与移除

```sh
python install_dots.py --argus-dir /path/to/Argus --check
python install_dots.py --argus-dir /path/to/Argus --recover
python install_dots.py --argus-dir /path/to/Argus --rollback
python install_dots.py --argus-dir /path/to/Argus --uninstall
```

- 同版本重复执行是幂等检查，不重复覆盖
- `--check` 不改源码，但会创建私有安装锁目录
- 不可识别版本/本地 core 改动/目标文件冲突在全量预检阶段拒绝
- 文件分别原子替换，整棵源码不是单一原子切换，因此必须先停止使用该目录的进程
- 备份和事务写在目标 `.argus-dots-install`，权限 0700。普通异常自动回退；进程/断电中断后执行 `--recover`。不要删备份
- `--rollback` 回到最近一次安装或升级之前；`--uninstall` 回到第一次受管安装之前。只还原本安装管理的文件，保留备份、独立 venv、用户状态和额外文件
- 安装后用户改过受管文件，恢复/回滚/卸载会拒绝冲突，不强制清理；先自行保存差异、恢复对应受管版本，再重试。不存在 `--force`
- 拒绝目标、备份或祖先路径 symlink，拒绝受管文件 hardlink/特殊文件。遵循普通本地单用户信任边界，不能抵御同 UID 恶意进程并发替换文件
- 新版本只有在 manifest 明列旧受管版本时才可升级；不会拿任意新 payload 覆盖旧安装

### 已安装最初 8 文件源码补丁

仅当 8 个旧快照全部逐字节匹配时允许显式迁移：

```sh
python install_dots.py --argus-dir /path/to/Argus --adopt-legacy
```

这会备份旧 8 文件安装并升级到当前 32 文件。此时卸载/回滚恢复原先的 8 文件安装，而非把它误当成本安装创建的数据删除。任何旧文件后改都会拒绝。手工装过其他完整版本且没有安装记录的目录也拒绝；保留该目录，另用干净基准。

## 验证范围与平台

- installer/host check：47 项标准库测试通过；原升级/安全验证 61 项通过，本次网络白名单/脱敏独审另有 23 项通过，见 `VALIDATION.json`
- 全库 Python：12086 passed、68 failed、136 skipped、0 errors（12290 项，857.84 秒），没有提前停止。失败为59项 procfs 环境要求、8项 AF_UNIX 被拒、1项已有 venv 导致的原测试前提；最后一项原测试在无 venv 干净树独立复跑1/1通过，不改写全库计数
- TypeScript npm check 的构建、类型检查和226项测试通过；Python类型检查仍有与原基准完全相同的30项诊断，不能称为全绿。完整统计/跳过原因见 `SOURCE_VALIDATION.json` 和 `payload/docs/workflow-validation-2026-10-02.zh-CN.md`
- 真正 native 有限协调、同角色两轮续接、dummy typed action/reply 已在原验收环境成功；完整五角色/生产 Reviewer 隔离未验收
- 安装器本地实测 Linux + Python 3.12。POSIX 设计适用于 Linux/macOS，但本包没有 macOS 实机结果
- 原生 Windows 明确拒绝。WSL 可作为待验证的 POSIX 路径，尚未实测，不宣称通过
- 无打包 venv、凭据、运行会话或实际 worker IDs；本次离线包验收不包含远端发布步骤，另行发布状态以实际交付说明为准

离线运行安装器测试：`python -m unittest discover -s tests -v`

上游 Argus 使用 MIT 许可证，见 `LICENSE.Argus`。本包的 manifest/SHA256 用于完整性检查，不是可信发布者数字签名；应从你信任的发送方取得整个 ZIP。
