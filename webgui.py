"""智慧树自动刷题 WebGUI 本地服务器。

用法：python webgui.py  ->  浏览器打开 http://127.0.0.1:8787

功能：
- 登录按钮：弹出浏览器登录智慧树，登录态持久记住（browser_profile/）
- 课程检测：自动发现账号下全部 AI 智慧课程及各知识点掌握度
- 一键刷课 / 停止；实时日志与进度
- 双 API Key（DeepSeek / Gemini）在线配置保存与连通性测试
"""
import asyncio
import json
import time
from pathlib import Path

from aiohttp import web, ClientSession

import shuati
from shuati import Sniffer, run_sweep, COURSE_HOME_T, PROFILE, STATUS, LOG_BUFFER, log
from ai_client import AI, load_config
from question_bank import QuestionBank

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "config.json"
INDEX_PATH = ROOT / "static" / "index.html"

HOME_URL = "https://onlineweb.zhihuishu.com/onlinestuh5"

STATE = {
    "login": None,           # None=未知 True/False
    "courses": [],           # 缓存的课程列表
    "courses_time": 0,
    "grind_task": None,
    "grind_course": None,
}


# ---------------- 浏览器管理 ----------------

class BrowserManager:
    def __init__(self):
        self.pw = None
        self.ctx = None

    async def get(self):
        if self.ctx is None:
            from playwright.async_api import async_playwright
            self.pw = await async_playwright().start()
            self.ctx = await self.pw.chromium.launch_persistent_context(
                str(PROFILE), channel="msedge", headless=False, no_viewport=True,
                args=["--start-maximized"],
            )
            if not self.ctx.pages:
                await self.ctx.new_page()
        return self.ctx

    async def close(self):
        if self.ctx:
            try:
                await self.ctx.close()
            except Exception:
                pass
        if self.pw:
            try:
                await self.pw.stop()
            except Exception:
                pass
        self.ctx = self.pw = None


BM = BrowserManager()


# ---------------- 课程发现 ----------------

def _extract_courses(payload):
    """从任意嵌套 JSON 中提取含 courseId+classId 的课程对象。"""
    out = []

    def walk(x):
        if isinstance(x, dict):
            cid = x.get("courseId")
            if cid is not None and (x.get("classId") is not None):
                out.append({
                    "courseId": str(cid),
                    "classId": str(x["classId"]),
                    "courseName": x.get("courseName") or x.get("name") or f"课程{cid}",
                    "className": x.get("className") or "",
                    "raw": {k: x.get(k) for k in ("teacherName", "schoolName", "termName") if x.get(k)},
                })
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(payload)
    seen, dedup = set(), []
    for c in out:
        k = (c["courseId"], c["classId"])
        if k not in seen:
            seen.add(k)
            dedup.append(c)
    return dedup


async def fetch_course_points(ctx, course_id: str, class_id: str):
    """打开课程首页，抓 list-knowledge-theme -> 知识点列表（含掌握度）。"""
    page = await ctx.new_page()
    cap = {}

    async def on_resp(resp):
        if "list-knowledge-theme" in resp.url and "theme" not in cap:
            try:
                cap["theme"] = await resp.json()
            except Exception:
                pass

    page.on("response", lambda r: asyncio.ensure_future(on_resp(r)))
    try:
        await page.goto(COURSE_HOME_T.format(course=course_id, cls=class_id),
                        wait_until="domcontentloaded")
        for _ in range(24):
            if "theme" in cap:
                break
            await asyncio.sleep(0.5)
    finally:
        try:
            await page.close()
        except Exception:
            pass
    if "theme" not in cap:
        return []
    points = []
    for theme in (cap["theme"].get("data", {}) or {}).get("courseThemeList") or []:
        for sub in theme.get("subThemeList") or []:
            for k in sub.get("knowledgeList") or []:
                points.append({
                    "uid": str(k.get("knowledgeId")),
                    "name": k.get("knowledgeName") or "?",
                    "mastery": k.get("masteryPercentage") or 0,
                })
    return points


