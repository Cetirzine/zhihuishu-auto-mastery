"""AI 答题客户端：支持 DeepSeek / Gemini 双供应商，文本题与图片题（视觉模型）。

用法：
    from ai_client import AI
    ai = AI(config)
    answer = ai.ask_text("题干", "A. xx
B. yy")
    answer = ai.ask_image(png_bytes, "题干提示")
在 config.json 里用 "provider": "deepseek" | "gemini" 切换。
"""
import base64
import json
import re
import urllib.request
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"

SYSTEM_PROMPT = (
    "你是一个考试答题助手。用户给你一道题（可能有选项）。"
    "只输出最终答案本身，不要解释、不要重复题目。"
    "选择题输出选项的完整内容文字（与题目给出的选项文字一致，可多选时用逗号分隔）；"
    "判断题输出'正确'或'错误'（若选项是'对/错'则输出'对'或'错'）；"
    "填空题直接输出答案文本，若有多个空，按空的出现顺序用分号（；）分隔每个空的答案。"
)


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _clean(text: str) -> str:
    """去掉模型可能带上的多余包装（markdown 记号、'答案：'前缀、'B.'选项字母前缀等）。"""
    text = re.sub(r"^```\w*\n?|```$", "", text.strip())
    text = re.sub(r"^(答案[:：]\s*)", "", text)
    return re.sub(r"^[A-Ha-h][.、．:：]\s*", "", text).strip()


