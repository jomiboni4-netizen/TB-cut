# Pipeline dependencies and runtime cache identity

依据：逐个检查当前 `scripts/` 的实际读取、写入与分支。表中 `<N>` 是商品编号，`R=.cache/product_<N>_rough/`，`W=.cache/product_<N>_windowed/`；`meta` 指同名文件后加 `.meta.json` 的身份文件。`PROJECT_STATE.json` 是本地状态，不进 Git。`config/*.json` 目前主要供规则说明和身份指纹；多数剪辑脚本的阈值仍写在源码中。

| stage | script | required inputs | generated outputs | validation | next stage |
| --- | --- | --- | --- | --- | --- |
| init | `init_project.py` / `tbcut.py init` | `PROJECT_STATE.template.json`；CLI 指定标题、字幕、视频、Resolve 名称 | `PROJECT_STATE.json` | 默认拒绝覆盖现有状态；未检查输入文件真实性 | fingerprint、subtitle index |
| fingerprint | `fingerprint_inputs.py` | `PROJECT_STATE.json` 的标题/字幕/视频路径 | `.cache/input_fingerprints.json` | 仅单文件计算 SHA-256；目录记录存在但 `sha256=null`，无人将此文件作为强制门槛 | subtitle index；身份 helper 另从配置输入直接计算指纹 |
| subtitle index | `index_subtitles.py` | `PROJECT_STATE.json`，SRT，与 SRT 同名的 MP4，`ffprobe` | `.cache/subtitle_index.json` + meta；更新本地状态中的字幕指纹/阶段 | SRT 内容指纹；新增批次、输入、规则、pipeline、meta schema、文件摘要检查；旧索引在再次运行本 stage 时从原始输入重建 | product ranges、expanded candidates |
| title index | `index_titles.py` → `inspect_title_workbook.mjs --json` | 当前 state 的 `title_path`；已有 Node 与 `@oai/artifact-tool`；Python 标准库 | `.cache/titles.json` + meta | 唯一可见 sheet、第一行表头、无损 ID、空行/缺字段/重复检查；当前 identity | product ranges、Resolve write |
| boundary suggestions / accepted ranges | `locate_link_intros.py`, `publish_confirmed_ranges.py`, `apply_range_overrides_v2.py` | authenticated subtitle index/titles; complete source-local human manifest or authenticated override | locator: `boundary_suggestions.json`; publisher or explicit override: `product_ranges.json` | unresolved anchors block suggestions; human publisher validates all rows and overlap before explicit publish; locator never writes accepted ranges | expanded requests and windowed ASR |
| expanded candidates | `build_expanded_codex_request.py` | 已验证的 `product_ranges.json`、`subtitle_index.json`、**已有 `R/mimo_request.json`** | `R/codex_expanded_request.json` + meta | 输入身份、商品 ID、范围/索引 schema；候选按禁用词过滤，无时长上限 | MiMo review |
| MiMo review | `mimo_review_v2.py` | 已验证的 request JSON，环境变量 `MIMO_API_KEY`；远端 MiMo | 指定响应 JSON + meta，另有旧式 `_meta.json` 请求摘要/状态和临时 lock | 请求身份；已有响应必须有有效 meta 且请求摘要相同才命中，遗留响应不自动重发或覆盖 | semantic audit |
| semantic audit | `audit_mimo_v2.py` | 已验证的 request 与 MiMo 响应 | 指定 selection/audit JSON + meta | 输入身份；检查候选边界、禁用内容、语义闭合、时长、重叠、语义重复；不通过仍写审核供返修 | repair 或粗选批准 |
| repair | `build_repair_request_v2.py`、`mimo_review_v2.py`、`merge_repair_result_v2.py`、`audit_mimo_v2.py`；后期失败可用 `collect_post_audit_failures.py`，必要时 `drop_failed_variants_v2.py` | 已验证的请求、selection、audit、返修响应；后期失败还需已验证的终审/人物审计；`drop` 还需 `EXHAUSTED` 证明 | 返修请求、响应、合并响应、复审/失败摘要及 meta | 返修上限、锁定通过版本、候选池剩余量；仅 `EXHAUSTED` 且证明完整搜索才可减条数 | `promote_rough_selection.py` |
| rough approval | `promote_rough_selection.py` | 已验证的 selection 与 PASS audit | `R/rough_selection.approved.json`、`R/rough_audit.approved.json` + meta | 仅 PASS 可提升，身份校验阻止串批次 | windowed ASR |
| windowed ASR | `windowed_asr_v2.py` | `PROJECT_STATE.json` 视频路径；已验证的 product ranges、粗选批准文件、现有 request；本机 `faster_whisper`、FFmpeg | `.cache/asr_windows/.../*.json`、`R/window_asr.json` + meta | 范围/粗选/request 身份；复用窗口时验证身份与模型名；只对选段附近转写 | `window_quality_v2.py`、refine plans |
| window quality | `window_quality_v2.py` | `PROJECT_STATE.json` 视频路径、已验证的 `R/window_asr.json`、FFmpeg | `R/window_quality.json` + meta | ASR 身份；FFmpeg 失败会报错 | refine plans |
| refine plans | `refine_plans_v2.py` | `PROJECT_STATE.json` 时间线名；已验证的粗选批准、expanded/base request、`window_asr.json`、`window_quality.json` | `W/cut_plan_variant_*.json`、`W/generated_plans.json`、`W/final_plan_audit.json` + meta | 输入身份；本地词/帧切点、时长、语义重复、源帧重叠、连续片段合并 | person coverage、final approval |
| person coverage | `person_coverage_audit_v2.py`、本地 `person_coverage_probe` | `PROJECT_STATE.json` 视频路径；默认已验证的 `W/approved_plans.json`，`--generated` 时读已验证的 `generated_plans.json`；各方案 | `.cache/person_coverage_audit.json` + meta | 方案身份与摘要；主播可见占比严格 `>70%`。有旧 approved 清单时默认优先读取，想查新生成方案需 `--generated` | final approval、Resolve write |
| final approval | `approve_final_plans.py` | 已验证的 `W/final_plan_audit.json`、`W/generated_plans.json`、所列方案 | `W/approved_plans.json` + meta | audit 仅 PASS/EXHAUSTED；过滤失败版本；方案时长严格 `>45s`、`<60s` | Resolve write |
| Resolve write | `resolve_write_v2.py` | `PROJECT_STATE.json`；已验证的 `.cache/titles.json`、人物审核、每款粗审/终审/批准清单/方案；Resolve 当前项目、源与目标时间线 | `.cache/backups/*.drp`；目标时间线追加音视频和标记；`.cache/resolve_write_report.json` + meta | **连接 Resolve 之前**先验证所有缓存身份、schema 与原有剪辑硬门槛；然后项目/时间线核对、备份、写后帧/音频/标记核对 | Resolve validation |
| Resolve validation | `validate_resolve_write_v2.py` | 已验证的写入报告、Resolve 当前时间线 | `.cache/resolve_write_validation.json` + meta | 报告 V2.1、项目名、源入出点 1 帧容差、音频及标记 | export |
| export | `export_marked_variants.py` | `PROJECT_STATE.json` Resolve 项目/目标时间线及商品标记；CLI 输出目录 | `.cache/export_manifest.json` + meta；仅 `--render` 时产生 MP4 | 当前项目名/时间线；按连续片段和音频终点设导出范围；每次更新 manifest 后刷新 meta | export verification |
| export verification | `verify_exports.py` | 已验证的 `.cache/export_manifest.json`、manifest 指向的 MP4、FFmpeg/ffprobe | `.cache/export_verification.json` + meta | 身份、文件数量/时长差、尾部黑帧 | 交付 |

