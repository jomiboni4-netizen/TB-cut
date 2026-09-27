# TB Cut V2.1 — Codex 更新说明

> 用途：把本文件交给当前 TB Cut V2 项目的 Codex 会话，让 Codex **先读取现有 `AGENTS.md`、`PROJECT_RULES.md`、`PROJECT_STATE.json` 和当前脚本实现，再按本文修改项目**。
> 本文是对现有 V2 流程的增量修正，不要求推倒重写整个项目。

---

## 0. 本次更新目标

本次更新主要解决 5 个已在实际剪辑中出现的问题：

1. **无意义剪辑点**：同一个连续完整口播中间被切一刀，但没有删除任何内容，也没有改变画面或语义。
2. **连接词误杀**：`因为 / 所以 / 但是 / 然后` 等词被过度当成失败条件，导致本来完整的中文表达被删除。
3. **片段尾巴生硬**：上一片段像话没说完就被切掉，切点为了时长或 MiMo 粗时间码而牺牲句尾完整性。
4. **审核过早、过严**：很多商品只生成 1–2 条，甚至整款被淘汰；但 Codex 人工重新检查时还能从原字幕中找回有效内容，说明自动流程没有充分搜索候选空间。
5. **商品范围过窄**：疑似把“标题命中的小片段”误当成“商品完整讲解范围”，导致粗筛素材池先天不足。

本次核心原则：

> **候选池宽进，最终成片严出。**
> **先确定商品完整范围，再清洗，再建完整候选池，再组合成片；不要在候选阶段使用成片级硬规则提前淘汰素材。**

---

# 1. 流程顺序必须调整

新的默认流程应为：

```text
全局字幕本地索引
    ↓
确定每个商品完整时间范围
    ↓
对完整商品范围做细粒度清洗
    ↓
建立完整 candidate pool（不限片段长度）
    ↓
MiMo 从完整候选池选片 / 删片 / 排片
    ↓
生成目标 3 条 variant
    ↓
本地语义审计
    ↓
失败进入 repair loop，而不是立即淘汰
    ↓
本地 ASR / VAD / 停顿 / 能量找真实句界
    ↓
物理片段归一化，合并无意义剪辑点
    ↓
视觉人物占比 / 时长 / 去重 / 接缝终检
    ↓
Resolve 写入
    ↓
写后验收
```

禁止继续采用下面这种隐式流程：

```text
标题命中一个局部字幕
    ↓
在这个小范围严格筛选
    ↓
第一次组合失败
    ↓
直接减少 variant 或淘汰商品
```

---

# 2. 商品范围：标题命中只能作为 anchor，不能直接作为 range

## 2.1 必须先建立完整商品范围

商品标题、标题强特征、商品号宣布话术，只用于确认：

- `product_start` 的高置信锚点；
- 当前正在讲哪个商品。

它们**不能直接决定候选池只读取标题附近的几句字幕**。

完成 `product_start` 后，应继续沿全局直播时间轴向后寻找：

- 下一商品的正式强起点；
- 或其他高置信商品边界。

最终：

```text
current_product_range =
[current_product_start, next_real_product_start)
```

然后对这个**完整连续范围**做内容清洗。

## 2.2 不要把标题表顺序等同于直播讲解顺序

继续保留已有原则：

- 标题表行序不是直播顺序的绝对依据；
- 搭配引用别的商品；
- “等下讲这个”；
- 口误；
- 回头补充；
- 临时提到其他链接；

都不能轻易当作商品边界。

## 2.3 商品范围低置信时

只有以下情况才进入异常：

- 无高置信起点；
- 多个起点置信度接近；
- 下一商品边界无法判断；
- 跨字幕文件映射失败。

不要因为某个局部标题命中成功，就停止继续查找完整范围。

---

# 3. Candidate Pool（粗筛）重新定义

## 3.1 Candidate Pool 不是粗成片

粗筛片段：

- **无最小时长**
- **无最大时长**
- 可以 2 秒；
- 可以 20 秒；
- 可以 70 秒；
- 可以超过 60 秒。

候选阶段只问：

> 这段内容是否包含当前商品值得保留的卖点、体感、版型、材质、工艺、上身效果或直接相关搭配信息？

不要问：

> 这段能不能直接成为 45–60 秒成片？

## 3.2 长候选允许内部再选 subrange

