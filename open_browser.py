"""打开一个受控 Edge 浏览器（会话持久化），供用户登录并演示操作。(async 版，修复死锁)

全程记录：
1. 每个标签页的 URL 变化（含 SPA 路由切换的轮询捕获） -> session_log.txt
2. 疑似题目/作业相关的接口响应体 -> session_data/*.json

用法：python open_browser.py   （关掉浏览器窗口或 Ctrl+C 结束）
"""
import asyncio
import hashlib
import json
import re
import time
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).parent
PROFILE = ROOT / "browser_profile"
LOG = ROOT / "session_log.txt"
DATA = ROOT / "session_data"
DATA.mkdir(exist_ok=True)

# 只记录 XHR/fetch/document 中可能和课程/题目有关的请求
INTEREST = re.compile(
    r"(exam|homework|question|quiz|paper|answer|practice|exercise|course|study|chapter|student|stu/)", re.I
)
# 这些接口的响应体整个存下来（OCS 就是靠 doHomework 响应拿整张试卷的）
SAVE_BODY = re.compile(r"(doHomework|doExam|examBase|question|paper|quiz|answer|analysis|result)", re.I)


def log(msg: str):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


async def hook_page(page):
    async def on_response(resp):
        try:
            req = resp.request
            rtype = req.resource_type
            if rtype not in ("xhr", "fetch", "document"):
                return
            url = resp.url
            if not INTEREST.search(url):
                return
            log(f"NET  {resp.status}  {rtype}  {url[:260]}")
            if SAVE_BODY.search(url) and resp.status == 200:
                body = await resp.text()
                s = body.lstrip()
                if 0 < len(body) < 3_000_000 and s[:1] in "{[":
                    name = f"{int(time.time() * 1000)}_{hashlib.md5(url.encode()).hexdigest()[:8]}.json"
                    (DATA / name).write_text(body, encoding="utf-8")
                    log(f"BODY {name}  <-  {url[:180]}")
        except Exception as e:
            log(f"ERR  resp handler: {type(e).__name__} {str(e)[:120]}")

    page.on("response", on_response)


async def main():
    LOG.write_text("", encoding="utf-8")  # 每次启动清空旧记录
    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            str(PROFILE),
            channel="msedge",
            headless=False,
            no_viewport=True,
            args=["--start-maximized"],
        )
        for pg in ctx.pages:
            await hook_page(pg)
        ctx.on("page", hook_page)

        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        if "zhihuishu.com" not in page.url:
            await page.goto("https://www.zhihuishu.com/", wait_until="domcontentloaded")

        log("=== 浏览器已打开 === 请进入刷题的课程页面并完整操作一遍（答题→提交→看错题分析）")
        log("=== URL 与题目相关接口记录到 session_log.txt，响应体存 session_data/ ===")

        last_urls = {}
        try:
            while True:
                await asyncio.sleep(1)
                for i, pg in enumerate(ctx.pages):
                    try:
                        u = pg.url
                        if u.startswith("devtools"):
                            continue
                        if last_urls.get(i) != u:
                            last_urls[i] = u
                            log(f"URL  [{u[:300]}]")
                    except Exception:
                        pass
        except KeyboardInterrupt:
            log("=== 会话结束（Ctrl+C）===")
        finally:
            await ctx.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
