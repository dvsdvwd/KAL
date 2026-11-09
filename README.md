# Kalshi + Perps Paper Monitor

A local Python dashboard and CLI that display open Kalshi event-contract odds
alongside a crypto-perpetual market signal, then track hypothetical perp
positions locally. They do not submit orders or require API keys. Kalshi
contracts and perp markets are shown separately because their outcomes are not
necessarily related.

The default perp data source is Kraken Futures public market data (`PF_XBTUSD`).
Kalshi data comes from its public markets REST endpoint. The monitor uses simple
EMA direction rules for demonstration; they are not a forecast or a promise of
profit. Paper results omit fees, funding, slippage, liquidation, and execution
constraints, so they should not be treated as expected live performance.

## Dashboard

Requires Python 3.10+. Google sign-in is optional.

```bash
python dashboard.py
```

Open http://127.0.0.1:8765. The dashboard polls public market data every 15
seconds. The paper bot only updates while the dashboard is polling. Its balance
is simulated, not an exchange wallet balance.

To enable Google sign-in, create a Google OAuth Web client with
`http://127.0.0.1:8765` as an authorized JavaScript origin, install the optional
verifier, and set the public client ID in the terminal before starting the
server:

```bash
pip install -r requirements.txt
export GOOGLE_CLIENT_ID="your-client-id.apps.googleusercontent.com"
python dashboard.py
```

Google sign-in identifies a dashboard user only; it does not authorize Kalshi
orders or provide a Kalshi balance. This prototype does not yet authenticate
Kalshi API requests. Do not store API private keys in the browser or repository.

The chart defaults to 15-minute candles (1-hour is also available), with
session VWAP and previous-20-candle high/low overlays. Price-action flags compare
the latest candle's range and volume with the prior 20 candles; they identify
expansion after it begins and do not predict when a future move will happen.

## CLI

```bash
python kalshi_perps.py --watch 60
```

This polls once per minute. Press Ctrl-C to stop. Use `--watch 0` for a single
snapshot, or pass another supported Kraken Futures instrument with `--symbol`.

The paper account starts at $10,000. Auto sizing uses up to 5% of equity as
simulated margin; leverage is configurable from 1× to 5×, and manual notional is
capped by that margin rule. Simulated orders below $1 are rejected, and a 2%
daily loss stops new positions. These are simulation settings, not an exchange's
leverage limits or a recommended trading strategy. Kalshi contracts are listed
for context only; no Kalshi trades are simulated.

The dashboard's event-contract view is read-only. Google sign-in, private
exchange balances, and live trading require additional OAuth/exchange account
configuration and are not implemented in this prototype.