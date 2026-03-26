import os
import yfinance as yf
import pandas as pd
import requests
from fastapi import FastAPI, Request, Query, Depends
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
from sqlmodel import SQLModel, Field, create_engine, Session, select
from typing import Optional, List

# --- DATABASE CONFIGURATION ---
# Using the Pooled URL for better performance on Vercel
DATABASE_URL = ""

engine = create_engine(DATABASE_URL, echo=False)

# Define your User table for Login/Auth
class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    email: str = Field(unique=True)
    hashed_password: str # Always hash passwords in a real app!

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session

# --- APP INITIALIZATION ---
app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.on_event("startup")
def on_startup():
    create_db_and_tables()

CURRENCY_SYMBOLS = {'INR': '₹', 'USD': '$', 'EUR': '€', 'GBP': '£', 'JPY': '¥'}

# --- CORE FUNCTIONS ---

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
            fallback = ["RELIANCE.NS", "NVDA", "TCS.NS", "AAPL", "TSLA", "ZOMATO.NS", "HDFCBANK.NS"]
            for sym in fallback:
                trending.append({"s": sym, "n": sym.replace(".NS", "")})
        return trending[:8]
    except Exception as e:
        print(f"Trending Error: {e}")
        return [{"s": "NVDA", "n": "NVIDIA"}, {"s": "RELIANCE.NS", "n": "Reliance"}]

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
    except: pass
    return news if news else [{"title": f"No specific headlines for {ticker.upper()}.", "link": "#"}]

# --- ROUTES ---

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")

# Example User Registration Route
@app.post("/api/register")
async def register_user(user: User, session: Session = Depends(get_session)):
    session.add(user)
    session.commit()
    session.refresh(user)
    return {"status": "User created", "username": user.username}

@app.get("/api/context")
async def context_api():
    return JSONResponse(content={"trending": get_realtime_trending()})

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
        m_state = info.get('marketState', 'CLOSED')
        is_open = m_state in ['REGULAR', 'PRE', 'POST']

        current_p = hist['Close'].iloc[-1]
        open_p = hist['Close'].iloc[0]
        change = ((current_p - open_p) / open_p) * 100
        color = '#00ffbb' if change >= 0 else '#ff3366'
        
        roe = info.get('returnOnEquity', 0)
        health = min(95, max(15, int(roe * 400))) if roe else 55
        hype = min(95, max(20, int(abs(change) * 18)))

        def f_val(v): return f"{v:,.2f}" if v else "---"
        m_cap = f"{info.get('marketCap',0)/1e7:,.1f}LCr" if curr == 'INR' else f"{info.get('marketCap',0)/1e9:,.1f}B"

        fundamentals = {
            "open": f_val(info.get('open')), "high": f_val(info.get('dayHigh')), "low": f_val(info.get('dayLow')),
            "mkt_cap": m_cap, "pe": f_val(info.get('trailingPE')), "h52": f_val(info.get('fiftyTwoWeekHigh')),
            "div": f"{info.get('dividendYield',0)*100:.2f}%", "q_div": f_val(info.get('lastDividendValue')), "l52": f_val(info.get('fiftyTwoWeekLow'))
        }

        chart_data = {
            "x": hist.index.strftime('%Y-%m-%d %H:%M:%S').tolist(),
            "y": hist['Close'].tolist(),
            "color": color,
            "status": "MARKET OPEN" if is_open else "MARKET CLOSED",
            "curr": curr
        }

        return {
            "symbol": ticker.upper(), "price": f"{current_p:,.2f}", "currency_text": curr, "change": f"{change:+.2f}%",
            "fundamentals": fundamentals, "news": fetch_robust_news(ticker), 
            "hype": hype, "health": health, "sentiment": 65, "target": f"{sym}{current_p*1.15:,.2f}",
            "ai_tip": f"AI Pulse: {health}% Health Score. Trend is {'Bullish' if change > 0 else 'Bearish'}.",
            "chart_json": chart_data
        }
    except Exception as e: return JSONResponse({"error": str(e)}, status_code=500)