# Argus dots 集成完整版

这个仓库包含原 Argus 项目的完整源码、dots 适配器，以及 2026-10-02 全流程测试后完成的局部修复。原始来源基准是 `9cfe9129fd90511c3a1865844ec7dfda1b5d1008`；保留原项目的 MIT `LICENSE` 和作者信息。

## 从哪里开始

- 最新 Reviewer 文件定位符修复：[`2.1.2 变更与边界`](docs/CHANGES-2.1.2.zh-CN.md)
- 真实交接记录：[`专项结果`](docs/FOCUSED-HANDOFF-RESULTS.zh-CN.md)、[`保留的历史失败`](docs/HISTORICAL-NATIVE-TRIALS.zh-CN.md)
- Native host 完成交付操作：[`完成确认与停止优先级`](docs/HOST-PRIORITY.zh-CN.md)

- 本 fork 的独立环境安装：[`README.zh-CN.md` 顶部 dots 入口](README.zh-CN.md)；其下原版安装章节用于原 Argus，不会安装本 fork
- dots 适配器入口和能力约束：`docs/dots-backend.md`
- 有限 host 协调及续接：`docs/dots-coordinator.md`、`docs/dots-role-host.md`
- 本轮修复、验证结果和未通过项：`docs/workflow-validation-2026-10-02.zh-CN.md`
- 可携带到另一环境的安装器与新 Dot 手册：`installers/dots-portable/`

## 本仓库已包含集成

克隆这个仓库得到的源码本身已经包含修复和 dots，无需再对它运行 portable installer。

portable installer 用于其 manifest 指定的原版 Argus 基准，或从受管的旧版安装升级。它会按设计拒绝未知 Git HEAD 和手工集成状态；不要为了强行安装而删除这些保护。

## 能力与测试边界

这是显式注入 host transport 的源码级集成。原来的九个 CLI 后端和使用方式保留，没有新增 `argus --backend dots` 选项。

需要在线授权 host 才能调用真实 native 工作者。文件队列和提示词不能提供操作系统只读隔离；完整 native 五角色生产流程仍取决于接收环境真正提供所需能力。原 Reviewer 审批权、工具参数校验、停止机制和能力不足时的明确拒绝保留。

本轮完整回归使用真实 Argus 流程和受控离线后端，另有真实本地 Git、文件、线程和 TCP loopback 测试。请阅读验证报告中的失败、跳过和平台限制，不要把 fixture、Windows 模拟或有限 native 探针当作完整 provider 验收。

## 目录卫生

这里不包含开发虚拟环境、node_modules、测试运行日志、用户运行状态或模型权重。原项目已经跟踪的前端运行 bundle、测试 fixture、文档和资源保留，以免损坏原有功能。安装器目录带有自身 manifest 和验证说明。
