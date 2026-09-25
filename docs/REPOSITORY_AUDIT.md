# Repository audit

本文记录公开仓库与本地 runtime 的安全边界及结构性限制，不记录具体机器、批次或运行结果。

关键缓存使用 metadata 身份校验；阶段依赖和遗留缓存处理方式见 `docs/PIPELINE_DEPENDENCIES.md`。

## Repository scope

Git repo 保存可复用的源码、长期配置、长期规则、架构文档、脱敏模板和合成测试。它不保存真实批次的输入、状态、中间数据、报告、工程备份或成片。

**Runtime workspace** 是同一个项目目录在个人电脑上的运行形态：从模板生成本地 `PROJECT_STATE.json`，从用户指定位置读取标题表、字幕与视频，在 `.cache/` 生成索引和方案，在 `reports/`、`plans/`、`exports/` 放运行结果。此工作区可继续存在；`.gitignore` 只限定未来提交边界，不删除本地数据。导入已有 Git 历史时，须另行检查历史对象；忽略规则不会清除已提交内容。

## Files safe to commit

| 类别 | 现有文件或目录 | 说明 |
| --- | --- | --- |
| 源码 | `tbcut.py`、`scripts/*.py`、`scripts/*.mjs`、`scripts/person_coverage_probe.swift` | 包含维护入口、处理、审计、Resolve 写入和导出工具；提交前按下文技术债复核。 |
| 长期配置 | `config/content_rules.json`、`config/vision_rules.json`、`config/local_models.json` | 通用规则与模型策略，无本批次输入。 |
| 长期文档 | `README.md`、`PROJECT_OVERVIEW_FOR_GPT.md`、`PROJECT_RULES.md`、`AGENTS.md`、`MIGRATION.md`、`CHANGELOG.md`、`TB_CUT_V2_1_CODEX_UPDATE.md`、本文件 | 操作入口、规则、设计背景和审计。需求背景文档不替代源码事实。 |
| 脱敏模板 | `PROJECT_STATE.template.json` | `null` 表示必须由使用者提供的路径、项目名或批次值；数组 `[]` 表示尚未选择。没有真实本机路径或当前 Resolve 项目名。保留现有 schema 与字段。 |
| 测试 | `tests/*.py` | 使用人工合成片段与文本；不得将真实业务数据作为 fixture。 |
| 仓库边界 | `.gitignore` | 忽略本地状态、数据、输出和依赖。 |

`scripts/` 的主要分组：

- 初始化/输入：`init_project.py`、`project_doctor.py`、`fingerprint_inputs.py`、`index_subtitles.py`、`inspect_title_workbook.mjs`。
- 商品范围与候选：`locate_link_intros.py`、`apply_range_overrides_v2.py`、`build_expanded_codex_request.py`、`enforce_v2_request_rules.py`。
- MiMo 与返修：`mimo_review_v2.py`、`audit_mimo_v2.py`、`build_repair_request_v2.py`、`merge_repair_result_v2.py`、`collect_post_audit_failures.py`、`drop_failed_variants_v2.py`、`promote_rough_selection.py`。
- 切点和人物审计：`windowed_asr_v2.py`、`window_quality_v2.py`、`refine_plans_v2.py`、`person_coverage_audit_v2.py`、`person_coverage_probe.swift`、`approve_final_plans.py`。
- Resolve、交付和维护：`resolve_write_v2.py`、`validate_resolve_write_v2.py`、`inspect_timeline_for_export.py`、`export_marked_variants.py`、`verify_exports.py`、`compare_timeline_versions.py`、`query_table_resize.mjs`、`update_title_counts.mjs`、`update_batch_state.py`、`cleanup.py`。

## Files that must stay local

| 类别 | 现有位置或类型 | 原因 |
| --- | --- | --- |
| 本地 runtime state | `PROJECT_STATE.json` | 含个人绝对路径、真实批次和 Resolve 项目信息。它不属于 Git 仓库。 |
| 可再生 cache | `.cache/` 全部，包括 `subtitle_index.json`、`titles.json`、`product_ranges.json`、`product_XX_rough/`、`product_XX_windowed/`、`asr/`、`asr_windows/`、`mimo/`、`vision/`、编译缓存 | 可含完整字幕、局部 ASR、商品标题、模型请求与响应、源视频路径、切点、旧项目数据。禁止挑选真实文件作公开示例。 |
| 工程备份 | `.cache/backups/*.drp` 及其他 Resolve 工程文件 | 可恢复真实项目和素材引用。 |
| 原始输入 | 用户提供的 `.srt`、视频/音频、标题工作簿及其他原始素材 | 用户业务数据；通常在仓库目录外，但仍由全局忽略规则挡住误放入仓库的文件。 |
| 输出 | `exports/`、`plans/`、`reports/` | 成片、切片方案和带真实商品信息的批次报告。运行报告必须保持本地。 |
| 历史遗留/待确认 | `TIMELINE_DIFFERENCE_REPORT.md` | 可能含项目时间线和商品级结果；按忽略规则留在本地。 |
| 本地依赖/临时文件 | `node_modules/`、`.artifact_runtime/`、虚拟环境、`__pycache__/`、`.DS_Store` | 可安装或可再生，与源码无关。 |
| 密钥 | `.env`、`.env.*`、任何 API key/token/私钥文件 | 不得进入提交或公开日志。 |