例如一个 90 秒连续口播中：

- 前 15 秒重复；
- 中间 48 秒是核心卖点；
- 后 27 秒偏题；

应该保留这个长候选给 MiMo，并允许 MiMo只选择中间有意义的子范围。

禁止因为：

```text
candidate_duration > 60s
```

就把整个候选提前丢掉。

## 3.3 候选池应覆盖完整商品范围

对完整商品范围逐段清洗，只删除明确无用内容：

- 价格 / 金额；
- 优惠 / 补贴 / 券；
- 红包 / 抽奖；
- 链接号；
- 欢迎 / 寒暄；
- 闲聊；
- 后台 / 流程 / 对焦；
- 与当前商品无关内容；
- 纯促销催单；
- 空洞填充；
- 无人物且没有必要语义的长片段；
- 单纯衣服特写且没有必要语义的长片段。

其余有商品价值的内容优先进入 candidate pool。

---

# 4. MiMo 职责：从完整候选池“选、删、排”，不是提前判死

MiMo 的输入应尽量是：

- 当前商品标题；
- 当前商品完整候选池；
- 每段候选文本；
- 时间码；
- 必要的上下文关系；
- 已使用的源范围（用于跨 variant 去重）。

MiMo 负责：

1. 选择有价值内容；
2. 从长候选中选 subrange；
3. 删除候选内部无意义部分；
4. 组合卖点；
5. 让每条 variant 侧重点尽量不同；
6. 提供粗时间线和语义结构。

MiMo 不负责：

- 最终帧级切点；
- 最终人物占比；
- 最终 Resolve 写入；
- 物理 clip 是否应合并；
- 本地硬规则最终否决。

---

# 5. 语义审核：连接词是“风险提示”，不是关键词硬删除

## 5.1 禁止简单规则

禁止实现成：

```text
看到“因为”
→ 必须同时出现“所以”
→ 否则 fail
```

禁止实现成：

```text
片段开头出现 因为/所以/但是/然后
→ 直接删除
```

这些只能作为：

```text
dependency_check_required
```

进一步判断上下文是否完整。

## 5.2 因果允许多种合法结构

以下均可成立：

### 原因 → 结果

```text
因为这个面料有筋骨感，所以穿起来不会贴身。
```

### 结果 → 原因

```text
这件穿起来不会贴身，因为这个面料本身有一点筋骨感。
```

### 先结论后解释

```text
我很喜欢这个领口，因为它不会卡脖子，而且脸看起来会更小。
```

### 隐式因果

```text
这个面料比较轻，所以夏天穿会更舒服。
```

真正失败的是：

```text
因为这个面料它……
```

然后内容结束。

或者成片从：

```text
因为它是这种织法……
```

开始，但观众无法理解“为什么在解释这个”。

## 5.3 语义闭环仍然是硬规则

最终成片仍必须检查：

- 枚举闭环；
- 因果闭环；
- 条件 → 结果；
- 转折两侧关系；
- 指代对象；
- 承诺兑现；
- 首句独立可懂；
- 末句完整。

### 枚举示例

如果保留：

```text
这个有三个颜色
```

就必须在同一个成片中兑现三个颜色。

如果只需要其中一个颜色，可以：

- 删除“三个颜色”的总领句；
- 从一个可以独立成立的完整句开始。

不要为了保留某一句而强制保留整个枚举。

---

# 6. 最终切点：总时长不能凌驾于完整句尾

MiMo 给的时间范围是**语义粗范围**，不是最终切点。

本地最终切点应综合：

- SRT cue；
- 局部 ASR；
- 词级时间；
- VAD；
- 停顿；
- 人声能量；
- 帧边界；
- 前后句语义。

## 6.1 新优先级

在最终必须满足 `45s < duration < 60s` 的前提下，剪辑决策优先级应为：

```text
语义完整
>
自然句首 / 句尾
>
删除低价值完整内容
>
调整总时长
```

如果某段自然句尾在 MiMo 粗结束点之后 2 秒：

- 应优先把句尾补完整；
- 如果导致总成片 >60 秒；
- 应删除或替换成片中其他低价值**完整片段**；
- 不允许直接把这句话尾巴砍掉。

## 6.2 尾部闭环风险检测

可以新增 `tail_closure_risk`。

