# TB Cut V2.1 项目说明（供 GPT 接手）

本文件描述可公开的项目结构。具体输入路径、Resolve 项目和批次结果以本地 `PROJECT_STATE.json` 为准；该文件不进入 Git 仓库。

## 1. 项目用途

本项目把淘宝直播长视频按商品切成独立口播短片。本地脚本负责字幕索引、商品范围、候选池、切点、人物检测和硬规则审计；MiMo `mimo-v2.5-pro` 负责语义选段及返修建议；最终由 DaVinci Resolve Python API 写入目标时间线并复验。

运行状态、写入验证和导出状态是三件事，分别检查本地状态文件、当前批次的写入报告和导出验收。`.cache` 可能混有旧批次或旧项目产物，不能仅凭文件存在判断当前结果有效。

## 2. 接手时先读什么

1. `AGENTS.md`：项目代理约束与启动顺序。
2. `PROJECT_RULES.md`：持续有效的剪辑硬规则。
3. `PROJECT_STATE.json`：本次输入路径、Resolve 项目和批次状态；不存在时看 `PROJECT_STATE.template.json`。
4. `docs/REPOSITORY_AUDIT.md`：仓库边界及已知风险。
5. `docs/PIPELINE_DEPENDENCIES.md`：各阶段真实输入、输出及缓存身份规则。
6. 具体工作涉及的 `scripts/` 源码，而非仅凭 `README.md`。README 是入门说明，示例不代表任何批次的运行状态。

规则优先级：**当前用户指令 > `PROJECT_RULES.md` > `AGENTS.md` > 自动化默认值**。执行清理或写 Resolve 前先运行 `python3 scripts/project_doctor.py --root .`。它只验证结构、基础配置和命令可用，不证明所有缓存、媒体或 Resolve 状态正确。

运行路径现分为 repo（源码、长期规则和配置）与 workspace（`PROJECT_STATE.json`、`.cache/`、`reports/`、`plans/`、`exports/`）。默认 workspace=repo，单独运行可用 `--workspace <path>` 或 `TB_CUT_WORKSPACE`。迁移前用 `python3 scripts/workspace_preflight.py --repo . --workspace <path>` 只读检查；遗留缓存可能因身份或路径校验失败而阻塞迁移；须证明来源或重建失效链，并通过预检后再考虑物理移动。

## 3. 本地目录

```text
AGENTS.md / PROJECT_RULES.md      代理约束、持续规则
PROJECT_STATE.json                当前项目路径、批次及运行状态
PROJECT_STATE.template.json       新项目状态模板
README.md / MIGRATION.md          入门和迁移说明
CHANGELOG.md                      规则与流程变更记录
TB_CUT_V2_1_CODEX_UPDATE.md       V2.1 需求背景，已落地情况仍以源码为准
config/                           时长、内容、人物、模型策略的 JSON 配置
tbcut.py                           少量维护命令入口
scripts/                           实际处理、审计、写入、导出脚本
tests/                             V2.1 回归测试
.cache/                            字幕索引、候选、MiMo、ASR、审核、备份等派生数据
reports/                           本地批次报告，不提交
plans/ / exports/                  本地方案与导出产物，不提交
```

`tbcut.py` 只封装 `init`、`doctor`、`fingerprint`、`index-subtitles`、`locate-links`、`clean`。它**不是**从输入到成片的一键总调度器；后续步骤由 `scripts/` 分段调用。

## 4. 核心数据流

