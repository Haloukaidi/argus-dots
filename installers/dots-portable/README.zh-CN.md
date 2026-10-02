# Argus dots 便携模块 2.2.0

本版加入有限producer接纳（协议3）及显式dots runtime路由，补齐对应CLI/Web/缓存/测试源码。旧九个CLI provider、默认值和权限门禁保留；选择dots后不会静默退回CLI。

重要边界：普通Web/CLI仍没有受支持的native接收服务，也不能满足完整研究五角色的执行控制。缺host或能力会明确unavailable/拒绝，不会靠放宽只读、工具或隔离要求启动研究。协议3需要在线、获授权的有限协调者，不是常驻后台服务。

## 安装或升级

先停止使用目标源码的Argus、producer和协调任务。需要Python3.11+及POSIX；部分机器命令名为python3。

```sh
python install_dots.py --argus-dir /absolute/path/to/Argus
```

兼容固定原版 `9cfe9129fd90511c3a1865844ec7dfda1b5d1008`。有Git时HEAD须精确匹配；源码快照通过730项基准文件hash识别。不是任意0.1.8版本，也不接受site-packages目录。

本包64项payload：27个原文件修改、37新增。原2.1.2的37项中8项更新，新增27项；全部与集成源码 `de3d72c7e01cb14200975453d3ab7ca361c38c26` 对应。新增基准校验覆盖既有TypeScript帮助源码和两个原测试文件，防止覆盖用户改动。

已有受管2.0.0、2.1.0、2.1.1或2.1.2：

```sh
python install_dots.py --argus-dir /absolute/path/to/Argus --check
python install_dots.py --argus-dir /absolute/path/to/Argus --upgrade
```

逐项验证对应旧20/32/32/37文件、原始备份与事务元数据；只有版本字符串相同不够。用户后改、未知版本、缺项或损坏均拒绝覆盖。2.2.0补上卸载前的完整状态/journal核对，避免旧实现中“缺一项记录却宣称卸载成功”的遗漏；现有事务/恢复算法不变。

完整集成仓库已经含源码修复，不要再对其自身运行原版补丁安装器。仓库内稳定入口仍是 `installers/dots-portable/`。根README.dots是完整fork专用入口，不覆盖到原版安装目标；执行所需Python、TypeScript、测试及相互链接的模块文档已纳入payload。

## 可选联网bootstrap

```sh
python bootstrap_argus.py --target /absolute/new/Argus --allow-network --venv --install-deps
```

只在显式开关下从官方固定仓库抓取原commit，安装模块，建立独立 `.venv-dots` 并用其pip安装本地源码/依赖。不覆盖已有目录、不改全局环境；只要源码可去掉后两个开关。依赖未完全锁定。

脚本仅保留明确proxy/CA白名单，排除模型/Git token、pip index、Git配置注入和TLS禁用开关，错误输出脱敏。脚本与2.1.2一致；其早期Linux含空格路径在线验收是历史证据，本轮未重跑联网bootstrap。

## 使用边界与操作入口

- `--backend dots`现为独立runtime选择，不是第十个provider CLI。模型/effort原值保留，缺能力提前拒绝
- Web请求在写transcript或调Manager前拒绝不可用dots，daemon在spawn前拒绝；配置切换会失效旧CLI warm缓存、thread和plan预览
- 宿主显式提供transport时仍须满足原五角色和逐调用能力。协议3不制造这些能力
- 协议3将同一授权producer后续产生的请求接纳到同一有限session，省去父级逐条转发；不扫描任意目录、不接纳无关请求
- 仅支持预先声明的producer/project/mission/roles、总调用数、并发和期限。结束时close-producer；持久journal不保证协调者常驻、自动唤醒或重启

具体步骤见 `NEW_DOT.zh-CN.md`、`payload/docs/dots-admission.md` 和 `payload/docs/dots-runtime-entry.md`。有效final先record并确认producer消费，测试/报告分离；取消/过期优先，不重放不确定工具动作。

frontend/core的帮助源码已包含dots；原仓库跟踪的预编译TUI/Web bundle没有重建或替换。源码帮助更新不等于已重建的UI发行物，CLI/runtime安全路由检查独立生效。

## 恢复与卸载

```sh
python install_dots.py --argus-dir /path/to/Argus --recover
python install_dots.py --argus-dir /path/to/Argus --rollback
python install_dots.py --argus-dir /path/to/Argus --uninstall
```

- 同版重跑幂等；check不改源码，但创建私有锁目录
- 每个文件原子替换，整体不是单一原子切换，必须先停止使用目标源码的任务
- `.argus-dots-install`以0700保存备份和事务，不要删除。普通异常自动回退，中断后recover
- rollback恢复前一受管版本，可多代回退；uninstall恢复首次base或legacy，只处理受管文件，保留用户状态、venv和备份
- 元数据/journal不完整或用户改过受管文件时拒绝；没有force选项。特殊文件、symlink祖先/目标和hardlink拒绝，同UID恶意并发修改不属隔离保证

最初8文件legacy只有完整已知快照可显式 `--adopt-legacy`，卸载恢复其原状态。

## 验证范围

当前准确结果见 `VALIDATION.json`、`RUNTIME-ADMISSION-VALIDATION.json`。安装器标准库检查、真实历史版本迁移、安装后源代码检查分别列示，不能把重叠子集相加。最早2.0.0及legacy的实际历史字节在本轮环境不可用：精确maps保留、通用fixture继续验证，但没有虚报最老实包重跑。

现有真实协议3验收仅一个native worker的一次spawn及一次followup，两个原gateway都exit0/consumed，未登记请求未触碰。它不证明完整研究、原生只读/工具隔离、真实取消竞争、崩溃恢复、用量/费用或常驻服务。完整报告在 `payload/docs/dots-admission-native-validation.md`。

旧四场景失败、focused取消INCONCLUSIVE及≤30秒未证实均保留历史；background自由文本hex路径仍已知未修。新精确发布commit的CI另验。实测Linux/Python3.12，未进行新native试验、跨平台安装或真实UI/provider生产运行。

标准库自检：`python -m unittest discover -s tests -v`

离线真实版本迁移复验（需原Git对象和合法历史包）：设置 `ARGUS_DOTS_BASE_REPO`、`ARGUS_DOTS_HISTORY_ROOT`，再运行 `python -m unittest discover -s verification -v`。历史目录结构及来源见该文件头部；不联网、不构造假历史hash。

MIT许可见 `LICENSE.Argus`。hash用于完整性，不是数字签名。未打包凭据、私有运行身份、raw日志或用户研究prompt；本包构建不上传、不发布、不重启服务。