若片段尾部出现或接近以下结构，应向后检查局部上下文：

- 因为
- 所以
- 但是
- 而且
- 如果
- 当……的时候
- 然后
- 另外
- 还有
- 它这个……
- 这个地方……
- ……的话
- 明显主谓宾未完成
- ASR 后 1–3 秒仍连续同一句

若后续是当前句自然结尾，应扩展到完整句界。

---

# 7. 无意义剪辑点：写 Resolve 前必须做物理片段归一化

新增强制步骤：

```text
normalize_physical_clips()
```

## 7.1 必须合并的情况

若两个相邻最终 clip：

```text
same_source == true
AND timeline_contiguous == true
AND source_contiguous == true
AND 中间没有明确需要删除的源内容
```

则必须合并成一个物理 clip。

例如：

```text
A: 100.00 - 108.53
B: 108.53 - 116.20
```

应变成：

```text
A: 100.00 - 116.20
```

不能因为：

- MiMo 返回了两个 role；
- 两个 candidate ID；
- 两个语义块；
- 两条 SRT；
- 内部有停顿；

就制造真实剪辑点。

## 7.2 帧误差

考虑 ASR / 浮点换算误差：

- 允许 `±1–2 frame` 做 source-contiguous 判断；
- 如果只是时间吸附误差，应先归一到真实连续帧再合并。

## 7.3 role 不等于物理剪辑点

一个连续片段可以同时承担：

```text
material + fit + wearing_feel
```

role 只保留在 audit metadata 中。

最终物理剪辑点必须对应：

> “这里真的删掉了一段源内容”
> 或
> “这里切换到了另一个非连续源范围”。

---

# 8. Variant 失败逻辑：PASS / REPAIRABLE / EXHAUSTED

不要只使用：

```text
PASS / FAIL
```

改为：

```text
PASS
REPAIRABLE
EXHAUSTED
```

## 8.1 REPAIRABLE

以下默认属于可修复，不应立即减少 variant 数：

- 当前组合只有 39–45 秒；
- 当前组合超过 60 秒；
- 尾句不完整；
- 首句依赖前文；
- 枚举缺一项；
- 因果缺前提或结果；
- 人物占比 65–70%；
- 与另一 variant 有源时间重叠；
- 与另一 variant 语义重复；
- 某段画面不好；
- 接缝生硬；
- 某段价值过低。

此时应该：

```text
回完整 candidate pool
→ 替换
→ 补上下文
→ 删除低价值块
→ 换卖点
→ 调整组合
→ 再审
```

## 8.2 EXHAUSTED

只有满足下面条件才允许最终减少条数：

> 当前商品完整范围已经扫描；全部有效候选已进入候选池；自动组合和修复循环已尝试足够的替换方案；仍无法生成满足全部硬规则的唯一 variant。

此时才：

```text
3 → 2
2 → 1
1 → 0
```

禁止第一次 MiMo/本地审核失败就减少条数。

---

# 9. Repair Loop

每个商品目标仍是 3 条。

建议实现：

```text
for variant in target_variants:
    generate_or_select_plan()
    audit()

    if PASS:
        lock_variant()

    elif REPAIRABLE:
        repair_using_remaining_candidate_pool()
        audit_again()

    elif EXHAUSTED:
        stop_trying_this_variant()
```

需要限制循环次数防止死循环，但循环次数不能太低。

每一次 repair 应记录：

```text
repair_reason
old_segments
removed_segments
added_segments
remaining_candidate_pool
audit_result
```

Codex 默认只看最后异常摘要，不读取整个字幕。

---

# 10. 跨 variant 去重规则不放宽

继续保持：

```text
physical source-frame overlap = 0
semantic duplicate = 0
```

这里不因为产出三条困难而放宽。

如果 variant 2 和 variant 1 冲突：

- variant 1 已 PASS 则锁定；
- variant 2 回 candidate pool 换内容；
- 不要重新生成全部 variant；
- 更不能允许 1 秒重叠。

---

# 11. 人物检测规则保持，但不要太早淘汰商品

最终：

```text
host_visible_duration / final_duration > 70%
```

保持不变。

视觉模型顺序继续：

```text
Apple Vision
→ low confidence 才 Qwen 4B
→ 仍 uncertain 才 Qwen 8B
```

衣服特写不算人物有效时长。

