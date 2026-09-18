"""公式题方案单元测试：option_key URL键 / 字母解析 / 视觉路径判定。"""
import asyncio
import re

import shuati
from question_bank import option_key, strip_html

# 1) option_key：图片选项 -> URL 键；文字选项 -> 文本
img_opt = '<div><span class="math-inline "><img src="https://hike-export.oss-cn-hangzhou.aliyuncs.com/paper/dev/20260915/ae6297c1.png"></span></div>'
txt_opt = "<p>作为溶剂</p>"
k1, k2 = option_key(img_opt), option_key(txt_opt)
assert k1 == "https://hike-export.oss-cn-hangzhou.aliyuncs.com/paper/dev/20260915/ae6297c1.png", k1
assert k2 == "作为溶剂", k2
print("1) option_key: 图片->URL / 文字->文本 ✓")

# 2) 图片选项判定（img_options）
qdata = {"optionVos": [{"content": img_opt} for _ in range(4)]}
texts = [strip_html(o["content"]) for o in qdata["optionVos"]]
assert bool(texts) and not any(texts)
print("2) img_options 判定 ✓")

# 3) 字母答案解析（视觉返回 C 或 A,C）
for ans, want in [("C", ["C"]), ("A,C", ["A", "C"]), ("a、b", ["A", "B"])]:
    got = re.findall(r"[A-H]", ans.upper()) if re.fullmatch(
        r"[A-Ha-h](?:[\s,，、;；/]*[A-Ha-h])*", ans.strip()) else None
    assert got == want, (ans, got)
print("3) 字母解析 ✓")

# 4) 回放匹配：题库存的 URL 键 vs 当前卷 optionVos 的 URL 键 -> 索引一致
class FakeEl:
    def __init__(self, i): self.i = i
class FakePage:
    async def query_selector_all(self, s): return [FakeEl(i) for i in range(4)]

qdata2 = {"questionTypeName": "4865RPA", "optionVos": [
    {"content": '<p><img src="https://x/1.png"></p>'},
    {"content": f'<div><img src="{k1}"></div>'},
    {"content": '<p><img src="https://x/3.png"></p>'},
    {"content": '<p><img src="https://x/4.png"></p>'}]}
m = asyncio.run(shuati.match_option_elements(qdata2, [k1], FakePage()))
assert [x.i for x in m] == [1], [x.i for x in m]
print("4) URL键回放匹配索引 [1] ✓")

print("ALL_PASS")
