import os
import urllib.request
import urllib.error
import json

api_key = os.getenv("OPENROUTER_API_KEY", "")

payload = {
    "date": "2026-09-16",
    "summary": {
        "net_pnl": -50.0,
        "win_rate": "0.0%",
        "daily_dd": "-0.94%",
        "trades": 1
    },
    "trades": [
        {
            "symbol": "XAUUSD",
            "direction": "BUY",
            "entry_price": 4335.07,
            "sl": 4321.35,
            "pnl": -50.0,
            "pnl_r": -0.9,
            "verdict": "1H Rubber-Band Trend Exhaustion",
            "root_cause": "Entry was severely overextended (1.86x 1H ATR away from 1H EMA20) while 1H ADX was rolling over. The trade bought into exhausted momentum and was snapped back by mean-reversion counter-flow.",
            "metrics": {
                "1H EMA Stretch": "1.86x ATR",
                "MFE": "0.18R",
                "MAE": "1.00R",
                "LSQI Score": "70/100"
            },
            "rule": "Block entries when distance from 1H EMA20 exceeds 1.35x ATR."
        }
    ]
}

prompt = f"""You are the Chief Risk Officer for an institutional proprietary trading firm running the DCC algorithmic strategy.
Below is the verified mathematical autopsy of today's trading session (2026-09-16).

{json.dumps(payload, indent=2)}

INSTRUCTIONS:
1. DO NOT change, alter, or recalculate ANY numbers, prices, or PnL figures.
2. Write a crisp, punchy 3-paragraph executive memo summarizing:
   - Overall session performance
   - Technical autopsy of why the Gold trade failed (referencing the 1.86x ATR rubber-band stretch and adverse excursion)
   - Desk takeaway rule
3. Format with clean markdown and emojis."""

for model in ["z-ai/glm-5.2:free", "inclusionai/ling-3.0-flash-fin:free"]:
    print(f"\n--- Trying {model} ---")
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a quantitative trading desk supervisor writing institutional trade analysis."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.2,
        "max_tokens": 800
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/RyzEnHunTer/Dc-Bot",
            "X-Title": "DCC Trade Forensics"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(">>> LLM GENERATED MEMO:")
            print(data["choices"][0]["message"]["content"].strip())
            break
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        print(f"HTTP {e.code}: {err[:120]}...")
    except Exception as e:
        print(f"Error: {e}")
