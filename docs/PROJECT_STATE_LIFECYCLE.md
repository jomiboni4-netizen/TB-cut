# 项目状态与批次切片生命周期

## 目标结构

一个 DaVinci Resolve 项目对应唯一长期状态记录：

```text
.cache/projects/<project_key>/PROJECT_STATE.json
```

该记录仅使用稳定 Resolve 项目 ID 识别项目，并绑定经确认的素材目录、视频、字幕、标题表、源/目标时间线名称及稳定 ID。启动时先读取当前打开的 Resolve 项目 ID，再按 ID 找到唯一绑定工作区；项目显示名只选择剪辑模式，时间线稳定 ID只保护写入目标。`scripts/init_project.py` 负责登记和验证；首次必须提供稳定项目 ID、源/目标时间线稳定 ID 与素材目录，后续窗口从该项目工作区继承路径和依赖。目录 inode 变化不改变项目身份；素材位置变更需显式 `--force` 更新配置。输入文件缺失仍作为配置阻塞处理。

同一项目的 `PROJECT_CONFIG.json` 与 `PROJECT_STATE.json` 是唯一长期项目记录。批次工作区包含 `PROJECT_REF.json` 与 `BATCH_STATE.json`，运行时通过 `RuntimePaths.load_state()` 从项目记录和批次状态组合读取；不得在批次工作区另存一份可编辑的项目绑定。

批次是临时切片，放在项目目录下：

```text
.cache/projects/<project_key>/batches/<batch_id>/
```

批次状态只保留项目引用、商品 ID 范围、阶段进度、完成时间和该批派生产物。它不重新注册项目，也不保存素材路径/Resolve 绑定。变更项目素材路径必须显式更新项目级记录，并使依赖旧输入的批次证据失效。旧工作区不会被自动发现为本次输入；旧批次和缓存原位保留。

## 保留与清理

- 批次开始时记录状态；完成时写入机器可读的 `completed_at` 时间。
- 仅已完成且已超过 7 天的批次可以清理。失败、运行中、暂停或没有可信完成时间的批次保留并报告原因。
- 清理器默认 dry-run，列出每个候选批次及其大小；真正删除须由显式 `--apply` 执行。
- 清理批次不得触及项目级 `PROJECT_STATE.json`、源素材、Resolve 项目、目标时间线或最终交付物。
- 仓库根目录旧 `PROJECT_STATE.json` 仅为迁移遗留，不参与项目识别或工作区选择。

## 旧状态处理

- 仓库根目录及旧工作区中的 `PROJECT_STATE.json` 仍视为历史记录，不会自动升级为当前登记。
- 若旧批次没有项目级记录，使用当前 Resolve 稳定项目 ID 和用户明确的素材目录重新登记；不需要从旧工作区复制配置，也不删除旧文件。
- `project_doctor.py --workspace ...` 检查项目记录、批次引用和状态一致性；Resolve 写前检查再核对实时项目和时间线身份。
