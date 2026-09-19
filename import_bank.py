"""把题库CSV导入脚本题库（question_bank.json）。

用法：
  python import_bank.py                    # 导入 题库导出.csv
  python import_bank.py 别的文件.csv       # 导入指定CSV

CSV 格式（题库导出.csv 同款，至少要有 题目/答案 两列）：
  序号,题目ID,题目,答案,采集时间,来源
答案多选用；分隔。已有同题答案不会被覆盖（以先入库为准）。
"""
import csv
import sys
from pathlib import Path

from question_bank import QuestionBank


def main(path="题库导出.csv"):
    f = Path(path)
    if not f.exists():
        print(f"找不到 {f}")
        return
    bank = QuestionBank()
    before = len(bank)
    added = skipped = 0
    with open(f, encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        # 宽容列名（题目/题干、答案/正确答案）
        cols = {c: c for c in reader.fieldnames or []}
        qcol = next((c for c in ("题目", "题干", "question") if c in cols), None)
        acol = next((c for c in ("答案", "正确答案", "answer") if c in cols), None)
        icol = next((c for c in ("题目ID", "ID", "id") if c in cols), None)
        if not qcol or not acol:
            print("CSV 缺少 题目/答案 列")
            return
        for row in reader:
            q = (row.get(qcol) or "").strip()
            a_raw = (row.get(acol) or "").strip()
            if not q or not a_raw:
                continue
            answers = [x.strip() for x in a_raw.split("；") if x.strip()]
            qid = (row.get(icol) or "").strip() or None
            if qid and not qid.isdigit():
                qid = None
            if bank.lookup(qid, q):
                skipped += 1
                continue
            bank.save(qid, q, answers, source="导入")
            added += 1
    print(f"导入完成：新增 {added} 题，跳过已存在 {skipped} 题；题库 {before} -> {len(bank)} 条")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "题库导出.csv")
