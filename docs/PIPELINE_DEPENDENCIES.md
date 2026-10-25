# Pipeline dependencies and runtime cache identity

## 当前淘宝注册调度链（2026-10-22）

实际注册调度顺序为：完整商品字幕候选池 → Codex 组合与单商品语义终审 → 首尾切点局部 ASR → 物理切点/声学边界精修 → 最终方案人物复核 → 本地硬门禁 → 已接通的交付适配器。语义终审回执绑定不可变 rough selection 摘要；PASS 后不在写入前重复做 Codex 语义终审。ASR 每段只处理首尾各 2.5 秒窗口，按源区间身份复用；人物比例超限会锁定通过版本，仅重组选受影响版本。各阶段计时保留每次调度耗时，不能把历史缺失的耗时估算成实测值。

依据：逐个检查当前 `scripts/` 的实际读取、写入与分支。表中 `<N>` 是商品编号，`R=.cache/product_<N>_rough/`，`W=.cache/product_<N>_windowed/`；`meta` 指同名文件后加 `.meta.json` 的身份文件。`PROJECT_STATE.json` 是本地状态，不进 Git。`config/*.json` 目前主要供规则说明和身份指纹；多数剪辑脚本的阈值仍写在源码中。

| stage | script | required inputs | generated outputs | validation | next stage |
| --- | --- | --- | --- | --- | --- |
| init | `init_project.py` / `tbcut.py init` | `PROJECT_STATE.template.json`；CLI 指定标题、字幕、视频、Resolve 名称 | `PROJECT_STATE.json` | 默认拒绝覆盖现有状态；未检查输入文件真实性 | fingerprint、subtitle index |
| fingerprint | `fingerprint_inputs.py` | `PROJECT_STATE.json` 的标题/字幕/视频路径 | `.cache/input_fingerprints.json` | 仅单文件计算 SHA-256；目录记录存在但 `sha256=null`，无人将此文件作为强制门槛 | subtitle index；身份 helper 另从配置输入直接计算指纹 |
| subtitle index | `index_subtitles.py` | `PROJECT_STATE.json`，SRT，与 SRT 同名的 MP4，`ffprobe` | `.cache/subtitle_index.json` + meta；更新本地状态中的字幕指纹/阶段 | SRT 内容指纹；新增批次、输入、规则、pipeline、meta schema、文件摘要检查；旧索引在再次运行本 stage 时从原始输入重建 | product ranges、expanded candidates |
| title index | `index_titles.py` → `inspect_title_workbook.mjs --json` | 当前 state 的 `title_path`；已有 Node 与 `@oai/artifact-tool`；Python 标准库 | `.cache/titles.json` + meta | 唯一可见 sheet、第一行表头、无损 ID、空行/缺字段/重复检查；当前 identity | product ranges、Resolve write |
| boundary suggestions / accepted ranges | `locate_link_intros.py`, `publish_confirmed_ranges.py`, `apply_range_overrides_v2.py` | authenticated subtitle index/titles; source-local human manifest for exactly current `batch.product_ids`, or authenticated override | locator: `boundary_suggestions.json`; publisher or explicit override: `product_ranges.json` | locator builds wide semantic windows from title attributes over continuous subtitle text; Excel row/encoding identify the catalog item but not its live time, and link mentions are auxiliary-only. Ambiguous evidence remains `PRODUCT_UNCERTAIN`. Rough windows are never accepted ranges; human publisher validates explicitly before physical editing | expanded requests and windowed ASR |
| expanded candidates | `build_expanded_codex_request.py` | 已验证的 `product_ranges.json`、`subtitle_index.json`、标题索引与 Resolve 项目模式 | `R/codex_expanded_request.json` + meta | 保留连续原字幕和逐字禁用子句注记；不在语义选择前删除相邻语境；时长与版本数从项目模式读取；旧候选协议重建 | Codex semantic selection |
| MiMo semantic stages | `mimo_review_v2.py`, `product_semantic_preview.py` | 每商品单独的本地预清洗标题、候选句群、时间引用；`MIMO_API_KEY`；MiMo v2.6 Pro | ignored per-product request/response/metrics and Markdown/JSON preview | A only assigns product ownership, extracts exact cited complete groups and relations; B only assembles from A-validated groups. Request fingerprint reuse; no video/audio or whole SRT. `closure_check` is untrusted | local hard audit, independent Codex review |
| semantic audit | `audit_mimo_v2.py` | 已验证的 request 与 MiMo 响应 | 指定 selection/audit JSON + meta | 输入身份；不使用 MiMo 的 `closure_check` 作证据；检查候选边界、禁用内容、时长、物理/语义重叠。硬规则通过但无独立 Codex 记录时标记 `REVIEW_PENDING` | repair or final quality gate |
| repair | `build_repair_request_v2.py`、`mimo_review_v2.py`、`merge_repair_result_v2.py`、`audit_mimo_v2.py`；后期失败可用 `collect_post_audit_failures.py`，必要时 `drop_failed_variants_v2.py` | 已验证的请求、selection、audit、返修响应；后期失败还需已验证的终审/人物审计；`drop` 还需 `EXHAUSTED` 证明 | 返修请求、响应、合并响应、复审/失败摘要及 meta | 返修上限、锁定通过版本、候选池剩余量；仅 `EXHAUSTED` 且证明完整搜索才可减条数 | `promote_rough_selection.py` |
| rough approval | `promote_rough_selection.py` | 已验证的 selection 与 PASS audit | `R/rough_selection.approved.json`、`R/rough_audit.approved.json` + meta | 仅 PASS 可提升，身份校验阻止串批次 | windowed ASR |
| windowed ASR | `windowed_asr_v2.py` | `PROJECT_STATE.json` 视频路径；已验证的 product ranges、粗选批准文件、现有 request；本机 `faster_whisper`、FFmpeg | `.cache/asr_windows/.../*.json`、`R/window_asr.json` + meta | 范围/粗选/request 身份；复用窗口时验证身份与模型名；只对选段附近转写 | `window_quality_v2.py`、refine plans |
| window quality | `window_quality_v2.py` | `PROJECT_STATE.json` 视频路径、已验证的 `R/window_asr.json`、FFmpeg | `R/window_quality.json` + meta | ASR 身份；FFmpeg 失败会报错 | refine plans |
| refine plans | `refine_plans_v2.py` | `PROJECT_STATE.json` 时间线名；已验证的粗选批准、expanded/base request、`window_asr.json`、`window_quality.json` | `W/cut_plan_variant_*.json`、`W/generated_plans.json`、`W/final_plan_audit.json` + meta | 输入身份；本地词/帧切点、时长、语义重复、源帧重叠、连续片段合并 | person coverage、final approval |
| person coverage | `project_scheduler.py` → `registered_pipeline.vision_audit()` → `person_coverage_audit_v2.registered_vision()` → `adaptive_vision.py` + `apple_frame_probe.swift` | Exact final physical plan, registered source media, current detector binary, `config/vision_rules.json`, and the editing policy frozen into the plan | Sealed `vision` checkpoint, per-frame physical cache and exact-plan digest | Current `person-presence-two-stage-record-v7`: 10 equal final-record strata; each absent/clothing-only midpoint triggers 10 local samples in a window capped at 6 seconds. Aggregate estimated no-person duration must be ≤10%; close-up duration independently follows the Resolve project mode (≤40% for `都督`, ≤30% for `淘宝`). This is an engineering sample, not framewise certification. Uncertain samples and detector errors block PASS. Old v4/v5 scoring snapshots cannot be reused; identity-matched underlying frame classifications can be reused. | final quality gate, checkpoint approval, Resolve preflight |
| final approval | `approve_final_plans.py` | 已验证的 `W/final_plan_audit.json`、`W/generated_plans.json`、所列方案 | `W/approved_plans.json` + meta | audit 仅 PASS/EXHAUSTED；过滤失败版本；方案时长严格 `>45s`、`<60s` | Resolve write |
| Resolve write | `resolve_write_v2.py` | Registered scope 下的已核准 checkpoint；非登记旧缓存被拒绝 | 新固定交付入口按本次授权限定目标时间线；未登记旧路径不执行写入 | 旧 `>70%` 可见比例产物不再被消费；登记链仍要求当前 exact-plan 质量门禁 | Resolve validation |
| Resolve validation | `validate_resolve_write_v2.py` | 已验证的写入报告、Resolve 当前时间线 | `.cache/resolve_write_validation.json` + meta | 报告 V2.1、项目名、源入出点 1 帧容差、音频及标记 | export |
| export | `export_marked_variants.py` | `PROJECT_STATE.json` Resolve 项目/目标时间线及商品标记；CLI 输出目录 | `.cache/export_manifest.json` + meta；仅 `--render` 时产生 MP4 | 当前项目名/时间线；按连续片段和音频终点设导出范围；每次更新 manifest 后刷新 meta | export verification |
| export verification | `verify_exports.py` | 已验证的 `.cache/export_manifest.json`、manifest 指向的 MP4、FFmpeg/ffprobe | `.cache/export_verification.json` + meta | 身份、文件数量/时长差、尾部黑帧 | 交付 |

