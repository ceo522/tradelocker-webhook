# TradeLocker Webhook Server

Receives TradingView webhook signals and executes trades via the TradeLocker API.

## Webhook Payload

```json
{
  "pair": "XAUUSD",
  "signal": "BUY",
  "source": "firestorm"
}
```

## Signal Aggregation Logic

- **XAUUSD:** 4 of 5 signals within a 5-minute window → fire trade
- **All other pairs:** 3 of 5 signals within a 5-minute window → fire trade

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/signal` | Receive TradingView webhook |
| GET | `/status` | Current signal state & recent trades |
| GET | `/health` | Health check |

## Deploy to Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/ceo522/tradelocker-webhook)

After deploy, set the `TL_PASSWORD` environment variable in Render dashboard.

## TradingView Alert Setup

Set your alert webhook URL to:
```
https://tradelocker-webhook.onrender.com/signal
```

Payload:
```json
{"pair":"XAUUSD","signal":"BUY","source":"firestorm"}
```
