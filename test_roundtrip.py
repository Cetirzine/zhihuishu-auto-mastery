"""回灌测试：导出的 CSV 导入空题库，验证命中率。"""
import csv
import tempfile
from pathlib import Path

from question_bank import QuestionBank

tmp = Path(tempfile.gettempdir()) / "bank_test.json"
tmp.unlink(missing_ok=True)
bank = QuestionBank(tmp)

rows = list(csv.DictReader(open("题库导出.csv", encoding="utf-8-sig")))
for row in rows:
    q = row["题目"].strip()
    ans = [x.strip() for x in row["答案"].split("；") if x.strip()]
    qid = row.get("题目ID") or None
    bank.save(qid if qid and qid.isdigit() else None, q, ans, "导入")

ok = sum(1 for row in rows[:200] if bank.lookup(row.get("题目ID") or None, row["题目"]))
print(f"回灌验证：前200题命中 {ok}/200，空库题量 {len(bank)}")
tmp.unlink(missing_ok=True)
