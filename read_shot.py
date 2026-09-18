"""用 DeepSeek 视觉读提交超时截图，弄清页面当时的实际状态。"""
import json
from pathlib import Path

from ai_client import _post
from question_bank import strip_html

cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))
import base64

png = Path("probe_out/submit_timeout.png").read_bytes()
b64 = base64.b64encode(png).decode("ascii")
data = _post(
    "https://api.deepseek.com/chat/completions",
    {
        "model": cfg["deepseek_model"],
        "messages": [
            {"role": "user", "content": [
                {"type": "text", "text": "这是一张智慧树考试页面的截图。请详细描述：1)页面中部显示的题目内容和作答状态（选项是否被选中、填空框里有没有字）2)页面上有没有弹窗、提示条（toast）？文字是什么 3)顶部和底部的按钮有哪些、文字是什么 4)整体看页面卡在什么状态。中文回答。"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}},
            ]}
        ],
        "temperature": 0,
        "max_tokens": 4096,
    },
    {"Content-Type": "application/json", "Authorization": f"Bearer {cfg['deepseek_api_key']}"},
    timeout=90,
)
print(data["choices"][0]["message"]["content"])
