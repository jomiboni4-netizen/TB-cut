# 固定执行入口

正常剪辑使用 `scripts/delivery_pipeline.py`，只准备参数和Codex选段数据，不在工作区生成Python执行器。新问题进入同一任务内的开发阶段，单独计时和测试；不能把开发时间混入ASR时间。这个入口收敛的是句子选择后的组装、本机检查和**已授权循环测试线或明确要求新建合并时间线**交付，不解锁旧registered writer，不代替商品归属、完整池选段或人工范围确认，也不处理成品媒体导出。

这是稳定的 P0 工作流：商品选择、局部返修、人工画面确认和例外都写入本次 workspace 的 JSON。日常商品不得修改脚本或创建临时执行器。共享流程缺陷才修改通用代码，并同时更新合成回归、契约版本、持久规则和变更日志。正常预热、无阻断的单商品流程目标约五分钟；这是优化目标，不是承诺。计时分别记录选段复核、本机计算、返修和外部等待，并报告缓存命中及未归因时间。

## 输入约定

显式指定 `--workspace`，继承该工作区已核验的 `RUN.json`、登记配置及本机输入。新任务初始化仍使用 `coldstart_semantics.py init --input-state ...` 的显式登记协议，不能默认挑旧工作区。

冷启动剪辑先完成只读 Resolve preflight，再开始索引。`coldstart_semantics.py index` 必须找到本轮 `delivery/human_ranges.json`，其中商品 ID 与批次完全一致，并且每条范围有用户确认来源；它直接从这些源素材起止点生成字幕候选，不运行全局标题定位器，也不以90秒、标题关键词数或全局定位分数淘汰范围内候选。只完整落在标记范围内的字幕可进入选段；标记文件摘要与Resolve preflight摘要写入候选索引，任一变化都会阻断旧候选。视频完整哈希延迟到 preflight 通过后。

Resolve preflight 同时验证登记的源/目标时间线、素材池来源、当前批次清空授权和逐款写入器入口；缺任一项时，不开始视频哈希或字幕候选分析。逐款 writer 只接受 `clear_configured_target_and_append` 授权，备份并清空登记的目标时间线后按批次商品顺序追加；默认商品间留25帧空白（25fps下1秒黑场），每款追加后检查音画片段位置和范围，再由独立 `readback` 记录通过后才能追加下一款。黑场必须能从相邻商品的回读位置差验证。

### 冷启动逐款写入器优先级

对 `coldstart_resolve.py` 预检绑定的清空后逐款写入流程，唯一写入入口是 `scripts/reusable_test_delivery.py`：它匹配“先备份、清空登记目标、逐商品追加、每款独立回读、商品间留黑场”的事务契约。旧 `scripts/resolve_write_v2.py` 属于不同的追加式登记工作流，不执行本流程要求的目标清空和逐款写入回读，不得作为该入口的自动备用，也不得在部分写入后切换重放。它仍被方案读取、预检、历史验证和其他测试依赖，暂不删除；仅当未来完整迁移并验证所有消费者后再移除。

写入前若新入口测试/预检不通过，停止写入并修复新入口；只有尚未产生任何 Resolve 改动，且替代入口已证明支持相同的项目/时间线身份、清空授权、备份、逐款追加、黑场及独立回读契约时，才可在写入前切换。当前旧写入器不满足这组等价条件。

工作区 `delivery/RECIPE.json` 为数据文件，字段如下。文件引用 `{path, sha256}` 支持工作区相对路径；外部派生索引只在显式给出路径和SHA、且 `input_digest` 与当前RUN一致时复用，不改旧run_id。

