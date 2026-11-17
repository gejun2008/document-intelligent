# Consolidation 结构复用与并发优化改造要求

> 本文为供目标项目执行的任务说明。先阅读现有代码，输出改动计划和涉及文件清单，确认后再实现。

你是资深后端工程师。请在现有 consolidation 代码上做如下改造，保持对外接口和最终输出格式不变。

## 目标

消除重复结构识别，缩短耗时，补齐可观测性。

## 1. StructureProvider

新增 StructureProvider，按优先级返回条款结构：

1. 缓存：`key=file_hash+parser_version+schema_version`。
2. 复用前置解析已保存的确定性结构，须通过 `validate_structure()`。
3. `validate_structure()` 检查：
   - offset 覆盖率。
   - 无重叠、无空洞。
   - 编号层级合法。
   - 每条款可回溯页码 / 原文位置。
   - 返回 `{passed, failed_ranges}`。
4. 仅对 `failed_ranges` 走 LLM 分窗提取；整体失败才全文 LLM。

返回结果需带 `structure_source: cache|deterministic|llm|mixed`。

## 2. LLM 兜底路径并发化

- 窗口提取用 `asyncio.Semaphore`，可配置，默认 4。
- 结果按窗口序号归并，保证顺序确定。
- 接缝判断在全部窗口完成后并发。
- 纠错限次重试，429 指数退避。
- 主协议与各补充协议结构提取并行。

## 3. 修订阶段

- 识别并发。
- 应用严格按生效日期 + 依赖拓扑串行。
- 同层无冲突的验证可并发。
- 无法确定的修订必须标注，不得擅自应用。

## 4. 进度

发出事件：

```text
{file, stage, window_done, window_total}
```

通过现有进度通道（SSE / 轮询）暴露给前端。

## 5. 日志

每次 LLM 调用记录：

- purpose。
- 耗时。
- token。
- cache_hit。
- structure_source。

## 6. Feature flags

- `CONSOLIDATION_REUSE_STRUCTURE`：默认关。
- `CONSOLIDATION_FORCE_LLM_STRUCTURE`。

## 7. 测试与对比验证

- `validate_structure` 单测：正常、空洞、重叠、乱序编号。
- 并发归并顺序一致性。
- mock LLM 验证仅失败区间被调用。
- 同一样本下确定性结构与 LLM 结构的最终合并结果对比脚本。

## 执行顺序

**先阅读现有代码，输出改动计划和涉及文件清单，确认后再实现。**
