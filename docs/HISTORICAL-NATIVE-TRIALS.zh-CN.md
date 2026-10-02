# 历史四场景真实试跑结果

旧批次：4个场景，共10个真实native turn。以下失败不会被后续focused成功覆盖；不与新批次合并计算成功率。

## A：发布清单导入

真实实现通过25/25固定测试，Reviewer实际读取文件且原typed审批成功；但共享Reviewer final未及时记录，producer超时exit124。功能通过，端到端审查确认交接失败。

## B：两轮TTL/LRU修复

同一真实worker续接，第一轮7/7、最终16/16通过；下一轮实际读取前轮文件，最终文件也被Reviewer读取。与A共享的final确认超时仍为失败，不能写成完整工作流通过。

## C：独立事故分析与合并

初次日志任务因平台容量失败；经授权仅一次新请求重试后，数值、来源引用与合并内容核对通过。原校验器假定引用为flat list，而任务未规定容器形状、模型返回grouped mapping，因此原接口断言失败保留。成功分析同时并行的证据不足；不把容量或校验规范问题称为Argus代码缺陷。

## D：取消与恢复

真实运行任务被interrupt并确认interrupted，gateway exit130且confirmed；通过新请求、新worker恢复，返回6条有据发现，gateway exit0。取消及恢复通过。这不使另一focused批次未命中的取消干扰自动变成通过。

本批全部请求已终态，无待处理工具；该批试跑没有修改Argus源码。完整生产Reviewer OS隔离、完整五角色流仍未验证。后续focused结果单列在 `FOCUSED-HANDOFF-RESULTS.zh-CN.md`，不能抹去A/B确认超时或C的原接口断言失败。
