import yfinance as yf
import os
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

# --- CONFIG ---
CURRENCY_SYMBOLS = {'INR': '₹', 'USD': '$', 'EUR': '€', 'GBP': '£', 'JPY': '¥'}
BREVO_API_KEY = os.getenv("BREVO_API_KEY")
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
GUARDIAN_API_KEY = os.getenv("GUARDIAN_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"

def get_db():
    try:
        return psycopg2.connect(DATABASE_URL)
    except:
        raise HTTPException(status_code=500, detail="Database Connection Failed")

# =========================
# 📈 PERSISTENT NEWS CACHE (NEON DB)
# =========================
def fetch_guardian_news(ticker):
    clean_ticker = ticker.upper().split('.')[0]
    conn = get_db()
    cur = conn.cursor()

    try:
        # Check if Neon has news less than 1 hour old
        cur.execute("""
            SELECT articles FROM news_cache 
            WHERE ticker = %s AND updated_at > NOW() - INTERVAL '1 hour'
        """, (clean_ticker,))
        
        row = cur.fetchone()
        if row:
            print(f"⚡ [CONSOLE] SERVING FROM NEON DB: {clean_ticker}")
            return row[0]

        # If not found or expired, call Guardian API
        print(f"🌐 [CONSOLE] CALLING GUARDIAN API: {clean_ticker}")
        url = "https://content.guardianapis.com/search"
        params = {
            "q": clean_ticker,
            "section": "business",
            "order-by": "newest",
            "api-key": GUARDIAN_API_KEY
        }

        res = requests.get(url, params=params, timeout=5).json()
        results = res.get('response', {}).get('results', [])
        articles = [{"title": i['webTitle'], "link": i['webUrl']} for i in results[:5]]

        if not articles:
            articles = [{"title": f"No recent news for {ticker}", "link": "#"}]

        # Upsert into DB
        cur.execute("""
            INSERT INTO news_cache (ticker, articles, updated_at) 
            VALUES (%s, %s, NOW())
            ON CONFLICT (ticker) DO UPDATE SET articles = EXCLUDED.articles, updated_at = NOW()
        """, (clean_ticker, psycopg2.extras.Json(articles)))
        conn.commit()
        
        return articles

    except Exception as e:
        print(f"❌ [CONSOLE] CACHE ERROR: {e}")
        return [{"title": "News sync error", "link": "#"}]
    finally:
        cur.close()
        conn.close()

# =========================
# 🔐 AUTH HELPERS
# =========================
def hash_password(p): return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()
def verify_password(p, h): return bcrypt.checkpw(p.encode(), h.encode())
def create_token(d):
    payload = d.copy()
    payload["exp"] = datetime.utcnow() + timedelta(hours=12)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

# =========================
# 📊 ROUTES
# =========================
@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={"request": request})

@app.get("/api/context")
async def context_api():
    try:
        s = yf.Search("Most Active", max_results=8)
        trending = [{"symbol": q['symbol'], "name": q.get('shortname', q['symbol'])} for q in s.quotes]
        return {"trending": trending}
    except: return {"trending": []}

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
        
        info = stock.info
        curr = info.get('currency', 'USD')
        sym = CURRENCY_SYMBOLS.get(curr, curr + " ")

        current_p = hist['Close'].iloc[-1]
        open_p = info.get('regularMarketOpen') or hist['Open'].iloc[0]
        change = ((current_p - open_p) / open_p) * 100

        m_state = info.get("marketState", "").upper()
        is_live = m_state in ["REGULAR", "PRE", "POST"]
        status = "LIVE" if m_state == "REGULAR" else ("EXTENDED" if is_live else "CLOSED")

        # Fixed Target Price Fallback
        raw_target = info.get('targetMeanPrice')
        target_val = f"{sym}{raw_target:,.2f}" if raw_target else "N/A"

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
            "target": target_val,
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