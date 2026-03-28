import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import requests
from fastapi import FastAPI, Request, Query, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse

# 🔐 AUTH IMPORTS
import psycopg2
import bcrypt
from jose import jwt
from datetime import datetime, timedelta

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# =========================
# 🔐 NEON DB CONFIG
# =========================
DATABASE_URL = "postgresql://neondb_owner:npg_EzgCr7D5jiqf@ep-cold-flower-amfeo0n6-pooler.c-5.us-east-1.aws.neon.tech/neondb?sslmode=require"

SECRET_KEY = "dev_secret_key"
ALGORITHM = "HS256"

def get_db():
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print("DB ERROR:", e)
        raise HTTPException(status_code=500, detail="Database connection failed")

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
# 🔐 AUTH ROUTES
# =========================

@app.post("/auth/register")
async def register(username: str, email: str, password: str):
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("SELECT * FROM users WHERE email=%s", (email,))
        if cur.fetchone():
            raise HTTPException(status_code=400, detail="User already exists")

        hashed = hash_password(password)

        cur.execute(
            "INSERT INTO users (username, email, password) VALUES (%s, %s, %s)",
            (username, email, hashed)
        )
        conn.commit()

        return {"message": "User registered successfully"}

    finally:
        cur.close()
        conn.close()


@app.post("/auth/login")
async def login(email: str, password: str):
    conn = get_db()
    cur = conn.cursor()

    try:
        cur.execute("SELECT id, password FROM users WHERE email=%s", (email,))
        user = cur.fetchone()

        if not user:
            raise HTTPException(status_code=404, detail="User does not exist")

        user_id, hashed_password = user

        if not verify_password(password, hashed_password):
            raise HTTPException(status_code=401, detail="Invalid password")

        token = create_token({"user_id": user_id})
        return {"access_token": token}

    finally:
        cur.close()
        conn.close()

# =========================
# STOCK SYSTEM
# =========================

CURRENCY_SYMBOLS = {'INR': '₹', 'USD': '$', 'EUR': '€', 'GBP': '£', 'JPY': '¥'}

def get_realtime_trending():
    try:
        s = yf.Search("Most Active", max_results=12)
        trending = []

        for quote in s.quotes:
            symbol = quote.get('symbol')
            if symbol and ('.' not in symbol or symbol.endswith(".NS") or symbol.endswith(".BO")):
                trending.append({
                    "s": symbol,
                    "n": quote.get('shortname') or quote.get('longname') or symbol
                })

        if not trending:
            fallback = ["RELIANCE.NS", "NVDA", "TCS.NS", "AAPL", "TSLA"]
            for sym in fallback:
                trending.append({"s": sym, "n": sym.replace(".NS", "")})

        return trending[:8]

    except Exception as e:
        print(f"Trending Error: {e}")
        return [{"s": "NVDA", "n": "NVIDIA"}]


def fetch_robust_news(ticker):
    news = []
    ticker_clean = ticker.upper().split('.')[0]

    try:
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={ticker}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5).json()

        for item in res.get('news', [])[:4]:
            related = [t.upper() for t in item.get('relatedTickers', [])]
            if ticker_clean in related or ticker_clean in item.get('title', '').upper():
                news.append({"title": item['title'], "link": item['link']})

    except:
        pass

    return news if news else [{"title": f"No news for {ticker.upper()}", "link": "#"}]


@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/context")
async def context_api():
    return JSONResponse(content={"trending": get_realtime_trending()})


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

        if hist.empty:
            return JSONResponse({"error": "No Data"}, status_code=404)

        info = stock.info
        curr = info.get('currency', 'USD')
        sym = CURRENCY_SYMBOLS.get(curr, curr + " ")

        current_p = hist['Close'].iloc[-1]
        open_p = info.get('regularMarketOpen') or hist['Open'].iloc[0]
        change = ((current_p - open_p) / open_p) * 100
        color = '#00ffbb' if change >= 0 else '#ff3366'

        def format_big_num(num):
            if not num or num == "N/A": return "N/A"
            for unit in ['', 'K', 'M', 'B', 'T']:
                if abs(num) < 1000.0:
                    return f"{num:3.1f}{unit}"
                num /= 1000.0
            return f"{num:.1f}T"

        # =========================
        # ✅ MARKET STATUS FIX
        # =========================
        market_state = info.get("marketState", "").upper()

        if market_state == "REGULAR":
            status = "LIVE"
        elif market_state in ["PRE", "POST"]:
            status = "EXTENDED"
        else:
            status = "CLOSED"

        return {
            "symbol": ticker.upper(),
            "price": f"{current_p:,.2f}",
            "currency_text": curr,
            "change": f"{change:+.2f}%",
            "news": fetch_robust_news(ticker),
            "target": f"{sym}{info.get('targetMeanPrice', current_p*1.15):,.2f}",
            "health": 70,
            "hype": 60,
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
                "color": color,
                "curr": curr,
                "status": status  # ✅ FIXED HERE
            }
        }

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)