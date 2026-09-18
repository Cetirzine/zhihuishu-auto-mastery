import shuati

assert shuati._parse_option_letters("C") == ["C"]
assert shuati._parse_option_letters("答案：C") == ["C"]
assert shuati._parse_option_letters("A, C") == ["A", "C"]
assert shuati._parse_option_letters("C.") == ["C"]
assert shuati._parse_option_letters("FAD") == []
assert shuati._parse_option_letters("ABC") == []

t = shuati._latex_norm(r"若 \(P(A\cup B)+P(AB)=1\),则 \(P(A)+P(B)=1\)")
c = shuati._latex_norm("A= 若 P(A∪B)+P(AB)=1，则 P(A)+P(B)=1")
assert t and (t in c or c in t), (t, c)

c2 = shuati._latex_norm(r"B= \(C = \overline{D}\)")
t2 = shuati._latex_norm(r"\(C = \overline{D}\)")
assert t2 in c2

c3 = shuati._latex_norm("C = D̄（互斥）")
t3 = shuati._latex_norm(r"\(C = \bar{D}\)")
print("t3:", t3, "| c3:", c3)

print("HELPERS_OK")