`enforce_v2_request_rules.py` 可对已认证请求原位更新规则并刷新 meta，但它不能从零生成基础请求。`inspect_timeline_for_export.py`、`compare_timeline_versions.py`、`update_title_counts.mjs`、`update_batch_state.py` 是辅助/历史或交付后步骤，不是上述主链的自动前置步骤。时间线快照和历史对比仍未接入统一身份校验，勿当成当前批次依据。`project_doctor.py` 检查必需路径、state JSON 和配置字段、命令可用性，以及 Codex 托管 artifact-tool 包身份和 workbook API 导入；不读取素材、不验证 runtime 缓存、不连接 Resolve。

前置顺序：`init → subtitle index + title index → product ranges → expanded candidates → MiMo review → semantic audit →（必要时 repair）→ rough approval → windowed ASR → window quality → refine plans → person coverage + final approval → Resolve write → Resolve validation → export → export verification`。`fingerprint_inputs.py` 可在 init 后运行，但它的输出目前不是后续脚本的强制输入；统一身份 helper 会直接计算当前输入指纹。

## Hidden prerequisites confirmed

1. **标题检查与正式生成分离。** `inspect_title_workbook.mjs <workbook> <preview>` 保留摘要/预览行为；`<workbook> --json` 只向 stdout 返回结构化 sheet/cell values。`index_titles.py` 从当前 state 获取输入，独立校验后生成 titles 与 metadata，不读取旧 titles 作为数据来源。
2. **扩展候选不是独立建池器。** `build_expanded_codex_request.py` 先加载既有 `R/mimo_request.json`，再保留其 `rules` 和其余结构，替换候选并写 `codex_expanded_request.json`。因此它依赖旧请求底稿的先前生成过程；当前仓库没有从零生成这份底稿的主流程脚本。读取处要求输入身份有效。
3. **粗选和方案有固定文件名。** `windowed_asr_v2.py`、`refine_plans_v2.py`、人物审核和批准脚本按商品编号读写；不按批次分目录。身份检验用于防止误用其他批次数据。
4. **人物审核默认可能读旧批准清单。** 若 `approved_plans.json` 已存在，不加 `--generated` 就不会审查刚生成的方案；现在旧清单缺 meta 会被拒绝，不会悄然复用。
5. **导出清单也会串项目。** 旧 `export_manifest.json` 可保留在相同路径；`verify_exports.py` 现在先验身份再看其中的输出目录。

## Metadata contract

元数据放在 `<artifact>.meta.json`，不改原 JSON 的业务内容。核心字段：`schema_version=1`（**metadata 格式版本**）、`pipeline_version`、`batch_id`、`input_fingerprint`、`rules_fingerprint`、`created_at`、`producer`、`artifact_sha256`；商品级文件另有 `product_id`。调用方若知道原 JSON 的 schema，另检查原文件 `schema_version`。错误包含 `legacy_cache_missing_metadata`、`batch_id_mismatch`、`input_fingerprint_mismatch`、`pipeline_version_mismatch`、`schema_version_mismatch`、`rules_fingerprint_mismatch` 和 `artifact_sha256_mismatch`。

`input_fingerprint` 对标题与 SRT 文件内容取 SHA-256；视频用路径、大小和纳秒修改时间，避免重复读取大视频；还包含 Resolve 项目/时间线配置、当前批次商品编号和批次策略。它不包含 `project_root`，便于将 runtime workspace 物理分离。`rules_fingerprint` 使用 `runtime-rules-v2` 算法标识，覆盖 `PROJECT_RULES.md` 中影响 runtime 的内容及全部 `config/*.json` 的文件名和内容。仅排除三个明确的二级章节：`Working communication`、`Git and public repository workflow`、`Temporary public review report`。其余内容（包括未知章节）默认参与指纹；代码块内的标题不作为章节边界，未闭合代码块会拒绝处理。剪辑硬规则不得放入这些纯流程章节。这能识别常见输入/规则变化，但视频若在大小和修改时间完全不变的情况下被替换，单靠该指纹无法识别；若未来需要抵御这种情况，再引入视频内容摘要。

