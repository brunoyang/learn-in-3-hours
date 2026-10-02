# learn-in-3-hours

一个 Claude Code skill：把“我想学 X”变成一套约 3 小时的图解 HTML 课程。事实逐条对照一手来源核查，图在手机宽度下实测，每页时长按内容量计算。

> **English:** A Claude Code skill that turns "I want to learn X" into a ~3-hour illustrated HTML course: every factual claim is checked against a primary source, diagrams are measured at phone width, and study times are computed from the content. The skill instructions and the generated courses are in Chinese.

<p>
<img src="docs/00-map.png" width="260" alt="示例课程的学习地图页">
&nbsp;
<img src="docs/01-compound.png" width="260" alt="示例课程第 1 页：复利">
</p>

随 skill 附带的示例课程（复利，4 页），390px 手机宽度截图。源文件：[`assets/example/`](skills/learn-in-3-hours/assets/example/)。

## 设计

| 问题 | 对策 |
|---|---|
| 需求没问清就开工 | 两个确认点：调研前确认靶心与范围，写页面前确认大纲 |
| 事实过时或错误 | 每条可核查的说法登记到 `claims.json`，单独一轮核查；存在未核查条目时质检不通过 |
| 手机上看不清 | 图按 340 宽绘制；质检在 390px 视口实测标签字号、重叠和溢出，并输出截图 |
| 标称时长与内容不符 | 每页时长由构建脚本按内容量计算 |
| 图只摆名词、不讲因果 | 新读者测试：未读正文的子 agent 仅凭截图和图注复述，复述不出因果则重画 |
| 各页之间脱节 | 连贯性通读：新读者按页序通读全课文本，标出衔接断层 |

课程分四幕：立靶、原理、应用、延伸。应用幕以“靶心实战”收尾，最后一页是费曼验收。每页含自测题（答案折叠），每个类比都注明失效边界。

```
learn-<slug>/
├── 00-map.html … 99-review.html   课程页面，无外部依赖，可离线打开
└── _work/                          课程配置、调研笔记、来源、事实台账、页面片段、质检报告
```

完整规范见 [`SKILL.md`](skills/learn-in-3-hours/SKILL.md) 和 [`references/`](skills/learn-in-3-hours/references/)。

## 安装

作为插件安装（在 Claude Code 中）：

```
/plugin marketplace add brunoyang/learn-in-3-hours
/plugin install learn-in-3-hours@learn-in-3-hours
```

或链接到个人 skills 目录：

```bash
git clone https://github.com/brunoyang/learn-in-3-hours.git
ln -s "$PWD/learn-in-3-hours/skills/learn-in-3-hours" ~/.claude/skills/learn-in-3-hours
```

依赖：Python 3（仅标准库）；手机渲染检查需要 Chrome、Chromium 或 Edge，可用 `CHROME_PATH` 指定，缺失时跳过该项并给出警告。

## 用法

在 Claude Code 中提出学习需求即可触发，例如“我想学 PostgreSQL 查询优化，做个三小时的课”。

1. **开课确认**：给出 2–3 个候选靶心、使用场景、范围、内容基线、时长与存放位置、预计成本，等待回复。
2. **大纲确认**：初步调研后给出四幕大纲，等待确认。
3. **生成**：写作、核查、构建、质检，中途不再询问。

说明“不用问我，直接做”可跳过两次确认。一门 3 小时的课约需 1.5 小时、50 万 token（不含核查子 agent）。

脚本也可单独运行：

```bash
S=skills/learn-in-3-hours/scripts
python3 $S/build_course.py   <课程目录>                               # 生成页面并估算时长
python3 $S/check_course.py   <课程目录> [--no-render] [--full-shots]  # 质检，有错误时退出码为 1
python3 $S/merge_verdicts.py <课程目录> [--dry-run]                   # 将核查结论合并进台账和页面
```

`check_course.py` 在没有 `_work/course.json` 的目录上以通用模式运行，只检查外部依赖和手机渲染，适用于任意 HTML 页面。

## 评测

与原版 learn-in-3-hours 对照，两个题目各运行 1 次，用 [`evals/grade.py`](evals/grade.py) 的 8 项客观检查评分。

| 指标（Kafka 4.x / 美联储） | 原版 | 本 skill |
|---|---|---|
| 客观检查通过 | 3/8 · 4/8 | 7/8 · 8/8 |
| 标称时长 vs 按内容估算（分钟） | 180 vs 72 · 180 vs 68 | 178 vs 196 · 167 vs 187 |
| 生成耗时（分钟） | 55 · 54 | 85 · 98 |
| token（不含核查子 agent） | 30 万 · 30 万 | 50 万 · 51 万 |

8 项检查为：无外部依赖、手机无横向滚动、图中字号 ≥ 11px、标签无重叠或溢出、每页有来源、写明内容基线、自测答案折叠、标称时长与内容量相差不超过 30%，与本 skill 的质检项有重合。两个版本的关键事实均正确；本 skill 的核查轮在初稿中改正了 19 处（Kafka）和 6 处（美联储）错误。开课确认、大纲确认、新读者测试和连贯性通读在此次对照之后加入，尚未重新对照。用例见 [`evals/`](evals/)。

## 致谢

灵感来自朱卫军的 [learn-in-3-hours](https://github.com/zhuweijun1003-source/zhuwj-skills)。

## 许可证

[MIT](LICENSE)
