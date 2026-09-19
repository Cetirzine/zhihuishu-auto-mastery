from ai_client import AI, load_config

ai = AI(load_config())
urls = [
    "https://hike-export.oss-cn-hangzhou.aliyuncs.com/paper/dev/20260523/faefb6d7-cf26-4a2b-b556-1db06bdbda0a.png",
    "https://hike-export.oss-cn-hangzhou.aliyuncs.com/paper/dev/20260915/ae6297c1-c93d-4f16-b050-ee120d2a3cf1.png",
    "https://hike-export.oss-cn-hangzhou.aliyuncs.com/paper/dev/20260523/0cd9e4cd-0925-4aee-8c62-c5f1b5414440.png",
    "https://hike-export.oss-cn-hangzhou.aliyuncs.com/paper/dev/20260523/13a33e76-36d0-4c2c-8cfe-b365f402776a.png",
]
print("转录结果:", ai.transcribe_options(urls), flush=True)