async def detect_courses(force=False):
    """检测登录 + 课程列表。返回 (login, courses)。"""
    if not force and STATE["courses"] and time.time() - STATE["courses_time"] < 300:
        return STATE["login"], STATE["courses"]
    ctx = await BM.get()
    page = await ctx.new_page()
    cap = {}

    async def on_resp(resp):
        u = resp.url
        for key in ("queryStudentAICourseList", "getCourseList"):
            if key in u and key not in cap:
                try:
                    cap[key] = await resp.json()
                except Exception:
                    pass

    page.on("response", lambda r: asyncio.ensure_future(on_resp(r)))
    logged_in = True
    try:
        await page.goto(HOME_URL, wait_until="domcontentloaded")
        for _ in range(30):
            if "passport" in page.url:
                logged_in = False
                break
            if "queryStudentAICourseList" in cap:
                break
            await asyncio.sleep(0.5)
        if "passport" in page.url:
            logged_in = False
    finally:
        try:
            await page.close()
        except Exception:
            pass
    STATE["login"] = logged_in
    if not logged_in:
        return False, []

    courses = _extract_courses(cap.get("queryStudentAICourseList")) or \
              _extract_courses(cap.get("getCourseList"))
    # 深度探测每门课的知识点掌握度（顺带过滤无知识点的课程）
    rich = []
    for c in courses[:6]:
        pts = await fetch_course_points(ctx, c["courseId"], c["classId"])
        below = sum(1 for p in pts if p["mastery"] < 100)
        rich.append({**c, "points_total": len(pts), "points_below_100": below,
                     "avg_mastery": round(sum(p["mastery"] for p in pts) / len(pts)) if pts else 0})
    STATE["courses"] = rich
    STATE["courses_time"] = time.time()
    return True, rich


# ---------------- 刷题任务控制 ----------------

async def _grind_wrapper(course, class_id, concurrency, dry_run):
    try:
        ctx = await BM.get()
        sniffer = Sniffer(ctx)
        for pg in ctx.pages:
            sniffer.hook(pg)
        points = await fetch_course_points(ctx, course["courseId"], class_id)
        if not points:
            log(f"⚠️ 课程 {course['courseName']} 未发现知识点，结束")
            return
        cfg = load_config()
        cfg["concurrency"] = concurrency
        ai = AI(cfg)
        bank = QuestionBank()
        log(f"🎯 开始刷课：{course['courseName']}（{len(points)} 个知识点，并发{concurrency}）")
        await run_sweep(ctx, sniffer, ai, bank, cfg, course["courseId"], class_id,
                        points, dry_run=dry_run, course_name=course["courseName"])
    except asyncio.CancelledError:
        log("🛑 任务已手动停止")
    except Exception as e:
        log(f"❌ 任务异常结束: {type(e).__name__} {str(e)[:150]}")
        STATUS["last_error"] = str(e)[:150]
    finally:
        STATE["grind_task"] = None
        STATE["grind_course"] = None


# ---------------- HTTP 接口 ----------------

def mask(key: str) -> str:
    if not key or "在这里" in key:
        return ""
    return key[:6] + "…" + key[-4:] if len(key) > 14 else key


async def api_status(request):
    cfg = load_config()
    bank = QuestionBank()
    return web.json_response({
        "login": STATE["login"],
        "running": STATE["grind_task"] is not None and not STATE["grind_task"].done(),
        "grind_course": STATE["grind_course"],
        "status": {k: STATUS.get(k) for k in
                   ("point", "pass", "mastery", "points_done", "points_total", "last_error")},
        "bank": len(bank),
        "provider": cfg.get("provider"),
        "keys": {"deepseek": bool(mask(cfg.get("deepseek_api_key", ""))),
                 "gemini": bool(mask(cfg.get("gemini_api_key", "")))},
    })


async def api_courses(request):
    force = request.query.get("force") == "1"
    try:
        login, courses = await detect_courses(force=force)
    except Exception as e:
        return web.json_response({"error": f"{type(e).__name__}: {e}"}, status=500)
    return web.json_response({"login": login, "courses": courses})


