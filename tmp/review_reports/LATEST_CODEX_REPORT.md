# GitHub代码同步与新窗口交接

## Task

按用户明确授权，将当前可公开源码、合成测试、配置及规则文档同步到独立GitHub草稿PR，并准备本机新窗口续接指令。

## Repo changes

待提交范围包括：通用句首语义与物理定位分工、ASR截取起点网格、有界切点与半帧估计、独立语义审核、单商品事件计时与修复入口；同时同步既有的显式工作区隔离、项目连接诊断、工作簿兼容性检查及安全回填代码。更新自我迭代计划的过时进展，保留计划文件。共39个公开文件；未打包历史archive或任何忽略的运行目录。

## Runtime changes

本次不处理媒体、不调用模型、不连接或写入Resolve。最新本机新商品试跑三版语义通过；声音仍为REPAIRABLE_AUDIO_ONLY，人物未审、时间线未写，不等于成片通过。输入、素材、字幕、ASR、计划、计时及人工修改快照留本地，新窗口按本机交接文件定位。

## Validation

本次Doctor结构检查通过；当前工作簿运行时尚未经兼容性审阅，警告保留。未新增或运行测试：同步前源码已于上轮完成47项定向回归（全部通过，139.60秒）；本次仅代码审阅、文件清单、隐私扫描与diff格式检查。39个待提交文件扫描未发现真实用户绝对路径、凭据赋值或私钥。提交前继续核查暂存diff。

## External actions

MiMo called: no。Resolve connected: no。export performed: no。已只读确认远端分支与现有PR；本次推送及创建草稿PR获用户授权，结果将在完成后更新。保留现有PR，不合并任何PR。

## Blockers

声音风险待核实，不能宣称通用切点已完全修好或获得同等质量的速度提升。工作簿当前运行时仍未通过审阅。旧自然度修订PR尚未合并，新的同步分支不表示已合并其修改；后续合并前需对比重叠。

## Git state

此为提交前快照：分支codex/sentence-boundaries-sync，HEAD b994e42d3bc0983aea440cafa8ac711a421ddc68。未暂存；仅显式选择公开文件，不采用git add全部。历史运行archive保留本地。提交后将核对远端提交与新草稿PR。

```text
 M CHANGELOG.md
 M PROJECT_RULES.md
 M docs/PIPELINE_DEPENDENCIES.md
 M package.json
 M scripts/asr_identity.py
 M scripts/check_workbook_dependencies.mjs
 M scripts/codex_worker.py
 M scripts/coldstart_resolve.py
 M scripts/coldstart_semantics.py
 M scripts/final_quality_gate.py
 M scripts/inspect_title_workbook.mjs
 M scripts/product_semantic_preview.py
 M scripts/project_doctor.py
 M scripts/query_table_resize.mjs
 M scripts/refine_plans_v2.py
 M scripts/registered_pipeline.py
 M scripts/resolve_connection_doctor.py
 M scripts/runtime_meta.py
 M scripts/runtime_paths.py
 M scripts/semantic_evidence.py
 M scripts/update_title_counts.mjs
 M tests/quality_fixtures.py
 M tests/test_coldstart_resolve.py
 M tests/test_coldstart_semantics.py
 M tests/test_migrate_runtime_layout.py
 M tests/test_resolve_connection.py
 M tests/test_runtime_meta.py
 M tests/test_runtime_paths.py
 M tests/test_workbook_dependencies.py
 M tmp/review_reports/LATEST_CODEX_REPORT.md
?? archive/
?? config/workbook-compatibility.json
?? docs/NATURALNESS_EVALUATION.md
?? docs/SELF_ITERATION_PLAN.md
?? scripts/check_workbook_structure.py
?? scripts/sentence_boundaries.py
?? scripts/sentence_trial.py
?? scripts/sentence_trial_repair.py
?? tests/test_sentence_boundaries.py
?? tests/test_workbook_runtime_compatibility.py
```
