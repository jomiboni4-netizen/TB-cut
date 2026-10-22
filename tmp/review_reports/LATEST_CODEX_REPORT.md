# Task
读取用户对商品16、17的手工切点修改，统一时间线商品标记，并判断商品997158679320是否还能新增两个版本。

# Repo changes
- 增加经淘宝调度器调用的商品标记整理操作：按登记标题表把商品起点标记统一为蓝色，名称为商品ID，备注为`商品序号｜标题`；保留片段及其他标记，并写前备份、写后回读。
- 更新变更记录与本报告。提交前 Git 快照：`e60fdef`；本次新增改动在快照后产生。

# Runtime changes
- 重新认证 Resolve 稳定项目 ID、绑定项目及“素材”/“成片”时间线后，执行只读 `manual-preview-review`。手改预审共7段，音视频行数相同、保护内容完整；5段切点改变，幅度依次约为末尾−17帧、开头−2帧、+9帧、+12帧、+91帧；另外2段未变。
- 用户要求统一标记后，检查到18个商品起点标记中5个需要补正（商品4、5、6、16、17）。只更新这5个标记；18个商品标记复查全部符合截图格式，片段及无关标记未改。
- 商品997158679320的唯一已登记范围约120.3秒，当前手改版本占约55.9秒，剩余源时长约64.4秒。两条额外版本最低共需超过90秒，且要求同源区间互斥，因此当前范围不可能再容纳两条；最多只有一条的理论时长空间，语义完整性尚未判定。
- 未追加新片段或修改切点；无导出、音频增益或外部语义服务调用。

# Validation
- 项目 doctor：通过；Resolve connection doctor：通过，当前项目和目标时间线 ID 匹配。
- 调度器 `plan`：通过，模式 `taobao_legacy`。
- 手工切点只读回读：`MANUAL_EDIT_READBACK`，`protected_content_preserved=true`，视频/音频各7段。
- 标记更新：`MARKERS_STANDARDIZED_AND_VERIFIED`；复查为18个商品标记、0个待更新。
- `python -m py_compile`：通过；`git diff --check`：通过。未运行测试。

# External actions
- MiMo called: no。Resolve connected: yes。Resolve markers written: yes；timeline clips written: no。export performed: no。
- 专用 Chrome 未使用；未发送字幕、素材或剪辑内容到外部服务。

# Blockers
- 商品997158679320当前可用范围没有足够的互斥时长制作两条额外版本。其余约64.4秒能否单独组成另一条语义完整版本，本轮未审核。
- 商品16、17仍为人工修边预审版本；本轮读取并记录修改，没有把局部 ASR 状态提升为通过。

# Git state
- Branch: `codex/sentence-boundaries-sync`；提交前快照 HEAD: `e60fdef`。
- 提交前快照之后新增未提交文件变更：`CHANGELOG.md`、`scripts/resolve_semantic_preview.py`、`scripts/workflow_scheduler.py`、`tmp/review_reports/LATEST_CODEX_REPORT.md`。`product18-main.png` 和 `archive/` 仍是本地未跟踪项；本轮未提交、推送或改写历史。