async def api_login(request):
    """打开登录页，后台轮询直到登录成功。"""
    ctx = await BM.get()
    page = await ctx.new_page()
    await page.goto(HOME_URL, wait_until="domcontentloaded")

    async def poller():
        try:
            for _ in range(1800):  # 最多等 1 小时
                await asyncio.sleep(2)
                try:
                    url = page.url
                except Exception:
                    return
                if "passport" not in url and "onlineweb" in url:
                    # 再确认一下首页能加载出课程接口
                    STATE["login"] = True
                    log("✅ 检测到登录成功（已自动记住，下次无需再登）")
                    STATE["courses_time"] = 0  # 触发课程重新检测
                    return
        except Exception:
            pass

    asyncio.ensure_future(poller())
    return web.json_response({"ok": True, "msg": "已打开登录页，请在弹出的浏览器中登录"})


async def api_start(request):
    if STATE["grind_task"] and not STATE["grind_task"].done():
        return web.json_response({"error": "已有任务在运行，请先停止"}, status=409)
    body = await request.json()
    course_id, class_id = str(body.get("courseId")), str(body.get("classId"))
    course = next((c for c in STATE["courses"]
                   if c["courseId"] == course_id and c["classId"] == class_id), None)
    if not course:
        return web.json_response({"error": "课程不存在，请先刷新课程列表"}, status=400)
    concurrency = max(1, min(3, int(body.get("concurrency", 1))))
    dry_run = bool(body.get("dry_run", False))
    STATE["grind_course"] = course["courseName"]
    STATE["grind_task"] = asyncio.ensure_future(
        _grind_wrapper(course, class_id, concurrency, dry_run))
    return web.json_response({"ok": True})


async def api_stop(request):
    t = STATE["grind_task"]
    if t and not t.done():
        t.cancel()
        return web.json_response({"ok": True, "msg": "停止信号已发送（等待当前动作收尾）"})
    return web.json_response({"ok": True, "msg": "当前没有运行中的任务"})


async def api_logs(request):
    return web.json_response({"lines": list(LOG_BUFFER)[-200:]})


async def api_config_get(request):
    cfg = load_config()
    return web.json_response({**cfg,
                              "deepseek_api_key": mask(cfg.get("deepseek_api_key", "")),
                              "gemini_api_key": mask(cfg.get("gemini_api_key", ""))})


async def api_config_post(request):
    body = await request.json()
    cfg = load_config()
    for k, v in body.items():
        if k in cfg and v not in (None, ""):
            cfg[k] = v
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    log("⚙️ 配置已保存")
    return web.json_response({"ok": True})


async def api_ai_test(request):
    body = await request.json() if request.can_read_body else {}
    cfg = load_config()
    if body.get("provider"):
        cfg["provider"] = body["provider"]
    try:
        ai = AI(cfg)
        t0 = time.time()
        r = ai.ask_text("连通性测试：中国的首都是哪里？", "A. 上海 B. 北京")
        return web.json_response({"ok": True, "provider": ai.provider,
                                  "answer": r, "seconds": round(time.time() - t0, 1)})
    except Exception as e:
        return web.json_response({"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"})


async def index(request):
    return web.Response(text=INDEX_PATH.read_text(encoding="utf-8"),
                        content_type="text/html")


async def main():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/status", api_status)
    app.router.add_get("/api/courses", api_courses)
    app.router.add_post("/api/login", api_login)
    app.router.add_post("/api/start", api_start)
    app.router.add_post("/api/stop", api_stop)
    app.router.add_get("/api/logs", api_logs)
    app.router.add_get("/api/config", api_config_get)
    app.router.add_post("/api/config", api_config_post)
    app.router.add_post("/api/ai_test", api_ai_test)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8787)
    await site.start()
    log("🌐 WebGUI 已启动: http://127.0.0.1:8787 （Ctrl+C 退出）")

    # 后台预热浏览器并检测登录态（不阻塞启动）
    async def warmup():
        try:
            await detect_courses(force=False)
        except Exception as e:
            log(f"预热检测失败（可忽略）: {type(e).__name__}")
    asyncio.ensure_future(warmup())

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        if STATE["grind_task"]:
            STATE["grind_task"].cancel()
        await BM.close()
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("WebGUI 已退出")
