# ============================================================
# اكتشاف المكتبات تلقائياً — بيقرا كود البوت ويطلع المكتبات المطلوبة
# ============================================================
import ast
import sys

# وحدات بايثون القياسية (مش محتاجة تثبيت)
STDLIB = set(sys.stdlib_module_names)

# أسماء موديولات معروفة إنها ملفات محلية للمشاريع — نعتبرها مش مكتبات
LOCAL_NAMES = {
    "config", "settings", "utils", "helpers", "database", "db", "models",
    "views", "main", "app", "bot", "keys", "texts", "functions", "data",
    "handlers", "plugins", "core", "sql", "tables", "users",
}

# خريطة: اسم الموديول في الكود ← اسم الحزمة على PyPI
# (لأن كتير من المكتبات اسمها على باي بي مختلف عن اسم الاستيراد)
PACKAGE_MAP = {
    # تليجرام
    "telegram": "python-telegram-bot",
    "telethon": "telethon",
    "pyrogram": "pyrogram",
    "tgcrypto": "tgcrypto",
    # صور
    "PIL": "pillow",
    "cv2": "opencv-python-headless",
    "imageio": "imageio",
    # ويب وسحب
    "requests": "requests",
    "httpx": "httpx",
    "aiohttp": "aiohttp",
    "bs4": "beautifulsoup4",
    "lxml": "lxml",
    "selenium": "selenium",
    "scrapy": "scrapy",
    # أطر عمل
    "flask": "flask",
    "django": "django",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
    # قواعد بيانات
    "pymongo": "pymongo",
    "motor": "motor",
    "redis": "redis",
    "sqlalchemy": "sqlalchemy",
    "psycopg2": "psycopg2-binary",
    # أدوات شائعة
    "yaml": "pyyaml",
    "dotenv": "python-dotenv",
    "dateutil": "python-dateutil",
    "sklearn": "scikit-learn",
    "numpy": "numpy",
    "pandas": "pandas",
    "Crypto": "pycryptodome",
    "jwt": "pyjwt",
    "git": "gitpython",
    "discord": "discord.py",
    "attr": "attrs",
    "dns": "dnspython",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "fitz": "pymupdf",
    "github": "pygithub",
    "qrcode": "qrcode",
    "apscheduler": "apscheduler",
    "gTTS": "gTTS",
    "emoji": "emoji",
    "humanize": "humanize",
    "psutil": "psutil",
    "socks": "pysocks",
    "googletrans": "googletrans==4.0.0rc1",
    "youtube_dl": "youtube_dl",
    "yt_dlp": "yt-dlp",
}


def detect_packages(code_text):
    """
    بتقرا كود بايثون وترجّع قائمة أسماء الحزم اللي محتاج تثبيت على PyPI.
    بتتجاهل مكتبات بايثون القياسية والملفات المحلية.
    """
    try:
        tree = ast.parse(code_text)
    except SyntaxError:
        return []

    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                modules.add(node.module.split(".")[0])

    packages = []
    seen = set()
    for mod in sorted(modules):
        if mod in STDLIB or mod in LOCAL_NAMES or mod in seen:
            continue
        seen.add(mod)
        packages.append(PACKAGE_MAP.get(mod, mod))  # مش معروفة؟ جرب الاسم زي ما هو

    return packages
