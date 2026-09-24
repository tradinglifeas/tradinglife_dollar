import os
import requests
import time

API_KEY = os.environ["ALPHA_VANTAGE_API_KEY"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

BASE_URL = "https://www.alphavantage.co/query"


def alpha_request(params):
    params["apikey"] = API_KEY

    response = requests.get(
        BASE_URL,
        params=params,
        timeout=30
    )
    response.raise_for_status()

    data = response.json()

    if "Information" in data:
        raise RuntimeError(data["Information"])

    if "Note" in data:
        raise RuntimeError(data["Note"])

    if "Error Message" in data:
        raise RuntimeError(data["Error Message"])

    return data


def get_metal(symbol):
    data = alpha_request({
        "function": "GOLD_SILVER_SPOT",
        "symbol": symbol
    })

    # Alpha Vantage commodity response
    price = data.get("price")
    timestamp = data.get("timestamp", "")
    unit = data.get("unit", "USD")

    if price is None:
        raise RuntimeError(
            f"Could not read {symbol} price: {data}"
        )

    return float(price), unit, timestamp


def get_crypto(symbol):
    data = alpha_request({
        "function": "CURRENCY_EXCHANGE_RATE",
        "from_currency": symbol,
        "to_currency": "USD"
    })

    info = data.get(
        "Realtime Currency Exchange Rate",
        {}
    )

    rate = info.get("5. Exchange Rate")
    refreshed = info.get("6. Last Refreshed", "")

    if rate is None:
        raise RuntimeError(
            f"Could not read {symbol} price: {data}"
        )

    return float(rate), refreshed


def format_price(value):
    if value >= 1000:
        return f"${value:,.2f}"

    if value >= 1:
        return f"${value:,.4f}"

    return f"${value:,.6f}"


results = []
errors = []

# -----------------------
# GOLD & SILVER
# -----------------------

metals = [
    ("🥇 Gold", "GOLD"),
    ("🥈 Silver", "SILVER")
]

for name, symbol in metals:
    try:
        price, unit, timestamp = get_metal(symbol)

        results.append(
            f"{name}\n"
            f"{format_price(price)}"
        )

    except Exception as e:
        errors.append(f"{name}: {e}")

    time.sleep(2)


# -----------------------
# CRYPTO
# -----------------------

cryptos = [
    ("₿ Bitcoin", "BTC"),
    ("Ξ Ethereum", "ETH"),
    ("◎ Solana", "SOL"),
    ("✕ XRP", "XRP"),
    ("₳ Cardano", "ADA"),
    ("🐕 Dogecoin", "DOGE"),
]

for name, symbol in cryptos:
    try:
        price, refreshed = get_crypto(symbol)

        results.append(
            f"{name} ({symbol})\n"
            f"{format_price(price)}"
        )

    except Exception as e:
        errors.append(f"{symbol}: {e}")

    time.sleep(2)


# -----------------------
# TELEGRAM MESSAGE
# -----------------------

message = (
    "📊 MARKET PRICES\n\n"
    + "\n\n".join(results)
)

if errors:
    message += (
        "\n\n⚠️ Some data unavailable:\n"
        + "\n".join(errors)
    )

message += (
    "\n\n"
    "Source: Alpha Vantage\n"
    "Prices quoted in USD."
)


telegram_url = (
    f"https://api.telegram.org/bot"
    f"{BOT_TOKEN}/sendMessage"
)

response = requests.post(
    telegram_url,
    data={
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": True
    },
    timeout=30
)

response.raise_for_status()

print("Market prices sent successfully.")
