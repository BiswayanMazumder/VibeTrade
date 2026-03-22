from fastapi import FastAPI, Request, Query
from fastapi.templating import Jinja2Templates
from fastapi.responses import JSONResponse
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.io as pio
import requests

app = FastAPI()
templates = Jinja2Templates(directory="templates")

CURRENCY_SYMBOLS = {'INR': '₹', 'USD': '$', 'EUR': '€', 'GBP': '£', 'JPY': '¥'}

def fetch_robust_news(ticker):
    """Fetches news and strictly filters for the specific ticker."""
    news = []
    ticker_upper = ticker.upper().split('.')[0] # Get 'TCS' from 'TCS.NS'
    
    try:
        # Use Search API which provides 'relatedTickers' metadata
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={ticker}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        res = requests.get(url, headers=headers, timeout=5).json()
        
        raw_news = res.get('news', [])
        for item in raw_news:
            # STRICT FILTER: Check if this ticker is explicitly listed as a related ticker
            related = [t.upper() for t in item.get('relatedTickers', [])]
            
            # If the ticker is in metadata OR the title contains the ticker/company name
            if ticker_upper in related or ticker_upper in item.get('title', '').upper():
                news.append({
                    "title": item.get('title'),
                    "link": item.get('link')
                })
            
            if len(news) >= 4: break # Keep top 4 relevant stories
            
    except Exception as e:
        print(f"News fetch error: {e}")
    
    if not news:
        news = [{"title": f"No specific recent headlines for {ticker.upper()}.", "link": f"https://finance.yahoo.com/quote/{ticker}"}]
    return news

def get_ai_analysis(ticker, current_price, info, news, symbol):
    """Maintains existing Sentiment Scoring + AI Target logic."""
    score = 50
    # Logic remains the same to avoid 'modifying existing features'
    pos, neg = ['bullish', 'growth', 'buy', 'up', 'profit'], ['bearish', 'risk', 'sell', 'down', 'loss']
    for n in news:
        txt = n['title'].lower()
        for w in pos: 
            if w in txt: score += 7
        for w in neg: 
            if w in txt: score -= 7
    score = max(10, min(90, score))
    
    try:
        hist = yf.Ticker(ticker).history(period="1y")
        ann_return = (current_price - hist['Close'].iloc[0]) / hist['Close'].iloc[0]
        target_val = info.get('targetMeanPrice', current_price * (1 + ann_return))
        return {"target": f"{symbol}{target_val:,.2f}", "sentiment": score, "tip": f"1Y Momentum: {ann_return*100:+.1f}%"}
    except:
        return {"target": "TBD", "sentiment": 50, "tip": "Analyzing data..."}

def get_ai_analysis(ticker, current_price, info, news, symbol):
    score = 50
    pos, neg = ['bullish', 'growth', 'buy', 'up', 'profit'], ['bearish', 'risk', 'sell', 'down', 'loss']
    for n in news:
        txt = n['title'].lower()
        for w in pos: 
            if w in txt: score += 7
        for w in neg: 
            if w in txt: score -= 7
    score = max(10, min(90, score))
    try:
        hist = yf.Ticker(ticker).history(period="1y")
        ann_return = (current_price - hist['Close'].iloc[0]) / hist['Close'].iloc[0]
        target_val = info.get('targetMeanPrice', current_price * (1 + ann_return))
        return {"target": f"{symbol}{target_val:,.2f}", "sentiment": score, "tip": f"1Y Momentum: {ann_return*100:+.1f}%"}
    except:
        return {"target": "TBD", "sentiment": 50, "tip": "Analyzing data..."}

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/search/{query}")
async def search(query: str):
    """Uses yfinance.Search for the most stable 2026 lookup."""
    try:
        # yf.Search is more robust than raw requests to the search endpoint
        search_results = yf.Search(query, max_results=8)
        
        results = []
        for quote in search_results.quotes:
            # We filter for only the most useful data to keep the dropdown clean
            results.append({
                "symbol": quote.get('symbol'),
                "name": quote.get('shortname') or quote.get('longname') or "Unknown Asset"
            })
        return results
    except Exception as e:
        print(f"Search error: {e}")
        return []

@app.get("/api/stream/{ticker}")
async def stream_data(ticker: str, period: str = Query("1d")):
    try:
        stock = yf.Ticker(ticker)
        interval = "1m" if period in ["1d", "5d"] else "1d"
        data = stock.history(period=period, interval=interval)
        if data.empty: return JSONResponse({"error": "No Data"}, status_code=404)
        
        info = stock.info
        curr_code = info.get('currency', 'USD')
        symbol = CURRENCY_SYMBOLS.get(curr_code, curr_code + " ")
        
        # Market Status
        market_state = info.get('marketState', 'CLOSED')
        is_open = market_state in ['REGULAR', 'PRE', 'POST']

        current_price = float(data['Close'].iloc[-1])
        open_p = float(data['Close'].iloc[0])
        change_pct = ((current_price - open_p) / open_p) * 100
        theme_color = '#00ffbb' if change_pct >= 0 else '#ff3366'
        
        # Helper for Currency Formatting
        def fmt(v): return f"{v:,.2f}" if v and v != "N/A" else "---"

        # Multi-Currency Mkt Cap
        m_cap_val = info.get('marketCap', 0)
        m_cap_str = f"{m_cap_val/1e7:,.2f}LCr" if curr_code == 'INR' else f"{m_cap_val/1e9:,.2f}B"

        fundamentals = {
            "open": fmt(info.get('open')),
            "high": fmt(info.get('dayHigh')),
            "low": fmt(info.get('dayLow')),
            "mkt_cap": m_cap_str,
            "pe": info.get('trailingPE', '---'),
            "h52": fmt(info.get('fiftyTwoWeekHigh')),
            "div": f"{info.get('dividendYield', 0)*100:.2f}%" if info.get('dividendYield') else "---",
            "q_div": fmt(info.get('lastDividendValue')),
            "l52": fmt(info.get('fiftyTwoWeekLow'))
        }

        news = fetch_robust_news(ticker)
        analysis = get_ai_analysis(ticker, current_price, info, news, symbol)

        # Chart
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=data.index, y=data['Close'], mode='lines', line=dict(color=theme_color, width=2, shape='hv')))
        fig.add_annotation(xref="paper", yref="paper", x=1, y=1.05, text="MARKET OPEN" if is_open else "MARKET CLOSED", showarrow=False, 
                           font=dict(size=10, color="#00ffbb" if is_open else "#ff3366"))
        
        fig.update_layout(template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', margin=dict(t=30,b=10,l=0,r=50), height=350, showlegend=False,
                          xaxis=dict(showgrid=False), yaxis=dict(side='right', showgrid=True, gridcolor='rgba(255,255,255,0.05)'))

        return {
            "symbol": ticker.upper(), "price": f"{current_price:,.2f}", "currency_text": curr_code, "change": f"{change_pct:+.2f}%",
            "fundamentals": fundamentals, "news": news, "target": analysis['target'], "ai_tip": analysis['tip'], "sentiment": analysis['sentiment'],
            "chart": pio.to_html(fig, full_html=False, config={'displayModeBar': False})
        }
    except Exception as e: return JSONResponse({"error": str(e)}, status_code=500)