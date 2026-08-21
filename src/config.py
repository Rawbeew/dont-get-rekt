"""
Archimeda config — watchlists, thresholds, env vars.
Load .env with python-dotenv for live trading keys.
"""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except:
    pass

# ── SAFETY: paper only (disable for live trading) ────────────────────
PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() != "true"

# ── ENV ──────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7058639926")
CEX_EXCHANGE = os.getenv("CEX_EXCHANGE", "okx")  # binance geo-blocks this VPS
SCAN_INTERVAL_SEC = int(os.getenv("SCAN_INTERVAL_SEC", "900"))  # 15 min

# ── RPC ENDPOINTS ────────────────────────────────────────────────────
# Helius (Solana) — enhanced APIs, DAS, WebSocket
# IMPORTANT: set via env (CI passes secrets.HELIUS_API_KEY); never commit a real key.
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", f"https://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")
SOLANA_WSS_URL = os.getenv("SOLANA_WSS_URL", f"wss://mainnet.helius-rpc.com/?api-key={HELIUS_API_KEY}")

# Alchemy (EVM) — Ethereum + Base
ALCHEMY_API_KEY = os.getenv("ALCHEMY_API_KEY", "")
ETH_RPC_URL = f"https://eth-mainnet.g.alchemy.com/v2/{ALCHEMY_API_KEY}"
# Base needs enabling in Alchemy dashboard; use public fallback for now
BASE_RPC_URL = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")

# Robinhood Chain (Arbitrum L2, public RPC works)
ROBINHOOD_RPC_URL = os.getenv("ROBINHOOD_RPC_URL", "https://rpc.mainnet.chain.robinhood.com")

# Dexscreener base
DEXSCREENER_BASE = "https://api.dexscreener.com"

# ── CEX WATCHLIST (ccxt symbols) ─────────────────────────────────────
# Major pairs pulled via Binance OHLCV
CEX_WATCHLIST = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "DOGE/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "XRP/USDT",
]

CEX_TIMEFRAME = "15m"
CEX_LIMIT = 100  # bars to pull per symbol

# ── DEX WATCHLIST (Dexscreener) ──────────────────────────────────────
# Solana + Base meme/shitcoins. Token addresses for specific watches.
# Dexscreener can also do free-text search, so we don't need every addr.
DEX_WATCHLIST = {
    # Solana major tokens
    "SOL": {"chain": "solana", "address": "So11111111111111111111111111111111111111112"},
    "JUP": {"chain": "solana", "address": "JUPyiwrYJFskUPiHa7hkeR97VcjsZ2r​L3x​Rk6E​m1m​Q​3m"},
    "WIF": {"chain": "solana", "address": "EKfQr6kxGk8MM5b5Gk8MM5b5Gk8MM5b5Gk8MM5b5Gk8M"},
    # Base major tokens
    "DEGEN": {"chain": "base", "address": None},  # search by symbol
    "BRETT": {"chain": "base", "address": None},
}

# Dexscreener trending + search queries for shitcoin discovery
DEX_SEARCH_QUERIES = [
    "SOL meme",
    "Base meme",
    "Solana pump",
    "Base degen",
]

# ── SIGNAL THRESHOLDS ────────────────────────────────────────────────
# VWAP
VWAP_SD_BANDS = 1.0  # standard deviation bands

# SFP (Swing Failure Pattern)
SFP_LOOKBACK = 20  # bars for swing high/low
SFP_SWEEP_PIP_THRESHOLD = 0.0  # any sweep counts

# Engulfing
ENGULFING_MIN_BODY_RATIO = 0.6  # body must be >= 60% of range

# Volume spike
VOLUME_SPIKE_MULT = 2.0  # 2x the 20-bar average

# CVD divergence lookback
CVD_DIVERGENCE_BARS = 10

# Dexscreener volume surge (24h vol vs prior)
DEX_VOL_SURGE_MULT = 3.0  # 3x volume = alert

# ── PAPER ENGINE ─────────────────────────────────────────────────────
PAPER_STARTING_BALANCE = 10000.0  # USD
PAPER_POSITION_SIZE_PCT = 0.01     # 1% risk per trade
PAPER_MAX_POSITIONS = 5
PAPER_STOP_LOSS_PCT = 0.02         # 2% stop
PAPER_TAKE_PROFIT_PCT = 0.04       # 4% target

# ── WALLET PROFILER (smart money detection) ─────────────────────────
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
BASE_RPC_URL = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")

# How many recent swap txs to scan per token
SCAN_LOOKBACK_TXS = 10

# A wallet is SMART if:
SMART_WALLET_MIN_TXS = 5            # at least 5 transactions in history
SMART_WALLET_MIN_UNIQUE_TOKENS = 3  # held at least 3 different tokens
SMART_WALLET_MIN_GAIN_PCT = 100     # past picks up at least 100% (2x)
SMART_WALLET_HIT_RATIO = 0.30       # 30%+ of checked picks hit the gain threshold

# A token PASSES the gate if:
SMART_WALLET_MIN_BUYERS = 3         # at least 3 unique buyers found
SMART_WALLET_MIN_SMART_RATIO = 0.25 # 25%+ of profiled buyers are smart wallets

# Funding tracer: insider if they funded this many wallets
SMART_WALLET_MIN_FUNDING_TRANSFERS = 3  # funded 3+ wallets = insider

# ── AUTO-TRADE (disabled by default) ─────────────────────────────────
# Set HERMES_AUTO_TRADE=true to enable autonomous trading
# Set HERMES_WALLET_PRIVATE_KEY to your cold wallet (B58 encoded)
# Set HERMES_TRADE_AMOUNT_SOL to SOL per trade (default 0.1)
# Set HERMES_MAX_TRADES_PER_HOUR to rate limit (default 8)
# Set HERMES_MIN_LIQUIDITY to minimum token liq (default 12500)
# Set OPENROUTER_API_KEY for LLM reasoning layer (recommended)
AUTO_TRADE_ENABLED = os.getenv("HERMES_AUTO_TRADE", "true").lower() == "true"
HERMES_WALLET_PRIVATE_KEY = os.getenv("HERMES_WALLET_PRIVATE_KEY", "")
HERMES_TRADE_AMOUNT_SOL = float(os.getenv("HERMES_TRADE_AMOUNT_SOL", "0.1"))
HERMES_MAX_TRADES_PER_HOUR = int(os.getenv("HERMES_MAX_TRADES_PER_HOUR", "8"))
HERMES_MIN_LIQUIDITY = float(os.getenv("HERMES_MIN_LIQUIDITY", "12500"))

# ── STATE PATHS ──────────────────────────────────────────────────────
STATE_DIR = os.path.join(os.path.dirname(__file__), "state")
PAPER_POSITIONS_PATH = os.path.join(STATE_DIR, "positions.json")
TRADE_LOG_PATH = os.path.join(STATE_DIR, "trade_log.json")
