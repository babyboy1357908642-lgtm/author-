"""Persistent welcome content, safe user placeholders, and owner-editable links."""
import html
import json
import re
from html.parser import HTMLParser
from types import SimpleNamespace
from urllib.parse import urlsplit

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .domain import RuleError

DEFAULT = {"photo": None, "text": "မင်္ဂလာပါ {mention} 👋\n🎴 Waifu auction bot မှ ကြိုဆိုပါတယ်။\nChannel ရဲ့ card post Comments ထဲမှာ /bid ပမာဏ နဲ့ လေလံဆွဲနိုင်ပါတယ်။", "buttons": []}
PLACEHOLDERS = ("mention", "first_name", "last_name", "full_name", "username", "user_id", "bot_name", "bot_username")
STYLES = {"red": "danger", "green": "success", "blue": "primary", "danger": "danger", "success": "success", "primary": "primary"}
HELP = """👋 /start Welcome Settings
ပုံ၊ စာ၊ link buttons ကို အောက်ကခလုတ်တွေနဲ့ ပြင်နိုင်ပါတယ်။ Save မလုပ်မီ preview ပို့ပေးပါမယ်။ /welcomecancel နဲ့ ရပ်နိုင်ပါတယ်။

Placeholders:
{mention} — user ကိုနှိပ်နိုင်သော mention
{first_name} {last_name} {full_name}
{username} {user_id} {bot_name} {bot_username}

📝 Formatted text: Telegram editor မှ Bold, Italic, Underline, Strike, Spoiler, Quote, Expandable Quote, Code, Link နဲ့ format လုပ်ပြီးပို့ပါ။
💻 HTML: <b>Bold</b>, <i>Italic</i>, <u>Underline</u>, <s>Strike</s>, <tg-spoiler>Spoiler</tg-spoiler>, <blockquote>Quote</blockquote>, <blockquote expandable>Quote</blockquote>, <code>Code</code>, <pre>Code block</pre>, <a href="https://t.me/example">Link</a>။
Placeholders ကို စာသားထဲမှာပဲသုံးပါ။ URL/HTML attribute ထဲမှာ မသုံးပါနှင့်။

🔗 Buttons: တစ်ကြောင်းလျှင် row တစ်ခု။
Channel | https://t.me/example | blue
Support | https://t.me/example_support | green
တစ်တန်းတည်းထားလိုလျှင် && နဲ့ခြားပါ။ red / green / blue သုံးနိုင်ပါတယ်။"""


def load(store):
    value = store.get("welcome")
    return json.loads(value) if value else dict(DEFAULT, buttons=[])


def save(store, value):
    store.set("welcome", json.dumps(value, ensure_ascii=False))


def valid_url(value):
    if any(c.isspace() or ord(c) < 32 for c in value):
        return False
    try:
        parts = urlsplit(value)
        return (parts.scheme in ("http", "https") and bool(parts.hostname) and not parts.username
                or parts.scheme == "tg" and parts.netloc == "user" and bool(re.fullmatch(r"id=[1-9][0-9]*", parts.query)))
    except ValueError:
        return False


