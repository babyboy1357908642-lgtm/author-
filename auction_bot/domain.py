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
PVP_DICE_PAYOUT_RULE = "tiered_v1"


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


def pvp_dice_sides(game):
    """Return the persisted low/high players; older rounds used requester/target."""
    low = game.get("low_player_id", game["requester_id"])
    if low not in (game["requester_id"], game["target_id"]):
        raise RuleError("PvP အံစာဘက် သတ်မှတ်ချက် မမှန်ပါ။")
    high = game["target_id"] if low == game["requester_id"] else game["requester_id"]
    return low, high


def pvp_dice_outcome(game):
    value = game.get("dice_value")
    if type(value) is not int or not 1 <= value <= 6:
        raise RuleError("PvP အံစာရလဒ် မမှန်ပါ။")
    low, high = pvp_dice_sides(game)
    prize = game["amount"] * 2
    if game.get("dice_payout_rule") == PVP_DICE_PAYOUT_RULE:
        # Integer subunits: round fractions down to the nearest 0.01 coin.
        prize = game["amount"] * (15, 17, 20)[(value - 1) % 3] // 10
    return (low if value <= 3 else high), prize