此前 `runtime-rules-v2` 修复仅改变身份计算范围，不改剪辑规则、pipeline 版本或 metadata schema。新算法不兼容旧全文件指纹：旧 metadata 仍会 fail closed，不自动认证、改写或接受旧摘要。此前新增标题编号规则也改变了规则摘要；本轮不改 PROJECT_RULES.md/config，真实 runtime 仍须在后续单独授权的恢复阶段重建。titles 已从 attest 白名单移除；摘要检查及拒绝覆盖已有 metadata 的行为保留。

代码本身未纳入指纹。剪辑语义发生变化时仍须按 `AGENTS.md` 升级 `pipeline_version` 并使旧派生产物失效；metadata 不代替这一步。

## Legacy and refresh path

- 旧 V2.1 JSON **保留原处，不自动删除、不自动补身份**。缺少 sidecar 就是 legacy；读取关键缓存的脚本会在外部调用或 Resolve 连接前失败。旧 MiMo 响应即使有历史 `_meta.json` 请求摘要，也不能单靠它证明批次身份，脚本不会自动重发。
- `index_subtitles.py` 可从原 SRT/MP4 重建；`index_titles.py` 可从当前工作簿重建。基础 `mimo_request.json` 仍没有完整生成器。缺少范围依赖记录的旧请求必须阻塞，不能通过 `attest-input` 重新盖章；重建入口需单独处理。人工覆盖记录仍可在独立授权、核对来源和文件摘要后走 `attest-input`，仅允许 `.cache/*range_overrides.json`。titles 必须由工作簿生成器重建，已有 metadata 不覆盖。

- 已认证请求对应的旧 MiMo 响应可走 `attest-response`：人工复核响应内容，并提供响应文件 SHA-256、当前批次/输入指纹、商品编号及**已认证的请求路径**。工具还会检查既有 `_meta.json` 的 `status=completed` 和 `request_digest` 是否等于该请求文件的 SHA-256；不匹配就拒绝。它不重发 MiMo，也不自动认证任何响应。
- 从已认证的输入重新运行商品范围、扩展候选、审核、ASR、方案和终审阶段，生成新 sidecar。**不要为旧终审/批准方案批量盖章**。若必须复用既有最终方案，需要另做逐文件来源核验与专门迁移；现有工具不提供绕过路径。
- 缓存刷新不改变 Resolve 历史结果；未通过身份校验的旧缓存不能作为写入或导出的有效依据。

## Workspace paths and relocation preflight

主链通过 `runtime_paths.py` 区分 repo 与 workspace：`--workspace` 优先，其次 `TB_CUT_WORKSPACE`，默认同目录。规则与 config 从 repo 读取，状态与派生产物从 workspace 读取。metadata schema 仍为 1，pipeline 仍为 2.1。

`migrate_runtime_layout.py` 默认 dry-run，`--apply` 才改写。它只对当前批次能由状态、源视频文件名、方案版本、批准清单包含关系和人物审计中的方案摘要交叉核验的引用做路径表示迁移；迁移前字节备份放在本地 `reports/runtime_migration_backups/`。旧方案内容、时间码、variant 和审核结果不变。带有效 metadata 的文件改写后才重建 sidecar；无 metadata 的文件绝不因路径迁移自动认证。工具再次运行不会继续改变已转换的文件。报告保存在本地 `reports/runtime_migration_report.json`，不进入 Git。

迁移工具扫描 runtime 目录中的业务 JSON，排除自己的报告及迁移前备份。无法证明来源的历史缓存保留为 stale，不自动认证。标题索引使用正式生成器从工作簿重建；基础 MiMo 请求缺少范围依赖时须重新生成，已有 metadata 的文件不能直接 attest；商品范围须显式确认与授权更新。终审、人物审计和批准方案必须从可信上游重新生成，不能靠路径迁移补 metadata。

`workspace_preflight.py` 以 `PROJECT_STATE.json` 配置的商品及全局关键缓存为检查范围，按字段语义验证 runtime 引用。状态中的 `project_root` 和外部素材绝对路径不视为 runtime 路径错误。遗留文件缺少 metadata 或身份不匹配时会阻塞预检；物理移动前须重新运行预检并处理所有阻塞。实际检查结果仅保存在本地报告中。

## Deterministic title index

正式入口：`python3 -B scripts/index_titles.py --root <repo> --workspace <workspace> --node <node-executable>`。必须使用 Codex 托管运行时解析到的 `@oai/artifact-tool`，并通过包身份和实际 API 能力检查；不按版本号阻断，不更换 XLSX parser，不使用旧缓存兜底。

Python 使用标准库读取 OOXML 的结构、原始数字和 number format 作为无损校验依据；业务单元格值由现有 Node workbook 路径提供。唯一可见 sheet、第一行精确表头、空行与连续编号、重复/缺字段、文本 ID 和数字零填充规则以 `PROJECT_RULES.md` 为准。公式、合并及隐藏结构拒绝处理。仅当本批状态显式设置 `batch_overrides.skip_blank_titles=true` 时，空标题行跳过且不占商品编号；该策略进入输入指纹。输出保留 `source`、`sheet`、`products`，新增 `schema_version=2`；当前生成器为 `index_titles.py:v4`（保留原工作簿行号时为 `v5`），并接受恰好一个 `编码 ID` 或 `ID` 表头。旧生成器缓存会因 provenance 不匹配自动从登记的工作簿重建。采用 UTF-8、两空格缩进及末尾换行。重复运行的 artifact 字节一致；metadata 的创建时间可变化。

两个生成器从开始到完成持有同一 cache 目录锁；并发调用立即失败。开始时固定 state bytes、expected identity 和完整 rules/config 摘要。subtitle 解析使用固定的 SRT bytes，并确认这些 bytes 与固定 input identity 一致；视频身份仍为路径、大小、纳秒修改时间，不是视频内容摘要。artifact/meta 在临时目录生成。发布前及发布各步骤之间重查 state、SRT、标题、视频身份和规则，变化则失败，发布前变化不写 artifact/meta/state。

