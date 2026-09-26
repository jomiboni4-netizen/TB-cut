# Pipeline dependencies and runtime cache identity

依据：逐个检查当前 `scripts/` 的实际读取、写入与分支。表中 `<N>` 是商品编号，`R=.cache/product_<N>_rough/`，`W=.cache/product_<N>_windowed/`；`meta` 指同名文件后加 `.meta.json` 的身份文件。`PROJECT_STATE.json` 是本地状态，不进 Git。`config/*.json` 目前主要供规则说明和身份指纹；多数剪辑脚本的阈值仍写在源码中。

| stage | script | required inputs | generated outputs | validation | next stage |
| --- | --- | --- | --- | --- | --- |
| init | `init_project.py` / `tbcut.py init` | `PROJECT_STATE.template.json`；CLI 指定标题、字幕、视频、Resolve 名称 | `PROJECT_STATE.json` | 默认拒绝覆盖现有状态；未检查输入文件真实性 | fingerprint、subtitle index |
| fingerprint | `fingerprint_inputs.py` | `PROJECT_STATE.json` 的标题/字幕/视频路径 | `.cache/input_fingerprints.json` | 仅单文件计算 SHA-256；目录记录存在但 `sha256=null`，无人将此文件作为强制门槛 | subtitle index；身份 helper 另从配置输入直接计算指纹 |
| subtitle index | `index_subtitles.py` | `PROJECT_STATE.json`，SRT，与 SRT 同名的 MP4，`ffprobe` | `.cache/subtitle_index.json` + meta；更新本地状态中的字幕指纹/阶段 | SRT 内容指纹；新增批次、输入、规则、pipeline、meta schema、文件摘要检查；旧索引在再次运行本 stage 时从原始输入重建 | product ranges、expanded candidates |
| title index | `index_titles.py` → `inspect_title_workbook.mjs --json` | 当前 state 的 `title_path`；已有 Node 与 `@oai/artifact-tool`；Python 标准库 | `.cache/titles.json` + meta | 唯一可见 sheet、第一行表头、无损 ID、空行/缺字段/重复检查；当前 identity | product ranges、Resolve write |
| product ranges | `locate_link_intros.py`；可选 `apply_range_overrides_v2.py` | 已验证的 `.cache/subtitle_index.json`、`.cache/titles.json`；覆盖时还需已认证的覆盖 JSON | `.cache/link_intros.json`、`.cache/product_ranges.json` + meta；覆盖会改后者及 meta | 输入身份；标题/链接只作锚点；覆盖检查终点晚于起点。范围阶段为 `link_intros.json` 和 `product_ranges.json` 生成 metadata | expanded candidates、windowed ASR |
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
- `index_subtitles.py` 可从原 SRT/MP4 重建；`index_titles.py` 可从当前工作簿重建。基础 `mimo_request.json` 仍没有完整生成器；请求及人工覆盖记录须先复核来源，再通过 `python3 scripts/attest_runtime_cache.py --root . identity` 取得当前身份，并使用 `attest-input` 加上**当前文件 SHA-256、批次 ID、输入指纹、商品编号（如适用）**显式认证。命令只允许 `.cache/*range_overrides.json` 和 `R/mimo_request.json`；titles 必须由当前工作簿生成器重建，即使旧文件结构看似合法，也不得认证。其他 legacy 文件被拒绝，已有 meta 不覆盖。
- 认证命令形态如下，所有尖括号值均须来自本机重新计算与人工复核，不能从旧缓存猜测：

  ```bash
  python3 scripts/attest_runtime_cache.py --root . identity
  shasum -a 256 .cache/product_01_rough/mimo_request.json
  python3 scripts/attest_runtime_cache.py --root . attest-input \
    --artifact .cache/product_01_rough/mimo_request.json \
    --artifact-sha256 '<reviewed-file-sha256>' \
    --batch-id '<current-batch-id>' \
    --input-fingerprint '<current-input-fingerprint>' \
    --product-id 1
  ```

- 已认证请求对应的旧 MiMo 响应可走 `attest-response`：人工复核响应内容，并提供响应文件 SHA-256、当前批次/输入指纹、商品编号及**已认证的请求路径**。工具还会检查既有 `_meta.json` 的 `status=completed` 和 `request_digest` 是否等于该请求文件的 SHA-256；不匹配就拒绝。它不重发 MiMo，也不自动认证任何响应。
- 从已认证的输入重新运行商品范围、扩展候选、审核、ASR、方案和终审阶段，生成新 sidecar。**不要为旧终审/批准方案批量盖章**。若必须复用既有最终方案，需要另做逐文件来源核验与专门迁移；现有工具不提供绕过路径。
- 缓存刷新不改变 Resolve 历史结果；未通过身份校验的旧缓存不能作为写入或导出的有效依据。

## Workspace paths and relocation preflight

主链通过 `runtime_paths.py` 区分 repo 与 workspace：`--workspace` 优先，其次 `TB_CUT_WORKSPACE`，默认同目录。规则与 config 从 repo 读取，状态与派生产物从 workspace 读取。metadata schema 仍为 1，pipeline 仍为 2.1。

`migrate_runtime_layout.py` 默认 dry-run，`--apply` 才改写。它只对当前批次能由状态、源视频文件名、方案版本、批准清单包含关系和人物审计中的方案摘要交叉核验的引用做路径表示迁移；迁移前字节备份放在本地 `reports/runtime_migration_backups/`。旧方案内容、时间码、variant 和审核结果不变。带有效 metadata 的文件改写后才重建 sidecar；无 metadata 的文件绝不因路径迁移自动认证。工具再次运行不会继续改变已转换的文件。报告保存在本地 `reports/runtime_migration_report.json`，不进入 Git。

迁移工具扫描 runtime 目录中的业务 JSON，排除自己的报告及迁移前备份。无法证明来源的历史缓存保留为 stale，不自动认证。标题索引使用正式生成器从工作簿重建；基础 MiMo 请求仍须独立核对来源和恢复路径，已有 metadata 的文件不能直接 attest；商品范围从有效输入重新生成。终审、人物审计和批准方案必须从可信上游重新生成，不能靠路径迁移补 metadata。

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
