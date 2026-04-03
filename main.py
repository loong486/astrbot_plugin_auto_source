import io
import requests
import hashlib
from PIL import Image
from bs4 import BeautifulSoup
from astrbot.api import Plugin, Event, Message

TIMEOUT = 10
CACHE = {}


class AutoSourcePlugin(Plugin):
    def __init__(self):
        super().__init__()

        # 从配置读取
        self.saucenao_key = self.conf.get("saucenao_key", "")
        self.bing_key = self.conf.get("bing_key", "")
        self.similarity_threshold = self.conf.get("similarity_threshold", 80)

        self.enable_saucenao = self.conf.get("enable_saucenao", True)
        self.enable_ascii2d = self.conf.get("enable_ascii2d", True)
        self.enable_bing = self.conf.get("enable_bing", True)

    def on_message(self, event: Event):
        msg = event.message

        if not msg.images:
            return Message("请发送图片，我来帮你查找来源")

        replies = []

        for img in msg.images:
            image_bytes = img.content

            # 缓存
            img_hash = hashlib.md5(image_bytes).hexdigest()
            if img_hash in CACHE:
                replies.append(f"### 🧩 缓存结果\n{CACHE[img_hash]}")
                continue

            # 自动压缩
            compressed = self.compress_image(image_bytes)

            # 自动识别图片类型
            img_type = self.detect_image_type(compressed)

            # 插画优先 SauceNAO / Ascii2D
            # 真实照片优先 Bing
            engines = []
            if img_type == "illustration":
                if self.enable_saucenao: engines.append(self.search_saucenao)
                if self.enable_ascii2d: engines.append(self.search_ascii2d)
                if self.enable_bing: engines.append(self.search_bing)
            else:
                if self.enable_bing: engines.append(self.search_bing)
                if self.enable_saucenao: engines.append(self.search_saucenao)
                if self.enable_ascii2d: engines.append(self.search_ascii2d)

            result = None
            for engine in engines:
                result = engine(compressed)
                if result:
                    break

            if not result:
                result = "未找到来源，可能是冷门图片或未被收录"

            CACHE[img_hash] = result
            replies.append(result)

        return Message("\n\n---\n\n".join(replies))

    # -------------------------
    # 图片自动压缩
    # -------------------------
    def compress_image(self, image_bytes):
        img = Image.open(io.BytesIO(image_bytes))

        max_size = 1024
        w, h = img.size

        if max(w, h) <= max_size:
            return image_bytes

        scale = max_size / max(w, h)
        new_size = (int(w * scale), int(h * scale))

        img = img.resize(new_size, Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
        return buf.getvalue()

    # -------------------------
    # 自动识别插画 / 真实照片
    # -------------------------
    def detect_image_type(self, image_bytes):
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        pixels = img.load()

        w, h = img.size
        sample = []

        for i in range(0, w, max(1, w // 32)):
            for j in range(0, h, max(1, h // 32)):
                sample.append(pixels[i, j])

        diffs = []
        for i in range(1, len(sample)):
            r1, g1, b1 = sample[i - 1]
            r2, g2, b2 = sample[i]
            diffs.append(abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2))

        avg_diff = sum(diffs) / len(diffs)

        return "illustration" if avg_diff < 25 else "photo"

    # -------------------------
    # SauceNAO
    # -------------------------
    def search_saucenao(self, image_bytes):
        try:
            params = {
                "output_type": 2,
                "api_key": self.saucenao_key,
                "numres": 5
            }
            files = {"file": ("image.jpg", image_bytes)}

            resp = requests.post(
                "https://saucenao.com/search.php",
                params=params,
                files=files,
                timeout=TIMEOUT
            ).json()

            if "results" not in resp:
                return None

            reply = "## 🔍 SauceNAO 查询结果\n"

            for r in resp["results"]:
                header = r["header"]
                data = r["data"]

                similarity = float(header.get("similarity", 0))
                if similarity < self.similarity_threshold:
                    continue

                title = data.get("title") or data.get("source") or "未知标题"
                url = data.get("ext_urls", ["无链接"])[0]

                reply += f"""
- **相似度**：{similarity}%
- **标题**：{title}
- **链接**：{url}
"""

            return reply if reply.strip() else None

        except Exception:
            return None

    # -------------------------
    # Ascii2D
    # -------------------------
    def search_ascii2d(self, image_bytes):
        try:
            files = {"file": ("image.jpg", image_bytes)}
            resp = requests.post(
                "https://ascii2d.net/search/file",
                files=files,
                timeout=TIMEOUT
            )

            soup = BeautifulSoup(resp.text, "lxml")
            items = soup.select(".item-box")

            if not items:
                return None

            reply = "## 🟣 Ascii2D 查询结果\n"

            for item in items[:5]:
                link = item.select_one("a")
                if link:
                    reply += f"- **链接**：{link['href']}\n"

            return reply if reply.strip() else None

        except Exception:
            return None

    # -------------------------
    # Bing Visual Search
    # -------------------------
    def search_bing(self, image_bytes):
        try:
            headers = {"Ocp-Apim-Subscription-Key": self.bing_key}
            files = {"image": ("image.jpg", image_bytes)}

            resp = requests.post(
                "https://api.bing.microsoft.com/v7.0/images/visualsearch",
                headers=headers,
                files=files,
                timeout=TIMEOUT
            ).json()

            tags = resp.get("tags", [])
            if not tags:
                return None

            reply = "## 🔵 Bing Visual Search 查询结果\n"

            for action in tags[0].get("actions", []):
                for item in action.get("data", {}).get("value", []):
                    url = item.get("hostPageUrl")
                    if url:
                        reply += f"- **链接**：{url}\n"

            return reply if reply.strip() else None

        except Exception:
            return None