发布是两个独立 rename，subtitle state 是第三个独立 rename，**不是跨文件事务，也不保证断电持久性**。先写 `<artifact>.invalid`；读者遇到标记即拒绝。发生发布错误时尽力恢复旧文件（包括 subtitle state），始终保留失效标记；rollback 自身失败也不能覆盖原始 publication error。即使残留 artifact 字节恰好匹配旧 SHA，标记仍阻止认证。只有完整受控重建成功才清除标记，禁止手工删标记或补签 metadata。锁约束协作生成器，不阻止外部编辑器；外部输入在最后检查后仍可改变，此后消费者重新计算 identity 时拒绝不匹配缓存，不能将文件系统描述为输入/输出跨文件事务。

`runtime_meta.load_checked_json()` 解析与 SHA 校验使用同一份 bytes，不再校验后重开文件。标题的两个消费者（范围生成、Resolve 写入）和 workspace preflight 均通过统一验证，要求 producer、titles schema/generator、连续商品编号、唯一字符串 ID、有效标题和 source/sheet。这些是来源约定，不是抵御恶意伪造的数字签名。subtitle 索引要求 `index_subtitles.py:v3` generator 标记；旧生成器产物不能仅凭通用 identity 复用。视频与字幕均按文件名数字区间排序后拼接全局时间。升级记录见 `docs/CACHE_SAFETY_UPGRADE.json`；真实 state/cache 未改写。

## Workbook runtime compatibility

日常模式信任 Codex 托管运行时解析出的 `@oai/artifact-tool` 包身份，不要求 Node 或依赖版本号命中白名单。每次运行检查包名、加载入口和 `FileBlob.load`、`SpreadsheetFile.importXlsx`、`SpreadsheetFile.exportXlsx` 能力；本地缓存记录当前包身份、版本、路径与分发摘要，供同一运行环境加速重复检查，不构成版本许可清单。

缓存失效或运行时包发生变化时重新检查其分发文件树和 API 导入；正式工作簿操作仍由本地结构检查、导出后回读及非目标内容保护门禁验证。若 API 缺失、包身份不符或真实工作簿结构不兼容，工作簿操作停止。`strict-history` 保留精确历史锁，仅供明确的历史复现使用。

The exact **2.8.93** records in `workbook-dependencies.lock.json` and `config/workbook-runtime-v4.lock.json` remain unchanged for historical reproduction. Run `node scripts/check_workbook_dependencies.mjs --mode strict-history` only when reproducing that distribution; it is not the daily default. These lock files are private bundled-distribution evidence, not an npm registry lock, so they do not claim that public `npm ci` can install the package.

日常检查：`<node-executable> scripts/check_workbook_dependencies.mjs`。Project Doctor 使用同一检查；工作簿解析与回填要求 `status=verified`。回填先导出临时文件并回读，比较非目标单元格值、公式和样式，核验计数后才原子替换。Excel 读写使用 Codex 托管 Node 与 artifact-tool，不切换其他解析器。真实本地工作簿操作仍需遵守登记输入和审核规则。

## Recovery authorization boundary

本轮仅代码修复，不执行真实恢复或 workspace preflight。代码重新审核通过并另获授权后，顺序为 `subtitle_index → titles → 只读 workspace preflight → 停止并报告`。该任务须记录每阶段执行前后的 identity/fingerprint/文件摘要、语义差异和剩余 blocker。preflight 只检查 state 配置、仓库依赖路径、已存在的关键缓存 identity（含标题 schema/provenance）及 runtime 路径引用；它会跳过部分缺失缓存，不证明完整上游齐备或剪辑硬规则通过。即使输出 READY，也不得称完整 runtime READY。`product_ranges` 始终需要再后面的单独授权，绝不自动继续。

## Authenticated range overrides

`apply_range_overrides_v2.py` validates current titles, subtitle index, existing ranges and the override input. Overrides may update an existing product or add a product missing from the ranges, but only if that product exists in current titles. Product identity fields always come from titles; override-supplied titles or encoding IDs are rejected. Duplicate overrides and unknown products are rejected. The sorted, unique result validates every range against current subtitle sources, including unchanged rows; partial overrides do not imply complete product coverage or reliable semantic boundaries.

Per-product source coverage does not establish cross-product boundary validity. Before staging or publication, `build_model()` checks every final range (including unchanged products) in actual `global_start` order, tracking the furthest preceding end. Any cross-product overlap greater than a fixed absolute `1e-9` seconds is rejected. This tolerance covers only floating-point noise in millisecond timestamps; it is not a one-millisecond allowance and does not grow with timestamp magnitude. Exactly touching boundaries are allowed. Errors contain only the overlap type and product indexes. No range is truncated, moved or silently repaired. Rejection leaves the previous artifact/metadata untouched and does not create an invalid marker. Output remains sorted by product index for serialization, independently of the live-time validation order. Non-overlap alone proves neither complete coverage nor semantic boundary correctness; both still require real boundary review.

The shared generator lock protects a fixed `BuildSnapshot`. Artifact, sidecar and invalid-marker snapshots of the consumed caches are rechecked before publication; source inputs, state and rules are also rechecked. Metadata authenticates a temporary artifact, and the staged pair is validated before `runtime_publication.publish_pair` publishes it. Publication uses two independent renames, not a cross-file transaction. Caught publication/rollback failures retain an invalid marker, preserve the original publication error and attempt to restore the previous bytes. Even restored bytes remain blocked by the marker. Prepublication validation failures leave the previous output pair untouched. No state update, automatic override attestation or marker bypass is performed.

This upgrade is code-only. Real boundary suggestions need independent review and separate application authorization; existing downstream artifacts are not certified by this change. After any authorized range change, dependent candidate/plan artifacts must be invalidated or regenerated before reuse.

## Boundary safety v1 (code-only)

The locator classifies every product as `confirmed`, `ambiguous`, or
`missing_or_low_confidence`. Here `confirmed` describes only an automatic anchor,
not a human-confirmed product boundary. Any unresolved product blocks the whole
automatic range set; its possible live position cannot safely be inferred from
product numbering. Diagnostics retain all anchor candidates. Confirmed anchors
are sorted by actual live time, never title-sheet order.