1. **输入与索引**：`scripts/init_project.py` 写项目状态；`fingerprint_inputs.py` 记录输入指纹；`index_subtitles.py` 读取配置的 SRT/同名 MP4，按视频时长映射成全局直播时间轴，写 `.cache/subtitle_index.json`。复用索引须通过身份校验。`inspect_title_workbook.mjs` 只检查标题工作簿，不生成 `.cache/titles.json`；标题索引须另行生成、核对来源并显式认证。
2. **商品范围**：`locate_link_intros.py` 从“几号链接”等话术找商品起点锚点，结合上下文打分；已确认起点至下一真实商品起点构成完整范围，写 `.cache/product_ranges.json`。标题表顺序只作线索。低置信或含糊边界需人工处理，可通过 `apply_range_overrides_v2.py` 覆盖。
3. **宽候选池**：`build_expanded_codex_request.py` 在完整商品范围内按字幕清理明显禁用内容，保留连续有用段；候选段没有时长上限。输出各商品的扩展 MiMo 请求及候选覆盖摘要。
4. **语义选择与返修**：`mimo_review_v2.py` 将压缩后的候选字幕、时间码和规则送给 MiMo，按请求指纹缓存结果；`audit_mimo_v2.py` 审查语义闭合、禁用内容、重叠等；`build_repair_request_v2.py`、`merge_repair_result_v2.py` 等处理失败版本。状态为 `PASS`、`REPAIRABLE`、`EXHAUSTED`；首次失败不能直接减条数，返修必须回完整剩余候选池。
5. **本地切点与终审**：`windowed_asr_v2.py` 只转写选段附近窗口；`window_quality_v2.py` 检查局部质量；`refine_plans_v2.py` 将粗时间码对齐词、句尾和帧，并合并相邻且未删源内容的物理片段，生成成片方案。`person_coverage_audit_v2.py` 调用本地人物探测程序审查方案；`approve_final_plans.py` 仅批准合格方案。
6. **Resolve 写入与交付**：`resolve_write_v2.py` 在写入前验证 V2.1 缓存、时长、人物占比、切点、内容和跨版本去重；核对当前项目及两条时间线，先备份项目，再向状态文件指定的目标时间线追加音视频与商品标记，并验收。`validate_resolve_write_v2.py` 可单独复验。`export_marked_variants.py` 根据商品标记和连续片段生成导出清单；只有加 `--render` 才渲染。`verify_exports.py` 用于导出文件复验。

主要中间文件按商品分布在 `.cache/product_XX_rough/`（MiMo 请求、结果、语义审核、局部 ASR 等）和 `.cache/product_XX_windowed/`（帧级方案、终审、批准清单）。`.cache/resolve_write_report.json` 与 `.cache/resolve_write_validation.json` 是写入验收记录；使用前须核对批次、项目和版本。不要把不同批次同名缓存混用。

新生成的关键缓存带同名 `.meta.json` 身份文件。缺少该文件的旧缓存保留，但不能直接通过关键读取点；刷新与显式认证步骤见 `docs/PIPELINE_DEPENDENCIES.md`。

## 5. 不可放宽的剪辑规则

- 每款目标最多 3 条；每条时长严格 `>45.0s` 且 `<60.0s`。若完整搜索并返修后确实不足，可只产 2、1 或 0 条，不能补重复或弱内容。
- 成片只留本商品的卖点、材质、版型、工艺、穿着感、上身效果及直接相关搭配；排除价格、优惠、抽奖、链接号、寒暄、闲聊、后台流程等。
- 首句独立、末句完整，枚举、因果、条件、转折、指代、承诺均须闭环。连接词只触发上下文检查，不能按关键词机械删除。
- 同一商品不同版本的**源帧重叠为零**，口播语义不能高度重复。
- 主播可见时长占比严格 `>70%`；衣服特写、无人镜头不计入。相邻同源片段若在 1–2 帧误差内连续且没有实际删内容，写入前必须合并。
- 状态文件指定的素材时间线只读；只能写状态文件指定的目标时间线。项目名、时间线名、路径必须取当前状态或用户明确指令，不可沿用旧项目值。

## 6. 实现边界与操作提醒

- 原始 SRT 应由本地脚本索引。GPT 后续优先查派生索引或最小必要字幕窗口，不反复读取整份 SRT，也不默认把完整 ASR/候选文本贴进对话。
- MiMo 只可接收本地预清理的标题、候选字幕、时间码、失败原因和返修摘要；不发送视频、音频、整份 SRT、凭据或无关本地标识。API 密钥来自环境变量 `MIMO_API_KEY`。
- 配置文件写了 `Apple Vision → Qwen 4B → Qwen 8B` 的目标顺序；当前 `person_coverage_audit_v2.py` 实际调用的是本地人物探测程序。不要仅凭配置断言 Qwen 回退已接入该审计脚本。
- 当前扩展候选脚本会读取既有 `product_XX_rough/mimo_request.json` 再改写；它不是空项目可直接独立运行的完整建池入口。接新批次时先核对前置文件、版本和脚本参数。
- 写 Resolve 前必须重新检查当前状态、审核清单、项目及时间线，并保留备份。清理由 `scripts/cleanup.py` 执行；默认 dry-run，只有明确加 `--apply` 才删除可复现数据。
- 永久默认规则改动写 `PROJECT_RULES.md` 并记 `CHANGELOG.md`；仅本批次变化写 `PROJECT_STATE.json`。流程语义升级时标记旧派生缓存失效。
