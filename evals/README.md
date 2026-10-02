# 评测

- `evals.json`：4 条用例。1、2 是完整出课（末尾带“不用向我确认”，让自动化运行不卡在确认点）；3、4 只测开课确认和大纲确认两个确认点。
- `grade.py`：对一次运行的产物做 8 项客观检查，适用于任何 HTML 课程（不限于本 skill 生成的）。

```bash
python3 evals/grade.py <run_dir>   # run_dir 下要有 outputs/，结果写到 <run_dir>/grading.json
```

`grade.py` 直接调用本仓库 `skills/learn-in-3-hours/scripts/` 里的检查函数，手机渲染相关的几项需要本机有 Chrome / Chromium / Edge。
