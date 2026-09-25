# Pipeline dependencies and runtime cache identity

依据：逐个检查当前 `scripts/` 的实际读取、写入与分支。表中 `<N>` 是商品编号，`R=.cache/product_<N>_rough/`，`W=.cache/product_<N>_windowed/`；`meta` 指同名文件后加 `.meta.json` 的身份文件。`PROJECT_STATE.json` 是本地状态，不进 Git。`config/*.json` 目前主要供规则说明和身份指纹；多数剪辑脚本的阈值仍写在源码中。

| stage | script | required inputs | generated outputs | validation | next stage |
| --- | --- | --- | --- | --- | --- |
| init | `init_project.py` / `tbcut.py init` | `PROJECT_STATE.template.json`；CLI 指定标题、字幕、视频、Resolve 名称 | `PROJECT_STATE.json` | 默认拒绝覆盖现有状态；未检查输入文件真实性 | fingerprint、subtitle index |
| fingerprint | `fingerprint_inputs.py` | `PROJECT_STATE.json` 的标题/字幕/视频路径 | `.cache/input_fingerprints.json` | 仅单文件计算 SHA-256；目录记录存在但 `sha256=null`，无人将此文件作为强制门槛 | subtitle index；身份 helper 另从配置输入直接计算指纹 |
| subtitle index | `index_subtitles.py` | `PROJECT_STATE.json`，SRT，与 SRT 同名的 MP4，`ffprobe` | `.cache/subtitle_index.json` + meta；更新本地状态中的字幕指纹/阶段 | SRT 内容指纹；新增批次、输入、规则、pipeline、meta schema、文件摘要检查；旧索引在再次运行本 stage 时从原始输入重建 | product ranges、expanded candidates |
| title inspect | `inspect_title_workbook.mjs` | CLI 传入标题工作簿及预览路径，`@oai/artifact-tool` | stdout 的工作簿摘要、指定 PNG 预览；**不产生** `.cache/titles.json` | 仅工具自身解析；无身份信息 | 人工/外部生成 `.cache/titles.json`；明确复核后 `attest_runtime_cache.py attest-input` |
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

`enforce_v2_request_rules.py` 可对已认证请求原位更新规则并刷新 meta，但它不能从零生成基础请求。`inspect_timeline_for_export.py`、`compare_timeline_versions.py`、`update_title_counts.mjs`、`update_batch_state.py` 是辅助/历史或交付后步骤，不是上述主链的自动前置步骤。时间线快照和历史对比仍未接入统一身份校验，勿当成当前批次依据。`project_doctor.py` 只查结构及配置存在性，不代替缓存身份或 Resolve 预检。

前置顺序：`init → subtitle index + title inspect/标题索引复核 → product ranges → expanded candidates → MiMo review → semantic audit →（必要时 repair）→ rough approval → windowed ASR → window quality → refine plans → person coverage + final approval → Resolve write → Resolve validation → export → export verification`。`fingerprint_inputs.py` 可在 init 后运行，但它的输出目前不是后续脚本的强制输入；统一身份 helper 会直接计算当前输入指纹。

## Hidden prerequisites confirmed

1. **标题检查 ≠ 标题索引生成。** `inspect_title_workbook.mjs` 只输出摘要和预览，不生成 `.cache/titles.json`。`locate_link_intros.py` 和 Resolve 写入却直接要求后者。它需要人工/外部生成、复核并显式认证；不能从文件存在推断属于当前批次。
2. **扩展候选不是独立建池器。** `build_expanded_codex_request.py` 先加载既有 `R/mimo_request.json`，再保留其 `rules` 和其余结构，替换候选并写 `codex_expanded_request.json`。因此它依赖旧请求底稿的先前生成过程；当前仓库没有从零生成这份底稿的主流程脚本。读取处要求输入身份有效。
3. **粗选和方案有固定文件名。** `windowed_asr_v2.py`、`refine_plans_v2.py`、人物审核和批准脚本按商品编号读写；不按批次分目录。身份检验用于防止误用其他批次数据。
4. **人物审核默认可能读旧批准清单。** 若 `approved_plans.json` 已存在，不加 `--generated` 就不会审查刚生成的方案；现在旧清单缺 meta 会被拒绝，不会悄然复用。
5. **导出清单也会串项目。** 旧 `export_manifest.json` 可保留在相同路径；`verify_exports.py` 现在先验身份再看其中的输出目录。

## Metadata contract

元数据放在 `<artifact>.meta.json`，不改原 JSON 的业务内容。核心字段：`schema_version=1`（**metadata 格式版本**）、`pipeline_version`、`batch_id`、`input_fingerprint`、`rules_fingerprint`、`created_at`、`producer`、`artifact_sha256`；商品级文件另有 `product_id`。调用方若知道原 JSON 的 schema，另检查原文件 `schema_version`。错误包含 `legacy_cache_missing_metadata`、`batch_id_mismatch`、`input_fingerprint_mismatch`、`pipeline_version_mismatch`、`schema_version_mismatch`、`rules_fingerprint_mismatch` 和 `artifact_sha256_mismatch`。

