# 2.3.2：有限请求预算与显式host lease

host-binding新增可选request_timeout_seconds：默认300秒，必须为大于0且≤3600的有限JSON数值。原Web Manager和daemon五角色工厂接收同一host设置；请求journal的最终期限不晚于原sessionexpiry，包含准备/排队/dispatch等待。timeout-only变更失效缓存，只影响新调用；已提交请求的原receipt/取消/结果恢复保持。

SupervisedDotsProfile可显式选择1..90秒lease_duration_seconds。原旧序列化和初始30秒默认保留，旧显式1..60秒选择在同owner/generation续租时保留；旧记录缺metadata时保守回30，handoff对legacy重置，明确profile选择保留。冲突覆盖拒绝；所有续租被原sessionexpiry封顶，停止或过期不能续租，取消/核对仍可进行。

这不更改strict/protocol3、权限、hard-idle与取消规则，不自动续租、不扩大根目录、不延长既有request/session。长调用仍可能达到默认300秒或更早的停止条件；补丁测试不证明任何真实研究目标已完成。

相对2.3.1仅纳入授权12项源码/测试/docs变更：8生产文件、1模块文档、2既有测试和1新增lease测试。payload88项，731原基准不变；增加精确2.3.1完整87文件升级map并保留全部旧map与legacy。三个安装执行脚本不变，升级/回滚/卸载保留首次原件和用户后改冲突保护。

当前结果见TIMEOUT-LEASE-VALIDATION.json及VALIDATION.json，历史CI与native记录继续保留；不同测试子集有重叠，不相加。新公开commit完整CI、跨平台安装与实际长研究完成需分别验证。
