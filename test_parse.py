import re


def parse_letters(s: str) -> list[str]:
    s = re.sub(r"^(答案|选项|正确选项)[:：\s]*", "", (s or "").strip())
    s = re.sub(r"[.、．。:：)\]！!]+$", "", s).strip().upper()
    if re.fullmatch(r"[A-H]([,，、;；/\s]+[A-H])*", s):
        return re.findall(r"[A-H]", s)
    return []


cases = [("C", ["C"]), ("C.", ["C"]), ("答案：C", ["C"]), ("A, C", ["A", "C"]),
         ("C = \\bar{D}", []), ("B、D", ["B", "D"]), ("FAD", []), ("A,C.", ["A", "C"]),
         ("ABC", []), ("a c", ["A", "C"])]
for t, w in cases:
    r = parse_letters(t)
    assert r == w, (t, r, w)
    print(f"{t!r:20} => {r}")
print("PARSE_OK")
