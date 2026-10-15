# Task

将四阶段自我迭代计划写入 GitHub，并完成阶段一的只读代码与 Git 盘点。

# Repo changes

新增 `docs/SELF_ITERATION_PLAN.md`：阶段一盘点，阶段二基线与质量评测，阶段三单变量隔离实验，阶段四受控迭代。迭代轮数遵循项目规则：至少三轮，通常三轮，必要时最多五轮。计划完成后按用户要求清理计划文档。

# Runtime changes

只读检查入口、架构与依赖文档、调度器、冷启动脚本、剪辑精修/最终门禁、近期提交、测试清单和本地 Git 状态。未读取媒体、字幕或缓存；未运行剪辑、MiMo 或 Resolve。

# Validation

项目 doctor：未运行。测试：未运行。Preflight：未运行。Git diff 检查：未运行本地检查；GitHub 草稿 PR #3 当前仅包含计划文档和本报告。阶段一静态盘点完成；基线复现与质量测量未完成。

# External actions

MiMo called: no。Resolve connected: no。export performed: no。草稿 PR #3 面向 `main`，未合并。PR #2（误选基线的初稿）已关闭且未合并。

# Blockers

本地工作区有24个已修改的跟踪文件及未跟踪文件/归档，未判定来源与有效性，本次原样保留。没有固定口播媒体回归集：`tests/fixtures` 不存在，测试主要使用合成数据。主入口 `tbcut.py` 是维护命令包装器；`project_scheduler.py` 编排已登记链；`coldstart_semantics.py` 和 `coldstart_resolve.py` 是分开的入口。后者读取物理方案与审核文件，冷启动语义入口未产出这些文件，二者完整衔接尚无仓库内证据。剪辑精修采用词级对齐和 RMS 音频风险检查；最终门禁检查时间、词边界、音画范围、重叠和重复，但没有固定样片的人为自然度对照。历史主分支只有空仓库起点，当前 PR #1 的 `b994e42` 是代码基线候选，尚未经本轮复现或质量比较。

# Git state

GitHub 计划分支 `codex/self-iteration-plan-clean`，本次报告更新前 HEAD 为 `f6cc34de7e404b754dbae10e62b1b34881cf1fc3`；PR #3 草稿。计划已与项目 3–5 轮规则对齐。本地分支 `codex/repository-baseline`，HEAD `b994e42`，比 `main` 多26个提交；本地未提交改动未纳入 PR。