`enforce_v2_request_rules.py` 可对已认证请求原位更新规则并刷新 meta，但它不能从零生成基础请求。`inspect_timeline_for_export.py`、`compare_timeline_versions.py`、`update_title_counts.mjs`、`update_batch_state.py` 是辅助/历史或交付后步骤，不是上述主链的自动前置步骤。时间线快照和历史对比仍未接入统一身份校验，勿当成当前批次依据。`project_doctor.py` 检查必需路径、state JSON 和配置字段、命令可用性，以及固定 Node/module 版本、分发包摘要和 workbook API 导入；不读取素材、不验证 runtime 缓存、不连接 Resolve。

前置顺序：`init → subtitle index + title index → product ranges → expanded candidates → MiMo review → semantic audit →（必要时 repair）→ rough approval → windowed ASR → window quality → refine plans → person coverage + final approval → Resolve write → Resolve validation → export → export verification`。`fingerprint_inputs.py` 可在 init 后运行，但它的输出目前不是后续脚本的强制输入；统一身份 helper 会直接计算当前输入指纹。

## Hidden prerequisites confirmed

1. **标题检查与正式生成分离。** `inspect_title_workbook.mjs <workbook> <preview>` 保留摘要/预览行为；`<workbook> --json` 只向 stdout 返回结构化 sheet/cell values。`index_titles.py` 从当前 state 获取输入，独立校验后生成 titles 与 metadata，不读取旧 titles 作为数据来源。
2. **扩展候选不是独立建池器。** `build_expanded_codex_request.py` 先加载既有 `R/mimo_request.json`，再保留其 `rules` 和其余结构，替换候选并写 `codex_expanded_request.json`。因此它依赖旧请求底稿的先前生成过程；当前仓库没有从零生成这份底稿的主流程脚本。读取处要求输入身份有效。
3. **粗选和方案有固定文件名。** `windowed_asr_v2.py`、`refine_plans_v2.py`、人物审核和批准脚本按商品编号读写；不按批次分目录。身份检验用于防止误用其他批次数据。
4. **人物审核默认可能读旧批准清单。** 若 `approved_plans.json` 已存在，不加 `--generated` 就不会审查刚生成的方案；现在旧清单缺 meta 会被拒绝，不会悄然复用。
5. **导出清单也会串项目。** 旧 `export_manifest.json` 可保留在相同路径；`verify_exports.py` 现在先验身份再看其中的输出目录。

