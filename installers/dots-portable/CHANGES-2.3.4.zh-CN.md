# 2.3.4：Manager阶段产物上下文修复

原Manager的阶段决策使用state目录构建上下文；当真实执行工作目录分离时，即使RESEARCH_NOTES等原产物存在，也可能未进入阶段上下文。本版将既有execution_workdir作为现有altitude_root参数传递，使产物从执行工作目录读取。

状态、checklist和阶段权限仍以原state目录为准；同目录布局与省略参数的旧调用保持原行为。不新增搜索根、工具、权限、自动重试或期限，不修改运行中的任务。

相对已发布2.3.3，仅改动两项生产文件与两个测试模块：

- argus/manager/_stage_ops.py
- argus/roles/prompts/manager.py
- tests/manager/test_stage_decider.py
- tests/roles/test_prompt_catalog.py

便携payload从91增至95项，旧91项字节不变。四项本来都是原版文件，安装前验证并保存原件；基准清单从733增至735项，新增两个原测试的hash。显式升级验证完整旧2.3.3的91文件与元数据，同时保留全部更早版本映射。rollback恢复前一版本；uninstall恢复首次原版或已接纳legacy原状，用户后改及损坏元数据拒绝覆盖。

源码定向回归1037通过、4跳过，独立改动模块66通过，范围有重叠，不能相加。准确环境与包安装结果见STAGE-ARTIFACT-VALIDATION.json和VALIDATION.json；使用已有Python3.12环境及额外可用NumPy依赖路径，未修改默认service venv。不是全库或新native研究完成验收；新公开commit的CI须另行验证。旧CI和native记录保留，不作为当前全量验证。