`complete_product_range()` remains a next-anchor/stream-end inference helper.
Its output is a suggestion requiring confirmation, not proof that the product
continues until that time. The locator now writes only `boundary_suggestions.json`
and exits 2 to stop automatic chaining. It never changes `link_intros.json`,
`product_ranges.json`, their metadata or invalid markers, regardless of confidence,
provenance, or missing old files. No existing downstream script consumes the new
suggestion file. Callers previously expecting locator-generated accepted ranges
must stop for separate confirmation instead. Removing accepted files cannot
promote a suggestion. `publish_confirmed_ranges.py` now accepts a complete
source-local manual manifest. It authenticates current titles/index, requires
one decision per title, validates every source and range, and runs the full
live-order overlap gate. Its default is read-only preflight. `--publish` is
required for first publication; an existing manually marked range set also
requires `--replace-confirmed`. Unknown-provenance ranges and incomplete markers
block replacement. Publication uses the existing lock, staged metadata and
fail-closed pair publisher. The per-decision SHA-256 links the reviewer, time,
source-local points and decision ID; it is a verifiable record checksum, not a
cryptographic user signature. No time is clamped or silently moved.

Explicit override application remains an alternative for a fully specified
source-local start/end update.
It records per-endpoint `explicit_override` provenance, without claiming human
review based on a confidence string. Invoking this writer requires separate
application authorization. It preserves actual gaps, checks every unchanged
range too, and never shifts a business start to a useful-selling-content start.
No current-batch coordinates are embedded in code or tests.

Coverage checks now inspect unrounded source topology before split rounding:
any real hole blocks a spanning product. Local override times must be inside
both source coverage and recorded duration when available; no clamping is used.
Split coordinates remain milliseconds. `SOURCE_REPRESENTATION_TOLERANCE_SECONDS`
is solely a representation check, never an allowance to bridge source gaps.
The cross-product `1e-9` absolute tolerance and furthest-end sweep are unchanged.

Downstream metadata binds candidate requests, reviews, ASR, plans and aggregate
outputs to the SHA-256 of authenticated `product_ranges.json`. A changed range
file, missing dependency metadata, or upstream invalid marker rejects reuse.
This conservatively invalidates all downstream products, even if just one range
changed. Base media/title/subtitle identity remains separate. Generators capture
`range_snapshot` before loading inputs and retain it when creating metadata;
a range change during generation cannot be silently rebound at completion.
The producer registry plus product IDs and known output paths identify legacy
dependents even when their sidecars have no dependency field. New producers
must be registered or provide a product ID. Do not bypass checked loaders.

The upstream generators' `BuildSnapshot` deliberately excludes the optional
range snapshot: the range writer changes that very output and separately checks
all consumed cache signatures. Cooperative locks, staged validation, rollback
and incomplete markers remain in force. Downstream stages that write directly
still fail validation on mismatched metadata; this change does not turn every
stage into an atomic publication transaction.

Legacy candidate attestation is refused: matching batch/input identity alone
does not prove which boundaries produced those candidates. Response attestation
still requires a matching authenticated current request. No stale artifact is
deleted, rebound or migrated by this upgrade. The expanded-request builder still
requires a current base request; legacy bootstrapping/regeneration needs a
separate authorized workflow, not automatic certification. This code-only change
does not assert Runtime READY or revalidate any real human boundary decisions.

## 第 1 轮：项目登记与断点基础（代码，尚未实测）

本节优先于上文旧版固定路径说明；新合同标识为 `project-resume-v2`，登记与阶段记录 schema 为 2。原 pipeline `2.1` 的剪辑硬规则没有改变。版本记录见 `docs/PROJECT_RESUME_UPGRADE.json`。

- `init_project.py` 首次登记稳定 Resolve ID、素材文件夹、标题/字幕/视频来源、时间线绑定、批次和商品清单。保存本地忽略的 `PROJECT_CONFIG.json` 与 `PROJECT_STATE.json`。项目身份只由稳定 Resolve ID 决定；按当前打开项目 ID 找到唯一绑定工作区。目录 inode 和显示名称不参与身份认证；素材路径可用性与时间线写入目标另行核验。素材路径更新需明确 `--force`；`--force` 不能切换项目身份。
- 新缓存目录为 `.cache/projects/<project_key>/batches/<batch_scope>/`；scope 包含批次 ID、商品清单与覆盖参数。reports/plans/exports 同样按项目和批次隔离。原固定文件名留在各自作用域，不回退查找旧根缓存。
- 两个真实上游生成器 `index_subtitles.py`、`index_titles.py` 已接入 `.cache/projects/<project_key>/inputs/<digest>/`。摘要包括整个配置输入、规则与生成器/helper/工作簿解析依赖版本；元数据明确 `scope=project_input`，只允许这两个 producer 跨批次复用。批次商品集合与批次 ID 不影响上游命中；批次审核仍严格校验批次。范围、方案和审核不使用这个豁免。模型与切点参数不靠移除 batch 校验解决冲突。
- 当前上游依赖粒度仍保守：一个源输入、全局规则或解析代码改变会生成新的项目索引命名空间；尚未做到单个源文件增量索引。视频仍用路径、大小和纳秒修改时间，无法识别刻意保持大小/时间的字节替换。没有声称所有计算缓存已精细迁移。
- `product_checkpoints.py` 保存不可覆盖的商品/阶段/版本结果。阶段身份绑定项目、商品编号与 encoding ID、输入、规则、覆盖参数、代码版本、模型标识和父结果 SHA。计算开始前必须 `capture_stage`，发布时重新验同一 snapshot；同身份不同内容拒绝覆盖。candidate/plan 要求当前 range 的直接依赖；新 range 使旧方案链失效。新 plan 更新当前方案记录，使旧 FINAL_PASS 立即 BLOCKED；重放历史结果不能把它重新设为当前方案。
- 检查点支持 `PENDING / IN_PROGRESS / REPAIRABLE / BLOCKED / FINAL_PASS / WRITTEN / EXPORT_VERIFIED`。FINAL_PASS 需要 final_plan 审核、当前方案摘要及 plan 依赖；WRITTEN 和 EXPORT_VERIFIED 需要逐级 VERIFIED 证据。选段 PASS 不能替代成片 PASS。记录器验证绑定关系，不运行语义/画面/时间线审核，不能凭人工填写 verdict 证明真实质量。
- `update_batch_state.py --inspect` 只读恢复清单；普通运行从重新核验的检查点恢复状态与完成集合。`--status` 更新单个商品时需要编码、对应方案/证据和 `--expected-revision`；裸 `--completed/--failed/--recovered/--stage` 已阻塞。账本先保存、状态后同步，中断后重复同步不会重复计数。完成集合表示已 FINAL_PASS 或更高，写入和导出以各状态分开统计，不能据此宣称项目全部交付。
- 登记、上游生成、阶段发布和检查点共用 workspace 目录锁，竞争者立即失败；检查点另有 revision 比较防止旧任务覆盖。JSON 使用独立临时文件、fsync 和原子 rename。登记双文件、方案/范围与当前记录是多次 rename，并非跨文件事务或断电持久性保证；中断标记阻塞读取，保留旧结果。标记只能通过后续明确恢复流程处理，本轮没有自动解除标记/认证旧产物。

