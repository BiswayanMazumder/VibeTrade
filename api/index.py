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

def get_pro_analysis(ticker, current_price, info):
    """Calculates professional 12M targets and insights."""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y")
        if hist.empty: return {"target": "N/A", "insight": "Awaiting historical baseline..."}
        
        start_price = float(hist['Close'].iloc[0])
        annual_return = (current_price - start_price) / start_price
        projected = current_price * (1 + annual_return)
        
        analyst_target = info.get('targetMeanPrice')
        if analyst_target:
            insight = f"Wall Street consensus sits at ${analyst_target:,.2f}. Based on {annual_return*100:+.1f}% momentum, our AI projects ${projected:,.2f}."
            return {"target": f"${analyst_target:,.2f}", "insight": insight}
        
        insight = f"Based on a {annual_return*100:+.1f}% yearly trajectory, our model projects a 12-month baseline of ${projected:,.2f}."
        return {"target": f"${projected:,.2f}", "insight": insight}
    except:
        return {"target": "TBD", "insight": "Syncing with market protocols..."}

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/search/{query}")
async def search(query: str):
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
        
        if isinstance(data.columns, pd.MultiIndex): data.columns = data.columns.get_level_values(0)
        
        all_dates = sorted(pd.Series(data.index.date).unique())
        df = data[data.index.date == all_dates[-1]].copy()
        current_price = float(df['Close'].iloc[-1])
        prev_close = float(data[data.index.date == all_dates[-2]]['Close'].iloc[-1]) if len(all_dates) > 1 else float(df['Open'].iloc[0])
        
        change_pct = ((current_price - prev_close) / prev_close) * 100
        theme_color = '#00ffbb' if change_pct >= 0 else '#ff3366'
        
        # Fundamental Data (Google Finance Style)
        info = stock.info
        mkt_cap_raw = info.get('marketCap', 0)
        mkt_cap = f"{mkt_cap_raw / 1e12:.2f}T" if mkt_cap_raw > 1e12 else f"{mkt_cap_raw / 1e7:.2f}Cr"
        
        fundamentals = {
            "open": f"{info.get('open', 0):,.2f}",
            "mkt_cap": mkt_cap,
            "pe": f"{info.get('trailingPE', 'N/A')}",
            "div": f"{info.get('dividendYield', 0)*100:.2f}%" if info.get('dividendYield') else "N/A",
            "high": f"{info.get('dayHigh', 0):,.2f}",
            "low": f"{info.get('dayLow', 0):,.2f}",
            "h52": f"{info.get('fiftyTwoWeekHigh', 0):,.2f}",
            "l52": f"{info.get('fiftyTwoWeekLow', 0):,.2f}"
        }

        # News & AI Prediction
        news = []
        try:
            raw_news = stock.news
            if raw_news:
                for n in raw_news[:3]:
                    if n.get('title') and n.get('link'): news.append({"title": n.get('title'), "link": n.get('link')})
            if not news: news = [{"title": "No recent headlines.", "link": "#"}]
        except: news = [{"title": "News offline.", "link": "#"}]
            
        analysis = get_pro_analysis(ticker, current_price, info)
        
        # Chart
        fig = go.Figure()
        fig.add_shape(type="line", x0=df.index[0], x1=df.index[-1], y0=prev_close, y1=prev_close, line=dict(color="rgba(255,255,255,0.1)", width=1, dash="dot"))
        fig.add_trace(go.Scatter(x=df.index, y=df['Close'].values.flatten(), line=dict(color=theme_color, width=3, shape='spline'), fill='tozeroy', fillcolor=f'rgba({ "0, 255, 187" if change_pct >= 0 else "255, 51, 102" }, 0.03)'))
        fig.update_layout(template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', margin=dict(t=10,b=0,l=0,r=40), height=350, xaxis=dict(showgrid=False), yaxis=dict(side='right', showgrid=True, gridcolor='rgba(255,255,255,0.02)', autorange=True))
        
        return {
            "symbol": ticker.upper(), "price": f"{current_price:,.2f}", "change": f"{change_pct:+.2f}%",
            "fundamentals": fundamentals, "target": analysis['target'], "ai_tip": analysis['insight'], 
            "news": news, "chart": pio.to_html(fig, full_html=False, config={'displayModeBar': False})
        }
    except Exception as e: return JSONResponse({"error": str(e)}, status_code=500)