但如果当前初版方案人物占比只有 68%，它是：

```text
REPAIRABLE
```

优先：

- 换掉无人物片段；
- 从候选池选择主播画面对应的同类卖点；
- 调整组合。

不要马上判整个 variant 失败。

---

# 12. Codex 仍不得反复读取完整字幕

保留已有约束：

- 原始 SRT 只由本地脚本读取 / 索引；
- 使用 fingerprint 缓存；
- 指纹没变，Codex 不重新读取完整 SRT；
- repair loop 通过本地 candidate index 查询；
- Codex 只在本地无法解决的异常中读取最小必要字幕窗口。

本次更新不能通过“让 Codex 每次重新看全部字幕”来弥补候选池问题。

正确修法是：

> **让本地商品范围和 candidate pool 建得更完整。**

---

# 13. Resolve 写入验证需要扩展

当前写入验证不能只检查：

- video count；
- audio count；
- 基础写入成功；
- source frame tolerance。

建议每个最终 variant 输出至少：

```json
{
  "duration": 0,
  "host_ratio": 0,
  "semantic_closure": true,
  "physical_overlap": 0,
  "semantic_duplicate": false,
  "meaningless_cut_count": 0,
  "seam_tail_risk_count": 0,
  "candidate_pool_duration": 0,
  "candidate_pool_segment_count": 0,
  "product_range_start": 0,
  "product_range_end": 0,
  "repair_attempts": 0,
  "status": "PASS"
}
```

商品级摘要建议输出：

```json
{
  "target_variant_count": 3,
  "final_variant_count": 3,
  "product_range_duration": 0,
  "candidate_pool_duration": 0,
  "candidate_pool_coverage": 0,
  "exhausted": false,
  "exhausted_reason": null
}
```

这样以后如果某商品只生成 1 条，可以直接判断：

- 商品范围是不是过窄；
- candidate pool 是不是过少；
- 哪个硬规则导致 repair；
- repair 是否真正穷尽；
- 而不是再次让 Codex 读完整字幕排查。

---

# 14. 缓存版本与失效策略

这次修改会影响：

- 商品范围；
- candidate pool；
- MiMo 请求；
- variant 组合；
- semantic audit；
- final cut plan。

因此建议新增：

```text
pipeline_version = "2.1"
```

或者等价字段。

升级后：

## 可以保留

- 原始字幕 fingerprint/index；
- 原视频 fingerprint；
- 本地模型权重；
- 不依赖旧商品范围的基础媒体元数据。

## 应失效并重新生成

- 旧 product range；
- 旧 candidate pool；
- 旧 MiMo selection；
- 旧 variant plan；
- 旧 semantic audit；
- 旧 seam audit；
- 旧 final cut plan。

不要删除原始视频、字幕、标题、Resolve 项目和最终成片。

---

# 15. 需要更新的项目规则

请 Codex 把以下内容合并进现有 `PROJECT_RULES.md`，避免与旧规则重复冲突：

### 新增/强化

- 标题命中只作为商品范围 anchor；
- 必须先确定完整商品范围再粗筛；
- candidate pool 无时长限制；
- candidate pool 宽进；
- 最终成片严出；
- connector keyword 只触发 dependency check，不直接 fail；
- 允许“结果 → 因为 → 原因”等中文正常因果顺序；
- 总时长不能通过硬切半句来满足；
- 无意义物理剪辑点必须自动合并；
- variant 采用 PASS / REPAIRABLE / EXHAUSTED；
- 只有 EXHAUSTED 才允许减少 variant 数；
- repair 必须回完整候选池寻找替代内容。

### 继续保持

- 最终每条严格 `>45s && <60s`；
- 每商品目标 3 条；
- 不足时允许 2/1 条，但必须经过 EXHAUSTED；
- 价格/优惠/红包/闲聊/流程话术删除；
- 人物占比严格 >70%；
- 衣服特写不算；
- 跨 variant 源帧零重叠；
- 语义零重复；
- 本地优先；
- Codex 不重复读取完整 SRT；
- MiMo 只审核已过滤的文本候选；
- Resolve source timeline 只读。

---

# 16. AGENTS.md 建议增加的约束

请在现有 `AGENTS.md` 增加以下行为约束：