## Security boundaries

- **Secret / token**：提交前扫描待提交内容，禁止明文凭据。MiMo 代码读取环境变量 `MIMO_API_KEY`，不将密钥写入配置或文档。工程备份整体保持本地。
- **本机路径**：本地状态、缓存和报告可能包含用户主目录、输入路径和 Resolve 项目名；公开示例只能使用合成占位值。
- **原始用户数据**：报告和时间线对比结果可能包含商品编码、标题及剪辑指标，不得复制到公开长期文档。
- **字幕/ASR/模型数据**：字幕索引、候选池、模型请求响应、ASR 与切点文件可能包含原始业务文本，必须留在 runtime workspace。
- **硬编码系统路径**：Resolve 脚本采用 macOS 的 `/Library/.../Scripting` 和 `/Applications/.../fusionscript.so` 安装位置。这是平台假设，不是个人路径或密钥。
- **私有业务名称**：真实批次 ID、项目名、商品名和标题工作簿名只允许保存在本地状态和 runtime 数据中。

## Architecture issues discovered

以下是源码及依赖的结构性限制：

1. `tbcut.py` 仅包装少数维护命令；完整处理链没有统一入口。多个脚本直接读取约定的 `.cache` 文件，前置条件未集中声明。例：`build_expanded_codex_request.py` 必须先有 `product_ranges.json`、`subtitle_index.json` 和每款旧 `mimo_request.json`。
2. `.cache` 文件名多按商品编号固定，未按批次隔离；同名文件可能来自不同批次。仅文件存在不能证明身份，读取前须验证 metadata。
3. 版本、schema 和指纹不统一：`subtitle_index.json` 有指纹；`titles.json` 无顶层 schema/指纹；`product_ranges.json` 有 schema 但无输入指纹或 pipeline 版本；`export_manifest.json` 无顶层 schema/指纹；MiMo 原始响应也无统一版本字段。业务 JSON 的 schema 不统一，调用方还须按阶段检查 schema；统一身份由 sidecar metadata 提供。
4. 输入与规则有多个来源：`PROJECT_RULES.md`、`config/*.json`、脚本中的正则/阈值、`PROJECT_STATE.json`、缓存中的规则副本。更新一处后不一定自动使全部派生产物失效。
5. Resolve API 安装路径写死 macOS 系统目录。项目名与时间线名在主要写入脚本中从当前状态读取；不得用历史报告里的项目值代替配置。
6. `PROJECT_STATE.template.json` 用 `null` 安全表示必填占位值，但仓库未有字段级初始化指南；`project_doctor.py` 只做基础存在性检查，不验证缓存批次、版本或 Resolve 连接。
7. `config/local_models.json` 描述 Apple Vision → Qwen 回退策略，而当前人物占比审计脚本只调用本地探测程序；配置与实际接入范围需进一步对齐。
8. JS 工具使用 `@oai/artifact-tool`，仓库没有包清单；`node_modules/` 是本机依赖，不能当作可复现安装说明。
9. `compare_timeline_versions.py` 等历史辅助脚本依赖时间线快照；未通过身份核验的历史结果不能作为主链输入。

## Recommended next migration

1. 在隔离的 Git 初始化/发布流程中复核 `git status` 和待提交清单；若导入旧 Git 历史，还需审计历史对象，`.gitignore` 不能清除已提交数据。
2. 为本地状态模板写字段级初始化指南，补一个只用人工占位数据的最小示例；不要复制真实缓存。
3. 维护派生文件的批次、schema、pipeline 和指纹校验；新增读取点时检查覆盖范围，并制定兼容与失效方案。
4. 梳理脚本前置条件和分段运行命令，提供安全的只读预检入口；维持现有剪辑规则与 Resolve 写入行为。
5. 确认 JS 依赖的公开安装方式，并复核历史辅助脚本是否仍被使用，再决定目录迁移。

## Workspace paths and migration tooling

主链通过 `scripts/runtime_paths.py` 解析 repo 与 runtime workspace，默认同目录，支持 `--workspace` 或 `TB_CUT_WORKSPACE`。`scripts/workspace_preflight.py` 提供只读迁移预检，区分外部输入路径和 runtime 引用。

`scripts/migrate_runtime_layout.py` 默认 dry-run，显式 apply 时仅转换来源可核验的 runtime 路径表示，并在本地保存备份和报告。它不执行物理 workspace 移动、不删除历史文件，也不给缺少身份凭证的派生结果补 metadata。遗留缓存须证明来源或从可信上游重建；实际迁移和预检结果只保存在被忽略的本地报告中。
