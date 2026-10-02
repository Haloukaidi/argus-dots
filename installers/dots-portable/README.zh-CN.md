# Argus dots 便携模块 2.1.2

本版修复Reviewer交接中合法hex文件路径被清理的问题，并补充完成结果优先记录的操作手册。37项payload包含相对原版的10个文件修改和27个新增文件；旧2.1.1的32项逐字节保留。

安装程序只使用Python标准库，不联网。它不能增加Dot原本没有的native工具，也不能自动变成无人值守的Argus provider。

## 已有固定原版Argus：一条命令

先停止所有使用这个源码目录的Argus、producer及协调任务。需要Python 3.11+，部分机器命令名为python3。

```sh
python install_dots.py --argus-dir /absolute/path/to/Argus
```

仅支持官方原基准 `9cfe9129fd90511c3a1865844ec7dfda1b5d1008`，不是任意同名0.1.8版本。有Git元数据时HEAD须精确匹配；无.git源码快照以727项基准Python/配置/schema哈希识别。全部37项payload和目标路径在写入前检查。

原core或受管文件有用户改动时拒绝覆盖，不擅自合并。默认provider、凭据、全局插件和用户运行数据不变。成功JSON中的 `self_check=hashes_and_python_syntax` 只表示源码自检；`native_tools=not_verified` 不代表新机器的native运行已验证。

如果拿到完整集成的argus-dots仓库，其源码已经包含修复；不要对该仓库自身再次运行此固定原版补丁安装器。仓库内安装器入口始终是 `installers/dots-portable/`，ZIP目录才带版本号。

## 已装portable 2.0.0 / 2.1.0 / 2.1.1：显式升级

```sh
python install_dots.py --argus-dir /absolute/path/to/Argus --upgrade
```

核对相应旧版的完整20或32文件清单、已知哈希、实际文件、原始备份和事务记录。任一后改、缺项或损坏都在写入前拒绝。只更改版本字符串不能绕过验证。普通安装命令不会擅自升级；可先用 `--check` 预检。

`--rollback` 恢复这次升级前的确切版本，多代升级可沿记录继续回滚。`--uninstall` 恢复首次安装前的base或8文件legacy状态。新修改的两个原版文件也会备份和恢复。未知手工集成、缺少合法安装元数据的完整快照不会被自动接管；保留该目录，另用固定干净基准。

## 没有Argus：可选联网bootstrap

```sh
python bootstrap_argus.py --target /absolute/new/Argus --allow-network --venv --install-deps
```

显式从官方固定仓库抓取并验证原commit，在新目录安装本模块，建立独立 `.venv-dots`，再由该venv的pip安装本地源码及依赖。不覆盖已有目录，不改全局环境。只要源码时去掉后两个开关；只建空venv时保留 `--venv`。需要系统git。

仅继承明确允许的既有proxy/CA网络配置；模型/Git token、pip index、Git配置注入及关闭TLS验证开关仍排除。实际网络配置不打包；错误输出会过滤配置值和proxy认证片段。上游传递依赖未全部锁定，不能宣称可重复构建。

该bootstrap脚本与2.1.0网络修订逐字节相同；当时在Linux含空格新路径实际完成固定commit、venv、pip依赖安装，pip check、目录外导入和bridge帮助检查通过。历史在线验收不是本版新locator代码的完整生产运行证明。

## 交给新Dot

一并交给它 `NEW_DOT.zh-CN.md`，明确本次有限任务、允许的文件/工具、外部动作及停止条件。接收端从其当前实际工具清单写出JSON字符串数组后运行：

```sh
python check_host.py --tools-file /path/to/actual-current-tools.json
```

缺少真实创建/续接/状态/打断/命令执行工具时非零退出。清单检查不等于执行证明，每个新host仍需获授权的真实有限probe。不存在 `argus --backend dots` 开关；使用原源码factory及bridge/host协议。

2.1.2手册要求专用协调者先记录有效当前final并确认producer消费，测试/报告分析另做；按最早deadline处理。审批ready不等于最终交付，不改变权限、timeout、取消或过期语义，也不承诺自动实时延迟。见 `HOST-PRIORITY.zh-CN.md`。

## 恢复与卸载

```sh
python install_dots.py --argus-dir /path/to/Argus --check
python install_dots.py --argus-dir /path/to/Argus --recover
python install_dots.py --argus-dir /path/to/Argus --rollback
python install_dots.py --argus-dir /path/to/Argus --uninstall
```

- 同版本重复安装幂等；check不改源码，但会创建私有锁目录
- 文件分别原子替换，整棵源码不是单一原子切换，操作前必须停止使用该目录的任务
- `.argus-dots-install` 保存0700权限的备份和事务；普通异常自动回退，中断后用recover。不要删除备份
- 恢复仅作用于受管文件，保留备份、venv和用户状态；用户后改冲突时拒绝，没有force开关
- 拒绝symlink祖先/目标、hardlink及特殊受管文件；遵循普通单用户信任边界，不防同UID恶意并发重写

最初8文件源码补丁，仅完整已知快照可显式迁入：

```sh
python install_dots.py --argus-dir /path/to/Argus --adopt-legacy
```

此时升级为37文件；卸载恢复迁入前的8文件状态，不把它误当成本安装创建的数据删除。

## 验证与明确限制

- 新locator相关回归：620 passed、14显式Docker skips、0失败/错误；独审76通过，计数重叠不相加，详见 `LOCATOR-VALIDATION.json`
- 真实focused：两个文件实际读到、原typed审批恰1次、两个producer exit0且consumed，本次正常完成交付通过
- 取消干扰因另一任务先自然结束而INCONCLUSIVE；缺独立arrival戳，50.918秒保守上界不能证明≤30秒目标；距deadline仅7.532秒。旧10-turn漏record超时FAIL仍保留；旧四场景及其接口断言/容量限制见 `HISTORICAL-NATIVE-TRIALS.zh-CN.md`，新批次见 `FOCUSED-HANDOFF-RESULTS.zh-CN.md`
- background_context自由文本hex路径仍已知未修，不开放未授权roots；完整生产Reviewer OS只读隔离和全五角色native运行仍未验
- 安装器47项标准库测试、14项独审与真实三代回滚/卸载通过，见 `VALIDATION.json`；历史2.1.0全库12086 passed / 68 failed / 136 skipped及30项既有Python类型诊断不改写成新版本全绿
- 2.1.1已发布commit `aa619ebd6ff158c4bda4554c70a2ad96f251aef9` 的CI成功属于旧版本；历史报告里的pending是当时快照。2.1.2精确新commit CI仍须另验
- 实测Linux/Python3.12。安装器Python3.11运行、macOS和WSL未在本轮验证；原生Windows拒绝
- 无打包venv、凭据、raw运行日志、实际worker IDs或用户研究prompt；本离线包构建不执行远端发布或服务重启

标准库自检：`python -m unittest discover -s tests -v`

固定上游MIT许可见 `LICENSE.Argus`。SHA256用于完整性校验，不是发布者数字签名，请从可信发送方取得完整包。
