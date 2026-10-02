# 2.2.0：有限接纳与明确runtime路由

集成源码：`de3d72c7e01cb14200975453d3ab7ca361c38c26`，tree `7305280e5322595511f4d95cb8f0ad490680cfe5`。原版安装基准不变，仍为 `9cfe9129fd90511c3a1865844ec7dfda1b5d1008`。

## 源码内容

- 新协议3：授权producer的后续依赖调用在同一有限session接纳，无父级逐条转发；仍由在线native协调者执行实际spawn/followup
- producer/project/mission/role/容量/期限绑定；先持久保留owner再发布；跨session操作、旧协议接管和无关请求拒绝
- 显式dots是runtime，不是CLI provider；缺host/原角色能力提前拒绝，无静默CLI fallback
- Web/daemon/Manager/knob配置入口保持一致，切换配置清理旧warm client、thread及plan缓存；模型/effort不被擦掉
- 保留9个原CLI、原角色流程、审批、取消和能力门禁。协议3不是完整研究支持，不提供永久监听或自动唤醒

## 打包完整性

64payload＝旧37中8更新＋27新增；相对原版27原件修改、37新增。新增Python、前端帮助源码、现有测试变更及模块操作文档均在内；补上admission文档依赖的HOST-PRIORITY。完整fork README保留外层，避免把fork专用安装链接错误覆盖到原版目标。

base校验从727扩为730，新增3个实际会被修改的原件hash：`frontend/core/src/commands.ts`、`tests/apps/test_cli_parser.py`、`tests/test_architecture_invariants.py`。旧727值、旧版本完整maps与legacy不变。

新版本完整映射旧2.1.2的37项，继续兼容2.1.1/2.1.0的32项和2.0.0的20项。原字节、模式与原始备份跨版本保存；未知/后改冲突拒绝。预编译前端bundle未重建，不将源码帮助变化宣传为已重建UI发行物。

## 安装器缺陷修复

独审复现：从active state.json删一项记录后，旧uninstall可能报告成功并留下未恢复文件。仅增加卸载前完整已知文件集/hash与原安装journal.after_state核对；active空状态、部分状态、伪造或矛盾记录均在写源文件前拒绝。标准空状态仍幂等。事务与recover/rollback算法未改。

## 证据和限制

验收数量/源范围见当前 `VALIDATION.json` 与 `RUNTIME-ADMISSION-VALIDATION.json`。集成广回归2805 passed/8 skipped；其6个Node接口跳过在补建后独立6/6通过，剩2个Windows junction未跑，不能改写原8 skips或累加重叠子集。真实两轮仅为受限transport/续接证据。旧PID文件竞态的首次失败保留，未改旧测试；后续独立和广回归通过。

本轮不做新native试验、不上传Library、不发布GitHub或启动研究。完整五角色native执行控制仍缺失，任何正式研究须先满足既有能力门禁。
