"""Private views of a bidder's history and currently open auctions."""
import html
import re
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .domain import RuleError, money


def button(label, action, style="primary"):
    return InlineKeyboardButton(label, callback_data="user:" + action, api_kwargs={"style": style})


def menu():
    return InlineKeyboardMarkup([
        [button("📜 History · Last 10", "history"), button("🏆 Won cards", "wins", "success")],
        [button("🎴 Active auctions", "auctions:0"), button("💰 Balance", "balance", "success")],
        [button("💳 Wallet history", "transactions")],
        [button("✖️ Close", "close", "danger")],
    ])


def back():
    return InlineKeyboardMarkup([[button("⬅️ Back", "menu")]])


def caption_page(text, markup, command, args, page=0):
    """Account HTML has complete tags per line; paginate without splitting a tag."""
    if type(page) is not int or not 0 <= page <= 1000000:
        raise RuleError("Page နံပါတ် မမှန်ပါ။")
    pages, lines, length = [], [], 0
    for line in text.split("\n"):
        plain = html.unescape(re.sub(r"<[^>]+>", "", line))
        size = len(plain.encode("utf-16-le")) // 2
        if size > 900:
            raise RuleError("စာသားရှည်လွန်းပါသည်။ /menu မှ ပြန်ဝင်ပါ။")
        if lines and length + size + 1 > 900:
            pages.append("\n".join(lines))
            lines, length = [], 0
        lines.append(line)
        length += size + 1
    pages.append("\n".join(lines))
    page = min(page, len(pages)-1)
    controls = []
    route = f"page:{command}:{args[0] if args else '0'}:"
    if page:
        controls.append(button("⬅️ Previous text", route + str(page-1)))
    if page+1 < len(pages):
        controls.append(button("Next text ➡️", route + str(page+1)))
    keyboard = ([controls] if controls else []) + [list(row) for row in markup.inline_keyboard]
    result = pages[page]
    if len(pages)>1:
        result += f"\n\n📄 {page+1}/{len(pages)}"
    return result, InlineKeyboardMarkup(keyboard)


def post_link(row):
    channel = str(row["channel_id"])
    if row["post_id"] and channel.startswith("-100") and channel[4:].isdigit():
        return f'https://t.me/c/{channel[4:]}/{row["post_id"]}'
    return None


def card_title(row):
    label = f'#{row["id"]} {html.escape(row["name"])}'
    url = post_link(row)
    return f'<a href="{url}">{label}</a>' if url else label


def outcome(row, user_id, now):
    if row["status"] == "cancelled":
        return "🚫 ဖျက်သိမ်းထား"
    closed = row["status"] == "closed" or row["status"] == "active" and row["ends"] <= now
    if closed:
        return "🏆 နိုင်ခဲ့ပါတယ်" if row["winner_id"] == user_id else "❌ မနိုင်ခဲ့ပါ"
    return "🥇 လက်ရှိဦးဆောင်" if row["winner_id"] == user_id else "⬆️ အခြားသူ bid ကျော်ထား"


def remaining(ends, now):
    seconds = max(0, ends - now)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return " ".join(value for value in (f"{days}d" if days else "", f"{hours}h" if hours else "", f"{minutes}m" if minutes else "", f"{seconds}s" if seconds or not (days or hours or minutes) else "") if value)


def history(store, user_id, wins=False):
    now = int(time.time())
    rows = store.user_history(user_id, wins=wins, now=now)
    lines = ["🏆 ကိုယ်နိုင်ခဲ့တဲ့ နောက်ဆုံးကဒ် 10 ခု" if wins else "📜 ပါဝင်ခဲ့တဲ့ နောက်ဆုံးလေလံ 10 ခု"]
    if not rows:
        lines.append("မှတ်တမ်းမရှိသေးပါ။")
    for row in rows:
        lines.append(f'\n{card_title(row)}\n{outcome(row, user_id, now)}\nကိုယ့် bid: {money(row["my_bid"])} · အမြင့်ဆုံး: {money(row["highest"])}')
    if wins and rows:
        lines.append("\nCard လွှဲပေးရန် owner နှင့်ဆက်သွယ်ပါ။")
    return "\n".join(lines)


def active(store, page=0, inline_enabled=True, button_label="🔎 Inline search", include_back=True):
    if type(page) is not int or not 0 <= page <= 1000000:
        raise RuleError("Page နံပါတ် မမှန်ပါ။")
    count, _, _ = store.active_auctions(0, now=int(time.time()))
    text = f"🎴 Active auctions — {count} ခု\n\nအောက်က Search button ကိုနှိပ်၍ လေလံကဒ်များကြည့်ပါ။"
    search_button = (InlineKeyboardButton(button_label, switch_inline_query_current_chat="", api_kwargs={"style":"primary"})
                     if inline_enabled else button("🔎 Inline search", "inlinehelp:0"))
    rows = [[search_button]]
    if include_back:
        rows.append([button("⬅️ Back", "menu")])
    return text, InlineKeyboardMarkup(rows)


def balance(store, user_id):
    row = store.wallet_balance(user_id)
    return (f'🪙 ကိုယ့် Coin Wallet · ID {user_id}\n\n'
            f'သုံးနိုင် coin: <b>{money(row["available"])}</b>\n\n'
            f'Bid အတွက်ထိန်းထား coin: {money(row["held"])}\n\n'
            f'စုစုပေါင်း coin: {money(row["total"])}\n\n'
            'Coin ထည့်/နုတ်ရန် owner နှင့်ဆက်သွယ်ပါ။')


def transactions(store, user_id):
    from datetime import datetime, timezone
    rows = store.wallet_history(user_id)
    lines = ["🪙 Coin transactions · Last 10"]
    for row in rows:
        sign = "+" if row["delta"]>0 else "−"
        date = datetime.fromtimestamp(row["created"],timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        auction = f' · Auction #{row["auction_id"]}' if row["auction_id"] else ""
        kind = {"gift_sent": "Gift sent", "gift_received": "Gift received"}.get(row["kind"], row["kind"])
        lines.append(f'\n{sign}{money(abs(row["delta"]))} · {kind}{auction}\n{date}\n{html.escape(row["note"][:80] + ("…" if len(row["note"])>80 else ""))}')
    if not rows:
        lines.append("ငွေစာရင်း မရှိသေးပါ။")
    return "\n".join(lines)