`input_fingerprint` 对标题与 SRT 文件内容取 SHA-256；视频用路径、大小和纳秒修改时间，避免重复读取大视频；还包含 Resolve 项目/时间线配置。它不包含 `project_root`，便于将 runtime workspace 物理分离。`rules_fingerprint` 覆盖 `PROJECT_RULES.md` 与 `config/*.json`。这能识别常见输入/规则变化，但视频若在大小和修改时间完全不变的情况下被替换，单靠该指纹无法识别；若未来需要抵御这种情况，再引入视频内容摘要。

代码本身未纳入指纹。剪辑语义发生变化时仍须按 `AGENTS.md` 升级 `pipeline_version` 并使旧派生产物失效；metadata 不代替这一步。

## Legacy and refresh path

- 旧 V2.1 JSON **保留原处，不自动删除、不自动补身份**。缺少 sidecar 就是 legacy；读取关键缓存的脚本会在外部调用或 Resolve 连接前失败。旧 MiMo 响应即使有历史 `_meta.json` 请求摘要，也不能单靠它证明批次身份，脚本不会自动重发。
- `index_subtitles.py` 可从原 SRT/MP4 重建索引并写新 meta。标题索引和基础 `mimo_request.json` 没有仓库内的完整生成器：先对照当前标题工作簿、字幕、商品和请求内容人工复核，再通过 `python3 scripts/attest_runtime_cache.py --root . identity` 取得当前身份，并使用 `attest-input` 加上**当前文件 SHA-256、批次 ID、输入指纹、商品编号（如适用）**显式认证。命令只允许 `.cache/titles.json`、`.cache/*range_overrides.json` 和 `R/mimo_request.json`；不接受其他 legacy 文件，也不覆盖已有 meta。
- 认证命令形态如下，所有尖括号值均须来自本机重新计算与人工复核，不能从旧缓存猜测：

  ```bash
  python3 scripts/attest_runtime_cache.py --root . identity
  shasum -a 256 .cache/titles.json
  python3 scripts/attest_runtime_cache.py --root . attest-input \
    --artifact .cache/titles.json \
    --artifact-sha256 '<reviewed-file-sha256>' \
    --batch-id '<current-batch-id>' \
    --input-fingerprint '<current-input-fingerprint>'
  ```

- 已认证请求对应的旧 MiMo 响应可走 `attest-response`：人工复核响应内容，并提供响应文件 SHA-256、当前批次/输入指纹、商品编号及**已认证的请求路径**。工具还会检查既有 `_meta.json` 的 `status=completed` 和 `request_digest` 是否等于该请求文件的 SHA-256；不匹配就拒绝。它不重发 MiMo，也不自动认证任何响应。
- 从已认证的输入重新运行商品范围、扩展候选、审核、ASR、方案和终审阶段，生成新 sidecar。**不要为旧终审/批准方案批量盖章**。若必须复用既有最终方案，需要另做逐文件来源核验与专门迁移；现有工具不提供绕过路径。
- 缓存刷新不改变 Resolve 历史结果；未通过身份校验的旧缓存不能作为写入或导出的有效依据。

## Workspace paths and relocation preflight

主链通过 `runtime_paths.py` 区分 repo 与 workspace：`--workspace` 优先，其次 `TB_CUT_WORKSPACE`，默认同目录。规则与 config 从 repo 读取，状态与派生产物从 workspace 读取。metadata schema 仍为 1，pipeline 仍为 2.1。

`migrate_runtime_layout.py` 默认 dry-run，`--apply` 才改写。它只对当前批次能由状态、源视频文件名、方案版本、批准清单包含关系和人物审计中的方案摘要交叉核验的引用做路径表示迁移；迁移前字节备份放在本地 `reports/runtime_migration_backups/`。旧方案内容、时间码、variant 和审核结果不变。带有效 metadata 的文件改写后才重建 sidecar；无 metadata 的文件绝不因路径迁移自动认证。工具再次运行不会继续改变已转换的文件。报告保存在本地 `reports/runtime_migration_report.json`，不进入 Git。

迁移工具扫描 runtime 目录中的业务 JSON，排除自己的报告及迁移前备份。无法证明来源的历史缓存保留为 stale，不自动认证。标题索引和基础 MiMo 请求须核对来源后通过 `attest_runtime_cache.py` 显式认证；商品范围从有效输入重新生成。终审、人物审计和批准方案必须从可信上游重新生成，不能靠路径迁移补 metadata。

`workspace_preflight.py` 以 `PROJECT_STATE.json` 配置的商品及全局关键缓存为检查范围，按字段语义验证 runtime 引用。状态中的 `project_root` 和外部素材绝对路径不视为 runtime 路径错误。遗留文件缺少 metadata 或身份不匹配时会阻塞预检；物理移动前须重新运行预检并处理所有阻塞。实际检查结果仅保存在本地报告中。
