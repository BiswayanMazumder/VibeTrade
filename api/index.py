import yfinance as yf
import os
import time
import requests
import bcrypt
import psycopg2
from datetime import datetime, timedelta
from dotenv import load_dotenv
from jose import jwt
from fastapi import FastAPI, Request, Query, HTTPException, BackgroundTasks
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()
app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory="templates")

# --- GLOBAL CACHE & CONFIG ---
news_cache = {}  # Format: { "AAPL": {"timestamp": 12345, "articles": [...] } }
CACHE_DURATION = 3600  # 1 Hour in seconds
CURRENCY_SYMBOLS = {'INR': '₹', 'USD': '$', 'EUR': '€', 'GBP': '£', 'JPY': '¥'}

BREVO_API_KEY = os.getenv("BREVO_API_KEY")
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
GUARDIAN_API_KEY = os.getenv("GUARDIAN_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = "HS256"

# =========================
# 🔐 DATABASE & AUTH HELPERS
# =========================
def get_db():
    try:
        return psycopg2.connect(DATABASE_URL)
    except Exception as e:
        print(f"DB Connection Error: {e}")
        raise HTTPException(status_code=500, detail="Database Unavailable")

def hash_password(password: str):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def verify_password(password: str, hashed: str):
    return bcrypt.checkpw(password.encode(), hashed.encode())

def create_token(data: dict):
    payload = data.copy()
    payload["exp"] = datetime.utcnow() + timedelta(hours=12)
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

def send_welcome_email(to_email: str, username: str):
    url = "https://api.brevo.com/v3/smtp/email"
    headers = {"accept": "application/json", "api-key": BREVO_API_KEY, "content-type": "application/json"}
    data = {
        "sender": {"name": "Vantedge", "email": SENDER_EMAIL},
        "to": [{"email": to_email, "name": username}],
        "subject": "Welcome to Vantedge 🚀",
        "htmlContent": f"<html><body style='background:#050505;color:white;padding:20px;'><h1>Welcome {username}!</h1><p>Your AI terminal is ready.</p></body></html>"
    }
    try:
        requests.post(url, json=data, headers=headers)
    except: pass

# =========================
# 📈 NEWS LOGIC (WITH SMART CACHING)
# =========================
def fetch_guardian_news(ticker):
    current_time = time.time()
    # Clean ticker for better search (e.g., RELIANCE.NS -> RELIANCE)
    clean_ticker = ticker.upper().split('.')[0]

    # ✅ STEP 1: Check Cache
    if clean_ticker in news_cache:
        cached_item = news_cache[clean_ticker]
        if current_time - cached_item['timestamp'] < CACHE_DURATION:
            print(f"DEBUG: Serving Cached News for {clean_ticker}")
            return cached_item['articles']

    # ✅ STEP 2: Call API (Only if cache expired or missing)
    print(f"DEBUG: Calling Guardian API for {clean_ticker}")
    url = "https://content.guardianapis.com/search"
    params = {
        "q": clean_ticker,
        "section": "business",
        "order-by": "newest",
        "api-key": GUARDIAN_API_KEY
    }

    try:
        res = requests.get(url, params=params, timeout=5).json()
        results = res.get('response', {}).get('results', [])
        articles = [{"title": i['webTitle'], "link": i['webUrl']} for i in results[:5]]
        
        # ✅ STEP 3: Store in Cache
        if articles:
            news_cache[clean_ticker] = {
                "timestamp": current_time,
                "articles": articles
            }
        return articles if articles else [{"title": f"No recent news for {ticker}", "link": "#"}]
    except:
        # Fallback to expired cache if API fails
        return news_cache.get(clean_ticker, {}).get('articles', [{"title": "News sync error", "link": "#"}])

# =========================
# 🔐 AUTH ROUTES
# =========================
@app.post("/auth/register")
async def register(username: str, email: str, password: str, background_tasks: BackgroundTasks):
    conn = get_db(); cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM users WHERE email=%s", (email,))
        if cur.fetchone(): raise HTTPException(status_code=400, detail="User already exists")
        hashed = hash_password(password)
        cur.execute("INSERT INTO users (username, email, password) VALUES (%s, %s, %s)", (username, email, hashed))
        conn.commit()
        background_tasks.add_task(send_welcome_email, email, username)
        return {"message": "Success"}
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
# 📊 TERMINAL API
# =========================
@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(
        request=request, 
        name="index.html", 
        context={"request": request}
    )

@app.get("/api/context")
async def context_api():
    try:
        s = yf.Search("Most Active", max_results=8)
        trending = [{"s": q['symbol'], "n": q.get('shortname', q['symbol'])} for q in s.quotes]
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
        if hist.empty: return JSONResponse({"error": "No data"}, status_code=404)

        info = stock.info
        curr = info.get('currency', 'USD')
        sym = CURRENCY_SYMBOLS.get(curr, curr + " ")

        current_p = hist['Close'].iloc[-1]
        open_p = info.get('regularMarketOpen') or hist['Open'].iloc[0]
        change = ((current_p - open_p) / open_p) * 100

        # Market Status logic
        m_state = info.get("marketState", "").upper()
        is_live = m_state in ["REGULAR", "PRE", "POST"]
        status = "LIVE" if m_state == "REGULAR" else ("EXTENDED" if is_live else "CLOSED")

        def fmt_n(n):
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
            "news": fetch_guardian_news(ticker), # ✅ USES CACHE
            "target": f"{sym}{info.get('targetMeanPrice', current_p*1.15):,.2f}",
            "health": 70, "hype": 60,
            "fundamentals": {
                "open": f"{sym}{open_p:,.2f}",
                "mkt_cap": fmt_n(info.get('marketCap')),
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