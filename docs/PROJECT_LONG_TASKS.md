# 单个 Resolve 项目的长任务

此层使新窗口能从项目文件接手，并限制重复执行。实际项目档案只放在 `.cache/projects/<project_key>/`；本文件是工具使用说明，不是任何具体项目的目标或进度。

## 文件职责

| 文件 | 内容 | 写入方 |
| --- | --- | --- |
| PROJECT.md | 目标、范围和完成定义 | 用户与 Codex；刷新不覆盖 |
| ACCEPTANCE.md | A1 质量、A2 回读、A3 全范围汇总、A4 按需导出 | 用户与 Codex；刷新不覆盖 |
| PLAN.md | 阶段、前置条件、执行及停止方式 | 用户与 Codex；刷新不覆盖 |
| STATUS.md | 限制、事务、商品记录、运行预算和下一步 | 工具自动生成 |
| DECISIONS.md | 决定、理由和人工备注 | 工具追加，保留人工备注 |
| PROJECT_TASKS.json | 稳定项目身份、结构化任务、限制和运行记录 | 工具原子写入 |

没有新增商品完成账本。状态页只读检查点和事务摘要，不读字幕、媒体或模型日志，也不发起模型调用。WRITTEN 是已记录状态，不是新的回读通过证明。即使没有阻塞，整项目完成仍显示 NOT_EVALUATED，直至现有全范围验收另行证明；本模块没有“强制完成”入口。

## 初始化与跨窗口接手

从仓库目录执行：

```bash
python3 scripts/project_task.py init --root . --goal "本项目经确认的交付目标"
python3 scripts/project_task.py refresh --root .
python3 scripts/project_task.py check --root .
```

这些命令调用共享 Resolve connection doctor，使用 `--fresh-identity` 强制重新读取稳定项目 ID。未连接、未登记或显式工作区与实时项目不符均停止。新建文件前确认唯一绑定工作区；已有文件不覆盖。中断初始化留下部分文件时阻断，需人工检查，禁止用 init 覆盖。

`--workspace` 可显式选择该项目的批次工作区，但文档仍写到唯一项目目录。没有显式工作区时，只从实时项目选择项目目录；不自动猜当前批次。

## 登记有界任务

把任务定义保存为本地 JSON 后执行：

```bash
python3 scripts/project_task.py add-task --root . --task-file /absolute/path/task.json
```

任务定义示例（全部为占位示例，不对应真实批次）：

```json
{
  "id": "T001",
  "goal": "对指定商品执行一次经批准的完整验收",
  "batch_id": "example-batch",
  "product_ids": [1],
  "acceptance_ids": ["A1", "A2"],
  "operations": ["default"],
  "max_runs": 1,
  "max_repairs_per_run": 1
}
```

登记后自动选择该任务。`select-task --task-id T001` 可选择已登记任务，不清零其计数。任务 ID 不可重复。预算按已登记任务累计，不因新窗口或更换工作区重置；商品、批次和操作必须匹配。`default` 是正常调度运行；其他 operation 必须逐个明确列入。不得为绕过耗尽预算自动登记同义新任务。

仍通过项目匹配的调度器执行 `plan` 后 `run`，例如：

```bash
python3 scripts/taobao_scheduler.py plan --root . --workspace /absolute/path/batch --target-timeline "本批指定名称"
python3 scripts/taobao_scheduler.py run --root . --workspace /absolute/path/batch --target-timeline "本批指定名称" --repair-limit 1
```

任务验收编号用于追踪标准，不构成验收通过证书。命令成功只表示本次运行退出码为零，仍需检查现有质量、事务和导出证据。全项目完成必须另行核对完整范围及跨批次一致性，不能从次数或 Markdown 勾选推断。

## 暂停与恢复

```bash
python3 scripts/project_task.py hold --root . --actions run,recover,write --reason "用户要求暂停执行和恢复，保留现场"
```

暂停保存在项目级记录，所有批次继承。`run` 是全体调度执行暂停；`recover` 额外约束恢复操作；`write` 约束交付入口。只读 doctor、检查和诊断仍可用。

仅在用户明确解除相应限制后：

```bash
python3 scripts/project_task.py release --root . --actions run,recover,write --reason "记录本次用户明确解除的范围和原因"
```

解除暂停不处理旧失败事务，不返还预算，也不批准写入。存在未验证事务时普通 run 仍阻断；恢复须显式 operation、匹配任务及原恢复器全部安全检查。不得把该命令当作更改 FAILED_VERIFICATION 的工具。

进程被强制终止时，RUNNING 记录保留并阻断后续 run。确认原进程已停止、检查事务后，用 `close-interrupted --run-id <id> --reason <原因>` 将记录标为中断；不会恢复片段或重放写入。

## 计划失效、并发与边界

目标、验收、计划、决定和控制配置进入调度计划摘要；改变后须重新 plan。STATUS 刷新及执行次数变化不改变计划配置。项目锁保护预算预占，同项目未关闭运行阻断另一窗口。

限制在运行前、阶段边界及交付入口检查；不会强制取消已经进入的外部 API 调用。工程代码修改无法由此层计数，运行预算约束的是调度器调用；max_repairs_per_run 约束该次调度器的返修上限。

此层不会增加任务自动派发、外部服务或自动提交。未登记长任务的旧项目保持兼容；已登记后移除控制文件而保留文档会阻断。生成文件留在本机忽略目录，清理器保护项目级档案。