def _post(url: str, payload: dict, headers: dict, proxy: str | None = None,
          timeout: float = 45, retries: int = 1) -> dict:
    """POST JSON。proxy=None 时强制直连（DeepSeek），否则走指定代理（Gemini）。带重试。"""
    import time as _time

    body = json.dumps(payload).encode("utf-8")
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))  # 绕过系统代理
    opener = urllib.request.build_opener(*handlers)

    last_err = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with opener.open(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:  # 超时/5xx/网络错误统一重试
            last_err = e
            if attempt < retries:
                _time.sleep(2 * (attempt + 1))
                continue
            raise
    raise last_err


def _key_valid(key: str | None) -> bool:
    return bool(key) and "在这里" not in key


class AI:
    def __init__(self, config: dict):
        self.config = config
        self.provider = config.get("provider", "deepseek")
        self.timeout = float(config.get("ai_timeout", 45))

    def _fallback_provider(self) -> str | None:
        """主供应商失效时，另一家 key 有效则切换。"""
        other = "deepseek" if self.provider == "gemini" else "gemini"
        key = self.config.get(f"{other}_api_key", "")
        return other if _key_valid(key) else None

    def _chat_parts(self, parts) -> str:
        """多模态 parts 级调用（当前供应商）。"""
        if self.provider == "gemini":
            # Gemini 的 parts 结构：文字 + 多张 inline 图
            gparts = []
            for p in parts:
                if p["type"] == "text":
                    gparts.append({"text": p["text"]})
                else:
                    url = p["image_url"]["url"]
                    b64 = url.split(",", 1)[1] if url.startswith("data:") else ""
                    gparts.append({"inline_data": {"mime_type": "image/png", "data": b64}})
            prompt = next((p["text"] for p in parts if p["type"] == "text"), "")
            return self._gemini(prompt, b"") if len(gparts) == 1 else self._gemini_multi(prompt, gparts)
        return self._deepseek(parts)

    def _gemini_multi(self, prompt: str, gparts) -> str:
        cfg = self.config
        data = _post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{cfg['gemini_model']}:generateContent",
            {"contents": [{"role": "user", "parts": gparts}],
             "generationConfig": {"temperature": 0, "maxOutputTokens": 1024}},
            {"Content-Type": "application/json", "x-goog-api-key": cfg["gemini_api_key"]},
            proxy=cfg.get("gemini_proxy"), timeout=self.timeout, retries=2,
        )
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]).strip()

    def transcribe_options(self, urls: list[str]) -> str:
        """逐张下载选项图片并转录内容（模型只见选项无法答题）。
        返回形如 'A=内容；B=内容…' 的文本。"""
        import urllib.request as _u
        opener = _u.build_opener(_u.ProxyHandler({}))  # 直连（国内CDN）
        parts = [{"type": "text", "text":
                  "这些图片依次是选择题的选项 A、B、C…（按顺序）。"
                  "请按『A=内容；B=内容；C=内容…』的格式逐项转录每张图片里的内容，"
                  "不要解题，不要输出任何其他文字。"}]
        for u in urls:
            try:
                b = opener.open(_u.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=15).read()
                parts.append({"type": "image_url", "image_url": {
                    "url": "data:image/png;base64," + base64.b64encode(b).decode("ascii")}})
            except Exception:
                continue
        if len(parts) == 1:
            raise RuntimeError("选项图片全部下载失败")
        return _clean(self._chat_parts(parts))

    # ---------- DeepSeek ----------
    def _deepseek(self, content, max_tokens: int = 4096) -> str:
        cfg = self.config
        data = _post(
            cfg["deepseek_base_url"].rstrip("/") + "/chat/completions",
            {
                "model": cfg["deepseek_model"],
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": content},
                ],
                "temperature": 0,
                "max_tokens": max_tokens,
            },
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {cfg['deepseek_api_key']}",
            },
            timeout=self.timeout,
        )
        msg = data["choices"][0]["message"]
        text = (msg.get("content") or "").strip()
        if not text and max_tokens < 8192:
            # 思考型模型可能把 max_tokens 全耗在推理上：提额重试一次
            return self._deepseek(content, max_tokens=16384)
        if not text:
            raise RuntimeError("DeepSeek 返回空答案（思考耗尽token）")
        return text

    # ---------- Gemini ----------
    def _gemini(self, prompt: str, image_png: bytes | None) -> str:
        cfg = self.config
        parts = [{"text": SYSTEM_PROMPT + "\n\n" + prompt}]
        if image_png:
            parts.append(
                {
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": base64.b64encode(image_png).decode("ascii"),
                    }
                }
            )
        data = _post(
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{cfg['gemini_model']}:generateContent",
            {
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"temperature": 0, "maxOutputTokens": 512},
            },
            {
                "Content-Type": "application/json",
                "x-goog-api-key": cfg["gemini_api_key"],
            },
            proxy=cfg.get("gemini_proxy"),
            timeout=self.timeout,
            retries=2,  # 代理节点间歇抽风，快速失败多次重试
        )
        return "".join(
            p.get("text", "") for p in data["candidates"][0]["content"]["parts"]
        ).strip()

    # ---------- 对外接口（含供应商故障切换） ----------
    def _ask(self, prompt: str, image_png: bytes | None) -> str:
        def via(p):
            if p == "gemini":
                return self._gemini(prompt, image_png)
            if image_png:
                return self._deepseek([
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {
                        "url": "data:image/png;base64,"
                               + base64.b64encode(image_png).decode("ascii"),
                        "detail": "high",
                    }},
                ])
            return self._deepseek(prompt)

        try:
            return via(self.provider)
        except Exception as e:
            fb = self._fallback_provider()
            if not fb:
                raise
            try:
                from shuati import log
                log(f"    [AI] {self.provider} 失败({type(e).__name__})，切换到 {fb}")
            except Exception:
                print(f"[AI] {self.provider} 失败({type(e).__name__})，切换到 {fb}", flush=True)
            self.provider = fb
            return via(fb)

    def ask_text(self, question: str, options: str = "") -> str:
        prompt = f"题目：\n{question}"
        if options:
            prompt += f"\n\n选项：\n{options}"
        return _clean(self._ask(prompt, None))

    def ask_image(self, image_png: bytes, question_hint: str = "") -> str:
        text_part = "请看这张题目截图，读出题目并直接给出答案。"
        if question_hint:
            text_part += f"\n补充说明：{question_hint}"
        return _clean(self._ask(text_part, image_png))
