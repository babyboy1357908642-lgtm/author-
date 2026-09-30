"""Database-agnostic wallet and auction domain helpers shared by storage backends."""
import re


class RuleError(ValueError):
    pass


MIN_PVP_WAGER = 25_000  # 250 coins, represented as 100 internal subunits per coin.
MAX_PVP_WAGER = 3_000_000  # 30000 coins.
MAX_ACTIVE_PVP_GAMES = 3
PVP_REQUEST_TIMEOUT_SECONDS = 15
BOOM_TURN_TIMEOUT_SECONDS = 60
USD_TO_COIN_RATE = 25  # $100 = 2500 coins.


def cents(value):
    if not re.fullmatch(r"[0-9]{1,9}(?:\.[0-9]{1,2})?", value):
        raise RuleError("Coin ပမာဏကို 10 သို့ 10.50 ပုံစံရေးပါ။ ငွေသင်္ကေတ မထည့်ပါနှင့်။")
    whole, _, fraction = value.partition(".")
    amount = int(whole) * 100 + int(fraction.ljust(2, "0"))
    if amount <= 0:
        raise RuleError("Coin ပမာဏသည် 0 ထက်များရပါမယ်။")
    return amount


def usd_to_coins(value):
    value = value.strip()
    if value.startswith("$"):
        value = value[1:]
    if not re.fullmatch(r"[0-9]{1,9}(?:\.[0-9]{1,2})?", value):
        raise RuleError("USD ပမာဏကို 100 သို့ $100.50 ပုံစံရေးပါ။")
    whole, _, fraction = value.partition(".")
    usd_subunits = int(whole) * 100 + int(fraction.ljust(2, "0"))
    if usd_subunits <= 0:
        raise RuleError("USD ပမာဏသည် 0 ထက်များရပါမယ်။")
    return usd_subunits * USD_TO_COIN_RATE


def money(amount):
    whole, fraction = divmod(amount, 100)
    decimals = f".{fraction:02d}".rstrip("0") if fraction else ""
    return f"{whole}{decimals}coin"
