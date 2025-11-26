# app.py
import os
import tempfile
import json
import time
import nltk
from flask import Flask, request, jsonify
import requests
from docx import Document
from duckduckgo_search import ddg
from sumy.parsers.plaintext import PlaintextParser
from sumy.nlp.tokenizers import Tokenizer
from sumy.summarizers.text_rank import TextRankSummarizer
from bs4 import BeautifulSoup
from PyPDF2 import PdfReader

# ================== NLTK setup (ensure 'punkt' is available) ==================
# Download punkt at startup if missing (downloads to /opt/render/nltk_data by default).
NLTK_DATA_DIR = os.environ.get("NLTK_DATA_DIR", "/opt/render/nltk_data")
os.makedirs(NLTK_DATA_DIR, exist_ok=True)
if NLTK_DATA_DIR not in nltk.data.path:
    nltk.data.path.append(NLTK_DATA_DIR)

try:
    nltk.data.find("tokenizers/punkt")
except LookupError:
    # Attempt a quiet download
    nltk.download("punkt", download_dir=NLTK_DATA_DIR, quiet=True)

# ================== Config ==================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
DEFAULT_TEMPLATE_PATH = os.environ.get("DEFAULT_TEMPLATE_PATH", "./Sample Lesson Plan.docx")
PORT = int(os.environ.get("PORT", 5000))

if not TELEGRAM_TOKEN:
    raise RuntimeError("Set TELEGRAM_TOKEN env var")

BASE_TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
app = Flask(__name__)

# In-memory sessions (ephemeral)
SESS = {}  # chat_id -> state dict

# ================== Telegram helpers ==================
def telegram_api(method, params=None, files=None, json_payload=None):
    url = f"{BASE_TELEGRAM_URL}/{method}"
    try:
        if files:
            return requests.post(url, params=params, files=files, timeout=30)
        if json_payload:
            return requests.post(url, json=json_payload, timeout=30)
        return requests.post(url, data=params, timeout=30)
    except Exception as e:
        print("telegram_api error:", e)
        raise

def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        telegram_api("sendMessage", params=payload)
    except Exception as e:
        print("send_message error:", e)

def download_file(file_id, dest_path):
    r = telegram_api("getFile", params={"file_id": file_id})
    r.raise_for_status()
    data = r.json()
    file_path = data["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    r2 = requests.get(file_url, timeout=60)
    r2.raise_for_status()
    with open(dest_path, "wb") as f:
        f.write(r2.content)
    return dest_path

# ================== Text extraction helpers ==================
def extract_text_from_pdf(path):
    text_parts = []
    try:
        reader = PdfReader(path)
        for page in reader.pages:
            try:
                t = page.extract_text()
                if t:
                    text_parts.append(t)
            except Exception:
                continue
    except Exception as e:
        print("PDF read error:", e)
        raise
    return "\n".join(text_parts)

def extract_text_from_url(url, max_chars=20000):
    """
    Lightweight extractor: fetch page and return concatenated <article> or <p> text.
    """
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent":"Mozilla/5.0"})
        r.raise_for_status()
    except Exception as e:
        print("fetch error for", url, ":", e)
        return ""
    html = r.text
    soup = BeautifulSoup(html, "html.parser")

    article = soup.find("article")
    if article:
        text = article.get_text(separator="\n")
    else:
        ps = soup.find_all("p")
        filtered = []
        for p in ps:
            t = p.get_text(strip=True)
            if not t:
                continue
            if len(t) < 30:
                continue
            filtered.append(t)
        text = "\n\n".join(filtered)

    if not text or len(text.strip()) < 100:
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        desc_tag = soup.find("meta", attrs={"name":"description"}) or soup.find("meta", attrs={"property":"og:description"})
        meta = desc_tag.get("content").strip() if desc_tag and desc_tag.get("content") else ""
        text = (title + "\n" + meta).strip()
    return (text or "")[:max_chars]

# ================== Summarization & heuristics ==================
def summarize_text(text, sentences_count=6):
    if not text:
        return ""
    try:
        parser = PlaintextParser.from_string(text, Tokenizer("english"))
        summarizer = TextRankSummarizer()
        summary_sentences = summarizer(parser.document, sentences_count)
        return "\n".join(str(s) for s in summary_sentences)
    except Exception as e:
        # fallback: naive substring
        print("summarize_text error:", e)
        return "\n".join((text.strip().splitlines() or [text])[:sentences_count])

def extract_objectives_from_text(text, max_points=5):
    lowered = (text or "").lower()
    candidates = []
    for sent in (text or "").split("."):
        s = sent.strip()
        if not s:
            continue
        sl = s.lower()
        if any(k in sl for k in ("able to", "will", "understand", "learn", "identify", "describe")):
            candidates.append(s.strip())
    if candidates:
        return "\n".join(f"• {c}" for c in candidates[:max_points])
    summ = summarize_text(text, sentences_count=max_points)
    if summ:
        return "\n".join(f"• {s.strip()}" for s in summ.split("\n") if s.strip())
    return "• Objective 1\n• Objective 2"

def generate_activities(text, max_items=4):
    return (
        "1. Read the summary and discuss key terms.\n"
        "2. Small-group activity: identify examples from the text.\n"
        "3. Hands-on/demo (if applicable): follow the experiment steps.\n"
        "4. Exit ticket: one short question to assess learning."
    )

def generate_assessment_questions(text, max_q=4):
    summary = summarize_text(text, sentences_count=4)
    qs = []
    for i, s in enumerate(summary.split("\n")[:m]()