### 第 2 轮必须接入的消费者

新登记模式下 `expected_identity(..., include_ranges=True)` 明确返回 `registered_downstream_not_integrated`，并拒绝降级传入 schema 1。已测试真实 refine 和 Resolve loader 在外部操作前停止；timeline snapshot、comparison、fingerprint 入口也先检查身份。旧 metadata 缺少新合同、旧原始状态、旧结果与新结果共存都不会自动认证。旧 schema 1 模式保留原有严格校验，不具备新断点能力；本轮不迁移真实工作区。

尚未接入：范围发现/确认/覆盖（`locate_link_intros.py`、`publish_confirmed_ranges.py`、`apply_range_overrides_v2.py`）；候选与选段/修复（`build_expanded_codex_request.py`、`audit_mimo_v2.py`、`build_repair_request_v2.py`、`merge_repair_result_v2.py`、`drop_failed_variants_v2.py`、`enforce_v2_request_rules.py`、`promote_rough_selection.py`、`collect_post_audit_failures.py`、`local_semantic_review_v2.py`、`mimo_review_v2.py`）；物理和画面审核（`windowed_asr_v2.py`、`window_quality_v2.py`、`refine_plans_v2.py`、`person_coverage_audit_v2.py`、`approve_final_plans.py`、`review_compute_cache.py`）；写入/导出（`resolve_write_v2.py`、`validate_resolve_write_v2.py`、`inspect_timeline_for_export.py`、`export_marked_variants.py`、`verify_exports.py`、`compare_timeline_versions.py`）；维护（`workspace_preflight.py`、`migrate_runtime_layout.py`、`attest_runtime_cache.py`、`fingerprint_inputs.py`、`cleanup.py`）以及完整调度入口。

这些入口需要接入不可变阶段记录与当前范围/方案依赖、计算复用、全项目进度和组间审核；尚不能把本轮登记模式用于真实完整流水线。Resolve 稳定 ID 的 API 回读、实际标记幂等性和全项目终审留待后轮及独立实测授权。本轮没有调用 MiMo、连接 Resolve、剪辑或导出。

## 第 2 轮：独立工作上下文与质量链（代码，合成验收）

