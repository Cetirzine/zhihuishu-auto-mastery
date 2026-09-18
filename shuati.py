"""智慧树掌握度自动刷题 · 整课模式（AI智慧课程）

模式：
  单知识点: python shuati.py --url "<learnPage地址>"
  整课扫题: python shuati.py --url "<任一learnPage地址>" --all [--concurrency 1]

整课流程：
  1. 刷第一个知识点，提交后的 point 页会自动返回全课程知识点树
     （get-graph-map-tree-with-mastery：nodeUid/名称/掌握度）
  2. 按树顺序逐点刷：learnPage -> 去提升 -> 答题(题库/AI) -> 提交 -> examPreview 采答案入库
  3. 每点循环到目标掌握度（或连续全对仍不涨则止损跳过）
  4. 一轮扫完若还有未达标且上轮有进步，再来一轮，直到全部达标或题池榨干
"""
import argparse
import asyncio
import json
import re
import time
from collections import deque
from pathlib import Path

from playwright.async_api import async_playwright, Page, BrowserContext

from ai_client import AI, load_config
from question_bank import QuestionBank, strip_html, normalize

ROOT = Path(__file__).parent
PROFILE = ROOT / "browser_profile"
LOGFILE = ROOT / "shuati_log.txt"
POINTS_CACHE = ROOT / "course_points.json"

EXAM_HOST = "studentexamcomh5.zhihuishu.com"
LEARN_URL_T = "https://ai-smart-course-student-pro.zhihuishu.com/learnPage/{course}/{point}/{cls}"
COURSE_HOME_T = (
    "https://ai-smart-course-student-pro.zhihuishu.com/singleCourse/knowledgeStudy/{course}/{cls}"
)

# 供 WebGUI 读取的运行状态与日志环形缓冲
STATUS = {
    "running": False, "course_id": None, "course_name": None, "point": None,
    "pass": 0, "mastery": None, "points_done": 0, "points_total": 0,
    "last_error": None,
}
LOG_BUFFER = deque(maxlen=600)


