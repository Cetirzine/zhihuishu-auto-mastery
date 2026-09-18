"""本地题库：题目 -> 正确答案（文本列表）。

主键：题目 id（智慧树每题有稳定数字 id）；
副键：题干指纹（跨试卷/换 id 兜底命中）。
答案统一存"选项内容文本列表"，作答时映射回当前卷子的选项元素，
这样换一套卷子（选项顺序打乱、id 变化）也能命中。
"""
import json
import re
import time
from pathlib import Path

BANK_PATH = Path(__file__).parent / "question_bank.json"


def strip_html(html: str) -> str:
    """HTML 转纯文本：去标签、实体、首尾空白。"""
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"&[a-z]+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def option_key(html: str) -> str:
    """选项的可比对键：有文字用文字；纯图片选项（数学公式）用图片 URL。
    采集和作答两侧都用这个键，URL 稳定，跨试卷精确命中。"""
    text = strip_html(html)
    if text:
        return text
    m = re.search(r'src="([^"]+)"', html or "")
    return m.group(1).strip() if m else ""


def normalize(text: str) -> str:
    """进一步去掉空白与标点，用于模糊比对（选项匹配/指纹）。"""
    text = re.sub(r"\s+", "", text or "")
    text = re.sub(r"[，。、；：？！,.;:?!‘’“”\"'（）()\[\]【】<>《》\-—_·…]", "", text)
    return text.lower()


def fingerprint(question_text: str) -> str:
    import hashlib

    return hashlib.md5(normalize(question_text).encode("utf-8")).hexdigest()


class QuestionBank:
    def __init__(self, path: Path = BANK_PATH):
        self.path = path
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))
        else:
            self.data = {}  # key: "id:936299034" 或 "fp:xxxx"

    # ---------- 查询 ----------
    def _keys(self, qid, question_text: str):
        keys = []
        if qid:
            keys.append(f"id:{qid}")
        keys.append(f"fp:{fingerprint(question_text)}")
        return keys

    def lookup(self, qid, question_text: str):
        """命中返回答案文本列表，未命中返回 None。"""
        for key in self._keys(qid, question_text):
            entry = self.data.get(key)
            if entry:
                return entry["answers"]
        return None

    # ---------- 保存 ----------
    def save(self, qid, question_text: str, answers: list[str], source: str = "harvest"):
        entry = {
            "question": question_text[:300],
            "answers": answers,
            "source": source,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        if qid:
            self.data[f"id:{qid}"] = entry
        self.data[f"fp:{fingerprint(question_text)}"] = entry
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    def __len__(self):
        return len(self.data)