```json
{
  "schema_version": 1,
  "run_id": "CURRENT_RUN_ID",
  "input_content_digest": "CURRENT_INPUT_DIGEST",
  "product_id": 1,
  "encoding_id": "00001",
  "index": {"path": "inputs/subtitle_index.json", "sha256": "SHA256"},
  "titles": {"path": "inputs/titles.json", "sha256": "SHA256"},
  "range_manifest": {"path": "inputs/human_ranges.json", "sha256": "SHA256"},
  "pool_review": {"reference": "editorial/full_pool_review.json", "sha256": "SHA256"},
  "frame_rates": {"path": "delivery/FRAME_RATES.json", "sha256": "SHA256"},
  "assembly_review": {"path": "editorial/ASSEMBLY_REVIEW.json", "sha256": "SHA256"},
  "timeline_fps": 25.0,
  "width": 750,
  "height": 1000,
  "asr_records": {"main": {"path": "delivery/asr_IMMUTABLE_DIGEST.json", "sha256": "SHA256"}},
  "variants": [
    {"variant_id": "v1", "focus": "当前版本卖点", "clips": [
      {"unit_id": "u1", "asr": "main", "window": 0, "first": 1, "last": 80}
    ]}
  ],
  "delivery_request": {
    "product_id": 1,
    "requested_variants": 3,
    "timeline_mode": "one_new_combined_timeline",
    "timeline_name": "USER_AUTHORIZED_NEW_NAME",
    "gap_frames": 50,
    "native_protocol": {"path": "inputs/native_protocol.json", "sha256": "SHA256"},
    "native_protocol_evidence": {"path": "inputs/native_roundtrip_evidence.json", "sha256": "SHA256"}
  }
}
```

示例省略另外两版，不能原样当成可交付方案。`first/last`是原ASR窗口中的词索引，不是手改时间；必须由Codex结合上下文决定保留词，程序不改选句子。ASR文件是含 `body`、`body_digest` 的记录，`body.windows` 每窗包含 `source_file`、`start/end`、原 `words`。来源模型和参数原样保留，不把不同模型的词窗口冒充同一次识别。

`range_manifest`沿用 `publish_confirmed_ranges.py` 的真人决定格式；标题和编码对照当前标题索引，调用现有范围证明与覆盖验证。`pool_review`是完整候选池的Codex选段记录引用，不是独立语义终审PASS；最终仍须独立回执。

## 执行顺序

准备局部识别时可在配方加入 `asr_jobs: [{"source_file":"raw","start":10.0,"end":70.0}]` 和 `asr_model`。`source_file`是字幕索引的源stem。首次可令 `asr_records`为空、`variants`为空，先识别再由Codex填写词选择。

```sh
python3 scripts/delivery_pipeline.py asr --workspace WORKSPACE
python3 scripts/delivery_pipeline.py prepare --workspace WORKSPACE
```

ASR按素材身份、模型权重、实际参数及窗口复用已有sealed compute缓存；包含当前请求的较大窗口也可复用。只有miss才加载/转写，不下载模型。`asr`输出不可变记录引用；把引用填回配方，不写执行代码。

`prepare`生成准确物理方案、局部声音审核和 `SEMANTIC_TASK.json`。音频阶段先保存切点附近20ms波形包络，再确定切点：先选干净静音；无干净静音且有足够证据时，才取相邻ASR词界的中点作为连读折中；不再因某一帧能量更低就把切点滑进发音。连续口播结果仍标记为词界估计，不代表音素级证明；中点不在有界搜索范围或附近音频证据不足时保存音频专属返修记录与波形证据，语义选段、ASR和卖点清单保持不变。算法契约为 `owned-gap-local-audio-v3-clean-pause-else-owned-edge`。失败保持REPAIRABLE，不自动减条数或挪词。连续物理片段使用现有normalize逻辑；来源词证据无法合并时保持阻断，不能造证明。

```sh
python3 scripts/delivery_pipeline.py check --workspace WORKSPACE --detector DETECTOR_BINARY
```

精确方案冻结后，独立语义与人物检查并行。没有本次文本外发授权、没有有效回执时，语义分支返回 `SEMANTIC_AUTHORIZATION_REQUIRED`；命令不会自行发给外部服务。先向用户明确数据范围和目的地，在得到授权后才用 `--approved-semantic-task EXACT_TASK_DIGEST` 运行。它只对应 `prepare`输出的本次任务摘要；上一商品或上一方案的授权不继承。已有同摘要sealed回执直接验证复用，绝不重复发送。不得为了使用这个选项推定用户已授权。

