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

def get_professional_prediction(ticker, current_price):
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y")
        if hist.empty: return {"target": "N/A", "insight": "Insufficient data."}
        start_price = float(hist['Close'].iloc[0])
        annual_return = (current_price - start_price) / start_price
        projected_price = current_price * (1 + annual_return)
        info = stock.info
        analyst_target = info.get('targetMeanPrice')
        if analyst_target:
            insight = f"Wall Street consensus sits at ${analyst_target:,.2f}. AI projects a high-end range of ${projected_price*1.1:,.2f}."
            return {"target": f"${analyst_target:,.2f}", "insight": insight}
        return {"target": f"${projected_price:,.2f}", "insight": f"Based on {annual_return*100:+.1f}% annual trend, AI projects ${projected_price:,.2f}."}
    except:
        return {"target": "TBD", "insight": "Analyzing market patterns..."}

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/search/{query}")
async def search_tickers(query: str):
    try:
        url = f"https://query1.finance.yahoo.com/v1/finance/search?q={query}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers)
        data = response.json()
        return [{"symbol": r.get('symbol'), "name": r.get('shortname')} for r in data.get('quotes', [])[:5]]
    except: return []

@app.get("/api/stream/{ticker}")
async def stream_data(ticker: str):
    try:
        ticker_obj = yf.Ticker(ticker)
        data = ticker_obj.history(period="5d", interval="1m")
        if data.empty: return JSONResponse({"error": "Not Found"}, status_code=404)
        if isinstance(data.columns, pd.MultiIndex): data.columns = data.columns.get_level_values(0)
        all_dates = sorted(pd.Series(data.index.date).unique())
        df = data[data.index.date == all_dates[-1]].copy()
        current_price = float(df['Close'].iloc[-1])
        prev_close = float(data[data.index.date == all_dates[-2]]['Close'].iloc[-1]) if len(all_dates) > 1 else float(df['Open'].iloc[0])
        change_pct = ((current_price - prev_close) / prev_close) * 100
        theme_color = '#00ffbb' if change_pct >= 0 else '#ff3366'
        prediction = get_professional_prediction(ticker, current_price)
        fig = go.Figure()
        fig.add_shape(type="line", x0=df.index[0], x1=df.index[-1], y0=prev_close, y1=prev_close, line=dict(color="rgba(255,255,255,0.15)", width=1, dash="dot"))
        fig.add_trace(go.Scatter(x=df.index, y=df['Close'].values.flatten(), line=dict(color=theme_color, width=3, shape='spline'), fill='tozeroy', fillcolor=f'rgba({ "0, 255, 187" if change_pct >= 0 else "255, 51, 102" }, 0.05)'))
        fig.update_layout(template='plotly_dark', paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)', margin=dict(t=10,b=0,l=0,r=40), height=400, xaxis=dict(showgrid=False), yaxis=dict(side='right', showgrid=True, gridcolor='rgba(255,255,255,0.03)', autorange=True))
        return {"symbol": ticker.upper(), "price": f"{current_price:,.2f}", "change": f"{change_pct:+.2f}%", "target": prediction['target'], "ai_tip": prediction['insight'], "chart": pio.to_html(fig, full_html=False, config={'displayModeBar': False}), "vol": round(np.std(np.diff(np.log(df['Close'].values.flatten() + 1e-9))) * 1000, 1), "hype": round(min(10, (df['Volume'].iloc[-1] / df['Volume'].mean() * 5)), 1)}
    except Exception as e: return JSONResponse({"error": str(e)}, status_code=500)