1. **不得因为第一次自动审核失败就减少 variant 数。**
2. 遇到 `REPAIRABLE` 必须优先调用本地 repair loop。
3. Codex 只有在：
   - 商品边界真正歧义；
   - repair 已穷尽；
   - 本地脚本异常；
   - 外部系统异常；
   时才人工介入。
4. Codex 人工介入也只能读取最小必要字幕区间，不得默认重新读取整个 SRT。
5. 新的稳定用户规则继续写入 `PROJECT_RULES.md + CHANGELOG.md`。
6. 本次更新后应把 pipeline version / rule version 写入状态或 manifest，避免旧缓存被当作新计划复用。

---

# 17. 实现时不要盲目重写全部脚本

Codex 应先检查当前项目脚本，找到实际负责以下职责的模块：

- product range；
- candidate pool；
- MiMo request；
- semantic audit；
- variant assembly；
- cut point refinement；
- cross variant dedup；
- Resolve plan normalization；
- Resolve validation。

优先在现有脚本上增量修改。

如果当前已有同等能力，不要因为本文示例函数名不同就重复创建另一套脚本。

示例名字只代表职责：

```text
build_product_ranges
build_candidate_pool
mimo_review
semantic_audit
repair_variant
refine_cut_points
normalize_physical_clips
final_audit
resolve_write
```

实际以项目当前代码为准。

---

# 18. 修改后必须做的测试

至少准备以下测试场景：

## Test A — 无意义切点

输入连续源范围：

```text
100.000–108.500
108.500–116.000
```

无删除内容。

预期：

```text
100.000–116.000
```

只有一个物理 clip。

---

## Test B — 结果在前、因为在后

字幕：

```text
这件穿起来不会贴身，因为这个面料本身有一点筋骨感。
```

预期：

```text
PASS
```

不得因为没有“所以”失败。

---

## Test C — 残缺因为

字幕：

```text
因为这个面料它……
```

后续无完整句。

预期：

```text
REPAIRABLE
```

先尝试扩展后文；找不到完整内容后才删除。

---

## Test D — 三个颜色缺失

字幕：

```text
这个有三个颜色，第一个是白色……
```

只保留一个颜色。

预期：

- 要么扩展到三个颜色完整兑现；
- 要么删除“三个颜色”的总领句，从独立完整颜色描述开始；
- 不允许保留不兑现的承诺。

---

## Test E — 句尾为了 60 秒被截断

当前组合：

```text
59 秒
```

最后完整句实际需要扩展到：

```text
61 秒
```

预期：

- 补完整句尾；
- 从其他位置删除/替换至少约 2 秒低价值完整内容；
- 最终回到 `<60 秒`；
- 禁止切掉最后半句话。

---

## Test F — 第一次失败但候选池仍有素材

variant 初次组合人物占比 68%。

预期：

```text
REPAIRABLE
```

从候选池替换无人物段。

不得直接：

```text
FAIL → 商品从 3 条降到 2 条
```

---

## Test G — 完整商品范围

标题强命中发生在商品开场附近，但该商品继续讲了数分钟。

预期：

- candidate pool 覆盖整个商品讲解范围；
- 不是只包含标题命中附近几十秒。

---

# 19. 完成更新后的 Codex 输出要求

更新完成后，不要把大量源码和日志贴回对话。

只汇报：

```text
1. 修改了哪些规则文件
2. 修改了哪些脚本
3. pipeline/rule version
4. 哪些旧缓存被标记失效
5. 上述测试 A–G 的结果
6. 是否需要清理旧派生缓存
7. 下一批剪辑是否可以直接运行
```

如果存在未解决问题，只列真正阻塞生产的问题。

---

# 20. 最终原则

本次更新后的系统应该遵循：

> **先把当前商品完整讲解范围找全。**

> **把明显垃圾剔除后，尽量完整地建立有价值候选池。**

> **MiMo 从完整素材池里负责选、删、排，不要让候选片段预先长得像成片。**

> **语义关系按中文实际表达理解，不能靠“因为/所以”等关键词机械判死。**

> **第一次方案失败只是 REPAIRABLE，只有搜索候选池并完成修复循环后仍无法通过，才是 EXHAUSTED。**

> **每个真实物理剪辑点都必须有存在的意义。**

> **最终成片仍然严格：45–60 秒、人物 >70%、无促销闲聊、语义完整、跨 variant 零源帧重叠、零语义重复。**
