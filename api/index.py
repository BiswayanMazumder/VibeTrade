
import yfinance as yf
import os
import pandas as pd
import time
import requests
import bcrypt
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from dotenv import load_dotenv
from jose import jwt
from fastapi import FastAPI, Request, Query, HTTPException, BackgroundTasks
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()
app = FastAPI()

# ✅ CORS (ADDED - no removal of anything)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

# =========================
# 🔐 CONFIG
# =========================
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
GUARDIAN_API_KEY = os.getenv("GUARDIAN_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"

CURRENCY_SYMBOLS = {'INR': '₹', 'USD': '$', 'EUR': '€', 'GBP': '£', 'JPY': '¥'}

# =========================
# 🔐 DB
# =========================
def get_db():
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print("DB ERROR:", e)
        raise HTTPException(status_code=500, detail="Database connection failed")

# =========================
# 📧 EMAIL
# =========================
def send_welcome_email(to_email: str, username: str):
    url = "https://api.api-key-key.brevo.com/v3/smtp/email"
    headers = {
        "accept": "application/json",
        "api-key": BREVO_API_KEY,
        "content-type": "application/json"
    }
    data = {
        "sender": {"name": "Vantedge", "email": SENDER_EMAIL},
        "to": [{"email": to_email, "name": username}],
        "subject": "Welcome to Vantedge 🚀",
        "htmlContent": f"<h1>Welcome {username} 👋</h1><p>Your AI terminal is live</p>"
    }
    try:
        requests.post(url, json=data, headers=headers)
    except:
        pass

# =========================
# 🔐 AUTH HELPERS
# =========================
def hash_password(password: str):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str):
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_token(data: dict):
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(hours=12)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

# =========================
# 📈 NEWS (MERGED: MEMORY + DB CACHE)
# =========================
news_cache = {}
CACHE_DURATION = 3600

def fetch_guardian_news(ticker):
    current_time = time.time()
    clean = ticker.upper().split('.')[0]

    # ✅ 1. Memory Cache (EXISTING)
    if clean in news_cache:
        cached = news_cache[clean]
        if current_time - cached['timestamp'] < CACHE_DURATION:
            return cached['articles']

    # ✅ 2. DB Cache (NEW - from your other file)
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT articles FROM news_cache 
            WHERE ticker = %s AND updated_at > NOW() - INTERVAL '1 hour'
        """, (clean,))
        row = cur.fetchone()

        if row:
            news_cache[clean] = {
                "timestamp": current_time,
                "articles": row[0]
            }
            return row[0]

        # ✅ 3. API CALL (ORIGINAL)
        url = "https://content.guardianapis.com/search"
        params = {
            "q": clean,
            "section": "business",
            "order-by": "newest",
            "api-key": GUARDIAN_API_KEY
        }

        res = requests.get(url, params=params, timeout=5).json()
        results = res.get('response', {}).get('results', [])

        articles = [
            {"title": i['webTitle'], "link": i['webUrl']}
            for i in results[:5]
        ]

        if not articles:
            articles = [{"title": f"No news for {ticker}", "link": "#"}]

        # ✅ SAVE TO DB
        cur.execute("""
            INSERT INTO news_cache (ticker, articles, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (ticker)
            DO UPDATE SET articles = EXCLUDED.articles, updated_at = NOW()
        """, (clean, psycopg2.extras.Json(articles)))
        conn.commit()

        # ✅ Update memory cache
        news_cache[clean] = {
            "timestamp": current_time,
            "articles": articles
        }

        return articles

    except Exception as e:
        print("NEWS ERROR:", e)
        return [{"title": "News error", "link": "#"}]
    finally:
        cur.close()
        conn.close()

# =========================
# 🔐 AUTH ROUTES
# =========================
@app.post("/auth/register")
async def register(username: str, email: str, password: str, background_tasks: BackgroundTasks):
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM users WHERE email=%s", (email,))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="User exists")

        hashed = hash_password(password)
        cur.execute(
            "INSERT INTO users (username, email, password) VALUES (%s, %s, %s)",
            (username, email, hashed)
        )
        conn.commit()

        background_tasks.add_task(send_welcome_email, email, username)

        return {"message": "User registered successfully"}
    finally:
        cur.close(); conn.close()

@app.post("/auth/login")
async def login(email: str, password: str):
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT id, password FROM users WHERE email=%s", (email,))
        user = cur.fetchone()

        if not user or not verify_password(password, user[1]):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        return {"access_token": create_token({"user_id": user[0]})}
    finally:
        cur.close(); conn.close()

# =========================
# 📊 ROUTES
# =========================
@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/context")
async def context_api():
    try:
        s = yf.Search("Most Active", max_results=8)
        trending = [{"s": q['symbol'], "n": q.get('shortname', q['symbol'])} for q in s.quotes]
        return JSONResponse(content={"trending": trending})
    except:
        return JSONResponse(content={"trending": [{"s": "AAPL", "n": "Apple"}]})

@app.get("/api/search/{query}")
async def search(query: str):
    try:
        s = yf.Search(query, max_results=8)
        return [{"symbol": q['symbol'], "name": q.get('shortname', 'Asset')} for q in s.quotes]
    except:
        return []

@app.get("/api/stream/{ticker}")
async def stream_data(ticker: str, period: str = Query("1d")):
    try:
        stock = yf.Ticker(ticker)
        interval = "1m" if period in ["1d", "5d"] else "1d"
        hist = stock.history(period=period, interval=interval)

        info = stock.info
        curr = info.get('currency', 'USD')
        sym = CURRENCY_SYMBOLS.get(curr, curr + " ")

        current_p = hist['Close'].iloc[-1]
        open_p = info.get('regularMarketOpen') or hist['Open'].iloc[0]
        change = ((current_p - open_p) / open_p) * 100

        m_state = info.get("marketState", "").upper()
        is_live = m_state in ["REGULAR", "PRE", "POST"]
        status = "LIVE" if m_state == "REGULAR" else ("EXTENDED" if is_live else "CLOSED")

        def fmt(n):
            if not n: return "N/A"
            for u in ['', 'K', 'M', 'B', 'T']:
                if abs(n) < 1000: return f"{n:3.1f}{u}"
                n /= 1000
            return f"{n:.1f}T"

        return {
            "symbol": ticker.upper(),
            "price": f"{current_p:,.2f}",
            "currency_text": curr,
            "change": f"{change:+.2f}%",
            "news": fetch_guardian_news(ticker),
            "target": f"{sym}{info.get('targetMeanPrice', current_p*1.15):,.2f}",
            "health": 70,
            "hype": 60,
            "fundamentals": {
                "open": f"{sym}{open_p:,.2f}",
                "mkt_cap": fmt(info.get('marketCap')),
                "pe_ratio": f"{info.get('trailingPE', 0):.2f}" if info.get('trailingPE') else "N/A",
                "dividend": f"{info.get('dividendYield', 0)*100:.2f}%" if info.get('dividendYield') else "0.00%",
                "high_52w": f"{sym}{info.get('fiftyTwoWeekHigh', 0):,.2f}",
                "low_52w": f"{sym}{info.get('fiftyTwoWeekLow', 0):,.2f}"
            },
            "chart_json": {
                "x": hist.index.strftime('%Y-%m-%d %H:%M:%S').tolist(),
                "y": hist['Close'].tolist(),
                "curr": curr,
                "status": status,
                "is_live": is_live
            }
        }

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)