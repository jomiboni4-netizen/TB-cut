### Task

建立固定、脱敏、可进入 Git diff 的 Codex 任务报告机制。

### Repo changes

- `PROJECT_RULES.md`：规定每轮覆盖本报告、固定章节、脱敏边界、Git 状态快照和用户审核后清理规则。
- `CHANGELOG.md`：记录报告机制能力。
- `.gitignore`：tmp 目录仅允许本报告进入 Git，其他临时文件默认忽略。
- `tmp/review_reports/LATEST_CODEX_REPORT.md`：创建本轮报告；不生成额外历史报告文件。

### Runtime changes

未修改本地状态、缓存、方案、报告或媒体。未执行缓存认证、重建或物理迁移。
长期规则文件的工作流说明发生变化；现有缓存指纹包含整个规则文件，因此旧 metadata 的规则指纹不再匹配。剪辑阈值和时间码决策未修改。

### Validation

- project doctor: PASS。
- tests: PASS，22/22 个现有测试通过。
- preflight: NOT_READY，10 个关键文件身份阻塞：6 个规则指纹不匹配，4 个缺 metadata。
- git diff check: PASS，检查 working tree 和 staged diff。
- public safety scan: PASS；报告仅含逻辑路径、通用说明和状态，无真实绝对路径、凭据或业务正文。
- Git boundary check: PASS；真实 runtime 文件未被跟踪或暂存，其他临时文件保持忽略。

### External actions

- MiMo called: no
- Resolve connected: no
- export performed: no
- Git commit created: no
- Git push performed: no
- PR created: no

### Blockers

- 规则指纹不匹配：`.cache/subtitle_index.json`、`.cache/product_ranges.json`、`.cache/titles.json`，以及商品 01–03 rough 目录中的基础 `mimo_request.json`。
- 缺 metadata：`.cache/person_coverage_audit.json`，以及商品 01–03 windowed 目录中的 `approved_plans.json`。
- 旧语义结果链仍需恢复；本轮没有调用 MiMo，也未重新判定历史响应可复用性。
- 报告目前只在本地 Git diff；尚无 GitHub PR 可供远端审核。

### Git state

本轮结束时的本地快照；没有创建新 commit。

- branch: `codex/repository-baseline`
- HEAD: `c4f624ed0705`
- PR: none
- changed files: `.gitignore`、`PROJECT_RULES.md`、`CHANGELOG.md`、`tmp/review_reports/LATEST_CODEX_REPORT.md`
- staged: 上述 4 个公开文件。
- unstaged: none。
- runtime tracked/staged: none。
