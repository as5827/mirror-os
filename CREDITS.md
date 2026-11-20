# 来源与署名

本项目的代码、提示词、skill 文本、知识库条目全部为原创撰写。
但「Agent Skill」这个格式和部分设计思路来自公开项目，这里逐项说明，避免任何人误以为是无来源的原创。

## 格式规范

**SKILL.md 格式**参照 [anthropics/skills](https://github.com/anthropics/skills) 公开的 Agent Skills 规范：
一个 skill = 一个文件夹 + `SKILL.md`，frontmatter 只有 `name` 与 `description` 两个必填字段。

- 只采用了**格式**，没有复制该仓库任何一个 skill 的正文内容。
- 格式本身是公开规范，规范文本见 [agentskills.io](https://agentskills.io)；该仓库内各 skill 的具体许可见其各自文件夹。
- `.` 本项目 `skills/` 下 7 个 skill 的全部文字均为原创。

## 设计思路参考

**[millwright-labs/minto-pyramid-skill](https://github.com/millwright-labs/minto-pyramid-skill)**（MIT License）

读完其 `SKILL.md` 后，借用了两个**设计思想**（不是文字）：

1. **Gate 机制**：先判断「这个情境该不该用这个 skill」，不适用就明确拒绝并说明理由，而不是硬套。
2. **结论先行 → 理由分组 → 证据垫底**的结构。

`skills/warm-reply/SKILL.md` 与 `app/phrasing.py` 的「情境判定 + 拒绝不适用」逻辑受此启发，
但全部文字与规则为本项目自行撰写。

金字塔原理本身来自 Barbara Minto 的著作《The Pyramid Principle》（1987），属于公开的方法论。

## 知识库条目

`app/knowledge.py` 里 37 条心理学 / 社会学 / 行为经济学条目：

- 全部是**本项目自行撰写的概念摘要**，不是任何书籍或论文的原文摘录。
- 每一条都带有 `source` 字段（作者 + 年份 + 文献名），供使用者自行核对原始出处。
- 其中「成长型心态」一条已明确标注存在**可重复性争议**，不作为结论使用。

## 外部服务

联网检索通过以下公开 API 完成，均无需密钥。文献元数据（标题、被引数、DOI）按其 API 使用条款缓存于本机数据库，并在展示时标注来源：

- [OpenAlex](https://openalex.org) — CC0 元数据
- [Crossref](https://www.crossref.org) — 元数据按其使用条款
- [arXiv](https://arxiv.org) — 元数据

AI 推理调用 **DeepSeek API**（使用者自备 Key）。数据不回传本项目作者。

## 第三方代码

**没有**。运行时不依赖任何第三方 Python 包，只用标准库（`http.server` / `sqlite3` / `urllib` 等）。

唯一的外部依赖是 `make_icon.py` 里用到的 [Pillow](https://python-pillow.org)（HPND License），
仅在**生成图标**时需要，不参与运行时。

## 图标

`make_icon.py` 用系统字体（微软雅黑 / 黑体）渲染「镜」字生成图标。字体本身不随项目分发。
如果你要商用分发，请注意 Windows 自带字体的授权范围，换成开源字体（如思源黑体）更稳妥。
