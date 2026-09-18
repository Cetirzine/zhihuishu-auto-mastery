import asyncio
import shuati


class FakeEl:
    def __init__(self, i):
        self.i = i


class FakePage:
    async def query_selector_all(self, sel):
        return [FakeEl(i) for i in range(6)]


qdata = {"questionTypeName": "多选题", "optionVos": [
    {"content": "NAD+", "sort": 1}, {"content": "FMN", "sort": 2}, {"content": "CoA", "sort": 3},
    {"content": "FAD", "sort": 4}, {"content": "NADP+", "sort": 5}, {"content": "TPP", "sort": 6}]}

m = asyncio.run(shuati.match_option_elements(qdata, ["FMN", "FAD"], FakePage()))
print("FAD/FMN 匹配索引:", [x.i for x in m], "(期望 [1,3])")

m2 = asyncio.run(shuati.match_option_elements(
    {"questionTypeName": "多选题", "optionVos": [{"content": "甲"}, {"content": "乙"}, {"content": "丙"}]},
    ["A, C"], FakePage()))
print("字母列表匹配索引:", [x.i for x in m2], "(期望 [0,2])")

m3 = asyncio.run(shuati.match_option_elements(
    {"questionTypeName": "单选题", "optionVos": [{"content": "提供能量"}, {"content": "作为溶剂"}]},
    ["B"], FakePage()))
print("单字母B匹配索引:", [x.i for x in m3], "(期望 [1])")
