# 能力与验收矩阵（portable 2.1.2）

| 范围 | 当前证据 | 限制 |
|---|---|---|
| Reviewer locator | 两生产文件修复；summary实际文件、固定source-cache目录及合法显式state-root日志定位符保留 | 原全局清理/凭据保护/权限门禁不变；background自由文本hex路径仍未修 |
| 相关回归 | 37测试文件634项：620通过、14显式Docker跳过；独审76通过 | 子集重叠；不是新commit全库或容器隔离通过 |
| 本次真实交接 | 实际读取两个32-hex目录内文件，原typed审批恰1次，两个producer exit0且consumed | 仅本次正常完成交付通过，不是全角色生产OS隔离 |
| 取消干扰 | 另一任务在action-ready前自然完成 | INCONCLUSIVE，未执行stop/interrupt，不能标为取消竞争通过 |
| final交付延迟 | prepared→record保守上界50.918秒，消费距deadline7.532秒 | 无独立arrival戳，≤30秒目标未证明；没有自动实时保证 |
| 旧批次失败 | 原10-turn中审批后漏record导致超时 | FAIL保留，新成功不覆盖历史 |
| Host操作 | 专用协调者先record有效final并确认消费，报告分析分离、最早deadline优先 | 只改手册，不加权限、不延timeout、不重发不确定action |
| 迁移与恢复 | 37payload，原32完整保留；2.0.0旧20、2.1.0旧32、2.1.1旧32精确映射及legacy8 | 用户后改/未知版本/备份异常拒绝，无force |
| 安装/启动 | 固定727基准；三入口脚本和事务保持；历史Linux在线bootstrap已通过 | 新host工具必须实际存在，安装不能授予native能力 |
| 默认角色/provider | Argus原五角色职责、typed dispatcher和原9CLI语义保留 | 不注册不存在的dots CLI，不静默切provider |
| 用量与运行控制 | 未知用量继续unknown；会话有限，不安装daemon | 未提供完整provider费用控制或长期自动接收服务 |

本版相对原Argus是10个原文件修改、27个新增文件，合计37payload。相对2.1.1仅增加授权两生产快照和三测试，原32项不变。

当前检查详见 `VALIDATION.json`、`LOCATOR-VALIDATION.json` 和 `FOCUSED-HANDOFF-RESULTS.zh-CN.md`。安装47项标准库测试、14项迁移独审与真实三代回滚CLI结果单列，不与业务子集累加。

历史2.1.0完整Python运行12086 passed / 68 failed / 136 skipped / 0 errors，Python类型检查仍30项既有诊断；历史2.1.1纯测试回归及旧CI均不当作2.1.2全库通过。此前native协调、续接及dummy typed-action证据也有各自明确范围。

源码locator修复、文件读取、typed审批、最终消费是不同事实。OS只读隔离、未授权roots、跨平台/跨环境传输和无法观测的到达时间都不由成功文件路径自动证明。
