import asyncio
import csv
import hashlib
import html
import io
import logging
import math
import re
import secrets
import time
from datetime import datetime, timezone

from telegram import (ReplyParameters, BotCommand, BotCommandScopeDefault, BotCommandScopeAllPrivateChats,
                      BotCommandScopeChat, MenuButtonCommands, InlineQueryResultCachedPhoto, InlineQueryResultCachedVideo, InlineQueryResultArticle, InputTextMessageContent, InlineKeyboardButton,
                      InlineKeyboardMarkup, MessageOriginChannel)
from telegram.error import BadRequest, RetryAfter, TelegramError
from telegram.ext import Application, CallbackQueryHandler, InlineQueryHandler, MessageHandler, filters

from . import account, welcome
from .config import Config
from .source_export import source_zip
from .domain import MAX_PVP_WAGER, MIN_PVP_WAGER, USD_TO_COIN_RATE, RuleError, cents, money, usd_to_coins
from .mongo_store import MongoStore
from pymongo.errors import PyMongoError

log = logging.getLogger(__name__)
POLLING_UPDATES = ["message", "callback_query", "inline_query"]
UPDATE_CONCURRENCY = 16
WORKER_TICK_INTERVAL_SECONDS = 0.5
GAME_COOLDOWN_SECONDS = 6.0
OWNER_ACTIONS = {
    "zip": "Bot code ZIP ယူရန်: /zip (Owner DM only)",
    "payout": "Bot ကပေးချေထားသော game payout စုစုပေါင်း: /payout (Owner only)",
    "new": "Card အသစ်တင်ရန်",
    "auctions": "လေလံစာရင်း",
    "view": "လေလံကြည့်ရန်: /view ID",
    "bids": "Bid မှတ်တမ်း: /bids ID",
    "close": "Winner သတ်မှတ်ပြီးပိတ်ရန်: /close ID",
    "cancelauction": "လေလံဖျက်သိမ်းရန်: /cancelauction ID",
    "extend": "အချိန်တိုးရန်: /extend ID minutes",
    "pause": "Bid အားလုံးခဏရပ်ရန်",
    "resume": "Bid ပြန်ဖွင့်ရန်",
    "setchannel": "Channel သတ်မှတ်ရန်: /setchannel -100…",
    "setgroup": "Discussion group သတ်မှတ်ရန်: /setgroup -100…",
    "increment": "လေလံအသစ်များအတွက် increment: /increment 250.00",
    "ban": "Bid ပိတ်ရန်: /ban USER_ID",
    "unban": "Bid ပြန်ဖွင့်ရန်: /unban USER_ID",
    "banned": "ပိတ်ထားသူစာရင်း",
    "stats": "စာရင်းချုပ်",
    "export": "Bid CSV ထုတ်ရန်: /export ID",
    "rules": "စည်းကမ်းကြည့်/ပြင်ရန်: /rules စည်းကမ်းစာသား",
    "settings": "လက်ရှိ settings",
    "check": "Channel/group ချိတ်ဆက်မှုစစ်ရန်",
    "welcome": "User /start ပုံ၊ စာ၊ buttons ပြင်ရန်",
    "auth": "Reply user message ဖြင့် +100/-100 ပို့ပါ (USD) သို့ /auth +$100 | ID: /auth USER_ID +$100",
    "credit": "USD credit: /credit USER_ID $100 note သို့ reply /credit +$100 note",
    "debit": "USD debit: /debit USER_ID $10 note သို့ reply /debit -$10 note",
    "wallet": "User coin wallet စစ်ရန်: /wallet USER_ID",
    "walletmode": "Bid ငွေကို ယာယီထိန်းထားရန်: /walletmode on",
}
OWNER_ONLY_COMMANDS = (set(OWNER_ACTIONS) - {"auctions", "rules"}) | {
    "panel", "help", "draftcancel", "welcomehelp", "welcomecancel",
}
USER_COMMANDS = [
    BotCommand("start", "Bot စတင်ရန်"),
    BotCommand("menu", "ကိုယ့်အကောင့် menu"),
    BotCommand("history", "နောက်ဆုံး လေလံမှတ်တမ်း 10 ခု"),
    BotCommand("wins", "ကိုယ်နိုင်ခဲ့သော လေလံများ"),
    BotCommand("auctions", "ဖွင့်ထားသော လေလံများ"),
    BotCommand("bal", "ကိုယ့် coin လက်ကျန်စစ်ရန်"),
    BotCommand("bcoin", "ကိုယ့် coin လက်ကျန်စစ်ရန်"),
    BotCommand("transactions", "Coin အဝင်အထွက်မှတ်တမ်း"),
]
AUCTION_GROUP_COMMANDS = [
    BotCommand("bid", "လေလံ comments မှာ /bid 10.50"),
    BotCommand("rules", "လေလံ comments မှာ စည်းကမ်းကြည့်ရန်"),
    BotCommand("bal", "ကိုယ့် coin လက်ကျန်စစ်ရန်"),
]
PVP_GROUP_COMMANDS = [
    BotCommand("bid", "Auction ID နဲ့ bid ဆွဲရန်: /bid AUCTION_ID 10.50"),
    BotCommand("pvp", "ပြိုင်ဘက် message ကို reply လုပ်ပြီး /pvp 250"),
    BotCommand("boom", "ပြိုင်ဘက်ကို Boom game စိန်ခေါ်ရန်"),
    BotCommand("replay", "Stuck ဖြစ်နေသော ကိုယ့် Solo game ကို refund/ရှင်းရန်"),
    BotCommand("btop", "Coin အများဆုံး Top 10"),
    BotCommand("bal", "ကိုယ့် coin လက်ကျန်စစ်ရန်"),
    BotCommand("bcoin", "သူ့ message ကို reply လုပ်ပြီး coin လက်ဆောင်ပို့ရန်"),
    BotCommand("dailycoin", "နေ့စဉ် coin reward ရယူရန်"),
]

PROMPTS = {
    "photo": "📷/🎥 Card photo သို့ video ပို့ပါ။ /draftcancel နဲ့ ရပ်နိုင်ပါတယ်။",
    "name": "Card name ရေးပါ (စာလုံး 60 အထိ)။",
    "anime": "Anime name ရေးပါ (စာလုံး 60 အထိ)။",
    "card_type": "Type ရေးပါ (စာလုံး 24 အထိ)။",
    "card_id": "Card ID ရေးပါ (စာလုံး 60 အထိ)။",
    "rarity": "Rarity ရေးပါ (ဥပမာ SSR, UR; စာလုံး 24 အထိ)။",
    "start": "Starting bid coin ပမာဏရေးပါ။ ဥပမာ 5.00",
    "duration_seconds": "တင်ပြီး ဘယ်လောက်ကြာရင် ပိတ်မလဲ? ကြာချိန်ကို စာပို့ပါ။ ဥပမာ 1sec, 5min, 1hours, 1day။ Publish တင်ပြီးမှ အချိန်စတွက်ပါမယ်။",
}
STEPS = list(PROMPTS)


AUTH_USAGE = "USD ကို coin အဖြစ်ပြောင်းရန် user message ကို reply လုပ်ပြီး /auth +$100 သို့ /auth -$5 ရေးပါ။ ID ဖြင့် /auth USER_ID +$100 သို့ /auth USER_ID -$5 ရေးနိုင်ပါတယ်။ Rate: $100 = 2500 coin."


def auth_adjustment(args, message):
    if not args:
        raise RuleError(AUTH_USAGE)
    if re.fullmatch(r"[0-9]{1,19}", args[0]):
        user_id = int(args[0])
        parts = args[1:]
    else:
        reply = message.reply_to_message
        user = reply.from_user if reply and not reply.sender_chat else None
        if not user or user.is_bot:
            raise RuleError("User ရဲ့ message ကို reply လုပ်ပါ။ Bot/channel/anonymous message ကို မသုံးနိုင်ပါ။ " + AUTH_USAGE)
        user_id = user.id
        parts = args
    if not 0 < user_id < 2**63:
        raise RuleError("မှန်ကန်သော numeric user ID ကို သုံးပါ။")
    if not parts:
        raise RuleError(AUTH_USAGE)
    if parts[0] in {"+", "-"}:
        if len(parts)<2:
            raise RuleError(AUTH_USAGE)
        sign, value, note = parts[0], parts[1], " ".join(parts[2:])
    elif parts[0].startswith(("+", "-")):
        sign, value, note = parts[0][0], parts[0][1:], " ".join(parts[1:])
    else:
        raise RuleError(AUTH_USAGE)
    amount = usd_to_coins(value)
    return user_id, amount if sign=="+" else -amount, note


def signed_owner_message_args(text):
    match = re.fullmatch(r"([+-])\s*(\$?[0-9]{1,9}(?:\.[0-9]{1,2})?)(?:\s+(.+))?", text.strip())
    if not match:
        return None
    return [match.group(1) + match.group(2)] + (match.group(3).split() if match.group(3) else [])


def duration_seconds(value):
    value = value.translate(str.maketrans("၀၁၂၃၄၅၆၇၈၉", "0123456789")).strip().lower()
    match = re.fullmatch(r"([0-9]{1,7})\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|မိနစ်|h|hr|hrs|hour|hours|d|day|days)?", value)
    if match:
        unit = match[2] or "min"  # Preserve older minute-only inputs.
        factor = 1 if unit.startswith("s") else 3600 if unit.startswith("h") else 86400 if unit.startswith("d") else 60
        seconds = int(match[1]) * factor
        if 1 <= seconds <= 604800:
            return seconds
    raise RuleError("ကြာချိန်ကို 1sec မှ 7days အတွင်းရေးပါ။ ဥပမာ 1sec, 5min, 1hours, 1day။")


def duration_text(seconds):
    remaining = max(0, math.ceil(seconds))
    parts = []
    for size, label in ((86400, "day"), (3600, "hour"), (60, "min"), (1, "sec")):
        count, remaining = divmod(remaining, size)
        if count:
            parts.append(f"{count}{label}")
    return " ".join(parts) or "0sec"


def button(text, data, style="primary"):
    # api_kwargs forwards the official Bot API style field with our pinned PTB.
    return InlineKeyboardButton(text, callback_data=data, api_kwargs={"style": style})