人物二进制必须由当前Swift源码构建并显式指定，Apple Vision按项目规则在沙盒外执行。新入口不自动编译，不生成巨大的重复模块缓存。源级clock和逐帧人物证据继续使用现有身份/封存协议；源、模型、策略或切点变化重算受影响证据，错误不作无人物结论。

### 固定人物抽样规则（P0）

`config/vision_rules.json`是本机人物门禁的唯一参数入口。每条最终成片按输出时间分为10个等长区间，各取中点帧；采样与物理剪辑段数量无关。若中点检测到无人/仅衣物，再将该区间均分10份，各抽1帧。区间时长为 `min(成片总时长 × 10%, 6秒)`，成片短于60秒时随片长同比缩短。

每个复检点代表该区间的1%，把所有无人点相加，估算整条成片无人时长占比。总值 `<= 10%` 通过人物存在门禁，`> 10%` 进入REPAIRABLE；正好10%允许通过。例：一个区间复检10帧中9帧有人，估算无人占比9%，通过；两个区间各有6/10无人，合计12%，返修。初筛10帧全有人则样本估计无人占比0%。不确定结果保持UNCERTAIN，检测异常BLOCKED。特写时长上限独立按配置检查。

这是有界样本估计而非逐帧证明。每版最少10帧；只有初筛命中无人/衣物的区间才额外取10帧，最多110帧/版，不做固定五轮补样。摘要保存初筛点、触发复检区间、复检结果、估算无人比例、精确方案摘要及检测器指纹。

## 新建时间线交付与恢复

仅在用户明确请求新时间线时使用 `--allow-new-timeline`；它不是额外用户授权的替代。已有源/目标/人工修改线全部只读。三版写入同一条新线，间隔和标记来自配方。

```sh
python3 scripts/delivery_pipeline.py deliver --workspace WORKSPACE --detector DETECTOR_BINARY --allow-new-timeline
```

`deliver`核实所有门禁、读取保护快照、运行Doctor、核对源SHA及安装版本对应的真实原生端点证据、校验备份后写入；之后在全新OS进程中回读音画、源范围、时长、标记、设置和原时间线。只有独立回读成功才为 `FINAL_VERIFIED`。不接收旧粗选PASS、手写verdict或缺少声音/人物证据的草稿放行。

可以分别执行 `preflight`、`write`、`readback`。每次write只允许全新事务。`RESOLVE_TRANSACTION.json`在创建和调用append前落盘，失败不自动重放、不删除半成品、不修改现有线。若状态为 `APPENDED_PENDING_VERIFY`，执行 `recover`只核对已写结果并保存，再执行 `deliver`或新进程`readback`；尚在创建/append中的状态需要先只读人工排查，入口直接阻断。恢复不再需要另写脚本。

## 复用与失效

- 组装依赖原词选择、输入引用、帧时钟与组装生产者。
- 每个物理片段的声音依赖自身词窗口、帧边缘、素材和声音生产者；其他片段变化或仅引用文件外壳SHA更新不重算它，但输出保留新的正确来源引用。
- 独立语义依赖整个最终方案、上下文、规则和语义任务生产者。
- 人物汇总依赖整个精确方案与当前分类器/策略；未变源帧仍可由底层缓存复用。
- 人物策略在JSON中版本化；策略变化使最终人物汇总失效，但不使同一素材、同一源帧、同一检测器的物理分类缓存失效。日常选段修正只重算方案摘要、受影响的片段音频边缘、语义任务和最终人物汇总。
- 写入器修改不让上游ASR、组装和声音失效。运行中代码、配方、规则或引用输入变化会停止，不给旧结果重新盖章。
- 新阶段记录不可变、正文摘要及HMAC校验。损坏记录阻断，不静默重算掩盖。旧trial/旧终审继续保留，不自动升级为新入口当前证据。生产者变化在manifest和事件账记录，不污染历史原件。

## 分项计时

每次执行自动写 `delivery/events.jsonl`、`TIMING.json`、`PIPELINE_MANIFEST.json`。`status`只汇总，不加载媒体或模型。

```sh
python3 scripts/delivery_pipeline.py time-start --workspace WORKSPACE --name choose_sentences --category editorial
python3 scripts/delivery_pipeline.py time-end --workspace WORKSPACE --token TOKEN_FROM_START
python3 scripts/delivery_pipeline.py status --workspace WORKSPACE
```