## Metadata contract

元数据放在 `<artifact>.meta.json`，不改原 JSON 的业务内容。核心字段：`schema_version=1`（**metadata 格式版本**）、`pipeline_version`、`batch_id`、`input_fingerprint`、`rules_fingerprint`、`created_at`、`producer`、`artifact_sha256`；商品级文件另有 `product_id`。调用方若知道原 JSON 的 schema，另检查原文件 `schema_version`。错误包含 `legacy_cache_missing_metadata`、`batch_id_mismatch`、`input_fingerprint_mismatch`、`pipeline_version_mismatch`、`schema_version_mismatch`、`rules_fingerprint_mismatch` 和 `artifact_sha256_mismatch`。

`input_fingerprint` 对标题与 SRT 文件内容取 SHA-256；视频用路径、大小和纳秒修改时间，避免重复读取大视频；还包含 Resolve 项目/时间线配置。它不包含 `project_root`，便于将 runtime workspace 物理分离。`rules_fingerprint` 使用 `runtime-rules-v2` 算法标识，覆盖 `PROJECT_RULES.md` 中影响 runtime 的内容及全部 `config/*.json` 的文件名和内容。仅排除三个明确的二级章节：`Working communication`、`Git and public repository workflow`、`Temporary public review report`。其余内容（包括未知章节）默认参与指纹；代码块内的标题不作为章节边界，未闭合代码块会拒绝处理。剪辑硬规则不得放入这些纯流程章节。这能识别常见输入/规则变化，但视频若在大小和修改时间完全不变的情况下被替换，单靠该指纹无法识别；若未来需要抵御这种情况，再引入视频内容摘要。

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

正式入口：`python3 -B scripts/index_titles.py --root <repo> --workspace <workspace> --node <node-executable>`。Node 和 `@oai/artifact-tool` 必须符合下述固定分发约定；缺失或版本/摘要不符直接失败，不更换 XLSX parser，不使用旧缓存兜底。

Python 使用标准库读取 OOXML 的结构、原始数字和 number format 作为无损校验依据；业务单元格值由现有 Node workbook 路径提供。唯一可见 sheet、第一行精确表头、空行与连续编号、重复/缺字段、文本 ID 和数字零填充规则以 `PROJECT_RULES.md` 为准。公式、合并及隐藏结构拒绝处理。输出保留 `source`、`sheet`、`products`，新增 `schema_version=2` 和 `generator=index_titles.py:v2`；采用 UTF-8、两空格缩进及末尾换行。重复运行的 artifact 字节一致；metadata 的创建时间可变化。

两个生成器从开始到完成持有同一 cache 目录锁；并发调用立即失败。开始时固定 state bytes、expected identity 和完整 rules/config 摘要。subtitle 解析使用固定的 SRT bytes，并确认这些 bytes 与固定 input identity 一致；视频身份仍为路径、大小、纳秒修改时间，不是视频内容摘要。artifact/meta 在临时目录生成。发布前及发布各步骤之间重查 state、SRT、标题、视频身份和规则，变化则失败，发布前变化不写 artifact/meta/state。

