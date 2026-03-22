from fastapi import FastAPI, Request
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

def get_pro_analysis(ticker, current_price):
    """Calculates professional 12M targets based on annual trends."""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y")
        if hist.empty: return {"target": "N/A", "insight": "Awaiting more data..."}
        
        start_price = float(hist['Close'].iloc[0])
        annual_return = (current_price - start_price) / start_price
        projected = current_price * (1 + annual_return)
        
        # Professional phrasing for the AI Oracle
        insight = f"Based on a {annual_return*100:+.1f}% yearly trajectory, our model projects a 12-month baseline of ${projected:,.2f}."
        return {"target": f"${projected:,.2f}", "insight": insight}
    except:
        return {"target": "TBD", "insight": "Syncing with market protocols..."}

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/search/{query}")
async def search(query: str):
    """Fetches real-time ticker suggestions."""
    try:
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={query}"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        return [{"symbol": r.get('symbol'), "name": r.get('shortname')} for r in res.json().get('quotes', [])[:5]]
    except: return []

@app.get("/api/stream/{ticker}")
async def stream_data(ticker: str):
    try:
        stock = yf.Ticker(ticker)
        data = stock.history(period="5d", interval="1m")
        if data.empty: return JSONResponse({"error": "Ticker Not Found"}, status_code=404)
        
        if isinstance(data.columns, pd.MultiIndex): 
            data.columns = data.columns.get_level_values(0)
        
        all_dates = sorted(pd.Series(data.index.date).unique())
        df = data[data.index.date == all_dates[-1]].copy()
        current_price = float(df['Close'].iloc[-1])
        prev_close = float(data[data.index.date == all_dates[-2]]['Close'].iloc[-1]) if len(all_dates) > 1 else float(df['Open'].iloc[0])
        
        change_pct = ((current_price - prev_close) / prev_close) * 100
        theme_color = '#00ffbb' if change_pct >= 0 else '#ff3366'
        
        # News Engine
        news = []
        try:
            raw_news = stock.news
            if raw_news:
                for n in raw_news[:3]:
                    t, l = n.get('title'), n.get('link')
                    if t and l: news.append({"title": t, "link": l})
            if not news: news = [{"title": "No recent headlines found.", "link": "#"}]
        except: news = [{"title": "News temporarily offline.", "link": "#"}]
            
        analysis = get_pro_analysis(ticker, current_price)
        
        # Chart Logic
        fig = go.Figure()
        fig.add_shape(type="line", x0=df.index[0], x1=df.index[-1], y0=prev_close, y1=prev_close, 
                      line=dict(color="rgba(255,255,255,0.1)", width=1, dash="dot"))
        fig.add_trace(go.Scatter(x=df.index, y=df['Close'].values.flatten(), 
                                 line=dict(color=theme_color, width=3, shape='spline'), 
                                 fill='tozeroy', fillcolor=f'rgba({ "0, 255, 187" if change_pct >= 0 else "255, 51, 102" }, 0.03)'))
        fig.update_layout(template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', 
                          margin=dict(t=10,b=0,l=0,r=40), height=400, xaxis=dict(showgrid=False), 
                          yaxis=dict(side='right', showgrid=True, gridcolor='rgba(255,255,255,0.02)', autorange=True))
        
        return {
            "symbol": ticker.upper(), "price": f"{current_price:,.2f}", "change": f"{change_pct:+.2f}%",
            "target": analysis['target'], "ai_tip": analysis['insight'], "news": news,
            "chart": pio.to_html(fig, full_html=False, config={'displayModeBar': False}),
            "hype": round(min(10, (df['Volume'].iloc[-1] / df['Volume'].mean() * 5)), 1) if not df['Volume'].empty else 1.0
        }
    except Exception as e: return JSONResponse({"error": str(e)}, status_code=500)