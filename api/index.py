import yfinance as yf
import os
import pandas as pd
import time
from dotenv import load_dotenv
import plotly.graph_objects as go
import plotly.io as pio
import requests
from fastapi import FastAPI, Request, Query, HTTPException, BackgroundTasks
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse

# 🔐 AUTH IMPORTS
import psycopg2
import bcrypt
from jose import jwt
from datetime import datetime, timedelta

load_dotenv()
app = FastAPI()
templates = Jinja2Templates(directory="templates")

# --- NEWS CACHE CONFIG ---
news_cache = {}
CACHE_DURATION = 3600  # 1 Hour Cache

# =========================
# 🔐 CONFIG & HELPERS
# =========================
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
GUARDIAN_API_KEY = os.getenv("GUARDIAN_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"

def get_db():
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print("DB ERROR:", e)
        raise HTTPException(status_code=500, detail="Database connection failed")

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
        "htmlContent": f"<h1>Welcome {username} 👋</h1><p>Your AI terminal is live at vantedgee.me</p>"
    }
    try:
        requests.post(url, json=data, headers=headers)
    except: pass

def hash_password(password: str):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str):
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_token(data: dict):
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(hours=12)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

# =========================
# 📈 SMART NEWS FETCH (CACHED)
# =========================
def fetch_guardian_news(ticker):
    current_time = time.time()
    clean_query = ticker.upper().split('.')[0] # e.g. TCS.NS -> TCS

    # 1. Check Cache
    if clean_query in news_cache:
        cached = news_cache[clean_query]
        if current_time - cached['timestamp'] < CACHE_DURATION:
            return cached['articles']

    # 2. Call API if no cache/expired
    url = "https://content.guardianapis.com/search"
    params = {
        "q": clean_query,
        "section": "business",
        "order-by": "newest",
        "api-key": GUARDIAN_API_KEY
    }

    try:
        res = requests.get(url, params=params, timeout=5).json()
        results = res.get('response', {}).get('results', [])
        
        news_articles = []
        for item in results[:4]:
            news_articles.append({
                "title": item['webTitle'],
                "link": item['webUrl']
            })

        if news_articles:
            news_cache[clean_query] = {
                "timestamp": current_time,
                "articles": news_articles
            }
        return news_articles if news_articles else [{"title": f"No news for {ticker}", "link": "#"}]
    except:
        return news_cache.get(clean_query, {}).get('articles', [{"title": "News sync error", "link": "#"}])

# =========================
# 🔐 AUTH ROUTES
# =========================
@app.post("/auth/register")
async def register(username: str, email: str, password: str, background_tasks: BackgroundTasks):
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM users WHERE email=%s", (email,))
        if cur.fetchone(): raise HTTPException(status_code=400, detail="User exists")
        hashed = hash_password(password)
        cur.execute("INSERT INTO users (username, email, password) VALUES (%s, %s, %s)", (username, email, hashed))
        conn.commit()
        background_tasks.add_task(send_welcome_email, email, username)
        return {"message": "User registered successfully"}
    finally: cur.close(); conn.close()

@app.post("/auth/login")
async def login(email: str, password: str):
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT id, password FROM users WHERE email=%s", (email,))
        user = cur.fetchone()
        if not user or not verify_password(password, user[1]):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return {"access_token": create_token({"user_id": user[0]})}
    finally: cur.close(); conn.close()

# =========================
# 📊 STOCK ROUTES
# =========================
CURRENCY_SYMBOLS = {'INR': '₹', 'USD': '$', 'EUR': '€', 'GBP': '£', 'JPY': '¥'}

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
    except: return []

@app.get("/api/stream/{ticker}")
async def stream_data(ticker: str, period: str = Query("1d")):
    try:
        stock = yf.Ticker(ticker)
        interval = "1m" if period in ["1d", "5d"] else "1d"
        hist = stock.history(period=period, interval=interval)
        if hist.empty: return JSONResponse({"error": "No Data"}, status_code=404)

        info = stock.info
        curr = info.get('currency', 'USD')
        sym = CURRENCY_SYMBOLS.get(curr, curr + " ")

        current_p = hist['Close'].iloc[-1]
        open_p = info.get('regularMarketOpen') or hist['Open'].iloc[0]
        change = ((current_p - open_p) / open_p) * 100

        # Market Status Logic
        m_state = info.get("marketState", "").upper()
        if m_state == "REGULAR": status = "LIVE"; is_live = True
        elif m_state in ["PRE", "POST"]: status = "EXTENDED"; is_live = True
        else: status = "CLOSED"; is_live = False

        def format_big_num(num):
            if not num or num == "N/A": return "N/A"
            for unit in ['', 'K', 'M', 'B', 'T']:
                if abs(num) < 1000.0: return f"{num:3.1f}{unit}"
                num /= 1000.0
            return f"{num:.1f}T"

        return {
            "symbol": ticker.upper(),
            "price": f"{current_p:,.2f}",
            "currency_text": curr,
            "change": f"{change:+.2f}%",
            "news": fetch_guardian_news(ticker), # CACHED NEWS
            "target": f"{sym}{info.get('targetMeanPrice', current_p*1.15):,.2f}",
            "health": 70, "hype": 60,
            "fundamentals": {
                "open": f"{sym}{open_p:,.2f}",
                "mkt_cap": format_big_num(info.get('marketCap')),
                "pe_ratio": f"{info.get('trailingPE', 'N/A'):.2f}" if isinstance(info.get('trailingPE'), (int, float)) else "N/A",
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