def date_text(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def pvp_name(user_id, name):
    return f'<a href="tg://user?id={user_id}">{html.escape(name or "Player")}</a>'


def usd_equivalent(coin_subunits):
    usd_subunits = coin_subunits // USD_TO_COIN_RATE
    whole, cents = divmod(usd_subunits, 100)
    return f"${whole}" if cents == 0 else f"${whole}.{cents:02d}"


def pvp_payouts(game):
    if game.get("mode") == "dice":
        return game["amount"] * 2, 0
    requester_percent = game["final_percent"]
    target_percent = 100 - requester_percent
    winner_id = game["requester_id"] if requester_percent > 50 else game["target_id"]
    loser_percent = target_percent if winner_id == game["requester_id"] else requester_percent
    pot = game["amount"] * 2
    loser_payout = 0 if loser_percent > 25 else pot * loser_percent // 100
    return pot - loser_payout, loser_payout


def pvp_animation_text(game):
    if game.get("mode") == "dice":
        text = (f'⚔️ <b>PvP · {money(game["amount"])} each</b>\n\n'
                f'🟦 {pvp_name(game["requester_id"], game["requester_name"])} — 1 , 2 , 3\n\n'
                f'🟥 {pvp_name(game["target_id"], game["target_name"])} — 4 , 5 , 6')
        if game["status"] == "finished":
            winner_name = game["requester_name"] if game["winner_id"] == game["requester_id"] else game["target_name"]
            text += (f'\n\n🏆 Winner: {pvp_name(game["winner_id"], winner_name)}'
                     f'\n🎲Result : {game["dice_value"]}\n🪙 Prize: {money(game["amount"] * 2)}')
        elif game["status"] == "cancelled":
            text += f'\n\n❌ အံစာရလဒ် မရသဖြင့် ပွဲပယ်ဖျက်ပြီး တစ်ယောက်စီ {money(game["amount"])} ပြန်အမ်းပြီးပါပြီ။'
        else:
            text += "\n\n🎲 အံစာလှိမ့်နေပါတယ်…"
        return text
    first = game.get("final_percent") if game.get("final_percent") is not None else 50
    if game["status"] == "pending":
        shown = 50
    elif game["status"] == "finished":
        shown = first
    else:
        step = game["step"]
        digest = hashlib.sha256(f'{game["id"]}:{step}'.encode()).digest()
        shown = 10 + (digest[0] % 81)
    if game.get("mode") == "solo":
        labels = {"heads": "Heads", "tails": "Tails", "higher": "Heads", "lower": "Tails"}
        choice = labels.get(game.get("choice"), "Heads")
        result = labels.get(game.get("result"), "Heads")
        won = game.get("winner_id") == game.get("requester_id")
        text = (f'⚔️ <b>PvP · {money(game["amount"])} each</b>\n\n'
                f'😈 {pvp_name(game["requester_id"], game["requester_name"])}\n\n'
                f'🎯 Your Choice — <b>{choice}</b>\n'
                f'🪙 Result — <b>{result}</b>')
        if game["status"] == "finished":
            if won:
                text += f'\n\n🏆 Winner: {pvp_name(game["requester_id"], game["requester_name"])}\n🪙 Prize: {money(game.get("prize", game["amount"] * 2))}'
            else:
                text += '\n\n❌ You lose\n🪙 Prize: 0coin'
            if game.get("refund"):
                text += f'\n↩️ Refund: {money(game["refund"])}'
        return text
    second = 100 - shown
    filled = max(1, min(12, math.ceil(shown / 100 * 12)))
    bar = "🟦" * filled + "🟥" * (12 - filled)
    text = (f'⚔️ <b>PvP · {money(game["amount"])} each</b>\n\n'
            f'🟦 {pvp_name(game["requester_id"], game["requester_name"])} — <b>{shown}%</b>\n\n'
            f'{bar}\n\n'
            f'🟥 {pvp_name(game["target_id"], game["target_name"])} — <b>{second}%</b>')
    if game["status"] == "finished":
        winner = game["requester_id"] if game["winner_id"] == game["requester_id"] else game["target_id"]
        winner_name = game["requester_name"] if winner == game["requester_id"] else game["target_name"]
        winner_payout, loser_payout = pvp_payouts(game)
        text += f'\n\n🏆 Winner: {pvp_name(winner, winner_name)}\n🪙 Prize: {money(winner_payout)}'
        if loser_payout:
            text += f'\n↩️ Refund: {money(loser_payout)}'
    else:
        text += "\n\nလောင်းကြေးကို ဖယ်ထားပြီး ပွဲပြီးချိန်မှာ အနိုင်ရသူကို ဆုငွေပေးပါမယ်။"
    return text


def boom_markup(game):
    rows=[]
    size=game.get("board_size") or 6
    revealed=set(game.get("revealed", []))
    for start in range(1,size+1,3):
        row=[]
        for number in range(start,min(start+3,size+1)):
            label="❄️" if number in revealed else str(number)
            if game.get("status") == "finished" and number in game.get("boom_positions", []):
                if game.get("mode") == "solo":
                    label="🟥"
                else:
                    owner=game.get("boom_owners",{}).get(str(number))
                    label="🟦" if owner == game["requester_id"] else "🟥"
            row.append(InlineKeyboardButton(label, callback_data=f'boom:pick:{game["id"]}:{number}'))
        rows.append(row)
    if game.get("mode") == "solo" and game.get("status") == "running":
        rows.append([button("💵 Cash", f'boom:cash:{game["id"]}', "success")])
    return InlineKeyboardMarkup(rows)


def solo_boom_prize(amount, safe_count):
    # Solo Boom uses the wager as base: Safe 1 = 1.2x, Safe 2 = 1.4x.
    return (amount * (10 + 2 * safe_count) + 5) // 10


def boom_text(game):
    if game.get("mode") == "solo":
        text=(f'💣 <b>Boom · {money(game["amount"])} each</b>\n\n'
              f'🟦 {pvp_name(game["requester_id"],game["requester_name"])}\n'
              f'🟥 BOOM\n\n')
        safe_count=len(game.get("revealed", []))
        prize=game.get("cash_prize", solo_boom_prize(game["amount"], safe_count))
        if game.get("status") == "finished":
            if game.get("winner_id") == game.get("requester_id"):
                return text + (f'🏆 Winner: {pvp_name(game["requester_id"],game["requester_name"])}\n'
                               f'🪙 Win Prize: {money(prize)}\n'
                               f'🫆 Last Click - {(game.get("last_click") or "-")}\n❄️ Safe - {safe_count}')
            if game.get("timeout"):
                return text + (f'⏱️ 1min အတွင်း button မနှိပ်သဖြင့် အလိုအလျောက် ရှုံးပါပြီ။\n'
                               f'🪙 Win Prize: 0coin\n❄️ Safe - {safe_count}')
            return text + (f'💥 Boom!\n🪙 Win Prize: 0coin\n'
                           f'🫆 Last Click - {(game.get("last_click") or "-")}\n❄️ Safe - {safe_count}')
        return text + (f'🪙 Now Win Prize: {money(prize)}\n'
                       f'🫆 Last Click - {(game.get("last_click") or "-")}\n❄️ Safe - {safe_count}')
    text=(f'💣 <b>Boom · {money(game["amount"])} each</b>\n\n'
          f'🟦 {pvp_name(game["requester_id"],game["requester_name"])}\n'
          f'🟥 {pvp_name(game["target_id"],game["target_name"])}\n\n')
    if game.get("status") == "finished":
        winner=game.get("winner_id")
        name=game["requester_name"] if winner==game["requester_id"] else game["target_name"]
        timeout_note='\n⏱️ 1min မနှိပ်သဖြင့် auto win' if game.get("timeout") else ''
        return text + f'🏆 Winner: {pvp_name(winner,name)}\n🪙 Prize: {money(game["amount"]*2)}{timeout_note}\n🫆 Last Click - {game.get("last_click", "-")}'
    turn=game.get("turn_id")
    name=game["requester_name"] if turn==game["requester_id"] else game["target_name"]
    return text + f'🎯 Turn: {pvp_name(turn,name)}\nButton တစ်ခုရွေးပါ။'


def caption(row, now=None):
    esc = html.escape
    status = row.get("status", "active")
    label = {"active": "🟢OPEN FOR BIDS", "publishing": "🟢OPEN FOR BIDS", "closed": "🏁AUCTION ENDED", "cancelled": "🚫AUCTION CANCELLED"}[status]
    current = money(row["highest"]) if row.get("highest") is not None else "No bids yet"
    winner = "—"
    if row.get("winner_id"):
        winner = f'<a href="tg://user?id={row["winner_id"]}">{esc(row["winner_name"])}</a>'
    result = f"👑HIGHEST BIDDER\n{winner}"
    if status == "closed":
        result = f"🏆WINNER\n{winner}" if row.get("winner_id") else "🏆WINNER\nNo winner — no bids"
    if status == "cancelled":
        result = "🏆WINNER\nNone — auction cancelled"
    if status in {"closed", "cancelled"}:
        end_text = "0sec"
    elif row.get("duration_seconds") and (row.get("step") == "confirm" or status == "publishing"):
        end_text = duration_text(row["duration_seconds"])
    else:
        end_text = duration_text(row["ends"] - (time.time() if now is None else now))
    payment = "Auction ပြီးဆုံးပြီး သင့် Guess harem ထဲ 3min အတွင်း ကဒ်ဝင်လာပါလိမ့်မယ်။"
    if row.get("wallet_required"):
        payment += "\n" + ("Winner wallet မှ ငွေဖြတ်ပြီး။" if status == "closed" and row.get("highest") is not None else "Winner wallet မှ ငွေဖြတ်ပါမယ်။")
    if status == "cancelled" or (status == "closed" and not row.get("winner_id")):
        payment = "အနိုင်ရသူမရှိသဖြင့် ကဒ်ပေးပို့မှု မရှိပါ။"
    bid_help = '💬PLACE YOUR BID\nComment မှာ အောက်ပါပုံစံအတိုင်း တင်ပါ။\n\n<code>/bid 10.50</code>\n\n' if status in {"active", "publishing"} else ""
    return (
        f'🎴WAIFU AUCTION #{row["id"]}\n{label}\n\n'
        f'┌─CHARACTER─────\n│ 👤 {esc(row["name"])}\n'
        f'│ 🎬 Anime: {esc(row["anime"])}\n│ 🏷 Type: {esc(row.get("card_type", ""))}\n│ 🆔 Card ID: {esc(row.get("card_id", ""))}\n│ 💎 Rarity: {esc(row["rarity"])}\n└──────────────────\n\n'
        f'🪙STARTING BID\n{money(row["start"])}\n\n📈MIN. INCREMENT\n{money(row["increment"])}\n\n'
        f'🔥CURRENT BID\n{current}\n\n{result}\n\n'
        f'⏳TIME LEFT\n{end_text}\n\n━━━━━━━━━━━━━━━━━━\n\n'
        f'{bid_help}🪙 Currency: Coin\n\n🤝PAYMENT\n{payment}'
    )


class AuctionBot:
    def __init__(self, config):
        self.config = config
        if not config.mongodb_uri:
            raise ValueError("MONGODB_URI is required; SQLite is not supported by the bot runtime.")
        self.store = MongoStore(config.mongodb_uri, config.mongodb_database)
        # Environment IDs are authoritative.  This lets a deployment move to a
        # new group/channel instead of silently reusing stale MongoDB settings.
        for key, value in [("channel_id", config.channel_id), ("group_id", config.group_id)]:
            if value and self.store.get(key) != value:
                self.store.set(key, value)
        self.channel_id = str(self.store.get("channel_id") or "")
        self.group_id = str(self.store.get("group_id") or "")
        # GROUP_ID is the single discussion/game group.  Ignore any legacy
        # pvp_group_id setting so old deployments cannot split the commands.
        self.pvp_group_id = self.group_id
        self.edit_after = {}
        self.last_bid_at = {}
        self.last_caption_at = {}
        self.bid_edit_due = {}
        self.winner_retry_after = {}
        self.pvp_slot_retry_after = {}
        self.button_cooldown_until = {}
        self.game_cooldown_until = {}
        self.global_edit_after = 0
        self.pvp_edit_after = 0
        self.pvp_render_retry = {}
        self.pvp_dice_retry_after = {}
        self.tick_lock = asyncio.Lock()

    async def store_call(self, operation, *args, **kwargs):
        """Keep synchronous MongoDB I/O off the asyncio event loop; SQLite stays local."""
        if isinstance(self.store, MongoStore):
            return await asyncio.to_thread(operation, *args, **kwargs)
        return operation(*args, **kwargs)

    def button_cooldown(self, user):
        """Return remaining seconds for callback clicks, then arm a six-second cooldown."""
        if not user or user.is_bot:
            return 0.0
        now = time.monotonic()
        until = self.button_cooldown_until.get(user.id, 0.0)
        if until > now:
            return until - now
        self.button_cooldown_until[user.id] = now + GAME_COOLDOWN_SECONDS
        if len(self.button_cooldown_until) > 2048:
            self.button_cooldown_until = {
                uid: expiry for uid, expiry in self.button_cooldown_until.items()
                if expiry > now
            }
        return 0.0
    def game_cooldown(self, user):
        """Return remaining game-start cooldown without consuming it."""
        if not user or user.is_bot:
            return 0.0
        now = time.monotonic()
        until = self.game_cooldown_until.get(user.id, 0.0)
        if until > now:
            return until - now
        return 0.0
    def arm_game_cooldown(self, user):
        if user and not user.is_bot:
            self.game_cooldown_until[user.id] = time.monotonic() + GAME_COOLDOWN_SECONDS

    def owner(self, update):
        return bool(update.effective_user and update.effective_user.id in self.config.owners
                    and update.effective_chat and update.effective_chat.type == "private")

    def group(self, update):
        return bool(update.effective_chat and update.effective_chat.type == "supergroup"
                    and str(update.effective_chat.id) == self.group_id)

    def pvp_group(self, update):
        return bool(update.effective_chat and update.effective_chat.type == "supergroup"
                    and self.pvp_group_id
                    and str(update.effective_chat.id) == self.pvp_group_id)

    async def panel(self, message):
        positive = {"new", "resume", "unban", "welcome", "credit", "auth"}
        destructive = {"close", "cancelauction", "pause", "ban", "debit"}
        buttons = [button(f"{i}. {key}", f"action:{key}",
                          "success" if key in positive else "danger" if key in destructive else "primary")
                   for i, key in enumerate(OWNER_ACTIONS, 1)]
        await message.reply_text(f"👑 Owner Panel — လုပ်ဆောင်ချက် {len(OWNER_ACTIONS)}\n/new — Card အသစ် | /welcome — User /start ပြင်ရန်",
                                 reply_markup=InlineKeyboardMarkup([buttons[i:i+2] for i in range(0, len(buttons), 2)]))

    async def linked(self, bot):
        channel_id, group_id = self.channel_id, self.group_id
        if not channel_id or not group_id:
            raise RuleError("/setchannel နှင့် /setgroup အရင်သတ်မှတ်ပါ။")
        channel = await bot.get_chat(int(channel_id))
        group = await bot.get_chat(int(group_id))
        if channel.type != "channel" or group.type != "supergroup" or channel.linked_chat_id != group.id or group.linked_chat_id != channel.id:
            raise RuleError("Telegram Channel Settings > Discussion မှာ ဒီ group ကို ချိတ်ထားရပါမယ်။")
        # Resolve the bot identity from Telegram before checking membership.  The
        # Bot.id property can be unset/stale before the application has completed
        # initialization, which made an already-promoted bot look like a member.
        bot_user = await bot.get_me()
        member = await bot.get_chat_member(channel.id, bot_user.id)
        if member.status not in ("administrator", "creator") or (member.status == "administrator" and not member.can_post_messages):
            raise RuleError("Bot ကို channel admin + Post Messages permission ပေးပါ။")
        member = await bot.get_chat_member(group.id, bot_user.id)
        if member.status not in ("administrator", "creator"):
            raise RuleError(f"Bot ကို discussion group admin ပေးပါ။ (Telegram status: {member.status})")

    async def remember(self, message):
        """Trust only Telegram automatic forwards from the configured channel."""
        if not message or str(message.chat_id) != self.group_id:
            return
        origin = message.forward_origin
        if message.is_automatic_forward and isinstance(origin, MessageOriginChannel) and str(origin.chat.id) == self.channel_id:
            row = await self.store_call(self.store.find_post, origin.chat.id, origin.message_id, message.chat_id)
            auction_id = row["id"] if row else None
            if auction_id is None:
                # Recover a sendPhoto whose successful response was lost in transit.
                match = re.match(r"🎴\s*WAIFU AUCTION #(\d+)\n", message.caption or "", re.IGNORECASE)
                if match:
                    candidate = await self.store_call(self.store.recoverable, int(match[1]), origin.chat.id, message.chat_id)
                    media_id = message.photo[-1].file_id if message.photo else message.video.file_id if message.video else None
                    if candidate and media_id and candidate["photo"] == media_id:
                        auction_id = candidate["id"]
            if auction_id:
                await self.store_call(self.store.attach, auction_id, origin.message_id,
                                      message.message_id, int(origin.date.timestamp()))

    async def resolve(self, message):
        reply=message.reply_to_message
        # Forwarded/inline copies are not authoritative auction comment roots.
        for candidate in (message,reply):
            if candidate and (candidate.via_bot or (candidate.forward_origin and not candidate.is_automatic_forward)):
                return None
        await self.remember(message)
        await self.remember(reply)
        thread=message.message_thread_id
        reply_auction=await self.store_call(self.store.resolve, message.chat_id, [reply.message_id]) if reply else None
        if thread:
            auction_id=await self.store_call(self.store.thread_auction, message.chat_id, thread)
            if not auction_id:
                return None
            if reply_auction and reply_auction!=auction_id:
                return None
            if reply and reply.message_thread_id and reply.message_thread_id!=thread:
                return None
        else:
            auction_id=reply_auction
            if reply and reply.message_thread_id:
                if await self.store_call(self.store.thread_auction, message.chat_id, reply.message_thread_id)!=auction_id:
                    return None
        if auction_id:
            await self.store_call(self.store.map_message, message.chat_id, message.message_id, auction_id)
        return auction_id

    async def inline_search(self, update, context):
        query=update.inline_query
        try:
            offset=int(query.offset or "0")
            rows,more=await self.store_call(self.store.search_active, query.query, offset=offset)
        except (RuleError,ValueError,PyMongoError):
            await query.answer([],cache_time=0,is_personal=True)
            return
        results=[]
        for row in rows:
            url=account.post_link(row)
            if not url:
                continue
            minimum=row["start"] if row["highest"] is None else row["highest"]+row["increment"]
            # Inline copies are browsing snapshots. Only the original post accepts bids.
            text=(f'🌸 {html.escape(row["name"])}\n\n'
                  f'📺 {html.escape(row["anime"])}\n'
                  f'💎 ⚜️ {html.escape(row["rarity"])}\n'
                  f'🆔 {row["id"]}\n'
                  f'🎴Start Bid - {money(row["start"])}')
            result_kwargs = dict(
                id=str(row["id"]), title=f'#{row["id"]} · {row["name"]}',
                description=f'{row["anime"]} · {row["rarity"]} · Next bid {money(minimum)}',
                caption=text, parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💬 Open original post · Comments",url=url,api_kwargs={"style":"primary"})]]))
            if row.get("media_type", "photo") == "video":
                results.append(InlineQueryResultCachedVideo(video_file_id=row["photo"], **result_kwargs))
            else:
                results.append(InlineQueryResultCachedPhoto(photo_file_id=row["photo"], **result_kwargs))
        if not results and offset == 0:
            text = ("ရှာထားတဲ့ကဒ်နဲ့ ကိုက်ညီတဲ့ လေလံ မတွေ့ပါ။" if query.query.strip()
                    else "လက်ရှိ လေလံတင်ထားတဲ့ကဒ် မရှိသေးပါ။")
            results.append(InlineQueryResultArticle(
                id="no-auctions",title="🔎 လေလံ မတွေ့ပါ။" if query.query.strip() else "🎴 လေလံ မရှိသေးပါ။",
                description=text,input_message_content=InputTextMessageContent(text)))
        await query.answer(results,cache_time=0,is_personal=True,
                           next_offset=str(offset+len(rows)) if more else "")

    async def message(self, update, context):
        # Edited bids never become fresh bids. Other groups receive no response.
        if update.message is None:
            return
        message = update.message
        private_user = bool(update.effective_chat and update.effective_chat.type == "private"
                            and update.effective_user and not update.effective_user.is_bot)
        if not self.owner(update) and not self.group(update) and not self.pvp_group(update) and not private_user:
            return
        if message.sender_chat:
            if self.group(update):
                await self.remember(message)
            return
        text = message.text or ""
        words = text.split()
        command = words[0].split("@")[0][1:].lower() if text.startswith("/") else ""
        if command and "@" in words[0] and words[0].split("@", 1)[1].lower() != context.bot.username.lower():
            return
        args = words[1:]
        is_owner = bool(update.effective_user and update.effective_user.id in self.config.owners)
        if not is_owner and (command in OWNER_ONLY_COMMANDS or (command == "rules" and args)):
            return
        try:
            if command == "auth":
                if (update.effective_user and update.effective_user.id in self.config.owners
                        and (self.owner(update) or self.group(update) or self.pvp_group(update))):
                    await self.auth_command(args, message, update.effective_user.id, context.bot)
                return
            if (is_owner and not command and message.reply_to_message
                    and (self.owner(update) or self.group(update) or self.pvp_group(update))
                    and not context.user_data.get("draft")
                    and not context.user_data.get("welcome_edit")):
                signed_args = signed_owner_message_args(text)
                if signed_args:
                    await self.auth_command(signed_args, message, update.effective_user.id, context.bot)
                    return
            if self.owner(update):
                if context.user_data.get("welcome_edit") and command not in set(OWNER_ACTIONS) | {"welcomecancel", "welcomehelp", "panel", "start", "help", "draftcancel"}:
                    await self.welcome_input(message, update.effective_user, context)
                elif command:
                    await self.owner_command(command, args, message, context)
                elif context.user_data.get("draft"):
                    await self.draft(message, context)
                return
            if private_user:
                if command == "start":
                    if getattr(self, "pvp_group_id", ""):
                        try:
                            is_new_user = await self.store_call(
                                self.store.claim_new_user, update.effective_user.id)
                            if is_new_user:
                                await context.bot.send_message(
                                    chat_id=int(self.pvp_group_id),
                                    text=(f'🆕 New user - {pvp_name(update.effective_user.id, update.effective_user.full_name)} '
                                          f'(ID: <code>{update.effective_user.id}</code>)'),
                                    parse_mode="HTML")
                        except TelegramError:
                            log.warning("New-user notification failed for user %s", update.effective_user.id)
                        except PyMongoError:
                            log.warning("New-user notification state unavailable for user %s", update.effective_user.id)
                    value = await self.store_call(welcome.load, self.store)
                    await welcome.send(message, value, update.effective_user, context.bot)
                elif command in {"menu", "history", "wins", "auctions", "balance", "bal", "bcoin", "transactions"}:
                    await self.user_command(command, args, message, update.effective_user)
                return
            if self.pvp_group(update) and command in {"dailycoin", "pvp", "boom", "replay", "btop", "bal", "bcoin"}:
                if command == "dailycoin":
                    await self.user_command(command, args, message, update.effective_user)
                elif command == "pvp":
                    await self.pvp_request(args, message, update.effective_user)
                elif command == "boom":
                    await self.boom_request(args, message, update.effective_user)
                elif command == "replay":
                    await self.replay_command(args, message, update.effective_user)
                elif command == "btop":
                    await self.btop_command(args, message, context)
                elif command == "bal":
                    if args:
                        raise RuleError("Group မှာ /bal ကို argument မပါဘဲ သုံးပါ။")
                    if not update.effective_user or update.effective_user.is_bot:
                        return
                    row = await self.store_call(self.store.wallet_balance, update.effective_user.id)
                    text = f"🪙 Coin balance\n\nAvailable: {money(row['available'])}"
                    if row["available"] == 0:
                        text += "\nမွဲပြီလေ🤣"
                    await message.reply_text(text)
                elif command == "bcoin":
                    await self.pvp_gift_command(args, message, update.effective_user)
                return
            if command == "bal":
                if not update.effective_user or update.effective_user.is_bot:
                    return
                if args:
                    raise RuleError("Group မှာ /bal ကို argument မပါဘဲ သုံးပါ။")
                row = await self.store_call(self.store.wallet_balance, update.effective_user.id)
                await message.reply_text(f"Guess Author Bal\n\nBal - {money(row['available'])}")
                return
            if command not in {"bid", "rules"}:
                return
            pvp_bid = command == "bid" and self.pvp_group(update) and not self.group(update)
            if pvp_bid:
                if len(args) != 2 or not re.fullmatch(r"[0-9]{1,19}", args[0]):
                    raise RuleError("PvP group မှာ /bid AUCTION_ID 10.50 ပုံစံရေးပါ။")
                auction_id = int(args[0])
            else:
                auction_id = await self.resolve(message)
            if command == "rules" and auction_id:
                await message.reply_text(await self.store_call(self.store.get, "rules"))
                return
            if command != "bid":
                return
            if not update.effective_user or update.effective_user.is_bot:
                raise RuleError("User account နဲ့ပဲ bid ဆွဲနိုင်ပါတယ်။")
            if not pvp_bid and len(args) != 1:
                raise RuleError("ဒီ card ရဲ့ Comments ထဲမှာ /bid 10.50 ပုံစံရေးပါ။")
            if not auction_id:
                raise RuleError("လေလံ post ရဲ့ Comments ထဲဝင်ပြီး post ကို reply လုပ်ပါ။")
            amount = cents(args[1] if pvp_bid else args[0])
            name = update.effective_user.full_name[:60]
            accepted = await self.store_call(self.store.bid, auction_id, update.effective_user.id,
                                             name, amount, message.chat_id, message.message_id)
            if accepted:
                self.note_bid(auction_id)
                receipt = await message.reply_text(
                    f"☑ Author #{auction_id} —\n\n"
                    f"{update.effective_user.mention_html()}\n\n"
                    f"{money(amount)} bid ဖြင့် လေလံဆွဲထားပါပီ",
                    parse_mode="HTML",
                )
                await self.store_call(self.store.map_message, message.chat_id, receipt.message_id, auction_id)
        except (RuleError, ValueError) as exc:
            await message.reply_text(str(exc) if isinstance(exc, RuleError) else "Command parameter မှားနေပါတယ်။ /panel မှာ အသုံးပြုပုံကြည့်ပါ။")
        except PyMongoError:
            await message.reply_text("Database ယာယီမရနိုင်ပါ။ ခဏစောင့်ပြီး /bal နှင့် /history မှာ စစ်ပါ။")
        except TelegramError:
            log.warning("Telegram operation failed; durable auction state retained")
            await message.reply_text("Telegram request မအောင်မြင်ပါ။ /check နှင့် /auctions ကိုစစ်ပါ။")

    async def wallet_credit_notification(self, bot, user_id, delta, available):
        if delta <= 0:
            return True
        text = f"🪙 USD {usd_equivalent(delta)} ကနေ {money(delta)} ပြောင်းထည့်ပေးထားပါတယ်။\nလက်ရှိသုံးနိုင် coin: {money(available)}"
        try:
            await bot.send_message(chat_id=user_id, text=text)
        except TelegramError:
            return False
        return True

    async def auth_command(self, args, message, actor_id, bot):
        user_id, delta, note = auth_adjustment(args, message)
        changed = await self.store_call(self.store.adjust_wallet, user_id, delta, actor_id,
            f"owner:{message.chat_id}:{message.message_id}", note)
        available = (await self.store_call(self.store.wallet_balance, user_id))["available"]
        notified = await self.wallet_credit_notification(bot, user_id, delta, available) if changed else True
        if changed:
            operation = "ထည့်" if delta>0 else "နုတ်"
            text = f"✅ User {user_id} အတွက် USD {usd_equivalent(abs(delta))} ({money(abs(delta))}) {operation}ပြီးပါပြီ။"
            if not notified:
                text += "\n⚠️ User ကို DM မပို့နိုင်ပါ။ သူ့ bot DM မှာ /start လုပ်ထားကြောင်းစစ်ပါ။"
        else:
            text = "ဒီ request ကို အရင်က လုပ်ပြီးပါပြီ။ ထပ်မံငွေမပြောင်းပါ။"
        # Group confirmations don't publish a user's full wallet balance.
        if message.chat.type == "private":
            text += f'\nAvailable: {money(available)}'
        await message.reply_text(text)

    def owner_wallet_adjustment(self, command, args, message):
        if not args:
            raise RuleError(OWNER_ACTIONS[command])
        if args and re.fullmatch(r"[0-9]{1,19}", args[0]) and len(args) >= 2 and not args[1].startswith(("+", "-")):
            user_id = int(args[0])
            amount = usd_to_coins(args[1])
            delta = amount if command == "credit" else -amount
            return user_id, delta, " ".join(args[2:])
        return auth_adjustment(args, message)

    async def pvp_gift_command(self, args, message, user):
        if not user or user.is_bot:
            raise RuleError("Telegram user account နဲ့ပဲ coin gift ပို့နိုင်ပါတယ်။")
        if len(args) != 1:
            raise RuleError("လက်ဆောင်လက်ခံမယ့် user ရဲ့ message ကို reply လုပ်ပြီး /bcoin 100 ပုံစံရေးပါ။")
        reply = message.reply_to_message
        target = reply.from_user if reply and not reply.sender_chat else None
        if not target or target.is_bot:
            raise RuleError("Coin gift လက်ခံမယ့် user ရဲ့ message ကို reply လုပ်ပါ။")
        if target.id == user.id:
            raise RuleError("ကိုယ့်ကိုယ်ကို coin gift ပို့လို့မရပါ။")
        amount = cents(args[0])
        event_key = f"pvp-gift:{message.chat_id}:{message.message_id}"
        changed = await self.store_call(self.store.transfer_coins, message.chat_id,
                                        user.id, target.id, amount, event_key)
        if changed:
            await message.reply_text(f"🎁 {money(amount)} ကို {target.full_name} ဆီ လက်ဆောင်ပို့ပြီးပါပြီ။")
        else:
            await message.reply_text("ဒီ coin gift ကို အရင်က လုပ်ပြီးပါပြီ။ ထပ်မံလွှဲ၍မရပါ။")

    async def pvp_request(self, args, message, user):
        if not user or user.is_bot:
            raise RuleError("Telegram user account နဲ့ပဲ PvP ကစားနိုင်ပါတယ်။")
        remaining = self.game_cooldown(user)
        if remaining:
            raise RuleError(f"တစ်ပွဲပြီးပါပြီ။ {remaining:.1f} sec စောင့်ပြီးမှ ထပ်ကစားပါ။")
        reply = message.reply_to_message
        target = reply.from_user if reply and not reply.sender_chat else None
        if len(args) != 1:
            raise RuleError("ပြိုင်ဘက်ရဲ့ message ကို reply လုပ်ပြီး /pvp 250 သို့မဟုတ် ပိုများသော coin ပမာဏရေးပါ။")
        if not target or target.is_bot:
            raise RuleError("PvP လုပ်မယ့် user ရဲ့ message ကို reply လုပ်ပါ။")
        if target.id == user.id:
            raise RuleError("ကိုယ့်ကိုယ်ကို PvP request လုပ်လို့မရပါ။")
        amount = cents(args[0])
        if not MIN_PVP_WAGER <= amount <= MAX_PVP_WAGER:
            raise RuleError("PvP လောင်းကြေးကို 250 မှ 30000 coin အတွင်းထားပါ။")
        game_id = secrets.token_hex(8)
        game = await self.store_call(self.store.create_pvp, game_id, message.chat_id,
                                     user.id, user.full_name, target.id, target.full_name, amount)
        markup = InlineKeyboardMarkup([[
            button("✅ Confirm", f"pvp:confirm:{game_id}", "success"),
            button("❌ Cancel", f"pvp:cancel:{game_id}", "danger"),
        ]])
        text = (f'⚔️ PvP စိန်ခေါ်မှု\n\n🟦 {pvp_name(user.id,user.full_name)} — 1 , 2 , 3\n'
                f'🪙 လောင်းကြေး: <b>{money(amount)}</b> တစ်ယောက်စီ\n'
                f'🟥 {pvp_name(target.id,target.full_name)} — 4 , 5 , 6\n\n'
                f'{pvp_name(target.id,target.full_name)} က Confirm Waiting။ 15sec အတွင်း မနှိပ်ပါက ပွဲပယ်ပါမယ်။ '
                '___________________________')
        try:
            posted = await message.reply_text(text, parse_mode="HTML", reply_markup=markup)
            await self.store_call(self.store.set_pvp_message, game["id"], posted.message_id)
            self.arm_game_cooldown(user)
        except Exception:
            try:
                await self.store_call(self.store.cancel_pvp, game["id"], user.id)
            except Exception:
                pass
            raise

    async def boom_request(self, args, message, user):
        if not user or user.is_bot:
            raise RuleError("Telegram user account နဲ့ပဲ Boom ကစားနိုင်ပါတယ်။")
        remaining = self.game_cooldown(user)
        if remaining:
            raise RuleError(f"တစ်ပွဲပြီးပါပြီ။ {remaining:.1f} sec စောင့်ပြီးမှ ထပ်ကစားပါ။")
        if len(args) != 1:
            raise RuleError("ပြိုင်ဘက် message ကို reply လုပ်ပြီး /boom 250 ပုံစံရေးပါ။")
        reply=message.reply_to_message
        target=reply.from_user if reply and not reply.sender_chat else None
        amount=cents(args[0])
        if not MIN_PVP_WAGER <= amount <= MAX_PVP_WAGER:
            raise RuleError("Boom လောင်းကြေးကို 250 မှ 30000 coin အတွင်းထားပါ။")
        if not target:
            raise RuleError("Boom လုပ်မယ့် user ရဲ့ message ကို reply လုပ်ပါ။")
        if target.is_bot:
            raise RuleError("Bot message ကို reply လုပ်ပြီး Boom မကစားနိုင်ပါ။")
        if target.id == user.id: raise RuleError("ကိုယ့်ကိုယ်ကို Boom request လုပ်လို့မရပါ။")
        game_id=secrets.token_hex(8)
        game=await self.store_call(self.store.create_boom,game_id,message.chat_id,user.id,user.full_name,target.id,target.full_name,amount)
        markup=InlineKeyboardMarkup([[
            button("✅ Confirm",f"boom:confirm:{game_id}","success"),
            button("❌ Cancel",f"boom:cancel:{game_id}","danger")]])
        text=(f'💣 Boom စိန်ခေါ်မှု\n\n{pvp_name(user.id,user.full_name)}\n'
              f'🪙 လောင်းကြေး: <b>{money(amount)}</b> တစ်ယောက်စီ\n'
              f'ပြိုင်ဘက်: {pvp_name(target.id,target.full_name)}\n\n'
              f'{pvp_name(target.id,target.full_name)} က Confirm Waiting။ 15sec အတွင်း မနှိပ်ပါက ပွဲပယ်ပါမယ်။')
        try:
            posted=await message.reply_text(text,parse_mode="HTML",reply_markup=markup)
            await self.store_call(self.store.set_boom_message,game_id,posted.message_id)
            self.arm_game_cooldown(user)
        except Exception:
            try: await self.store_call(self.store.cancel_boom,game_id,user.id)
            except Exception: pass
            raise

    async def replay_command(self, args, message, user):
        if not user or user.is_bot:
            raise RuleError("Telegram user account နဲ့ပဲ /replay သုံးနိုင်ပါတယ်။")
        if args:
            raise RuleError("/replay ကို argument မပါဘဲ သုံးပါ။")
        games = await self.store_call(self.store.replay_user, message.chat_id, user.id)
        if not games:
            await message.reply_text("ရှင်းရန် stuck ဖြစ်နေတဲ့ ကိုယ့် Solo PvP/Boom ပွဲ မတွေ့ပါ။")
            return
        refunded = sum(game.get("amount", 0) for game in games)
        await message.reply_text(
            f"✅ Stuck Solo game {len(games)} ပွဲကို ရှင်းပြီး {money(refunded)} refund ပြန်ထည့်ပေးပါပြီ။\n"
            "အခု /boom သို့ /pvp နဲ့ ပြန်ကစားနိုင်ပါပြီ။")

    async def btop_command(self, args, message, context):
        if args: raise RuleError("/btop ကို argument မပါဘဲ သုံးပါ။")
        rows=await self.store_call(self.store.boom_top,10)
        lines=["🏆 Coin Top 10"]
        for index,row in enumerate(rows,1):
            try:
                member=await context.bot.get_chat_member(message.chat_id,row["user_id"])
                name=member.user.full_name
            except TelegramError:
                name=f'ID {row["user_id"]}'
            lines.append(f'{index}. {html.escape(name)} > {money(row["balance"])}')
        await message.reply_text("\n".join(lines),parse_mode="HTML")

    async def auther_command(self, message, context):
        row = next((item for item in await self.store_call(self.store.listing, 30)
                    if item.get("post_id")), None)
        if not row:
            raise RuleError("Inline search ခလုတ်တပ်ရန် လေလံပုံ မရှိသေးပါ။")
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔎 Inline search", switch_inline_query_current_chat="",
                                 api_kwargs={"style":"primary"})
        ]])
        await context.bot.edit_message_reply_markup(chat_id=row["channel_id"],
                                                     message_id=row["post_id"],
                                                     reply_markup=markup)
        await message.reply_text(f'✅ နောက်ဆုံးလေလံပုံ #{row["id"]} အောက်မှာ Inline search ခလုတ်တပ်ပြီးပါပြီ။')

    async def author_command(self, message):
        text, markup = await self.store_call(
            account.active, self.store, 0, inline_enabled=True,
            button_label="🔎 Search Auth", include_back=False)
        await message.reply_text(text, parse_mode="HTML", reply_markup=markup,
                                 disable_web_page_preview=True)

    async def user_command(self, command, args, message, user, *, edit=False, content_page=0):
        if (command != "auctions" and args) or (command == "auctions" and len(args)>1):
            raise RuleError("ကိုယ့်အကောင့်ကိုသာ စစ်နိုင်ပါတယ်။ /menu ကိုသုံးပါ။")
        markup = account.back()
        if command == "menu":
            markup = account.menu()
            text = f"👤 My account · ID {user.id}\nHistory၊ နိုင်ခဲ့သောကဒ်၊ လက်ကျန်နှင့် လက်ရှိလေလံကို ရွေးကြည့်ပါ။"
        elif command in {"history", "wins"}:
            text = await self.store_call(account.history, self.store, user.id, wins=command=="wins")
        elif command == "auctions":
            page = int(args[0])-1 if args else 0
            text, markup = await self.store_call(account.active, self.store, page,
                                                 inline_enabled=bool(message.get_bot().supports_inline_queries))
        elif command in {"balance", "bal", "bcoin"}:
            text = await self.store_call(account.balance, self.store, user.id)
        elif command == "dailycoin":
            result = await self.store_call(self.store.claim_dailycoin, user.id)
            markup = None
            if result["claimed"]:
                text = (f'🎁 Daily coin ရပါပြီ — <b>{money(result["reward"])}</b>\n\n'
                        f'Total coin: <b>{money(result["available"])}</b>\n\n'
                        '24h , 0Min , 0Sec')
            else:
                remaining = max(0, int(result["remaining"]))
                hours, remainder = divmod(remaining, 3600)
                minutes, seconds = divmod(remainder, 60)
                text = f'⏳ Daily coin ကို ထပ်ယူရန် <b>{hours}h , {minutes}Min , {seconds}Sec</b> စောင့်ပါ။'
        elif command == "transactions":
            text = await self.store_call(account.transactions, self.store, user.id)
        elif command == "close":
            text = "Menu ပိတ်ပြီးပါပြီ။ ပြန်ဖွင့်ရန် အောက်ကခလုတ် သို့မဟုတ် /menu ကိုသုံးပါ။"
            markup = InlineKeyboardMarkup([[account.button("👤 Open menu", "menu")]])
        else:
            raise RuleError("/menu ကိုပြန်ဖွင့်ပါ။")
        if edit and message.photo:
            text, markup = account.caption_page(text, markup, command, args, content_page)
            await message.edit_caption(caption=text, parse_mode="HTML", reply_markup=markup)
        elif edit:
            await message.edit_text(text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
        else:
            await message.reply_text(text, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)

    async def owner_command(self, command, args, message, context):
        if command in {"menu", "history", "wins", "balance", "bal", "transactions"}:
            await self.user_command(command, args, message, message.from_user)
            return
        no_args = {"zip", "payout", "start", "help", "panel", "new", "draftcancel", "auctions", "pause", "resume", "banned", "stats", "settings", "check", "welcome", "welcomehelp", "welcomecancel"}
        one_arg = {"view", "bids", "close", "cancelauction", "setchannel", "setgroup", "increment", "ban", "unban", "export", "wallet", "walletmode"}
        if (command in no_args and args) or (command in one_arg and len(args) != 1) or (command == "extend" and len(args) != 2):
            raise RuleError(OWNER_ACTIONS.get(command, "Parameter မလိုပါ။"))
        if command in ("start", "help", "panel"):
            context.user_data.pop("welcome_edit", None)
            await self.panel(message)
            return
        if command == "zip":
            with source_zip() as archive:
                await message.reply_document(
                    document=archive, filename="auth-bot-code.zip",
                    caption="📦 Bot source code + Railway files + .env.example",
                )
            return
        if command == "welcome":
            context.user_data.pop("welcome_edit", None)
            await self.welcome_panel(message)
            return
        if command == "welcomehelp":
            await message.reply_text(welcome.HELP)
            return
        if command == "welcomecancel":
            context.user_data.pop("welcome_edit", None)
            await message.reply_text("Welcome ပြင်ဆင်ခြင်း ရပ်ပြီးပါပြီ။")
            return
        if command == "new":
            context.user_data.pop("welcome_edit", None)
            context.user_data["draft"] = {"step": "photo", "nonce": secrets.token_hex(8)}
            await message.reply_text(PROMPTS["photo"])
            return
        if command == "draftcancel":
            context.user_data.pop("draft", None)
            await message.reply_text("Draft ဖျက်ပြီးပါပြီ။")
            return
        if command in {"credit", "debit"}:
            user_id, delta, note = self.owner_wallet_adjustment(command, args, message)
            changed = await self.store_call(self.store.adjust_wallet, user_id, delta,
                message.from_user.id, f"owner:{message.chat_id}:{message.message_id}", note)
            available = (await self.store_call(self.store.wallet_balance, user_id))["available"]
            result = ("✅ ပြင်ပြီးပါပြီ။" if changed else "ဒီ request ကို အရင်က လုပ်ပြီးပါပြီ။") + f" User {user_id} · USD {usd_equivalent(abs(delta))} → {money(abs(delta))} · Available {money(available)}"
            if changed and delta > 0:
                notified = await self.wallet_credit_notification(context.bot, user_id, delta, available)
                if not notified:
                    result += "\n⚠️ User ကို DM မပို့နိုင်ပါ။ သူ့ bot DM မှာ /start လုပ်ထားကြောင်းစစ်ပါ။"
        elif command == "wallet":
            user_id = int(args[0])
            if user_id<=0:
                raise RuleError("Positive user ID ရေးပါ။")
            text = await self.store_call(account.balance, self.store, user_id)
            await message.reply_text(text,parse_mode="HTML")
            return
        elif command == "walletmode":
            if args[0].lower() not in {"on","off"}:
                raise RuleError(OWNER_ACTIONS[command])
            if args[0].lower()=="off":
                raise RuleError("Bid အတွက် wallet hold လိုအပ်ပါတယ်။ ပိတ်၍မရပါ။")
            await self.store_call(self.store.set, "wallet_mode", int(args[0].lower()=="on"))
            result = "✅ လေလံအသစ်များအတွက် Wallet " + args[0].lower() + " ဖြစ်ပါပြီ။ ရှိပြီးသားလေလံတွေရဲ့ payment ပုံစံ မပြောင်းပါ။"
        elif command == "check":
            await self.linked(context.bot)
            result = "✅ Channel/group ချိတ်ဆက်မှုနဲ့ admin permission မှန်ပါတယ်။"
        elif command in ("setchannel", "setgroup"):
            chat_id = int(args[0])
            if chat_id >= 0:
                raise RuleError("Negative numeric chat ID ရေးပါ။ ဥပမာ -1001234567890")
            chat = await context.bot.get_chat(chat_id)
            if chat.type != ("channel" if command == "setchannel" else "supergroup"):
                raise RuleError("Channel / supergroup အမျိုးအစား မမှန်ပါ။")
            await self.store_call(self.store.target,
                                  "channel_id" if command == "setchannel" else "group_id", chat_id)
            if command == "setchannel":
                self.channel_id = str(chat_id)
            else:
                self.group_id = str(chat_id)
            result = "✅ သိမ်းပြီးပါပြီ။ /check နဲ့ ချိတ်ဆက်မှု စစ်ပါ။"
        elif command == "increment":
            await self.store_call(self.store.set, "increment", cents(args[0]))
            result = "✅ လေလံအသစ်တွေအတွက် increment သိမ်းပြီးပါပြီ။"
        elif command in ("pause", "resume"):
            await self.store_call(self.store.set, "paused", int(command == "pause"))
            result = "⏸ Bid ခဏရပ်ထားသည်။ End time ဆက်သွားပါမယ်။" if command == "pause" else "▶️ Bid ပြန်ဖွင့်ပြီးပါပြီ။"
        elif command in ("ban", "unban"):
            user_id = int(args[0])
            if user_id <= 0 or user_id in self.config.owners:
                raise RuleError("Owner မဟုတ်တဲ့ positive user ID ရေးပါ။")
            await self.store_call(self.store.ban, user_id, command == "ban")
            result = "✅ ပြင်ပြီးပါပြီ။ ယခင် bid များ ဆက်လက်အကျုံးဝင်ပါတယ်။"
        elif command == "banned":
            rows = await self.store_call(self.store.banned)
            result = "Banned IDs (ပထမ 100):\n" + ("\n".join(str(r) for r in rows) or "မရှိပါ။")
        elif command == "rules":
            if args:
                value = " ".join(args)
                if len(value) > 1000:
                    raise RuleError("Rules စာလုံး 1000 အထိသာ ရေးပါ။")
                await self.store_call(self.store.set, "rules", value)
            result = await self.store_call(self.store.get, "rules")
        elif command == "settings":
            keys = ("wallet_mode", "increment", "paused")
            values = await asyncio.gather(*(self.store_call(self.store.get, key) for key in keys))
            wallet_mode, increment, paused = values
            result = (f'Wallet for new auctions: {"ON" if wallet_mode=="1" else "OFF"}\n'
                      f'Channel: {self.channel_id or "မသတ်မှတ်ရသေး"}\n'
                      f'Auction group: {self.group_id or "မသတ်မှတ်ရသေး"}\n'
                      f'PvP group: {self.pvp_group_id or "မသတ်မှတ်ရသေး"}\n'
                      f'Increment: {money(int(increment))}\nPaused: {paused}\n'
                      'Currency: Coin · Owner USD rate: $100 = 2500 coin\n'
                      'Bid edits: immediate; bursts wait for 2 quiet seconds\n'
                      f'Owners: {", ".join(map(str, sorted(self.config.owners)))}')
        elif command == "auctions":
            rows = await self.store_call(self.store.listing)
            result = "နောက်ဆုံး လေလံ 30:\n" + ("\n".join(f'#{r["id"]} {r["status"]} • {r["name"]}' for r in rows) or "မရှိသေးပါ။")
        elif command == "view":
            row = await self.store_call(self.store.auction, int(args[0]))
            await message.reply_photo(row["photo"], caption=caption(row), parse_mode="HTML")
            return
        elif command in ("close", "cancelauction"):
            await self.store_call(self.store.finish, int(args[0]),
                                  "closed" if command == "close" else "cancelled")
            result = "✅ ပိတ်ပြီးပါပြီ။ Channel post ကို နောက် update မှာ ပြင်ပေးပါမယ်။"
        elif command == "extend":
            await self.store_call(self.store.extend, int(args[0]), int(args[1]))
            result = "✅ End time တိုးပြီးပါပြီ။"
        elif command == "bids":
            rows = await self.store_call(self.store.history, int(args[0]))
            result = "နောက်ဆုံး bids 30:\n" + ("\n".join(f'{money(r["amount"])} • {r["user_id"]} • {date_text(r["created"])}' for r in rows) or "မရှိသေးပါ။")
        elif command == "export":
            auction_id = int(args[0])
            await self.store_call(self.store.auction, auction_id)
            data = io.StringIO()
            writer = csv.writer(data)
            writer.writerow(["bid_id", "auction_id", "user_id", "amount_coin", "created_utc"])
            # Numeric identifiers only: user names cannot inject spreadsheet formulas.
            rows = await self.store_call(lambda: list(self.store.export_bids(auction_id)))
            for r in rows:
                writer.writerow([r["id"], auction_id, r["user_id"], f'{r["amount"] // 100}.{r["amount"] % 100:02d}', date_text(r["created"])])
            await message.reply_document(io.BytesIO(data.getvalue().encode("utf-8")), filename=f"auction-{auction_id}-bids.csv")
            return
        elif command == "stats":
            rows, total, sales = await self.store_call(self.store.stats)
            result = "📊 Auctions\n" + "\n".join(f"{r[0]}: {r[1]}" for r in rows) + f"\nBids: {total}\nWinning bids (payment မစစ်ရသေး): {money(sales)}"
        elif command == "payout":
            payout = await self.store_call(self.store.payout_total)
            result = (f'💸 Bot Game Payout Total\n\n'
                      f'🪙 Total: {money(payout["total"])}\n'
                      f'🏆 PvP Win: {money(payout["pvp_win"])}\n'
                      f'↩️ PvP Refund: {money(payout["pvp_refund"])}\n'
                      f'💣 Boom Win: {money(payout["boom_win"])}\n'
                      f'🔥 Streak Reward: {money(payout["streak_reward"])}')
        else:
            raise RuleError("/panel မှာ command စာရင်းကြည့်ပါ။")
        await message.reply_text(result)

    async def welcome_panel(self, message):
        await message.reply_text("👋 User /start Welcome\nပြင်လိုသည့်အရာ ရွေးပါ။ စာ format နှင့် {mention} အသုံးပြုပုံကို Help မှာကြည့်ပါ။",
            reply_markup=InlineKeyboardMarkup([
                [button("📷 Photo", "welcome:photo", "success"), button("📝 Formatted text", "welcome:text")],
                [button("💻 HTML text", "welcome:html"), button("🔗 Buttons / colours", "welcome:buttons")],
                [button("👀 Preview", "welcome:preview", "success"), button("📖 Help", "welcome:help")],
                [button("🗑 Remove photo", "welcome:clearphoto", "danger"), button("🗑 Clear buttons", "welcome:clearbuttons", "danger")],
                [button("❌ Cancel edit", "welcome:cancel", "danger")]]))

    async def welcome_action(self, action, message, user, context):
        if action in {"photo", "text", "html", "buttons"}:
            context.user_data["welcome_edit"] = action
            prompts = {
                "photo": "Welcome photo ပို့ပါ။ Caption ပါရင် caption ကို welcome စာသားအဖြစ်ပါ သိမ်းပေးပါမယ်။ /welcomecancel နဲ့ရပ်နိုင်ပါတယ်။",
                "text": "Welcome စာသားပို့ပါ။ Telegram မှ bold/italic/quote/spoiler စတဲ့ format လုပ်ပြီးပို့နိုင်ပါတယ်။ {mention}, {first_name}, {username}, {user_id} သုံးနိုင်ပါတယ်။ /welcomecancel နဲ့ရပ်ပါ။",
                "html": 'HTML စာပို့ပါ။ ဥပမာ <b>မင်္ဂလာပါ {mention}</b>\n<blockquote>လေလံမှ ကြိုဆိုပါတယ်</blockquote>\n/welcomehelp မှာ format အပြည့်အစုံကြည့်ပါ။ /welcomecancel နဲ့ရပ်ပါ။',
                "buttons": "Button တစ်ခုစီကို Name | URL | colour ပုံစံရေးပါ။\nChannel | https://t.me/example | blue\nSupport | https://t.me/example_support | green\nအရောင် red/green/blue။ တစ်တန်းတည်းဆို && နဲ့ခြားပါ။ /welcomecancel နဲ့ရပ်ပါ။",
            }
            await message.reply_text(prompts[action])
        elif action == "preview":
            value = await self.store_call(welcome.load, self.store)
            await welcome.send(message, value, user, context.bot)
        elif action == "help":
            await message.reply_text(welcome.HELP)
        elif action == "cancel":
            context.user_data.pop("welcome_edit", None)
            await message.reply_text("Welcome ပြင်ဆင်ခြင်း ရပ်ပြီးပါပြီ။")
        elif action in {"clearphoto", "clearbuttons"}:
            value = await self.store_call(welcome.load, self.store)
            value["photo" if action == "clearphoto" else "buttons"] = None if action == "clearphoto" else []
            await self.save_welcome(message, value, user, context)

    async def save_welcome(self, message, value, user, context):
        welcome.validate(value["text"])
        try:
            # Telegram validates markup and URLs before settings become public.
            await welcome.send(message, value, user, context.bot)
        except TelegramError:
            raise RuleError("Preview ပို့မရလို့ မသိမ်းရသေးပါ။ ပုံ၊ HTML format၊ URL တွေကို စစ်ပြီး ပြန်ပို့ပါ။ /welcomehelp")
        await self.store_call(welcome.save, self.store, value)
        context.user_data.pop("welcome_edit", None)
        await message.reply_text("✅ အပေါ်က preview အတိုင်း သိမ်းပြီးပါပြီ။ User တွေ နောက် /start ပို့တဲ့အခါ ပြပေးပါမယ်။")

    async def welcome_input(self, message, user, context):
        mode = context.user_data["welcome_edit"]
        value = await self.store_call(welcome.load, self.store)
        if mode == "photo":
            if not message.photo:
                raise RuleError("Photo အဖြစ်ပို့ပါ။ /welcomecancel နဲ့ရပ်နိုင်ပါတယ်။")
            value["photo"] = message.photo[-1].file_id
            if message.caption:
                value["text"] = message.caption_html
        elif mode in {"text", "html"}:
            if not message.text:
                raise RuleError("Welcome စာသားပို့ပါ။")
            value["text"] = message.text_html if mode == "text" else message.text
        elif mode == "buttons":
            value["buttons"] = welcome.parse_buttons(message.text or "")
        await self.save_welcome(message, value, user, context)

    async def draft(self, message, context):
        draft = context.user_data["draft"]
        step = draft["step"]
        if step == "confirm":
            draft["duration_seconds"] = duration_seconds((message.text or "").strip())
            draft["nonce"] = secrets.token_hex(8)
            await self.preview(message, draft)
            return
        text = (message.text or "").strip()
        if step == "photo":
            if message.photo:
                draft[step] = message.photo[-1].file_id
                draft["media_type"] = "photo"
            elif message.video:
                draft[step] = message.video.file_id
                draft["media_type"] = "video"
            else:
                raise RuleError("Photo သို့ video အဖြစ်ပို့ပါ (document မဟုတ်ပါ)။")
        elif step in ("name", "anime", "card_type", "card_id", "rarity"):
            maximum = 24 if step in ("card_type", "rarity") else 60
            if not text or len(text) > maximum or any(ord(c) < 32 for c in text):
                raise RuleError(f"တစ်ကြောင်းတည်း စာလုံး 1–{maximum} ရေးပါ။")
            draft[step] = text
        elif step == "start":
            draft[step] = cents(text)
        elif step == "duration_seconds":
            draft[step] = duration_seconds(text)
        index = STEPS.index(step) + 1
        if index < len(STEPS):
            draft["step"] = STEPS[index]
            await message.reply_text(PROMPTS[draft["step"]])
        else:
            await self.preview(message, draft)

    async def preview(self, message, draft):
        draft["step"] = "confirm"
        increment, wallet_mode = await asyncio.gather(
            self.store_call(self.store.get, "increment"),
            self.store_call(self.store.get, "wallet_mode"),
        )
        row = dict(draft, id="DRAFT", increment=int(increment), wallet_required=int(wallet_mode))
        markup = InlineKeyboardMarkup([
            [button("✅ Publish", f'publish:{draft["nonce"]}', "success"),
             button("❌ Cancel", f'discard:{draft["nonce"]}', "danger")]])
        if draft.get("media_type", "photo") == "video":
            await message.reply_video(draft["photo"], caption=caption(row), parse_mode="HTML",
                                      reply_markup=markup)
        else:
            await message.reply_photo(draft["photo"], caption=caption(row), parse_mode="HTML",
                                      reply_markup=markup)

    async def callback(self, update, context):
        query = update.callback_query
        remaining = self.button_cooldown(update.effective_user)
        if remaining:
            await query.answer(f"ခဏစောင့်ပါ ({remaining:.1f} sec)", show_alert=True)
            return
        if (query.data or "").startswith("boom:"):
            if not self.pvp_group(update) or not update.effective_user or update.effective_user.is_bot:
                await query.answer("Boom ကို သတ်မှတ်ထားတဲ့ game group မှာပဲ သုံးနိုင်ပါတယ်။", show_alert=True)
                return
            try:
                parts=query.data.split(":")
                action,game_id=parts[1],parts[2]
                if action == "confirm":
                    game=await self.store_call(self.store.accept_boom,game_id,update.effective_user.id)
                    await query.answer("Boom ပွဲ စတင်ပါပြီ။")
                    await query.edit_message_text(boom_text(game),parse_mode="HTML",reply_markup=boom_markup(game))
                elif action == "cancel":
                    game=await self.store_call(self.store.cancel_boom,game_id,update.effective_user.id)
                    await query.answer("Boom request ကို ဖျက်သိမ်းပြီးပါပြီ။")
                    await query.edit_message_text(f'❌ Boom request ပယ်ဖျက်ပြီးပါပြီ။\n{pvp_name(game["requester_id"],game["requester_name"])} · {pvp_name(game["target_id"],game["target_name"])}',parse_mode="HTML")
                elif action == "cash":
                    game=await self.store_call(self.store.cash_boom,game_id,update.effective_user.id)
                    await query.answer("Cash ထုတ်ပြီးပါပြီ။")
                    await query.edit_message_text(boom_text(game),parse_mode="HTML",reply_markup=boom_markup(game))
                elif action == "pick" and len(parts)==4:
                    game=await self.store_call(self.store.pick_boom,game_id,update.effective_user.id,int(parts[3]))
                    await query.answer("Boom!" if game["status"]=="finished" else "Safe button ပါ။")
                    if game["status"] == "finished":
                        await query.edit_message_text(boom_text(game),parse_mode="HTML",reply_markup=boom_markup(game))
                    else:
                        await query.edit_message_text(boom_text(game),parse_mode="HTML",reply_markup=boom_markup(game))
                else:
                    await query.answer("လုပ်ဆောင်ချက် မမှန်ပါ။",show_alert=True)
            except (RuleError,ValueError) as exc:
                await query.answer(str(exc) if isinstance(exc,RuleError) else "Boom request မမှန်ပါ။",show_alert=True)
            except (PyMongoError,TelegramError):
                await query.answer("Boom game ယာယီမရနိုင်ပါ။ ခဏနေ ပြန်စမ်းပါ။",show_alert=True)
            return
        if (query.data or "").startswith("pvp:"):
            if not self.pvp_group(update) or not update.effective_user or update.effective_user.is_bot:
                await query.answer("သတ်မှတ်ထားတဲ့ PvP group မှာပဲ သုံးနိုင်ပါတယ်။", show_alert=True)
                return
            try:
                _, action, game_id = query.data.split(":", 2)
                if action == "confirm":
                    game = await self.store_call(self.store.accept_pvp, game_id,
                                                 update.effective_user.id)
                    await query.answer("PvP ပွဲ စတင်ပါပြီ။")
                    await query.edit_message_text(pvp_animation_text(game), parse_mode="HTML")
                elif action == "cancel":
                    game = await self.store_call(self.store.cancel_pvp, game_id,
                                                 update.effective_user.id)
                    await query.answer("PvP request ကို ပယ်ချ/ဖျက်သိမ်းပြီးပါပြီ။")
                    await query.edit_message_text(
                        f'❌ PvP request ကို ဖျက်သိမ်းပြီးပါပြီ။\n{pvp_name(game["requester_id"],game["requester_name"])} · '
                        f'{pvp_name(game["target_id"],game["target_name"])}', parse_mode="HTML")
                else:
                    await query.answer("လုပ်ဆောင်ချက် မမှန်ပါ။", show_alert=True)
            except (RuleError, ValueError) as exc:
                await query.answer(str(exc) if isinstance(exc, RuleError) else "Request မမှန်ပါ။", show_alert=True)
            except PyMongoError:
                await query.answer("Database ယာယီမရနိုင်ပါ။ ခဏနေ ပြန်စမ်းပါ။", show_alert=True)
            except TelegramError:
                log.warning("PvP callback message update failed")
            return
        if (query.data or "").startswith("user:"):
            if not update.effective_chat or update.effective_chat.type != "private" or not update.effective_user or update.effective_user.is_bot:
                await query.answer("Bot private chat မှာ /menu ကိုသုံးပါ။", show_alert=True)
                return
            try:
                action = query.data[5:]
                if action.startswith("inlinehelp:"):
                    page=int(action.split(":",1)[1])
                    me=await context.bot.get_me()
                    if not me.supports_inline_queries:
                        hint = "@BotFather → /setinline → ဒီ bot ကိုရွေးပြီး Inline mode ဖွင့်ပါ။" if self.owner(update) else "Inline search သုံးရန် owner က Inline mode ဖွင့်ပေးဖို့ လိုပါသေးတယ်။"
                        await query.answer(hint,show_alert=True)
                        return
                    await self.user_command("auctions",[str(page+1)],query.message,update.effective_user,edit=True)
                    await query.answer("Inline search ခလုတ်ကို ထပ်နှိပ်ပါ။")
                    return
                content_page = 0
                if action.startswith("page:"):
                    _, action, argument, page = action.split(":")
                    content_page = int(page)
                    if not 0 <= content_page <= 1000000:
                        raise RuleError("Page နံပါတ် မမှန်ပါ။")
                    if action not in {"menu", "history", "wins", "auctions", "balance", "bal", "transactions", "close"}:
                        raise RuleError("Menu action မမှန်ပါ။")
                    if action != "auctions" and argument != "0":
                        raise RuleError("Page နံပါတ် မမှန်ပါ။")
                    args = [argument] if action == "auctions" else []
                elif action.startswith("auctions:"):
                    page = int(action.split(":",1)[1])
                    action, args = "auctions", [str(page+1)]
                else:
                    args = []
                await self.user_command(action, args, query.message, update.effective_user,
                                        edit=True, content_page=content_page)
                await query.answer()
            except (RuleError, ValueError) as exc:
                await query.answer(str(exc) if isinstance(exc, RuleError) else "Page မမှန်ပါ။ /menu ကိုပြန်ဖွင့်ပါ။", show_alert=True)
            except PyMongoError:
                await query.answer("Database ယာယီမရနိုင်ပါ။ ခဏနေ ပြန်စမ်းပါ။", show_alert=True)
            except BadRequest as exc:
                if "message is not modified" in str(exc).lower():
                    await query.answer()
                else:
                    await query.answer("ဒီ menu ကိုပြင်မရပါ။ /menu နဲ့ ပြန်ဖွင့်ပါ။", show_alert=True)
            except TelegramError:
                await query.answer("ခဏစောင့်ပြီး ပြန်နှိပ်ပါ။", show_alert=True)
            return
        if not self.owner(update):
            # Callback answers are private to the clicker; no group message is sent.
            await query.answer("Owner only", show_alert=True)
            return
        await query.answer()
        try:
            kind, value = (query.data or "").split(":", 1)
            if kind == "welcome":
                await self.welcome_action(value, query.message, update.effective_user, context)
                return
            if kind == "action":
                if value in {"new", "auctions", "pause", "resume", "banned", "stats", "rules", "settings", "check", "welcome"}:
                    await self.owner_command(value, [], query.message, context)
                elif value in OWNER_ACTIONS:
                    await query.message.reply_text(OWNER_ACTIONS[value])
                return
            draft = context.user_data.get("draft")
            if kind in ("duration", "editduration"):
                raise RuleError("အချိန်ခလုတ်ကို မသုံးတော့ပါ။ 1sec, 5min, 1hours, 1day လို့ စာပို့ပါ။")
            if not draft or draft["nonce"] != value or draft["step"] != "confirm":
                raise RuleError("ဒီ preview သက်တမ်းကုန်ပါပြီ။ /new နဲ့ ပြန်စပါ။")
            if kind == "discard":
                context.user_data.pop("draft", None)
                await query.message.reply_text("Draft ဖျက်ပြီးပါပြီ။")
                return
            if kind != "publish":
                return
            await self.linked(context.bot)
            auction_id = await self.store_call(self.store.create, draft)
            context.user_data.pop("draft", None)
            row = await self.store_call(self.store.auction, auction_id)
            try:
                if row.get("media_type", "photo") == "video":
                    post = await context.bot.send_video(row["channel_id"], row["photo"],
                                                        caption=caption(row), parse_mode="HTML")
                else:
                    post = await context.bot.send_photo(row["channel_id"], row["photo"],
                                                        caption=caption(row), parse_mode="HTML")
            except TelegramError:
                await query.message.reply_text(f"⚠️ #{auction_id} publication မသေချာပါ။ ထပ်မတင်သေးပါနှင့်။ Channel စစ်ပါ။ Auto-forward ရရင် bot ကပြန်ချိတ်ပါမယ်။ Post မရှိတာသေချာမှ /cancelauction {auction_id} နဲ့ပိတ်ပြီး အသစ်တင်ပါ။")
                return
            await self.store_call(self.store.published, auction_id, post.message_id,
                                  int(post.date.timestamp()))
            await self.store_call(self.store.rendered, auction_id, row["version"])
            rules = await self.store_call(self.store.get, "rules")
            await query.message.reply_text(f"✅ Auction #{auction_id} တင်ပြီးပါပြီ။\n" + rules)
        except (RuleError, ValueError) as exc:
            await query.message.reply_text(str(exc) if isinstance(exc, RuleError) else "လုပ်ဆောင်ချက် မမှန်ပါ။ /panel ကိုပြန်ဖွင့်ပါ။")
        except PyMongoError:
            await query.message.reply_text("Database ယာယီမရနိုင်ပါ။ ခဏနေ ပြန်စမ်းပါ။")
        except TelegramError:
            log.warning("Owner Telegram request failed")
            await query.message.reply_text("Telegram request မအောင်မြင်ပါ။ /check နှင့် /auctions ကိုစစ်ပါ။")

    def note_bid(self, auction_id):
        now = time.monotonic()
        previous = self.last_bid_at.get(auction_id)
        self.last_bid_at[auction_id] = now
        # Leading edit for isolated bids; every bid in a burst moves its trailing edit.
        recent_bid = previous is not None and now - previous < 2
        recent_edit = now - self.last_caption_at.get(auction_id, float("-inf")) < 2
        self.bid_edit_due[auction_id] = now + 2 if recent_bid or recent_edit else now

    async def announce_winners(self, context):
        rows = await self.store_call(self.store.pending_winners)
        for row in rows:
            if time.monotonic() < self.winner_retry_after.get(row["id"], 0):
                continue
            # Never fall back to a different group or its general chat.
            if str(row["group_id"]) != self.group_id:
                continue
            url = account.post_link(row)
            if not url:
                continue
            mention = f'<a href="tg://user?id={row["winner_id"]}">{html.escape(row["winner_name"] or "Winner")}</a>'
            text = (
                f'🏆 AUCTION WON!\n\n🎨 {mention}\n'
                f'🎴 Character: {html.escape(row["name"])}\n'
                f'💎 Rarity: {html.escape(row["rarity"])}\n'
                f'💰 Winning Bid: {money(row["highest"])}\n\n'
                'You won this auction.\n\n⏰ Payment Deadline: 5 Min'
            )
            try:
                await context.bot.send_message(
                    chat_id=row["group_id"], message_thread_id=row["root_id"],
                    reply_to_message_id=row["root_id"], allow_sending_without_reply=False,
                    text=text, parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("View Win Card", url=url)
                    ]]),
                )
            except RetryAfter as exc:
                delay = exc.retry_after.total_seconds() if hasattr(exc.retry_after, "total_seconds") else exc.retry_after
                self.global_edit_after = time.monotonic() + delay + 1
                return
            except TelegramError:
                self.winner_retry_after[row["id"]] = time.monotonic() + 30
                log.warning("Winner announcement failed for auction %s; retry in 30s", row["id"])
                continue
            await self.store_call(self.store.winner_notified, row["id"])
            self.winner_retry_after.pop(row["id"], None)

    async def tick(self, context):
        async with self.tick_lock:
            await self.store_call(self.store.close_due)
            if time.monotonic() < self.global_edit_after:
                return
            rows = await self.store_call(self.store.dirty)
            for row in rows:
                if time.monotonic() < self.edit_after.get(row["id"], 0):
                    continue
                if row["status"] == "active" and time.monotonic() < self.bid_edit_due.get(row["id"], 0):
                    continue
                try:
                    await context.bot.edit_message_caption(chat_id=row["channel_id"], message_id=row["post_id"], caption=caption(row), parse_mode="HTML")
                except RetryAfter as exc:
                    delay = exc.retry_after.total_seconds() if hasattr(exc.retry_after, "total_seconds") else exc.retry_after
                    self.global_edit_after = time.monotonic() + delay + 1
                    return
                except BadRequest as exc:
                    if "message is not modified" in str(exc).lower():
                        await self.store_call(self.store.rendered, row["id"], row["version"])
                        self.last_caption_at[row["id"]] = time.monotonic()
                    else:
                        self.edit_after[row["id"]] = time.monotonic() + 60
                        log.warning("Caption edit rejected for auction %s; retry in 60s", row["id"])
                    continue
                except TelegramError:
                    self.edit_after[row["id"]] = time.monotonic() + 15
                    log.warning("Caption update failed for auction %s; retry in 15s", row["id"])
                    continue
                await self.store_call(self.store.rendered, row["id"], row["version"])
                self.edit_after.pop(row["id"], None)
                self.last_caption_at[row["id"]] = time.monotonic()

            await self.announce_winners(context)
            await self.tick_pvp(context)

    async def tick_pvp(self, context):
        expired = await self.store_call(self.store.expire_pvp)
        for game in expired:
            if not game.get("message_id"):
                continue
            try:
                await context.bot.edit_message_text(
                    chat_id=game["group_id"], message_id=game["message_id"],
                    text=(f'❌ PvP request 15sec အတွင်း Confirm မလုပ်သဖြင့် အလိုအလျောက် ပယ်ဖျက်ပြီးပါပြီ။\n\n'
                          f'{pvp_name(game["requester_id"], game["requester_name"])} · '
                          f'{pvp_name(game["target_id"], game["target_name"])}'),
                    parse_mode="HTML")
            except TelegramError:
                log.warning("Expired PvP request message update failed for round %s", game["id"])
        expired_boom = await self.store_call(self.store.expire_boom)
        for game in expired_boom:
            if not game.get("message_id"):
                continue
            try:
                await context.bot.edit_message_text(
                    chat_id=game["group_id"], message_id=game["message_id"],
                    text=(f'❌ Boom request 15sec အတွင်း Confirm မလုပ်သဖြင့် အလိုအလျောက် ပယ်ဖျက်ပြီးပါပြီ။\n\n'
                          f'{pvp_name(game["requester_id"],game["requester_name"])} · '
                          f'{pvp_name(game["target_id"],game["target_name"])}'), parse_mode="HTML")
            except TelegramError:
                log.warning("Expired Boom request message update failed for round %s", game["id"])
        timed_out_boom = await self.store_call(self.store.timeout_boom)
        for game in timed_out_boom:
            if not game.get("message_id"):
                continue
            try:
                await context.bot.edit_message_text(
                    chat_id=game["group_id"], message_id=game["message_id"],
                    text=boom_text(game), parse_mode="HTML", reply_markup=boom_markup(game))
            except TelegramError:
                log.warning("Boom turn timeout message update failed for round %s", game["id"])
        now = time.monotonic()
        for game_id, retry in list(self.pvp_render_retry.items()):
            if now < retry["after"]:
                continue
            game = retry["game"]
            try:
                await context.bot.edit_message_text(
                    chat_id=game["group_id"], message_id=game["message_id"],
                    text=pvp_animation_text(game), parse_mode="HTML")
            except RetryAfter as exc:
                delay = exc.retry_after.total_seconds() if hasattr(exc.retry_after, "total_seconds") else exc.retry_after
                retry["after"] = time.monotonic() + delay + 1
                self.pvp_edit_after = retry["after"]
            except TelegramError:
                retry["after"] = time.monotonic() + 10
            else:
                self.pvp_edit_after = time.monotonic() + 1.5
                self.pvp_render_retry.pop(game_id, None)

        due_rounds = await self.store_call(self.store.due_pvp)
        for due in due_rounds:
            if due.get("mode") == "dice":
                await self.tick_pvp_dice(due, context)
                continue
            if due["id"] in self.pvp_render_retry or time.monotonic() < self.pvp_edit_after:
                continue
            game = None
            try:
                game = await self.store_call(self.store.advance_pvp, due["id"])
                await context.bot.edit_message_text(
                    chat_id=game["group_id"], message_id=game["message_id"],
                    text=pvp_animation_text(game), parse_mode="HTML")
                self.pvp_edit_after = time.monotonic() + 1.5
            except RetryAfter as exc:
                delay = exc.retry_after.total_seconds() if hasattr(exc.retry_after, "total_seconds") else exc.retry_after
                self.pvp_render_retry[due["id"]] = {"game": game, "after": time.monotonic() + delay + 1}
                self.pvp_edit_after = self.pvp_render_retry[due["id"]]["after"]
            except BadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    log.warning("PvP message edit rejected for round %s", due["id"])
                    self.pvp_render_retry[due["id"]] = {"game": game, "after": time.monotonic() + 10}
            except (RuleError, PyMongoError, TelegramError):
                log.warning("PvP animation/settlement update failed for round %s", due["id"])
                if game is not None:
                    self.pvp_render_retry[due["id"]] = {"game": game, "after": time.monotonic() + 10}
        await self.announce_pvp_dice_results(context)
        now = time.monotonic()
        notifications = await self.store_call(self.store.pending_pvp_slot_notifications)
        for game in notifications:
            if now < self.pvp_slot_retry_after.get(game["id"], 0):
                continue
            try:
                await context.bot.send_message(
                    chat_id=game["group_id"],
                    text="✅ 1Round Can Be Start")
            except RetryAfter as exc:
                delay = exc.retry_after.total_seconds() if hasattr(exc.retry_after, "total_seconds") else exc.retry_after
                self.pvp_slot_retry_after[game["id"]] = now + delay + 1
            except TelegramError:
                self.pvp_slot_retry_after[game["id"]] = now + 30
                log.warning("PvP slot notification failed for round %s", game["id"])
            except PyMongoError:
                log.warning("PvP notification state unavailable for round %s", game["id"])
            else:
                await self.store_call(self.store.mark_pvp_slot_notified, game["id"])
                self.pvp_slot_retry_after.pop(game["id"], None)

    async def tick_pvp_dice(self, game, context):
        try:
            claimed = await self.store_call(self.store.claim_pvp_dice, game["id"])
            if claimed:
                dice = await context.bot.send_dice(
                    chat_id=claimed["group_id"], emoji="🎲",
                    reply_parameters=ReplyParameters(claimed["message_id"], allow_sending_without_reply=True) if claimed.get("message_id") else None,
                    read_timeout=20, write_timeout=20, connect_timeout=10, pool_timeout=5)
                await self.store_call(self.store.record_pvp_dice, game["id"],
                                      dice.dice.value, dice.message_id)
            else:
                await self.store_call(self.store.advance_pvp, game["id"])
        except (RuleError, PyMongoError, TelegramError):
            # The durable claim prevents a retry from rolling a different outcome.
            # If no result was saved, advance_pvp refunds both stakes after timeout.
            log.warning("PvP dice processing failed for round %s", game["id"])

    async def announce_pvp_dice_results(self, context):
        for game in await self.store_call(self.store.pending_pvp_dice_results):
            if time.monotonic() < self.pvp_dice_retry_after.get(game["id"], 0):
                continue
            reply_id = game.get("dice_message_id") or game.get("message_id")
            try:
                await context.bot.send_message(
                    chat_id=game["group_id"], text=pvp_animation_text(game), parse_mode="HTML",
                    reply_parameters=ReplyParameters(reply_id, allow_sending_without_reply=True) if reply_id else None)
                await self.store_call(self.store.mark_pvp_dice_result, game["id"])
            except RetryAfter as exc:
                delay = exc.retry_after.total_seconds() if hasattr(exc.retry_after, "total_seconds") else exc.retry_after
                self.pvp_dice_retry_after[game["id"]] = time.monotonic() + delay + 1
            except (TelegramError, PyMongoError):
                self.pvp_dice_retry_after[game["id"]] = time.monotonic() + 10
                log.warning("PvP dice result delivery failed for round %s", game["id"])
            else:
                self.pvp_dice_retry_after.pop(game["id"], None)

    async def error(self, update, context):
        # Avoid logging Telegram URLs/tokens, message bodies, or bidder identities.
        log.error("Unhandled bot error: %s", type(context.error).__name__)

    async def configure_menu(self, application):
        scopes = [
            (BotCommandScopeDefault(), USER_COMMANDS),
            (BotCommandScopeAllPrivateChats(), USER_COMMANDS),
        ]
        auction_group = self.group_id
        pvp_group = self.pvp_group_id
        if auction_group and auction_group == pvp_group:
            merged = {command.command: command for command in AUCTION_GROUP_COMMANDS + PVP_GROUP_COMMANDS}
            scopes.append((BotCommandScopeChat(int(auction_group)), list(merged.values())))
        else:
            if auction_group:
                scopes.append((BotCommandScopeChat(int(auction_group)), AUCTION_GROUP_COMMANDS))
            if pvp_group:
                scopes.append((BotCommandScopeChat(int(pvp_group)), PVP_GROUP_COMMANDS))
        # Replace owner chat overrides too; privileged commands stay in /panel only.
        scopes.extend((BotCommandScopeChat(owner_id), USER_COMMANDS) for owner_id in sorted(self.config.owners))
        try:
            for scope, commands in scopes:
                for language in ("", "my", "en"):
                    await application.bot.set_my_commands(commands, scope=scope, language_code=language)
            await application.bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        except TelegramError as exc:
            delay = 60
            if isinstance(exc, RetryAfter):
                retry = exc.retry_after
                delay = max(delay, retry.total_seconds() if hasattr(retry, "total_seconds") else retry) + 1
            application.job_queue.run_once(self.retry_menu, when=delay)
            log.warning("Command menu setup failed; retry scheduled (%s)", type(exc).__name__)

    async def retry_menu(self, context):
        await self.configure_menu(context.application)

    async def shutdown(self, application):
        self.store.close()

    def application(self):
        app = (Application.builder().token(self.config.token)
               .concurrent_updates(UPDATE_CONCURRENCY)
               .post_init(self.configure_menu).post_shutdown(self.shutdown).build())
        app.add_handler(MessageHandler(filters.ALL, self.message))
        app.add_handler(CallbackQueryHandler(self.callback))
        app.add_handler(InlineQueryHandler(self.inline_search))
        app.add_error_handler(self.error)
        app.job_queue.run_repeating(self.tick, interval=WORKER_TICK_INTERVAL_SECONDS,
                                    first=1, job_kwargs={"max_instances": 1, "coalesce": True})
        return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler.executors.default").setLevel(logging.WARNING)
    try:
        config = Config.from_env()
    except ValueError as exc:
        raise SystemExit(str(exc))
    try:
        service = AuctionBot(config)
    except (PyMongoError, ValueError):
        raise SystemExit("MongoDB startup failed. Check MONGODB_URI, database access and replica-set support.") from None
    service.application().run_polling(allowed_updates=POLLING_UPDATES, drop_pending_updates=False)