class Template(HTMLParser):
    TAGS = {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "tg-spoiler", "span", "a", "code", "pre", "blockquote", "tg-emoji"}

    def __init__(self, user, bot):
        super().__init__(convert_charrefs=False)
        self.user = user
        self.values = {
            "first_name": user.first_name or "", "last_name": user.last_name or "",
            "full_name": user.full_name, "mention": user.full_name,
            "username": "@" + user.username if user.username else user.full_name,
            "user_id": str(user.id), "bot_name": bot.first_name,
            "bot_username": "@" + bot.username,
        }
        self.stack = []
        self.output = []
        self.plain = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag not in self.TAGS:
            raise RuleError(f"HTML tag <{tag}> မသုံးနိုင်ပါ။ /welcomehelp မှာကြည့်ပါ။")
        valid = not attrs
        if tag == "a":
            valid = set(attrs) == {"href"} and valid_url(attrs["href"] or "")
        elif tag == "span":
            valid = attrs == {"class": "tg-spoiler"}
        elif tag == "tg-emoji":
            valid = set(attrs) == {"emoji-id"} and bool(re.fullmatch(r"[0-9]+", attrs.get("emoji-id") or ""))
        elif tag == "blockquote":
            valid = not attrs or attrs in ({"expandable": None}, {"expandable": ""})
        elif tag == "code" and attrs:
            valid = self.stack == ["pre"] and set(attrs) == {"class"} and bool(re.fullmatch(r"language-[A-Za-z0-9_+-]+", attrs.get("class") or ""))
        if not valid or any("{" in (v or "") or "}" in (v or "") for v in attrs.values()):
            raise RuleError("HTML attribute/URL မှားနေပါတယ်။ /welcomehelp မှာကြည့်ပါ။")
        self.output.append("<" + tag + "".join(" " + k + ("" if v is None else '="' + html.escape(v, quote=True) + '"') for k, v in attrs.items()) + ">")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            raise RuleError("HTML opening/closing tags ကို အစီအစဉ်မှန်အောင်ရေးပါ။")
        self.output.append(f"</{tag}>")

    def handle_data(self, value):
        start = 0
        for match in re.finditer(r"\{([a-z_]+)\}", value):
            self.output.append(html.escape(value[start:match.start()]))
            self.plain.append(value[start:match.start()])
            key = match[1]
            if key not in self.values:
                raise RuleError("မသိသော placeholder: {" + key + "}။ /welcomehelp မှာကြည့်ပါ။")
            replacement = self.values[key]
            escaped = html.escape(replacement)
            if key == "mention" and not any(tag in self.stack for tag in ("a", "code", "pre", "tg-emoji")):
                escaped = f'<a href="tg://user?id={self.user.id}">{escaped}</a>'
            self.output.append(escaped)
            self.plain.append(replacement)
            start = match.end()
        self.output.append(html.escape(value[start:]))
        self.plain.append(value[start:])

    def handle_entityref(self, name):
        decoded = html.unescape("&" + name + ";")
        self.output.append(html.escape(decoded))
        self.plain.append(decoded)

    def handle_charref(self, name):
        decoded = html.unescape("&#" + name + ";")
        self.output.append(html.escape(decoded))
        self.plain.append(decoded)

    def handle_comment(self, value):
        raise RuleError("HTML comments မသုံးပါနှင့်။")

    def handle_decl(self, decl):
        raise RuleError("HTML declaration မသုံးပါနှင့်။")


def render(source, user, bot):
    parser = Template(user, bot)
    parser.feed(source)
    parser.close()
    if parser.stack:
        raise RuleError("HTML tag ပိတ်ရန် ကျန်နေပါတယ်။")
    text = "".join(parser.plain)
    length = len(text.encode("utf-16-le")) // 2
    if not text.strip() or length > 4096:
        raise RuleError("Welcome စာသားကို 1–4096 စာလုံးအတွင်းရေးပါ။ Placeholder အရှည်အတွက် နေရာချန်ပါ။")
    return "".join(parser.output), length


def validate(source):
    # Reserve room for maximum-length Telegram user names, including astral emoji.
    user = SimpleNamespace(id=9999999999999999999, first_name="🦄"*64, last_name="🦄"*64,
                           full_name="🦄"*64 + " " + "🦄"*64, username=None)
    bot = SimpleNamespace(first_name="🦄"*64, username="b"*32)
    render(source, user, bot)


def parse_buttons(text):
    rows = []
    total = 0
    for line in text.strip().splitlines():
        if not line.strip():
            continue
        row = []
        for entry in line.split("&&"):
            parts = [part.strip() for part in entry.split("|")]
            if len(parts) != 3:
                raise RuleError("Button ပုံစံ: Name | https://t.me/example | blue")
            label, url, style = parts
            if not 1 <= len(label) <= 40 or not valid_url(url) or style.lower() not in STYLES:
                raise RuleError("Button name (1–40), URL နှင့် red/green/blue အရောင်ကို စစ်ပါ။")
            row.append({"text": label, "url": url, "style": STYLES[style.lower()]})
        total += len(row)
        if len(row) > 3 or total > 12:
            raise RuleError("တစ်တန်းမှာ 3 ခု၊ စုစုပေါင်း 12 ခုအထိ button ထည့်နိုင်ပါတယ်။")
        rows.append(row)
    if not rows:
        raise RuleError("Button အနည်းဆုံးတစ်ခု ရေးပါ။ ဖယ်ရန် Clear buttons ကိုသုံးပါ။")
    return rows


def keyboard(value):
    rows = [[InlineKeyboardButton(b["text"], url=b["url"], api_kwargs={"style": b["style"]}) for b in row] for row in value["buttons"]]
    rows.append([InlineKeyboardButton("👤 My account · History / Balance", callback_data="user:menu", api_kwargs={"style": "primary"})])
    return InlineKeyboardMarkup(rows)


async def send(message, value, user, bot):
    rendered, length = render(value["text"], user, bot)
    markup = keyboard(value)
    if value["photo"] and length <= 1024:
        await message.reply_photo(value["photo"], caption=rendered, parse_mode="HTML", reply_markup=markup)
    else:
        if value["photo"]:
            await message.reply_photo(value["photo"])
        await message.reply_text(rendered, parse_mode="HTML", reply_markup=markup, disable_web_page_preview=True)