发布是两个独立 rename，subtitle state 是第三个独立 rename，**不是跨文件事务，也不保证断电持久性**。先写 `<artifact>.invalid`；读者遇到标记即拒绝。发生发布错误时尽力恢复旧文件（包括 subtitle state），始终保留失效标记；rollback 自身失败也不能覆盖原始 publication error。即使残留 artifact 字节恰好匹配旧 SHA，标记仍阻止认证。只有完整受控重建成功才清除标记，禁止手工删标记或补签 metadata。锁约束协作生成器，不阻止外部编辑器；外部输入在最后检查后仍可改变，此后消费者重新计算 identity 时拒绝不匹配缓存，不能将文件系统描述为输入/输出跨文件事务。

`runtime_meta.load_checked_json()` 解析与 SHA 校验使用同一份 bytes，不再校验后重开文件。标题的两个消费者（范围生成、Resolve 写入）和 workspace preflight 均通过统一验证，要求 producer、titles schema/generator、连续商品编号、唯一字符串 ID、有效标题和 source/sheet。这些是来源约定，不是抵御恶意伪造的数字签名。subtitle 新增 generator 标记，旧生成器产物不能仅凭通用 identity 复用。升级记录见 `docs/CACHE_SAFETY_UPGRADE.json`；真实 state/cache 未改写。

## Pinned workbook dependencies

`package.json` 固定 Node **24.19.0**、`@oai/artifact-tool` **2.8.59**。该 module 是私有、带 bundled dependencies 的分发包，不能声称公开 `npm ci` 可安装。`workbook-dependencies.lock.json` 锁定 Codex primary runtime **26.909.12148 / darwin arm64** 的完整 artifact-tool 文件树摘要（包含全部传递依赖）；它是分发锁，不是 npm package-lock。取得授权的同版 bundle，将其中 `node_modules` 链接到仓库，使用其中 Node；不得用其他 XLSX parser 替代，也不要复制真实业务工作簿到审核环境。其他平台或拿不到相同私有 bundle 的审核环境仍有依赖 blocker，必须如实报告。

只读依赖检查：`<node-executable> scripts/check_workbook_dependencies.mjs`。检查 Node/平台/module 版本、完整文件树摘要及 workbook API 可导入性，不加载工作簿；正式 parser 同样强制检查。`project_doctor.py --root . --node <node-executable>` 执行相同检查，失败返回非零。完整 synthetic suite：`TB_CUT_TEST_NODE=<node-executable> python3 -B -m unittest discover -s tests -v`。单独真实 Node→Python synthetic integration：`TB_CUT_TEST_NODE=<node-executable> PYTHONPATH=tests python3 -B -m unittest test_index_titles.IndexTitlesTests.test_node_machine_parser_and_real_generator_on_synthetic_workbook -v`。不能通过跳过 integration 来宣称依赖通过。

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

- `init_project.py` 首次需要明确的素材文件夹、稳定 Resolve ID（用户离线确认，不接受显示名称作唯一身份）、标题/字幕/视频来源、项目和时间线名、批次和商品清单。保存本地忽略的 `PROJECT_CONFIG.json` 与 `PROJECT_STATE.json`。项目 UUID、目录真实路径/设备/inode、Resolve ID 与配置摘要共同核验；新窗口只需同一 `--workspace`。输入更新需明确 `--force`，缺失标题可只补标题；`--force` 不能切换项目身份。登记只验证配置，不表示已核对真实 Resolve。
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
- `codex_worker.py` 使用新 `codex exec --ephemeral --json --sandbox read-only --ignore-user-config` 进程，不用 resume/fork。隔离的临时工作目录只含本任务规则和 JSON schema，当前商品清理后的任务从 stdin 进入；禁止工具行为，收到工具事件、失败/超时、错误身份或缺完成事件就拒绝结果。每个商品选择与终审再分开上下文；最终还有独立项目语义复核上下文。
- CLI 成功结果绑定 task 摘要、不同 thread ID、完成事件摘要和返回结果；本地随机密钥封存执行记录。严格导入校验封存、任务角色、当前方案/输入及结果字段。该记录是本机执行来源完整性约定，不是防止本机所有者恶意伪造的远端数字签名。CLI 缺失/不可用时工作单留待审核；不会自动换模型或使用启发式冒充 Codex。
- 工作单只带当前商品的确认范围、认证候选或精修方案、规则/配置引用、有效阶段引用与简短问题；不传前一商品对话。超过 128KB 必须拆任务并 BLOCKED，不能截掉候选。任务、执行记录与详细文本只在本地忽略目录保存。

### 已接入的关键质量链

