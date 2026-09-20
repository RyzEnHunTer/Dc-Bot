# DCC Community Intelligence & Strategy Reverse-Engineering Report

**Date:** September 15, 2026  
**Source:** DCC Official Discord Trade Channel + Live Chart Attachments  
**Extracted File:** [`experiments/discord_trades_scraped.json`](file:///d:/FOREX/DC/experiments/discord_trades_scraped.json)  
**Charts Downloaded:** [`experiments/scraped_charts/`](file:///d:/FOREX/DC/experiments/scraped_charts)

---

## 1. Official Indicators & Parameters Discovered

From the live TradingView screenshots uploaded by community members (`_72200478_f29c0c06-46e7-4bce-8164-716f1f414d1b.jpg` and `dipon_52859206_IMG_6951.png`), we extracted the exact Pine Script indicator parameters:

### Indicator Metadata
- **Indicator Name:** `DCC Indicator 2.0 (No Checklist)` / `DCC Indicator (Advanced)`
- **Core Parameters:** `STATUS 14 14 0.9`
  - **ADX Length:** `14` (Threshold: `> 20.0`)
  - **ATR Length:** `14` (Higher timeframe: `1H`)
  - **ATR SL Multiplier:** `0.9` (Default: `1H ATR * 0.9`)
- **Key Levels Monitored in Real-Time:**
  - `Tren H1`: `BEARISH (EMA 9 < 20)` or `BULLISH (EMA 9 > 20)`
  - `Session VWAP`: Intraday benchmark (orange/brown line)
  - `SH-H2 / SL-H2`: 2-Hour Swing High / Low Room Filter (seen on NQ chart `oui_07521350_image.png`)
  - `Last Flip SL`: Dynamic swing invalidation level

---

## 2. Setup Tier Classification (A+ vs B-Tier)

The community strictly grades trade setups:
- **A+ Setup:**
  - 1H 9/20 EMA aligned with trend
  - 1H ADX > 20.0
  - **Session VWAP perfectly aligned** with trade direction (price on the correct side of VWAP)
  - 5M bar closes across EMA ribbon (5M flip)
  - 2-Hour room clear to key structure
- **B-Tier Setup:**
  - *Quote from member `@ramyn`:* `"(B tier setup, vwap wasnt aligned)"`
  - When the 5M flip occurs against or before Session VWAP has flipped, the setup is downgraded to B-Tier (higher risk; members either reduce position sizing or skip).

---

## 3. Active Community Assets (Community Universe)

Community members are trading far beyond just NAS100. From today's live trade posts:

| Symbol | Category | Trader | Notes |
| :--- | :--- | :--- | :--- |
| **XAUUSD** | Metals / Commodities | `ramyn`, `joyful_beetle_01837` | Multiple winning short trades today; heavy volume |
| **BTCUSD** | Crypto | `Abdullah Bin Shams` | Clean 1:2 RR short drop ("Btc done and dusted!") |
| **USDJPY** | Forex Majors | `Mahmud`, `Unknown` | 1:2 RR long bounce from Session VWAP & 20 EMA |
| **AUDUSD** | Forex Majors | `dipon` | 1:2 RR short drop from 0.71295 down to 0.71220 |
| **NAS100 (NQ)** | US Indices | `oui` | 1:2.02 RR short during NY pre-market / school lunch |
| **US500 (SPX)** | US Indices | `Nihad` | M5 short on MT5 ("Without drawdown") |
| **EURUSD** | Forex Majors | YouTube Breakdown | Confirmed in DC's video analysis |
| **GBPNZD** | Forex Crosses | YouTube Breakdown | High ATR volatility cross |
| **GBPCAD** | Forex Crosses | YouTube Breakdown | High trending momentum pair |
| **GBPJPY** | Forex Crosses | YouTube Breakdown | Classic momentum Yen cross |

---

## 4. Execution & Risk Rules
1. **Timeframe:** **5M** entry trigger, **1H** trend & ATR filter.
2. **Stop Loss:** `1H ATR * 0.9` or swing level (`SH-H2`), whichever is safer.
3. **Target:** Fixed **1:2 Risk/Reward Ratio** (Milestone / partial target at **+1.5R**).
4. **Entry Trigger:** **Bar Close** of 5M candle confirming the flip.
