# Task

自我迭代第 1–3 轮：改进短时重复表达处理、ASR 起点对齐，以及终审对成片内部重复意思的检查。

# Repo changes

分支 `codex/naturalness-correction-1`：
- `scripts/registered_pipeline.py`：选择任务要求检查短时重复用词及近义改述；保留信息最完整的表达及新增证据、限定条件、指代/因果和自然强调。
- `scripts/refine_plans_v2.py`：粗起点位于词级时间内时保留完整词；在词间时选随后的词，不因更远处停顿而跳过有效开头。
- `scripts/final_quality_gate.py`：终审逐条检查成片内部的短时重复表达，区分可删冗余与增量细节。
- `tests/test_project_scheduler.py`：新增 4 项指令/切点反例断言。
原始工作区用户改动未纳入。

# Runtime changes

在基线 `b994e42` 的隔离副本完成改动与验证。仅用合成词级时间戳、合成文本及纯音/静音信号。未读真实素材或原始字幕，未调用 MiMo/Resolve，未导出。

# Validation

完整 `test_project_scheduler`：17 项通过。新增定向断言：4 项通过。差异检查 `git diff --check`：通过。项目 doctor、完整测试集、preflight、秘密扫描本轮未运行；没有真实自然度或吞字率数据。

# External actions

MiMo called: no。Resolve connected: no。export performed: no。GitHub 草稿 PR #4 已更新，未合并。

# Blockers

重复语义提炼只有合成断言确保任务要求和终审问题已传入，未验证真实模型输出质量。ASR 起点算法只在合成时间戳上验证；音频边界、词级识别误差和结尾闭合仍待真实样本回听。当前结果不支持宣称真实听感改善。

# Git state

GitHub 分支 `codex/naturalness-correction-1`，报告提交前代码/测试 HEAD 为 `401a171abd8a50bfdbd15999008638069c635dad`；PR #4 草稿，基础为 `codex/repository-baseline`。临时副本工作树有对应源码和测试差异，原工作区分支 `codex/repository-baseline` 及用户修改保持不变。