`registered_pipeline.py` 复用真实 `build_confirmed_ranges` 人工证明/源覆盖验证、`build_wide_candidates` 全范围清理、现有本机 Whisper 加载及合并窗口、`refine_words`、RMS 开头/句尾检查和 `normalize_physical_clips`。它把 range → candidate → Codex rough → local ASR → physical plan → exact vision → independent Codex final review → final audit 写入第 1 轮不可变商品记录，而非把旧固定路径产物贴新身份。

ASR/物理阶段计算前 capture_stage，完成后复核依赖；模型按需加载，已有有效窗口可复用。物理方案用源媒体帧率、向外保留词音的帧边界、当前完整范围，保存实际帧、同步音频范围、保留文字、词级窗口和接缝邻域。不通过截句尾凑时长；失败进入 REPAIRABLE/BLOCKED。源文件同名歧义及跨人工边界的粗字幕 cue 暂时阻塞，等待局部对齐，不能静默排除后称完整覆盖。

`final_quality_gate.py` 独立计算精确时长、禁用内容、词边界截断、音画范围一致、物理源重叠、跨版本文本重复和无意义接缝；所有检查只是本地 veto。完整逻辑、因果/条件/指代/列举/卖点以及语义重复仍需要独立 Codex；不按“因为/所以”关键词自动拒绝。最终任务带实际顺序/源帧、可靠词窗口和源邻域，绑定方案与终审输入摘要。任何切点/顺序/对齐/规则/源输入变化撤销旧审核。

独立终审命令：`final_quality_gate.py --workspace <workspace> --plan <stage-ref>` 默认只生成 REVIEW_PENDING；同时提供 `--vision <exact-plan-stage-ref> --receipt <sealed-Codex-receipt-ref>` 才能严格导入、重算门禁并更新 FINAL_PASS。直接人工写 verdict、粗选 PASS、旧 closure_check 或旧 final_audit 都不能更新新 FINAL_PASS。原 `approve_final_plans.py` 的登记模式现校验该链并发布不可变 approved 阶段；原 Resolve `load_products` 接入此审核读取，`resolve_write_v2.py --check-only` 可只读验证全项目，绝不连接 Resolve。登记模式真实写入仍显式 BLOCKED。

人物接口 `person_coverage_audit_v2.registered_vision` 对精确方案收集真实 detector 输出；现有 Swift 只有任意人物样本数，无法证明主播身份及全方案覆盖，故明确 UNCERTAIN，不能拿 >70% 样本数强行 PASS。门禁要求同方案全部版本、已验证主播、完整覆盖及严格 >70% 的保守比例。合成测试的视觉执行边界替身不代表该真实检测器已通过，也未修改抽样算法。

### 项目完成与统计

全部目标商品都通过后，仍需独立项目 Codex 审核当前最终文字摘要/方案和单商品审核引用，检查跨商品、跨组语义重复；没有该执行记录保持 PROJECT_CODEX_REVIEW_PENDING。本地还检查跨组实际源秒范围重叠。区分 final_review_complete、written_count、export_verified_count；delivery_complete 始终因未迁移真实写入/导出复验而阻塞，不能宣称全部交付。

每商品按阶段记录实测耗时、模型/ASR/物理/人物/终审阶段、缓存命中和失效原因、失败状态及修复计数入口。不宣称优化幅度。商品级 REPAIRABLE 需要修正当前方案并重新提交，自动全池返修编排尚未完成。

### 尚未迁移，不能绕过

旧固定路径主流程的范围发布/覆盖、候选批量 CLI、MiMo/旧语义审稿 CLI、粗选修复/批准 CLI、ASR/window-quality/refine/person 的旧 batch CLI、真实 Resolve 写入与回读、导出及验证、迁移/attest/preflight/cleanup 等仍保持原身份门禁或明确登记模式 BLOCKED。本轮接入的是这些真实算法的登记适配器、批准入口与写前质量接口，不是所有旧 CLI 的兼容迁移。旧 schema 1 工具仍不具有新最终 Codex 质量许可。Resolve 稳定 ID 回读、真实检测器主播保证、VFR 的精确帧时间证明、自动返修/跨批次选择性物理计算复用和实际 timeline/export 证据需要后续收敛。真实素材、MiMo、Resolve 和导出均未执行。

旧 schema 1 的批准命令和 Resolve 写入命令也明确阻塞，不能走旧启发式许可绕过新终审。旧读取 helper 可作历史身份校验，不是新写入授权。实际 ASR 执行需要带本机 faster-whisper 依赖的 Python 环境及已确认模型版本；本轮不调用真实模型或媒体，未验证该执行环境。自动阶段拆包和自动语义返修仍未接入。
