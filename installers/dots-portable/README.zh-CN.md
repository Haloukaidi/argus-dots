# Argus dots 便携模块 2.3.1

2.3.0已加入显式 `supervised-approx-v1` 与协议4，把原Web前门、daemon及角色流程绑定到在线、获授权的有限native host。严格dots默认、原九个CLI provider及Reviewer typed审批保留；选择dots不会静默回退CLI。

监督近似模式的只读、禁工具、工作目录和安全选项是指令约束，不能宣称操作系统强制隔离。必须明确接受这些限制并选用准确profile；缺少host、越界或仍不支持的选项会拒绝。安装包本身不创建native能力，不启动常驻服务。

本补丁版只修正两个Reviewer测试fixture的同步；生产代码、执行权限、超时和锁等待限制不变。原公开2.3.0的CI失败仍保留：根据日志进度符号重建为12,542 passed、119 skipped、3 failed。受控复现证实锁竞争可触发同样的取消，但原CI缺完整现场，不能将其原因当成已证实。详见 `CI-FIX-2.3.1.txt`。

## 安装或升级

先停止使用目标源码的Argus、producer和协调任务。需要Python3.11+及POSIX；部分机器命令名为python3。

```sh
python install_dots.py --argus-dir /absolute/path/to/Argus
```

兼容固定原版 `9cfe9129fd90511c3a1865844ec7dfda1b5d1008`。有Git时HEAD须精确匹配；源码快照通过731项基准文件hash识别。不是任意0.1.8版本，也不接受site-packages目录。

本包87项payload（31原文件修改、56新增），清单见 `PAYLOAD_FILES.txt`，包括本次全部执行代码及测试、相关模块文档。原基准新增管理daemon启动、daemon状态、Web diagnostics及tests/conftest.py，均保存原件再更新；新增基准hash覆盖原tests/conftest.py。

已有受管2.0.0、2.1.0、2.1.1、2.1.2、2.2.0或2.3.0：

```sh
python install_dots.py --argus-dir /absolute/path/to/Argus --check
python install_dots.py --argus-dir /absolute/path/to/Argus --upgrade
```

验证对应旧20/32/32/37/64/87文件的完整hash集合、原始备份与事务元数据；版本字符串相同不够。未知版本、用户后改、缺项或损坏拒绝覆盖，不提供force选项。旧完整8文件legacy可显式 `--adopt-legacy`，卸载恢复其原状态。

完整fork已含集成源码，无需对自身运行原版补丁安装器。仓库稳定入口为 `installers/dots-portable/`；根README.dots是完整fork入口，不覆盖到原版安装目标。

## 可选联网bootstrap

```sh
python bootstrap_argus.py --target /absolute/new/Argus --allow-network --venv --install-deps
```

仅显式开关下从官方固定仓库抓取原commit，安装模块，建立独立 `.venv-dots` 并用其pip安装本地源码/依赖。不覆盖已有目录，不改全局环境；只要源码可去掉后两个开关。依赖未完全锁定。

脚本仅保留明确proxy/CA白名单，排除模型/Git token、pip index、Git配置注入和TLS禁用开关，错误输出脱敏。脚本未变；早期Linux含空格路径联网验收是历史证据，本轮未重跑联网bootstrap。

## 运行入口与边界

- 原严格dots及协议1/2/3保留，缺少角色所需能力仍拒绝
- 协议4需显式匹配profile、project、producer、workflow、角色、调用数、并发、期限及必要外部roots；不扫描任意队列或放宽未知选项
- 在线协调者真实参与时维持短lease；配置文件、Python对象和持久journal均不能唤醒host或授予权限
- 精确模型与effort由实际工具目录确定；允许的近似映射需显式记录，实际模型未知时保持unknown
- 原Reviewer typed actions及工具参数校验继续生效；候选/证据快照用于事后检测，不是防写入的沙箱。原Docker命令隔离不降级为无沙箱执行
- 费用/token未提供，账本为空及cost为null；不声称费用控制。原前端在缺失费用时不显示虚构的零支出

先读 `NEW_DOT.zh-CN.md`，再按 `payload/docs/dots-supervised.md` 与 `payload/docs/dots-supervised-web.md` 建立有限host并启动原Web前门。有效final先record/确认消费；取消/期限优先，未知动作不重放。安装完成不等于此接收端完成了native验收。

保留原仓库跟踪的预编译TUI/Web bundle，本轮未重建。新增前端测试验证原组件对缺失usage的显示；不宣称重新构建UI发行物。

## 恢复与卸载

```sh
python install_dots.py --argus-dir /path/to/Argus --recover
python install_dots.py --argus-dir /path/to/Argus --rollback
python install_dots.py --argus-dir /path/to/Argus --uninstall
```

同版重跑幂等；check不改源码，但创建私有锁目录。每个文件原子替换，整体不是单一原子切换，操作前必须停止使用目标源码的任务。

`.argus-dots-install`以0700保存备份和事务，不要删除。普通异常自动回退，中断后recover；rollback恢复前一受管版本，可多代回退，uninstall恢复首次base或legacy。只处理受管文件，保留用户数据、venv及备份。

元数据/journal不完整、备份损坏或用户改过受管文件时拒绝。特殊文件、symlink祖先/目标和hardlink拒绝；这不是针对同UID恶意并发修改目录者的隔离保证。

## 验证范围

准确当前结果见 `VALIDATION.json`；历史2.3.0记录保存在 `VALIDATION-2.3.0.json`，历史2.2.0记录保存在 `VALIDATION-2.2.0.json`，其他旧记录继续保留。各定向测试、独审和历史回归有重叠，不能相加或冒充当前全库测试。

最早2.0.0及legacy的实际历史字节在本轮环境不可用：精确maps保留、通用fixture继续验证，没有虚报最老实包重跑。当前安装验证在Linux执行；Python3.11、macOS、Windows/WSL未重新实测，Windows不宣称通过。

标准库自检：`python -m unittest discover -s tests -v`

离线真实版本迁移复验：设置 `ARGUS_DOTS_BASE_REPO`、`ARGUS_DOTS_HISTORY_ROOT`，再运行 `python -m unittest discover -s verification -v`。需合法原Git对象及2.1.0/2.1.1/2.1.2/2.2.0/2.3.0历史包；不联网，不构造假历史hash。

MIT许可见 `LICENSE.Argus`。hash用于完整性，不是数字签名。不包含凭据、真实运行身份、raw日志或用户研究prompt。构建阶段与后续授权发布、服务启动分别记录。

安装器另修复已复现的rollback/recover日志漏项：读取journal时先验证完整受管集合、已知版本hash及前后状态指纹；不完整记录在写文件前拒绝，原正常事务/恢复控制流程不变。

2.3.0历史原Web API软件任务经过Manager→Planner→Engineer→Reviewer原typed审批并由原mission完成（8请求：5completed、3cancelled）；另一个有限scope验证原Planner同worker续接及原Curator callback，3请求均completed/consumed。这些证据分开记录，不是完整五角色研究、浏览器UI、策略内容质量或严格控制等价验收。先前失败仍保留，详见 `payload/docs/dots-supervised-native-validation.md` 与 `SUPERVISED-VALIDATION.json`。