人工选段用editorial、代码修改用development、明确等待授权用waiting；本机计算和验证分别自动计入compute/verification。返工命令加 `--repair`，保留失败及历史用时。并行/嵌套类别是包含子阶段的时间，不能相加当总耗时；墙钟、已观测区间并集、未归因间隔和未关闭span分别列出。没有实际标记的过去时间不补造。

本次优化只验证执行方式和复用次数，不以合成测试耗时冒充真实新商品速度；未执行新商品剪辑、外部语义调用或新的真实时间线写入。


## 循环测试模式

当前用户要求测试剪辑固定复用登记的测试时间线。配方的timeline_mode设为reusable_test_timeline，timeline_name必须匹配本项目runtime.test_timeline_binding中的名称与稳定ID。使用deliver --allow-test-reuse（其他workspace/detector参数不变）；新进程回读继承同一选项。全部门禁、源核对和备份通过后，仅删除该线全部片段和标记，再写入本轮内容；不创建新线。每轮在独立工作区保存旧测试快照和项目备份，失败事务不重放。配置的源和正式目标不能作为循环清空对象；名称/ID不匹配时阻断。coldstart init只从显式input-state继承同一项目绑定，不继承文本外发授权。

## 前置组合细审与只读帧率登记（v2）

先执行 `project-read --workspace WORKSPACE`（Resolve连接按规则在沙盒外执行）。首次读取项目、每条时间线和全部登记素材的标称/平均帧率，输出不可覆盖的 `delivery/FRAME_RATES.json`，在本工作区状态中登记绝对引用与SHA。后续从这个明确状态初始化的同项目工作区自动继承；配方也可显式引用已有登记。再次 `project-read` 只验证引用和素材身份，返回 `PROJECT_RATES_REUSED`，不会重新连接或探测。根登记状态可保存同一引用用于后续窗口。输入摘要/素材身份变更需明确建立新的登记，不能删除旧证据伪装第一次读取。

登记中的素材 `fps` 是容器标称值、`average_fps` 是容器平均值，均不是逐帧恒定帧率证明。二者差异保留观察；实际选中源继续使用已有封存完整PTS时钟，真实源fps必须匹配时间线，否则阻断、不转换。写前重新读取项目及既有时间线的设置作轻量一致性检查，不改帧率；这不重复素材探测。

有本机ASR选词后执行 `assembly-task --workspace WORKSPACE`，生成最小文本任务 `delivery/ASSEMBLY_TASK.json`。主Codex必须实际审读，记录 `assembly_review` 指向的JSON：`contract=source-bound-assembly-review-v2-disfluency`、同任务 `task_digest`、`reviewer=primary_codex`、`verdict=PASS`、空 `issues`。`units` 每行包含 `variant_id/unit_id/checks`；`seams` 每行包含 `variant_id/left/right/checks`。所有任务列明的check均为 `{passed: true, reason: "具体文本判断理由"}`。另需 `cross_variant_duplicates` 的判断和具体理由。没有自动生成PASS的命令。

任务保留实际选中的词、相邻排除词及独立字幕源上下文，逐段核对句首主语/对象、内部残话、句尾、明显卡壳/假起句/重复/无效填充和删除安全性，逐接缝检查指代、因果条件、转折和重复，最后跨版核对信息重复。每段初审都要记录 `disfluency_reviewed` 及理由；独立终审提示也要求复核明显口播卡壳。仅按真实保留词和最小声学片段证据返修，不把常规语气或正常重复机械删除。上下文不足先查询更大最小派生窗口并更新来源，不凭口头承接词拒绝，也不靠静音或删主语来让本机声音门禁通过。修改选词、上下文、顺序或卖点后任务摘要改变，旧记录阻断；局部声音仍按片段依赖复用。

`prepare`在声音修边及源帧时钟计算前验证上述主Codex记录与帧率登记。随后仍生成独立终审任务，最终声音、人物及精确回读标准不变。旧成品证据只读；旧配方不补写虚构细审，未来自动执行需新契约记录。此次改进尚无新商品完整对照，不保证终审零返工或量化提速。