本节优先于第 1 轮的下游“一律未接入”描述。登记 schema 仍为 2；质量合同升级为 `final-gate-v3`，项目调度清单 schema=3。相关代码内容摘要进入商品阶段身份，旧阶段/检查点不自动认证；真实 state/cache 未改。正式独立上下文接口已核对 [Codex 非交互文档](https://developers.openai.com/codex/noninteractive)。

### 新的真实入口

- `project_scheduler.py --workspace <workspace> --mode package|codex`：从认证标题索引得到全部非空商品，必要时沿用原项目配置更新完整目标清单。内部默认 3 款、可设 2 款；分组不改 batch identity。一组中失败或 REPAIRABLE 保留其他有效成果，并停止进入下一组。总控结果仅含编号、状态、数量和短错误，没有聊天/字幕/原始模型日志。
- `--product <index>` 是独立单商品入口；配置仍来自同一登记 workspace。每个商品用独立目录锁，checkpoint 用 workspace 锁/revision；不同商品可通过多个独立进程执行，同商品并发立即阻塞。默认 package 模式只发布结构化工作单，保存 REVIEW_PENDING/商品 BLOCKED，不冒充已自动执行。
- `codex_worker.py` 使用新 `codex exec --ephemeral --json --sandbox read-only --ignore-user-config` 进程，不用 resume/fork。隔离的临时工作目录只含本任务规则和 JSON schema，当前商品清理后的任务从 stdin 进入；禁止工具行为，收到工具事件、失败/超时、错误身份或缺完成事件就拒绝结果。每个商品选择与终审分开上下文；商品级语义终审通过后不重复执行项目级语义复核。
- CLI 成功结果绑定 task 摘要、不同 thread ID、完成事件摘要和返回结果；本地随机密钥封存执行记录。严格导入校验封存、任务角色、当前方案/输入及结果字段。该记录是本机执行来源完整性约定，不是防止本机所有者恶意伪造的远端数字签名。CLI 缺失/不可用时工作单留待审核；不会自动换模型或使用启发式冒充 Codex。
- 工作单只带当前商品的确认范围、认证候选或精修方案、规则/配置引用、有效阶段引用与简短问题；不传前一商品对话。超过 128KB 必须拆任务并 BLOCKED，不能截掉候选。任务、执行记录与详细文本只在本地忽略目录保存。

### 已接入的关键质量链

`registered_pipeline.py` 支持两类明确区分的范围来源：人工确认，或当前批次经用户授权的语义推断。语义推断必须保留标题特征锚点与精确字幕锚点、来源本地时间及授权来源；校验当前商品标题/索引、批次范围、商品顺序、来源覆盖和全局无重叠。锚点或身份不确定时阻塞，不伪装成人工确认。发布器将语义范围作为当前商品 range checkpoint，候选阶段仍按独立质量规则执行。

`registered_pipeline.py` 复用真实 `build_confirmed_ranges` 人工证明/源覆盖验证、`build_wide_candidates` 全范围清理、现有本机 Whisper 加载及合并窗口、`refine_words`、RMS 开头/句尾检查和 `normalize_physical_clips`。它把 range → candidate → Codex rough → local ASR → physical plan → exact vision → independent Codex final review → final audit 写入第 1 轮不可变商品记录，而非把旧固定路径产物贴新身份。

ASR/物理阶段计算前 capture_stage，完成后复核依赖；模型按需加载，已有有效窗口可复用。物理方案用源媒体帧率、向外保留词音的帧边界、当前完整范围，保存实际帧、同步音频范围、保留文字、词级窗口和接缝邻域。不通过截句尾凑时长；失败进入 REPAIRABLE/BLOCKED。源文件同名歧义及跨人工边界的粗字幕 cue 暂时阻塞，等待局部对齐，不能静默排除后称完整覆盖。

`final_quality_gate.py` 独立计算精确时长、禁用内容、词边界截断、音画范围一致、物理源重叠、跨版本文本重复和无意义接缝；所有检查只是本地 veto。完整逻辑、因果/条件/指代/列举/卖点以及语义重复仍需要独立 Codex；不按“因为/所以”关键词自动拒绝。最终任务带实际顺序/源帧、可靠词窗口和源邻域，绑定方案与终审输入摘要。任何切点/顺序/对齐/规则/源输入变化撤销旧审核。

独立终审命令：`final_quality_gate.py --workspace <workspace> --plan <stage-ref>` 默认只生成 REVIEW_PENDING；同时提供 `--vision <exact-plan-stage-ref> --receipt <sealed-Codex-receipt-ref>` 才能严格导入、重算门禁并更新 FINAL_PASS。直接人工写 verdict、粗选 PASS、旧 closure_check 或旧 final_audit 都不能更新新 FINAL_PASS。原 `approve_final_plans.py` 的登记模式现校验该链并发布不可变 approved 阶段；原 Resolve `load_products` 接入此审核读取，`resolve_write_v2.py --check-only` 可只读验证全项目，绝不连接 Resolve。登记模式真实写入仍显式 BLOCKED。

人物接口 `person_coverage_audit_v2.registered_vision` 对精确方案调用 `adaptive_vision.audit`。分类对象是“人物是否为明显画面主体”；`HOST_VISIBLE` 是兼容标签，不验证主播身份。Apple Vision 的人体框、面部框和姿态关键点按 `config/vision_rules.json` 的独立几何充分条件分类；小范围人物信号是 UNCERTAIN，空镜/无明显人物主体不计入。协议和阈值进入检测器指纹；精确方案摘要及封存仍由统一门禁校验。合成测试与真实素材质量分别报告。

### 项目完成与统计

商品级 Codex 语义终审及本地硬门禁通过后，项目级只检查商品清单与跨商品/跨组实际源帧重叠；不得再次发起 Codex 语义终审，也不得以字符串相似度代替语义判断。通过后进入 Resolve 身份核对、写入和写后回读。区分 final_review_complete、written_count、export_verified_count；未完成的导出验证仍单独反映在 delivery_complete 中。

淘宝注册追加器按全批次视频顺序在每两条成片视频之间留 2 秒（项目帧率换算），商品边界共用一个间隔；一条视频内部的物理素材段连续。纯布局修复依据写入回读快照识别待审预览，只重排已通过的源帧和同步音画并移动商品标记，不读取标题缓存或重新审核语义。

每商品按阶段记录实测耗时、模型/ASR/物理/人物/终审阶段、缓存命中和失效原因、失败状态及修复计数入口。不宣称优化幅度。商品级 REPAIRABLE 需要修正当前方案并重新提交，自动全池返修编排尚未完成。

### 尚未迁移，不能绕过

旧固定路径主流程的范围发布/覆盖、候选批量 CLI、MiMo/旧语义审稿 CLI、粗选修复/批准 CLI、ASR/window-quality/refine/person 的旧 batch CLI、真实 Resolve 写入与回读、导出及验证、迁移/attest/preflight/cleanup 等仍保持原身份门禁或明确登记模式 BLOCKED。本轮接入的是这些真实算法的登记适配器、批准入口与写前质量接口，不是所有旧 CLI 的兼容迁移。旧 schema 1 工具仍不具有新最终 Codex 质量许可。Resolve 稳定 ID 回读、真实检测器主播保证、VFR 的精确帧时间证明、自动返修/跨批次选择性物理计算复用和实际 timeline/export 证据需要后续收敛。真实素材、MiMo、Resolve 和导出均未执行。

旧 schema 1 的批准命令和 Resolve 写入命令也明确阻塞，不能走旧启发式许可绕过新终审。旧读取 helper 可作历史身份校验，不是新写入授权。实际 ASR 执行需要带本机 faster-whisper 依赖的 Python 环境及已确认模型版本；本轮不调用真实模型或媒体，未验证该执行环境。自动阶段拆包和自动语义返修仍未接入。

## 第 3 轮：主播分层审核与共享计算（2026-10-12；评分门槛已由 2026-10-19 P0 规则取代）

登记链：registered_pipeline → adaptive_vision → final_quality_gate（final-gate-v4-host-strata）→ product_checkpoints → approve_final_plans / project_scheduler / Resolve --check-only。新人物证据包含精确方案摘要、每个物理片段的源帧、轮次、分类及确定性采样策略；封存后才允许门禁复核。普通人检测只能 NO_PERSON/UNCERTAIN，不能证明主播主体。原 person batch CLI 明确阻塞旧计数许可，旧文件保留；注册模式使用 scheduler --detector 指向新 apple_frame_probe 的二进制。旧 person_coverage_probe 协议不自动兼容。

- 历史规则：初始 ceil(时长/60×16)，每个有时长的物理片段至少覆盖可用的首尾帧；短片段多时允许超过基础预算。固定种子抖动，追加最长未覆盖间隙的中点，最多五轮，不重复源帧；不同 FPS 用源帧映射。极短素材耗尽后停止。此采样规则已废止。
- 历史规则：每片段以邻近样本的时间分层权重估计，按实际片段时长汇总，并以严格 >70% 下界放行。此估值与门槛已废止，不用于当前门禁。
- ffprobe 全解码时间戳证明 CFR；r_frame_rate 不作证明。VFR、非零首帧时间戳、不完整时间戳明确 BLOCKED，精确 VFR 映射未编码。首次时间戳验证成本单独记录，后续共享封存 clock 缓存。
- compute_store/compute_v3 只共享底层 ASR、clock 和源帧分类。项目 UUID、真实媒体路径/device/inode/大小/mtime/内容 SHA、模型权重/库版本、范围/帧、参数和协议参与身份。锁、原子发布、HMAC 和正文摘要拒绝静默覆盖及无证明缓存。旧目录不导入，不清空；损坏新版记录阻塞而非自动重算认证。签名用于本机执行完整性，不能抵御持有本机密钥的所有者。
- ASR 重叠/相邻任务仍用既有 merge_windows；相同或被已验证窗口包含的请求共享完整词窗口，保留更大上下文，当前 ASR 阶段另绑定粗选依赖。模型加载只在 miss；权重本机解析 local_files_only，不下载。模型权重文件 SHA 与实际 Whisper/CT2 版本入身份，进程内按文件 stat 复用权重 SHA。模型/参数更新在许可和 cursor 中复核，重建受影响方案及终审。
- compute_metrics 记录候选、选择/终审/项目审核请求等待、ASR 加载/转写/VAD、音频解码/RMS、视频抽帧/Vision、质量与检查点、cache/签名/clock，统计次数、单位量、hits、失效原因、错误和重试。Qwen/MiMo 未调用计零。时长为包含子阶段的墙钟，不能相加宣称总耗时；ASR/VAD 内置一体，未声称拆出独立 VAD 耗时。

真实环境状态按本轮实际记录更新。未完成的 workbook、ASR、Resolve 写入与回读门禁仍需分别验证；主播身份库、Qwen 或人物追踪不属于人物占比的依赖。

未编码/未迁移：真实 Resolve 写入/回读/幂等恢复与导出验证、精确 VFR、完整自动语义返修；旧固定路径候选/范围覆盖、window_quality/ASR/refine batch CLI、旧粗选/语义/repair、validate/export/attest/migration/preflight/cleanup 仍不作为新登记链消费者。保留第 2 轮阻塞，不宣称全面迁移。统一批准和 Resolve loader/check-only 使用新门禁，实际写入入口仍拒绝。

来源：Apple VNDetectHumanRectanglesRequest 仅检测人体（https://developer.apple.com/documentation/vision/vndetecthumanrectanglesrequest）；Wilson 的二项比例公式参考 NIST（https://itl.nist.gov/div898/handbook/prc/section2/prc241.htm），连续视频相关性限制由本轮明确保守处理，不作该公式的统计保证。

## 句首约束与新商品试跑

- `sentence_boundaries.py` 负责句子原文对齐、绑定逐词上下文的有界修边及半帧取整估计；静音不能替代语义独立判断。
- `registered_pipeline.py` 和旧版物理生成入口均停止按附近较大停顿改选首词；`final_quality_gate.py` 升级到v7，缺失或过期的句首证据会被否决。
- `sentence_trial.py --workspace ... --product ...` 复用显式新运行中已校验的索引，以单个执行器完成选句、本机ASR、物理检查与独立复核。事件日志区分失败、同轮缓存、阶段执行和执行间间隔。
- 词边界不可切时，`sentence_trial_repair.py` 可在同轮ASR的安全整帧边界内做一次整池重选；不修改原ASR时间，不把候选减少伪报为完成，不授予草稿写入豁免。
- 该试跑入口不写Resolve；人物与时间线阶段只有在前序检查真正通过后才有意义。试跑失败应记录为REPAIRABLE，不能宣称成品通过或整轮提速。

- Codex负责语义，本机负责定位与声学风险。ASR取整不是音素准确性认证；20ms窗口起点对齐发生在真实声音截取之前，原ASR结果不后移或重写。

## 切点附近声音与字音归属

- `audio_boundary_evidence.py` 的 `cut-local-activity-v1` 只检查切点两侧紧邻且完整的电平窗；后方重新起声不再自动算作跨切点延续。浮点数误差不把右侧窗口算到左侧，缺失或无效窗口保持未知。
- `refine_plans_v2.py` 的首尾检查使用该观察值；旧 `speech_*` 字段仅保留兼容，明确记录风险范围为声音连续性。连续声音不是已证实吞字；没有连续声音也不是完整字音认证。
- `sentence_audio_diagnostic.py` 对明确工作区内的既有方案做局部核实，不重新选句、不改原始ASR或切点。风险窗复用带素材/模型/参数/范围身份及完整性封存的源级计算，缓存齐全时不加载模型。原方案、输入正文摘要、源媒体内容、模型及执行期间生产者均需核对。
- 结果仅为 `DIAGNOSTIC_ONLY`，声音完整性仍是 `UNVERIFIED`，不签发最终门禁或写入许可。新算法使旧声音汇总失效；身份匹配的原ASR和未改动方案的语义证据仍可用于各自职责范围。真实阶段计时及失败、缓存命中留本地，不发布私有运行正文。

`owned_audio_refinement.py`先记录20ms RMS/峰值/样本范围波形，再按“词完整优先、静音/低能量优先；连续口播时取相邻ASR词界之间最低能量帧”确定物理切点。连续口播回退只改切点，不改ASR词或语义组合；找不到合法词间帧时生成音频专属返修记录，保留选段/ASR/卖点清单，不退回语义选段。修边后方案摘要变更，最终语义和人物证据必须绑定新摘要。该记录不证明音素精度。

Resolve回读原始整数源帧不改写；当其恰好少一帧时，只有精确源时间乘FPS及左侧偏移均在百万分之一帧内与计划一致，才接受浮点截断解释；缺失、冲突或实际偏移均拒绝。

## 固定交付入口（2026-10-19）

`delivery_pipeline.py`使用显式workspace和JSON配方，消费已核验索引、真人范围、完整池选段记录、原始ASR词选择；`delivery_assembly.py`复用现有有界句边/normalize及sealed源级计算，`delivery_runtime.py`提供分项事件账和分阶段不可变缓存，`delivery_resolve.py`实现仅新建合并时间线的备份、事务、恢复和回读。完整命令、配方和授权边界见[DELIVERY_EXECUTION.md](DELIVERY_EXECUTION.md)。

这是先前单商品临时执行脚本的通用替代，未解锁历史registered writer或旧批量CLI，也没有迁移旧证据许可。每段声音可独立复用，方案变化撤销全局语义和人物汇总；底层人物源帧/clock/ASR仍按当前身份核验复用。运行中代码/配方变化停止。全部审核通过后才写入，独立新进程回读成功才记FINAL_VERIFIED。正常运行不编写/重构代码，代码缺陷另记development与repair。

循环测试线模式按当前项目登记的名称与稳定ID核验，只将该线从保护快照中单独列出；全部审核、源指纹与备份通过后才清空并复写，其他线全部保护。恢复和独立回读不清空、不重复追加。源结束帧精度核实同时读取精确结束时间和左偏移加时长，原整数读数不改写；两份证据不一致时仍阻断。

### 组合细审与帧率登记前置

`project-read` → 同项目身份绑定的帧率登记；`assembly-task` → 主Codex逐段/逐接缝实际审读 → 绑定任务摘要的 `assembly_review` → 组装/局部声音 → 精确方案独立终审及人物 → 备份/写入/精确回读。初审不增加外部调用，不取代终审；缺少或过期的初审在昂贵计算前阻断。`delivery_editorial.py`/`delivery_fps.py`纳入组装生产者指纹，新manifest为 `fixed-delivery-v2-early-editorial-fixed-rates`，旧计划不自动继承新契约PASS。写前帧率只读一致性校验在清空前执行，既有线帧率不设置，元数据探测和完整帧时钟证明职责分开。
