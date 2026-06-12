# Bilingual Documentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use compose:subagent (recommended) or compose:execute to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert 3 documentation files to bilingual (Chinese + English) format, matching the existing style in `scene-graph-khronos/CLAUDE.md`.

**Architecture:** Each file gets its Chinese/English translation added inline. Chinese content comes first, English follows, separated by `/` in headings. No structural changes to existing content.

**Tech Stack:** Markdown only, no code changes needed.

---

### Task 1: Convert LLM-robot-taskplanning-chaining/CLAUDE.md

**Covers:** [S1]
<!-- File is currently English-only; add Chinese translation -->

**Files:**
- Modify: `LLM-robot-taskplanning-chaining/CLAUDE.md`

- [ ] **Step 1: Read current file**

Read `LLM-robot-taskplanning-chaining/CLAUDE.md` to understand current structure (119 lines, English only).

- [ ] **Step 2: Rewrite with bilingual format**

Transform each section to bilingual format. For each `##` heading, use the pattern:

```markdown
## 中文标题 / English Title

中文段落...

English paragraph...
```

Key sections to translate:
- "What This Project Does" → "项目简介 / What This Project Does"
- "Environment Setup (WSL2 + Windows)" → "环境配置（WSL2 + Windows）/ Environment Setup (WSL2 + Windows)"
- "Running" → "运行 / Running"
- "Architecture" → "架构 / Architecture"
- "Key Files" → "关键文件 / Key Files"
- "Conventions" → "约定 / Conventions"

For code blocks, keep the English comments but add Chinese comments where helpful. Example:

```bash
# Activate conda environment
conda activate llm-task-planner  # 激活 conda 环境
```

- [ ] **Step 3: Verify file is valid markdown**

Run: `wc -l LLM-robot-taskplanning-chaining/CLAUDE.md`
Expected: > 150 lines (roughly doubled from 119)

---

### Task 2: Convert LLM-robot-taskplanning-chaining/README_USAGE.md

**Covers:** [S2]
<!-- File is currently Chinese-only; add English translation -->

**Files:**
- Modify: `LLM-robot-taskplanning-chaining/README_USAGE.md`

- [ ] **Step 1: Read current file**

Read `LLM-robot-taskplanning-chaining/README_USAGE.md` to understand current structure (192 lines, Chinese only).

- [ ] **Step 2: Rewrite with bilingual format**

Transform each section to bilingual format. For each `##` heading, use the pattern:

```markdown
## English Title / 中文标题

English content...

中文内容...
```

Key sections to translate:
- "前置条件" → "Prerequisites / 前置条件"
- "运行" → "Running / 运行"
- "配置说明" → "Configuration / 配置说明"
- "自定义输入" → "Custom Input / 自定义输入"
- "输出示例" → "Output Example / 输出示例"
- "后续集成" → "Future Integration / 后续集成"

For tables, add English column headers or create bilingual tables:

```markdown
| 场景 Scenario | 建议参数 Recommended Params |
|---|---|
```

- [ ] **Step 3: Verify file is valid markdown**

Run: `wc -l LLM-robot-taskplanning-chaining/README_USAGE.md`
Expected: > 250 lines (roughly doubled from 192)

---

### Task 3: Convert LLM-robot-taskplanning-chaining-rag/CLAUDE.md

**Covers:** [S3]
<!-- File is currently English-only; add Chinese translation -->

**Files:**
- Modify: `LLM-robot-taskplanning-chaining-rag/CLAUDE.md`

- [ ] **Step 1: Read current file**

Read `LLM-robot-taskplanning-chaining-rag/CLAUDE.md` to understand current structure (119 lines, English only). Note: this file is identical to `LLM-robot-taskplanning-chaining/CLAUDE.md`.

- [ ] **Step 2: Rewrite with bilingual format**

Apply the same bilingual format as Task 1. The content is identical, so the translation will be the same.

Key sections to translate (same as Task 1):
- "What This Project Does" → "项目简介 / What This Project Does"
- "Environment Setup (WSL2 + Windows)" → "环境配置（WSL2 + Windows）/ Environment Setup (WSL2 + Windows)"
- "Running" → "运行 / Running"
- "Architecture" → "架构 / Architecture"
- "Key Files" → "关键文件 / Key Files"
- "Conventions" → "约定 / Conventions"

- [ ] **Step 3: Verify file is valid markdown**

Run: `wc -l LLM-robot-taskplanning-chaining-rag/CLAUDE.md`
Expected: > 150 lines (roughly doubled from 119)

---

### Task 4: Verify all files

**Covers:** [S1, S2, S3]
<!-- Verify all three files are valid and consistent -->

**Files:**
- Read: `LLM-robot-taskplanning-chaining/CLAUDE.md`
- Read: `LLM-robot-taskplanning-chaining/README_USAGE.md`
- Read: `LLM-robot-taskplanning-chaining-rag/CLAUDE.md`

- [ ] **Step 1: Check line counts**

Run: `wc -l LLM-robot-taskplanning-chaining/CLAUDE.md LLM-robot-taskplanning-chaining/README_USAGE.md LLM-robot-taskplanning-chaining-rag/CLAUDE.md`
Expected: All files have roughly doubled in line count

- [ ] **Step 2: Check for bilingual headings**

Run: `grep -c " / " LLM-robot-taskplanning-chaining/CLAUDE.md LLM-robot-taskplanning-chaining/README_USAGE.md LLM-robot-taskplanning-chaining-rag/CLAUDE.md`
Expected: Each file has 5-10 bilingual headings

- [ ] **Step 3: Check for Chinese content**

Run: `grep -c "[\x{4e00}-\x{9fff}]" LLM-robot-taskplanning-chaining/CLAUDE.md LLM-robot-taskplanning-chaining/README_USAGE.md LLM-robot-taskplanning-chaining-rag/CLAUDE.md`
Expected: Each file has 20+ lines with Chinese characters

- [ ] **Step 4: Check for English content**

Run: `grep -c "[a-zA-Z]" LLM-robot-taskplanning-chaining/CLAUDE.md LLM-robot-taskplanning-chaining/README_USAGE.md LLM-robot-taskplanning-chaining-rag/CLAUDE.md`
Expected: Each file has 50+ lines with English text

---

## Self-Review

**1. Spec coverage:**
- [S1] Task 1 covers LLM-robot-taskplanning-chaining/CLAUDE.md
- [S2] Task 2 covers LLM-robot-taskplanning-chaining/README_USAGE.md
- [S3] Task 3 covers LLM-robot-taskplanning-chaining-rag/CLAUDE.md

**2. Placeholder scan:** No TBD/TODO placeholders found.

**3. Type consistency:** All tasks use consistent bilingual format with `/` separator in headings.