def log(msg: str):
    line = f"[{time.strftime('%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG_BUFFER.append(line)
    with open(LOGFILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ============================ 接口嗅探（按页面归属） ============================

class Sniffer:
    """被动捕获接口响应。题目/答卷等状态按 page 归属，支持多标签并行不串台。"""

    def __init__(self, ctx: BrowserContext):
        self.ctx = ctx
        self.tree = None            # 全课程知识点树（任一 point 页触发）
        self.state = {}             # page -> dict(question/sheet/paper/result/submit_done)
        self._lock = asyncio.Lock()  # 去提升->定位考试页 的临界区
        ctx.on("page", self.hook)

    def st(self, page: Page) -> dict:
        return self.state.setdefault(
            page, {"question": None, "sheet": None, "paper": None, "result": None, "submit_done": False}
        )

    def hook(self, page: Page):
        page.on("response", lambda resp: asyncio.ensure_future(self._on_resp(page, resp)))

    async def _on_resp(self, page: Page, resp):
        try:
            url = resp.url
            if resp.request.resource_type not in ("xhr", "fetch"):
                return
            st = self.st(page)
            if "get-graph-map-tree-with-mastery" in url:
                data = await resp.json()
                if data.get("data", {}).get("nodeList"):
                    self.tree = data["data"]
            elif "getExamQuestionInfo" in url:
                data = await resp.json()
                if data.get("data"):
                    st["question"] = data["data"]
            elif "getExamSheetInfo" in url:
                data = await resp.json()
                if data.get("data"):
                    st["sheet"] = data["data"]
            elif url.endswith("/stu/exam/questions-paper"):
                data = await resp.json()
                if data.get("data") and data["data"].get("paperId") is not None:
                    st["paper"] = data["data"]
            elif "questions-paper-result-page" in url:
                data = await resp.json()
                if data.get("data"):
                    st["result"] = data["data"]
            elif "/answer/saveAnswer" in url:
                st["saves"] = st.get("saves", 0) + 1
            elif "/exam/user/submit" in url:
                st["submit_done"] = True
        except Exception:
            pass

    async def wait_question_change(self, page: Page, prev_id, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            q = self.st(page)["question"]
            if q and q["id"] != prev_id:
                return q
            await asyncio.sleep(0.25)
        return None

    async def wait_paper(self, page: Page, timeout=20):
        deadline = time.time() + timeout
        while time.time() < deadline:
            p = self.st(page)["paper"]
            if p is not None:
                return p
            await asyncio.sleep(0.5)
        return None

    def reset_round(self, page: Page):
        st = self.st(page)
        st["submit_done"] = False
        st["result"] = None


# ============================ 答题核心 ============================

TYPE_SINGLE, TYPE_MULTI, TYPE_JUDGE, TYPE_FILL = "single", "multiple", "judgement", "completion"


def q_type(qdata) -> str:
    """题型前缀匹配，兼容平台的各种变体命名（如'填空客观题（自动批阅）'）。"""
    name = (qdata.get("questionTypeName") or "").strip()
    if "多选" in name:
        return TYPE_MULTI
    if "判断" in name:
        return TYPE_JUDGE
    if "填空" in name or "问答" in name or "简答" in name:
        return TYPE_FILL
    return TYPE_SINGLE


def is_image_question(qdata) -> bool:
    if "<img" in (qdata.get("content") or ""):
        return True
    if qdata.get("dataFileVos"):
        return True
    return any("<img" in (o.get("content") or "") for o in qdata.get("optionVos") or [])


def resolve_answers(qdata, png: bytes, ai: AI, bank: QuestionBank) -> tuple[list[str], str]:
    """题库优先，未命中调 AI（图片题用已截好的题目截图走视觉模型）。"""
    qid = qdata["id"]
    qtext = strip_html(qdata.get("content") or "")
    options = [strip_html(o.get("content") or "") for o in qdata.get("optionVos") or []]

    hit = bank.lookup(qid, qtext)
    if hit:
        return hit, "题库"

    if png:
        hint = qtext[:200] + ("；选项：" + " / ".join(options) if options else "")
        answer = ai.ask_image(png, hint)
        return [answer], "AI-视觉"

    opt_text = "\n".join(f"{chr(65 + i)}. {t}" for i, t in enumerate(options) if t)
    answer = ai.ask_text(qtext, opt_text)
    return [answer], "AI"


def _expand_answer(ans: str, qtype: str) -> list[str]:
    """把一条 AI/题库答案展开为可匹配单元：
    - 'A, B, C' / 'ABC' / 'A、B' -> ['A','B','C']（按字母映射）
    - 判断题同义词归一：正确/是/√ -> 对；错误/否/× -> 错
    """
    a = (ans or "").strip()
    if re.fullmatch(r"[A-Ha-h](?:[\s,，、;；/]*[A-Ha-h])*", a):
        return re.findall(r"[A-Ha-h]", a)
    if qtype == TYPE_JUDGE:
        if a in ("正确", "对", "是", "√", "T", "t", "true", "True", "对的"):
            return ["对"]
        if a in ("错误", "错", "否", "×", "x", "F", "f", "false", "False"):
            return ["错"]
    return [a]


async def match_option_elements(qdata, answers: list[str], page: Page):
    """答案文本 -> 当前卷面选项 DOM。先整体文本匹配（防止'FAD'这类词被误拆成字母），
    整体匹配失败才回退字母/同义词展开。"""
    qtype = q_type(qdata)
    if qtype == TYPE_MULTI:
        items = await page.query_selector_all(".checkbox-views label.el-checkbox")
    else:
        items = await page.query_selector_all(".radio-view li.clearfix")
    if not items:
        items = await page.query_selector_all(".questionContent li, .questionContent label")

    option_texts = [strip_html(o.get("content") or "") for o in qdata.get("optionVos") or []]

    def find_index(ans_n: str, used: set) -> int:
        for i, it in enumerate(option_texts):  # 精确
            if i in used:
                continue
            if ans_n == normalize(it):
                return i
        for i, it in enumerate(option_texts):  # 包含
            if i in used:
                continue
            it_n = normalize(it)
            if it_n and ans_n and (it_n in ans_n or ans_n in it_n):
                return i
        return -1

    matched, used = [], set()
    for ans in answers:
        # 1) 整体匹配优先（'FMN'/'FAD'/'作为溶剂'等真实选项文本）
        best_i = find_index(normalize(ans), used)
        if best_i != -1:
            matched.append(items[best_i]); used.add(best_i)
            continue
        # 2) 整体失败 -> 字母列表/判断题同义词展开
        for unit in _expand_answer(ans, qtype):
            if re.fullmatch(r"[A-Ha-h]", unit) and option_texts:
                idx = ord(unit.upper()) - 65
                if 0 <= idx < len(items) and idx not in used:
                    matched.append(items[idx]); used.add(idx)
                continue
            ui = find_index(normalize(unit), used)
            if ui != -1:
                matched.append(items[ui]); used.add(ui)
    return matched


async def click_option(item, qtype: str):
    if qtype == TYPE_MULTI:
        target = await item.query_selector(".el-checkbox__input:not(.is-checked)")
    else:
        target = await item.query_selector("i.iconfont:not(.checkedIcon)")
    await (target or item).click()


async def answer_current(exam_page: Page, qdata, ai, bank, cfg, dry_run=False, t_start=None,
                         sniffer: "Sniffer" = None) -> str:
    """作答当前题。填空题以 saveAnswer 真正触发为准，未保存则换打字方式重试。"""
    qtype = q_type(qdata)
    png = b""
    if is_image_question(qdata):
        el = await exam_page.query_selector(".questionContent")
        if el:
            png = await el.screenshot(type="png")
    answers, source = await asyncio.to_thread(resolve_answers, qdata, png, ai, bank)
    if dry_run:
        log(f"    [dry-run] {source} -> {answers}")
        return source
    if t_start is not None:
        remain = float(cfg.get("min_seconds_per_question", 0.4)) - (time.time() - t_start)
        if remain > 0:
            await asyncio.sleep(remain)

    if qtype == TYPE_FILL:
        inputs = await exam_page.query_selector_all(
            ".questionContent input:not([type=hidden]):not([type=radio]):not([type=checkbox]),"
            ".questionContent textarea,"
            ".questionContent [contenteditable=true], .questionContent [contenteditable='']"
        )
        if inputs:
            st = sniffer.st(exam_page) if sniffer else {}
            # 多空题：把答案拆分到每个空（分号/顿号优先，不足再按逗号）
            if len(inputs) > 1:
                flat = []
                for a in answers:
                    flat.extend(p.strip() for p in re.split(r"[；;、|]", a) if p.strip())
                if len(flat) < len(inputs):
                    flat2 = []
                    for p in flat:
                        flat2.extend(q.strip() for q in re.split(r"[，,]", p) if q.strip())
                    flat = flat2
                fill_answers = (flat + [""] * len(inputs))[:len(inputs)]
            else:
                fill_answers = answers[:1]
            for attempt in range(2):
                before = st.get("saves", 0)
                for inp, ans in zip(inputs, fill_answers):
                    if attempt == 0:
                        try:
                            await inp.click()
                            await inp.fill(ans)
                        except Exception:
                            await inp.evaluate(
                                "(el,v)=>{el.innerText=v;el.dispatchEvent(new Event('input',{bubbles:true}));}",
                                ans)
                    else:  # 第二次尝试：真实键盘输入
                        try:
                            await inp.click()
                            await inp.fill("")
                            await exam_page.keyboard.type(ans, delay=30)
                        except Exception:
                            pass
                    await asyncio.sleep(0.3)
                    try:
                        await inp.press("Enter")
                    except Exception:
                        pass
                # 失焦兜底
                try:
                    title = await exam_page.query_selector(".centent-pre")
                    if title:
                        await title.click()
                except Exception:
                    pass
                # 验证 saveAnswer 真的触发了
                for _ in range(12):
                    if st.get("saves", 0) > before:
                        log(f"    填空已保存 ✓（{answers}）")
                        await asyncio.sleep(0.3)
                        return source
                    await asyncio.sleep(0.25)
                log(f"    ⚠️ 填空未见保存信号（第{attempt + 1}次尝试）")
            log("    ⚠️ 填空保存失败，依赖下轮采答案修复")
            return source
        # 没有输入框：可能未渲染/是选择式填空。落盘DOM供排查，并回退按选择题匹配
        log("    ⚠️ 填空题未见输入框，回退选项匹配（DOM已落盘）")
        try:
            html = await exam_page.evaluate(
                "() => document.querySelector('.questionContent')?.outerHTML || ''")
            (ROOT / "probe_out" / "fill_dom_live.html").write_text(
                html[:8000], encoding="utf-8")
        except Exception:
            pass

    items = await match_option_elements(qdata, answers, exam_page)
    if not items:
        log(f"    ⚠️ 答案未匹配到选项: {answers}")
        return "未匹配"
    for it in items:
        await click_option(it, qtype)
        await asyncio.sleep(0.3)
    return source


async def submit_exam(exam_page: Page):
    """提交（顶部 span.reviewDone「提交作业」），处理含'存在未作答'提示在内的确认弹窗。"""

    async def find_submit():
        btn = await exam_page.query_selector("span.reviewDone, .right-H .reviewDone")
        if btn and await btn.is_visible():
            return btn
        for el in await exam_page.query_selector_all("button, span, div, a"):
            txt = (await el.inner_text() or "").strip()
            if txt in ("提交作业", "提 交", "提交", "交卷") and await el.is_visible():
                return el
        return None

    async def handle_confirm():
        """点掉确认弹窗：优先带 确定/提交/确认 字样的按钮。"""
        try:
            box = await exam_page.query_selector(".el-message-box")
            if not box:
                return
            for b_el in await exam_page.query_selector_all(".el-message-box .el-button"):
                t = (await b_el.inner_text() or "").strip()
                if any(k in t for k in ("确定", "提交", "确认")):
                    await b_el.click()
                    return
            primary = await exam_page.query_selector(
                ".el-message-box__btns .el-button--primary")
            if primary:
                await primary.click()
        except Exception:
            pass

    btn = await find_submit()
    if not btn:
        raise RuntimeError("找不到提交按钮")
    await btn.click()
    for _ in range(3):  # 可能连续多级弹窗
        await asyncio.sleep(1.5)
        await handle_confirm()
        if "/point/" in exam_page.url:
            return
    # 弹窗处理后等导航
    for _ in range(20):
        if "/point/" in exam_page.url:
            return
        await asyncio.sleep(1)
    # 仍未跳转：重试一次点击，再不行截图留证
    btn2 = await find_submit()
    if btn2:
        await btn2.click()
        await asyncio.sleep(3)
        await handle_confirm()
        for _ in range(15):
            if "/point/" in exam_page.url:
                return
            await asyncio.sleep(1)
    try:
        await exam_page.screenshot(path="probe_out/submit_timeout.png")
        dialogs = await exam_page.evaluate(
            "() => [...document.querySelectorAll('.el-message-box,.el-dialog__wrapper,[class*=modal],[class*=dialog],[class*=confirm]')]"
            ".filter(el => el.getBoundingClientRect().width > 0)"
            ".map(el => ({cls: (el.className||'').toString().slice(0,100),"
            " text: (el.innerText||'').replace(/\\s+/g,' ').slice(0,300),"
            " buttons: [...el.querySelectorAll('button,.el-button,span')].map(b=>(b.innerText||'').trim()).filter(Boolean).slice(0,8)}))"
        )
        (ROOT / "probe_out" / "submit_dialog_live.json").write_text(
            json.dumps(dialogs, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass
    raise TimeoutError("提交后未跳转结果页")


async def run_exam(exam_page: Page, sniffer: Sniffer, ai, bank, cfg, dry_run=False):
    """逐题作答 -> 提交。极速模式。"""
    await exam_page.wait_for_selector(".questionContent", timeout=20000)
    await asyncio.sleep(1.2)
    st = sniffer.st(exam_page)
    answered, bank_hits = set(), 0
    total = (st["sheet"] or {}).get("questionCount") or "?"
    t0 = time.time()
    while True:
        q = st["question"]
        if q and q["id"] not in answered:
            answered.add(q["id"])
            tq = time.time()
            qtext = strip_html(q.get("content") or "")
            log(f"    第{len(answered)}/{total}题 [{q.get('questionTypeName')}] {qtext[:36]}")
            src = await answer_current(exam_page, q, ai, bank, cfg, dry_run,
                                       t_start=tq, sniffer=sniffer)
            if "题库" in src:
                bank_hits += 1
        nxt = await exam_page.query_selector(".next-topic.next-t")
        if not nxt or not await nxt.is_visible():
            break
        prev_id = (q or {}).get("id")
        await nxt.click()
        if prev_id is not None:
            got = await sniffer.wait_question_change(exam_page, prev_id, timeout=8)
            if not got:
                await asyncio.sleep(1.2)
        await asyncio.sleep(cfg.get("answer_delay_seconds", 0.5))
    used = time.time() - t0
    log(f"    作答 {len(answered)} 题 / {used:.0f}s（题库命中 {bank_hits}），提交…")
    if not dry_run:
        await submit_exam(exam_page)
        await exam_page.wait_for_url("**/point/**", timeout=60000)
        log("    已提交 ✓")
    return {"seconds": used, "count": len(answered), "bank_hits": bank_hits}


# ============================ 采答案 & 知识点树 ============================

def harvest_result(result: dict, bank: QuestionBank):
    """结果页 -> 题库。返回 (入库数, 是否全对)。"""
    if result and "aiExamQuestionInfo" not in result and isinstance(result.get("data"), dict):
        result = result["data"]
    n, all_correct = 0, True
    for q in result.get("aiExamQuestionInfo") or []:
        qtext = strip_html(q.get("content") or "")
        correct = [strip_html(o.get("content") or "") for o in q.get("optionDtos") or [] if o.get("isCorrect") == 1]
        if correct:
            bank.save(q["id"], qtext, correct, source="错题采集")
            n += 1
        else:
            r = (q.get("result") or "").strip().rstrip("。")
            if r:
                bank.save(q["id"], qtext, [r], source="result字段")
                n += 1
        for ua in q.get("userAnswerDtos") or []:
            if ua.get("isCorrect") != 1:
                all_correct = False
                # 错题详情：定位是匹配错还是点击失败
                ans_ids = str(ua.get("answer") or "").split(",")
                opts = {str(o.get("id")): strip_html(o.get("content") or "")
                        for o in q.get("optionDtos") or []}
                mine = [opts.get(a, a) for a in ans_ids if a]
                log(f"    ❌ 判错: {qtext[:38]} | 我方提交: {mine} | 正确: {correct or q.get('result')}")
    return n, all_correct


async def goto_exam_preview_and_harvest(exam_page: Page, sniffer: Sniffer, bank):
    m = re.search(r"/point/(\d+)/(\d+)/(\d+)/(\d+)/(\d+)", exam_page.url)
    if not m:
        log("    ⚠️ 无法解析 point 页地址，跳过采集")
        return 0, False
    course, paper, exam_test, point, cls = m.groups()
    st = sniffer.st(exam_page)
    st["result"] = None
    await exam_page.goto(
        f"https://ai-smart-course-student-pro.zhihuishu.com/examPreview/"
        f"{course}/{paper}/{exam_test}/{point}/{cls}",
        wait_until="domcontentloaded",
    )
    deadline = time.time() + 20
    while time.time() < deadline and st["result"] is None:
        await asyncio.sleep(0.5)
    if st["result"] is None:
        log("    ⚠️ 结果页数据未捕获")
        return 0, False
    n, all_correct = harvest_result(st["result"], bank)
    log(f"    采答案入库 {n} 题{'，本轮全对 ✓' if all_correct else '，有错题'}")
    return n, all_correct


def parse_tree_points(tree: dict) -> list[dict]:
    """知识点树 -> 有序知识点列表 [{uid,name,mastery}]。"""
    nodes = tree.get("nodeList") or []
    by_pid = {}
    for n in nodes:
        by_pid.setdefault(n.get("pid"), []).append(n)
    for v in by_pid.values():
        v.sort(key=lambda x: x.get("sorted") or 0)
    out, seen = [], set()

    def walk(pid):
        for n in by_pid.get(pid, []):
            uid = str(n.get("nodeUid"))
            if uid in seen:
                continue
            seen.add(uid)
            out.append({"uid": uid, "name": n.get("nodeName") or uid,
                        "mastery": n.get("masteryPercentage") or 0})
            walk(n.get("id") or uid)

    walk(None)
    walk(0)
    # 兜底：没挂上树的散节点
    for n in nodes:
        uid = str(n.get("nodeUid"))
        if uid not in seen:
            seen.add(uid)
            out.append({"uid": uid, "name": n.get("nodeName") or uid,
                        "mastery": n.get("masteryPercentage") or 0})
    return out


# ============================ 单知识点流程 ============================

async def wait_captcha_gone(page: Page):
    while await page.query_selector(".yidun_popup"):
        log("    ⏸️ 检测到验证码，请手动完成后继续…")
        await asyncio.sleep(3)


async def open_exam(sniffer: Sniffer, main_page: Page, timeout=30) -> Page:
    """两跳开卷：learnPage「去提升」-> masteryHistory 页「去提升(improve-btn)」-> 考试页。
    临界区内完成，并发安全。"""
    async with sniffer._lock:
        before = set(id(p) for p in main_page.context.pages)

        def new_pages():
            return [pg for pg in main_page.context.pages if id(pg) not in before]

        async def find_exam():
            if EXAM_HOST in main_page.url:
                return main_page
            for pg in new_pages():
                try:
                    if EXAM_HOST in pg.url:
                        return pg
                except Exception:
                    pass
            return None

        # 第一跳：去提升 -> masteryHistory（建设中/不可测的点会停在原地）
        btn = await main_page.wait_for_selector(".simplified-mastery__action", timeout=10000)
        await btn.click()
        deadline = time.time() + 15
        while time.time() < deadline:
            if "masteryHistory" in main_page.url or await find_exam():
                break
            await asyncio.sleep(0.4)
        exam = await find_exam()
        if exam:
            await exam.wait_for_load_state("domcontentloaded")
            return exam
        if "masteryHistory" not in main_page.url:
            # 平台偶发"建设中"状态：快速失败，下一轮扫雷自动重试该点
            raise RuntimeError("开卷失败（疑似建设中/暂不可测），跳过本轮")
        improve = await main_page.wait_for_selector(".improve-btn", timeout=10000)
        await improve.click()
        deadline = time.time() + timeout
        while time.time() < deadline:
            exam = await find_exam()
            if exam:
                await exam.wait_for_load_state("domcontentloaded")
                return exam
            await asyncio.sleep(0.4)
        raise TimeoutError("掌握度历史页点击开卷后未出现测试页")


async def grind_point(main_page: Page, sniffer: Sniffer, ai, bank, cfg, course, point, cls,
                     dry_run=False):
    """刷一个知识点。返回 (high, passes, perfect_streak_stopped)。"""
    learn_url = LEARN_URL_T.format(course=course, point=point["uid"], cls=cls)
    STATUS["point"] = point["name"]
    log(f"▶ 知识点：{point['name']}（树显掌握度 {point['mastery']}%）")
    st = sniffer.st(main_page)
    st["paper"] = None
    try:
        await main_page.goto(learn_url, wait_until="domcontentloaded")
        await main_page.wait_for_selector(".simplified-mastery", timeout=15000)
    except Exception:
        log("  ⏭️ 该节点无测试入口（非考点/纯资源节点），跳过")
        return point["mastery"], 0, False

    paper = await sniffer.wait_paper(main_page)
    if not paper or not paper.get("questionNum"):
        log("  ⏭️ 无试卷（questionNum=0），跳过")
        return point["mastery"], 0, False

    target = cfg.get("target_mastery", 100)
    max_p = cfg.get("max_passes_per_point", 8)
    min_p = cfg.get("min_passes_per_point", 1)
    max_streak = cfg.get("max_perfect_streak", 3)
    passes, perfect_streak = 0, 0
    streak_stopped = False

    while (paper["highMasteryScore"] < target or passes < min_p) and passes < max_p:
        if perfect_streak >= max_streak:
            log(f"  连续{max_streak}轮全对仍未达{target}%，止损跳下一知识点")
            streak_stopped = True
            break
        passes += 1
        STATUS["pass"] = passes
        STATUS["mastery"] = paper["highMasteryScore"]
        log(f"  第 {passes} 轮（最高掌握度 {paper['highMasteryScore']}%）")
        await wait_captcha_gone(main_page)
        exam_page = await open_exam(sniffer, main_page)
        sniffer.reset_round(exam_page)
        try:
            stats = await run_exam(exam_page, sniffer, ai, bank, cfg, dry_run)
            if not dry_run:
                _, all_correct = await goto_exam_preview_and_harvest(exam_page, sniffer, bank)
                perfect_streak = perfect_streak + 1 if all_correct else 0
        finally:
            if exam_page != main_page:
                try:
                    await exam_page.close()
                except Exception:
                    pass
        if dry_run:
            break
        st["paper"] = None
        await main_page.goto(learn_url, wait_until="domcontentloaded")
        await main_page.wait_for_selector(".simplified-mastery", timeout=15000)
        paper = await sniffer.wait_paper(main_page) or paper
        log(f"  本轮 {stats['seconds']:.0f}s（{stats['count']}题） -> 掌握度 "
            f"{paper['masteryScore']}%（最高 {paper['highMasteryScore']}%）｜题库 {len(bank)} 条")
    return paper["highMasteryScore"], passes, streak_stopped


# ============================ 扫题引擎（可编程调用） ============================

async def run_sweep(ctx, sniffer, ai, bank, cfg, course, cls, points,
                    dry_run=False, bootstrap_tree=False, course_name=None):
    """对给定知识点列表循环刷题到目标。

    points: [{uid,name,mastery}]
    bootstrap_tree: True 时首轮后用 sniffer.tree 扩充列表（CLI 单点起步用）
    返回 {uid: {name, high, passes, stopped}}
    """
    results = {}
    concurrency = max(1, min(3, int(cfg.get("concurrency", 1))))
    max_sweeps = cfg.get("max_sweeps", 3)
    sem = asyncio.Semaphore(concurrency)
    STATUS.update({"running": True, "course_id": course, "course_name": course_name,
                   "points_done": 0, "points_total": len(points), "last_error": None})
    try:
        for sweep in range(1, max_sweeps + 1):
            if bootstrap_tree and sniffer.tree and len(points) <= 1:
                points = parse_tree_points(sniffer.tree)
                POINTS_CACHE.write_text(
                    json.dumps(points, ensure_ascii=False, indent=1), encoding="utf-8")
                log(f"📚 课程共 {len(points)} 个知识点（已缓存 course_points.json）")
                STATUS["points_total"] = len(points)

            target = cfg.get("target_mastery", 100)
            todo = [p for p in points
                    if results.get(p["uid"], {}).get("high", p["mastery"]) < target]
            log(f"===== 第 {sweep} 轮扫描：{len(todo)}/{len(points)} 个知识点未达标 =====")
            if not todo:
                break
            progress_before = {p["uid"]: results.get(p["uid"], {}).get("high", p["mastery"])
                               for p in points}

            async def worker(p):
                async with sem:
                    page = await ctx.new_page()
                    try:
                        high, passes, stopped = await grind_point(
                            page, sniffer, ai, bank, cfg, course, p, cls, dry_run)
                        results[p["uid"]] = {"name": p["name"], "high": high,
                                             "passes": passes, "stopped": stopped}
                        STATUS["points_done"] = sum(
                            1 for r in results.values() if r.get("passes", 0) > 0)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        log(f"  ❌ {p['name']} 出错: {type(e).__name__} {str(e)[:100]}")
                        STATUS["last_error"] = f"{p['name']}: {type(e).__name__}"
                        results[p["uid"]] = results.get(p["uid"]) or {
                            "name": p["name"], "high": p["mastery"], "passes": 0, "stopped": False}
                    finally:
                        try:
                            await page.close()
                        except Exception:
                            pass

            await asyncio.gather(*(worker(p) for p in todo))
            if dry_run:
                break
            any_attempted = any(r.get("passes", 0) > 0 for r in results.values())
            improved = any(
                results.get(p["uid"], {}).get("high", 0) > progress_before.get(p["uid"], 0)
                for p in points if p["uid"] in results
            )
            if not any_attempted:
                log("⚠️ 本轮没有任何知识点完成作答（全部异常？），停止以免空转")
                break
            if not improved:
                log("本轮无进步，题池已榨干，收工")
                break

        log("===== 总结 =====")
        for uid, r in sorted(results.items(), key=lambda kv: -kv[1]["high"]):
            tag = "✓" if r["high"] >= cfg.get("target_mastery", 100) else ("止损" if r["stopped"] else "未达")
            log(f"  [{tag}] {r['name']}: 最高{r['high']}% / {r['passes']}轮")
        log(f"题库总量：{len(bank)} 条")
        return results
    finally:
        STATUS.update({"running": False, "point": None})


async def run_all(ctx, sniffer, ai, bank, cfg, course, cls, first_point, dry_run=False):
    """CLI 整课模式：从单个知识点起步，靠 sniffer 捕获的树自动扩充。"""
    points = [{"uid": first_point, "name": "起点知识点", "mastery": 0}]
    await run_sweep(ctx, sniffer, ai, bank, cfg, course, cls, points,
                    dry_run=dry_run, bootstrap_tree=True)


# ============================ 主入口 ============================

def parse_learn_url(url: str):
    m = re.search(r"/learnPage/(\d+)/(\d+)/(\d+)", url)
    if not m:
        raise SystemExit("--url 需是 learnPage 地址：/learnPage/{courseId}/{pointId}/{classId}")
    course, point, cls = m.groups()
    return course, point, cls


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="任一知识点 learnPage 地址")
    ap.add_argument("--all", action="store_true", help="整课模式：自动发现并刷完全部知识点")
    ap.add_argument("--concurrency", type=int, default=1, help="并发知识点数 1-3（默认1，最低并发最稳）")
    ap.add_argument("--provider", choices=["deepseek", "gemini"])
    ap.add_argument("--dry-run", action="store_true", help="只解析答案不点击")
    args = ap.parse_args()

    cfg = load_config()
    if args.provider:
        cfg["provider"] = args.provider
    cfg["concurrency"] = args.concurrency
    course, first_point, cls = parse_learn_url(args.url)
    ai = AI(cfg)
    bank = QuestionBank()
    log(f"=== 启动 | AI={cfg['provider']} | 题库{len(bank)}条 | 整课={args.all} | 并发={args.concurrency} | dry={args.dry_run} ===")

    # AI 连通性预检：避免开卷后挂在超时上
    if not args.dry_run:
        try:
            probe = ai.ask_text("预检：1+1=?", "A. 1 B. 2")
            log(f"AI 预检通过（{ai.provider} 回答 {probe}）")
        except Exception as e:
            log(f"❌ AI 预检失败（{ai.provider}: {type(e).__name__}），请检查网络/代理/key 后重试")
            raise SystemExit(1)

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(PROFILE), channel="msedge", headless=False, no_viewport=True,
            args=["--start-maximized"],
        )
        sniffer = Sniffer(ctx)
        for pg in ctx.pages:
            sniffer.hook(pg)
        main_page = ctx.pages[0] if ctx.pages else await ctx.new_page()

        if args.all:
            await run_all(ctx, sniffer, ai, bank, cfg, course, cls, first_point, args.dry_run)
        else:
            page = await ctx.new_page()
            await grind_point(page, sniffer, ai, bank, cfg, course,
                              {"uid": first_point, "name": "指定知识点", "mastery": 0}, cls, args.dry_run)
            try:
                await page.close()
            except Exception:
                pass
        log("=== 结束 ===")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("已停止")
