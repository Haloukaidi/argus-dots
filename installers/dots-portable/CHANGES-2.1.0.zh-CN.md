# portable 2.1.0 变更

固定基准仍是 `9cfe9129fd90511c3a1865844ec7dfda1b5d1008`。完整32文件清单见 `PAYLOAD_FILES.txt`。本次实际包含下列已授权源码修复；原任务职责、默认CLI provider和权限边界不变。

- Git项目/路径：Git错误显式返回；处理特殊路径时保持字节保真；不误改相邻仓库；原子状态更新及损坏/故障恢复，不放宽Git安全检查。涉及 `argus/core/campaign_workdir.py`、`argus/core/project.py`、`argus/skills/round_checkpoint.py`
- Reviewer：并行执行传播现有context，保留停止信号与验证行为。涉及 `argus/reviewer/_core.py`
- 工具桥：受限控制通道为取消保留容量，业务认证/并发上限不放宽。涉及 `argus/core/role_tool_bridge.py`
- dots模型默认：非CLI角色调用不再错误进入CLI模型解析；只规范框架空字符串默认值，仍拒绝不支持的模型请求；保持原有9个CLI行为。涉及 `argus/core/knobs.py`、`argus/adapters/dots_backend.py`
- 增加5个工作流回归文件，覆盖角色生命周期、模型默认、Git路径故障、Reviewer故障及已知环境/测试前提差异
- 安装器升级保护：旧版只按版本名称准入升级不足以确认完整已知快照；本版新增旧20文件哈希、原始备份、元数据一致性校验和显式 `--upgrade`，不改既有事务/取消/恢复语义

测试结果必须按最终报告解读。环境受限检查和保留的原测试失败会如实列出；不能把局部通过、mock、原生dummy工具测试或安装成功称为完整生产五角色通过。
