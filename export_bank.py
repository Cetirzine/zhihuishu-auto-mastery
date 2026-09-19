"""导出题库：Markdown（阅读版）+ CSV（表格版），去重（id/fp 双键）。"""
import csv
import json
from collections import OrderedDict

d = json.load(open("question_bank.json", encoding="utf-8"))

# 去重：同一题 id:/fp: 两条只留一条（优先 id: 键）
uniq = OrderedDict()
for k, v in d.items():
    q = v.get("question", "")
    if not q:
        continue
    key = (q, tuple(v.get("answers") or []))
    if key not in uniq or k.startswith("id:"):
        uniq[key] = {**v, "key": k}

rows = sorted(uniq.values(), key=lambda r: r.get("time") or "")

# Markdown
with open("题库导出.md", "w", encoding="utf-8") as f:
    f.write(f"# 题库导出（{len(rows)} 题）\n\n")
    f.write(f"> 导出时间：{__import__('time').strftime('%Y-%m-%d %H:%M')}｜来源：question_bank.json\n\n")
    for i, r in enumerate(rows, 1):
        f.write(f"**{i}. {r['question']}**\n\n")
        f.write(f"答案：{'；'.join(r['answers'])}\n\n")
        f.write(f"<sub>采集：{r.get('time', '')}｜{r.get('source', '')}</sub>\n\n---\n\n")

# CSV（含题目ID，可经 import_bank.py 回灌进脚本）
with open("题库导出.csv", "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["序号", "题目ID", "题目", "答案", "采集时间", "来源"])
    for i, r in enumerate(rows, 1):
        qid = r["key"][3:] if r["key"].startswith("id:") else ""
        w.writerow([i, qid, r["question"], "；".join(r["answers"]), r.get("time", ""), r.get("source", "")])

print(f"导出完成：{len(rows)} 题（原始键 {len(d)} 条，去重后）")
print("  -> 题库导出.md（阅读版）")
print("  -> 题库导出.csv（Excel 可直接打开）")
