# authbid-bot 🎴

Telegram waifu ကဒ်လေလံနှင့် PvP bot — လေလံ bid နှင့် PvP လောင်းကြေးကို coin ဖြင့်သုံးသည်။

## ပါဝင်သော စနစ်များ

- Owner-only private admin panel၊ လုပ်ဆောင်ချက် **၂၈**။ “owner plan” ကို admin panel လို့ယူဆထားသည်။
- `/new` → photo → card name → anime name → rarity → starting bid → တင်ပြီးပိတ်မည့် ကြာချိန် (`1sec`, `5min`, `1hours`, `1day`) → preview → Publish။
- Owner သတ်မှတ်ထားသော channel ထဲ photo post တင်ပေးသည်။
- အဲဒီ channel နှင့်ချိတ်ထားသော discussion supergroup ရဲ့ auction comments ထဲမှာ `/bid 10.50`။
- လေလံအတွက် discussion group နှင့် PvP အတွက် သီးခြား game group သတ်မှတ်နိုင်သည်။ အခြား groups ကို မတုံ့ပြန်ပါ။ PvP group မှာ `/pvp`, `/boom`, `/btop`, `/author`, `/bal`, `/bcoin` သုံးနိုင်သည်။ `/boom` သည် player တစ်ယောက်စီအတွက် Boom တစ်ခုပါသော 6/9-button game ဖြစ်ပြီး safe button နှိပ်လျှင် အခြား player အလှည့်ပြောင်းသည်။ `/btop` က coin အများဆုံး Top 10 ကိုပြသည်။ `/author` က Active auctions စာရင်းနှင့် `🔎 Search Auth` inline search ခလုတ်ကို ပြပေးသည်။ လေလံမရှိသေးလျှင်လည်း `Active auctions — 0` နှင့် ခလုတ်ကို ပြပေးသည်။ `/bcoin` က reply လုပ်ထားသူကို coin gift ပို့သည်။ Non-owner private chats မှာ `/start` welcome နှင့် ကိုယ်ပိုင် account commands ကိုသုံးနိုင်သည်။ Owner admin commands ကို private chat မှာသုံးပါ။ `/auth` ကို owner က သတ်မှတ်ထားသော discussion group မှာလည်း သုံးနိုင်သည်။
- Bid လက်ခံပြီး user ကို receipt ကို ချက်ချင်းပြန်ပို့သည်။ Channel caption ကို ပုံမှန်အားဖြင့် နောက် worker tick (**၀.၅ စက္ကန့်အတွင်း**) ပြင်ပြီး ဆက်တိုက် bids များကို နောက်ဆုံး bid မှ **၂ စက္ကန့်ငြိမ်မှ** တစ်ခါတည်း update လုပ်သည်။ Telegram rate limit / network error ရှိရင် နောက်ကျနိုင်သည်။ မပြောင်းလဲသည့် post ကို ထပ်မပြင်ပါ။
- Bid ရောက်လာချိန်အလိုက် database transaction ဖြင့် လက်ခံသည်။ တူညီသည့် bid ပမာဏကို ပြိုင်ဆွဲလျှင် ပထမ commit ဖြစ်သူ အနိုင်ရသည်။
- End time အတိအကျရောက်လျှင် bid မလက်ခံတော့ပါ။ နောက် worker tick မှာ winner ကို မူရင်း post ထဲပြပေးသည်။ Bid မရှိပါက winner မရှိပါ။
- MongoDB မှာ auctions၊ bids၊ wallets/holds၊ PvP rounds၊ settings၊ bans နှင့် comment mappings သိမ်းသည်။ Restart ပြီး expired auctions နှင့် PvP animation များကို ဆက်လုပ်သည်။
- Coin ကို decimal ၂ နေရာအထိ သုံးသည်။ Owner က coin ledger ကို `/auth`, `/credit`, `/debit` ဖြင့်စီမံသည်။ Payment gateway၊ အလိုအလျောက် deposit/withdrawal နှင့် card ownership transfer မပါဝင်ပါ။ Card ကို owner ကပေးပို့ရသည်။

## Setup

Python **3.9+** လိုအပ်သည်။ ဤ project ကို Python 3.9.25 နှင့် စမ်းသပ်ထားသည်။

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock
cp .env.example .env
```

`.env` ထဲတွင်:

```dotenv
BOT_TOKEN=your_botfather_token
OWNER_IDS=123456789
CHANNEL_ID=-1001234567890
GROUP_ID=-1009876543210
MONGODB_URI=mongodb+srv://USERNAME:PASSWORD@YOUR_CLUSTER/?retryWrites=true&w=majority
MONGODB_DATABASE=authbid_bot
```

- `BOT_TOKEN`: @BotFather ကပေးသော token။ Git ထဲမတင်ပါနှင့်။
- `OWNER_IDS`: Owner Telegram **numeric user ID**။ Owner အများကြီးဆို comma ခြားရေးပါ။ Username မသုံးပါနှင့်။ Owner IDs ကို environment မှသာ သတ်မှတ်နိုင်သည်။
- `CHANNEL_ID`, `GROUP_ID`: Numeric chat IDs။ အစမှာ ချန်ထားပြီး owner private chat ရဲ့ `/setchannel` နှင့် `/setgroup` နဲ့ သတ်မှတ်လည်းရသည်။
- Environment chat IDs သည် empty database အတွက် bootstrap ဖြစ်သည်။ Bot commands နှင့်သိမ်းထားသော settings က restart ပြီးလည်း အကျုံးဝင်သည်။
- `MONGODB_URI`: မဖြစ်မနေလိုအပ်သော MongoDB Atlas သို့ replica set connection string။ Standalone MongoDB သည် multi-document transactions မရသဖြင့် မသုံးနိုင်ပါ။ `MONGODB_DATABASE`: database name (default `authbid_bot`)။
- URI မရှိလျှင် startup ရပ်သည်။ Bot runtime သည် MongoDB တစ်ခုတည်းသုံးပြီး SQLite fallback/backend မပါပါ။ SQLite ကို legacy data migration နှင့် test utility အတွက်သာ ထားသည်။ `.env` နှင့် backups ကို private ထားပါ။

### Telegram ပြင်ဆင်ခြင်း

1. @BotFather ဖြင့် bot ဖန်တီးပါ။
2. Channel တစ်ခုနှင့် discussion **supergroup** တစ်ခုဖန်တီးပါ။
3. Channel Settings → Discussion မှာ အဆိုပါ group ကို link လုပ်ပါ။
4. Bot ကို channel admin ထည့်ပြီး **Post Messages** ခွင့်ပေးပါ။
5. Bot ကို discussion group **admin** ထည့်ပါ။ ဒါမှ auto-forward နှင့် comment messages အားလုံးကို ဖတ်နိုင်ပါမည်။ Group members ကို text messages ပို့ခွင့်ပေးထားပါ။
6. Bot ကို run ပြီး owner account မှ private chat မှာ `/start` ပို့ပါ။ လိုအပ်လျှင် `/setchannel -100…`, `/setgroup -100…` သတ်မှတ်ပါ။
7. `/check` နဲ့ linked chats နှင့် bot admin permissions ကိုစစ်ပါ။
8. `/new` နဲ့ card အသစ်တင်ပါ။

```bash
python run.py
```

Long polling သုံးသည်။ Public website / webhook server မလိုပါ။ Bot token တစ်ခုလျှင် process **တစ်ခုသာ** run ပါ။ ရပ်ရန် `Ctrl+C`။ VPS မှာ process supervisor သုံးပြီး MongoDB backup ကိုစီစဉ်ပါ။

### Card ထည့်ပုံ

Owner က private chat မှာ `/new` ပို့ပြီး prompt တစ်ခုစီကိုဖြေပါ။ ပထမအဆင့်မှာ photo သို့ video ပို့နိုင်သည်။

| Field | Example |
| --- | --- |
| Photo / Video | Telegram photo သို့ video အဖြစ်ပို့ရန် (file document မဟုတ်) |
| Name | Rem |
| Anime | Re:Zero |
| Rarity | SSR |
| Starting bid | `5.00` |
| ကြာချိန် | `1sec`, `5min`, `1hours`, `1day` |

ကြာချိန်ကို owner က စာပို့သတ်မှတ်ပါ။ အချိန်ရွေး/ပြင်ခလုတ် မပါပါ။

- `1sec` → တင်ပြီး ၁ စက္ကန့်အကြာပိတ်
- `5min` → တင်ပြီး ၅ မိနစ်အကြာပိတ်
- `1hours` → တင်ပြီး ၁ နာရီအကြာပိတ်
- `1day` → တင်ပြီး ၁ ရက်အကြာပိတ်

`sec/second/seconds`, `min/minute/minutes`, `h/hr/hour/hours`, `d/day/days` လက်ခံသည်။ 1sec မှ 7days အထိ သတ်မှတ်နိုင်သည်။ ယခင် `5`, `5 မိနစ်`, `၅ မိနစ်` ပုံစံများကို ၅ မိနစ်ဟု ဆက်လက်လက်ခံသည်။ Preview အဆင့်မှာ ကြာချိန်အသစ်ကို စာပို့ပြီး ပြန်ပြင်နိုင်သည်။

**Channel မှာ post တကယ်တင်ပြီးမှ** အချိန်စတွက်သည်။ Preview ကြည့်နေစဉ်အချိန် မတွက်ပါ။ အချိန်ကို Telegram post date မှတွက်ပြီး database ထဲသိမ်းသည်။ Restart သို့မဟုတ် forward ထပ်ရခြင်းကြောင့် countdown ပြန်မစပါ။ ပိတ်ချိန်ရောက်လျှင် bid ချက်ချင်းပိတ်ပြီး post caption ကို နောက် worker tick (0.25 စက္ကန့်ခြား) မှာ ပြင်သည်။ Post မှာ ကျန်ချိန်ကို `5min`, `4min 50sec` ပုံစံဖြင့်ပြသည်။

Publish အစိမ်း၊ Cancel အနီ ဖြစ်သည်။ Owner panel မှာ Telegram official `success` (အစိမ်း), `danger` (အနီ), `primary` (အပြာ) styles ဆက်သုံးသည်။ Telegram client ဗားရှင်းအဟောင်းများတွင် အရောင်မပြနိုင်ပါ။

Preview ကိုစစ်ပြီး **Publish** နှိပ်ပါ။ `/draftcancel` နဲ့ draft ကိုဖျက်နိုင်သည်။ Draft သည် memory ထဲရှိသဖြင့် restart ဖြစ်လျှင် `/new` နဲ့ပြန်စပါ။ Publish ပြီးသော auction နှင့် bids က database ထဲရှိသည်။

### Bid ဆွဲပုံ

Channel ရဲ့ card post ကိုဖွင့် → **Comments** ကိုဝင် → `/bid 5.00` ပို့ပါ။ Bot က thread မသိနိုင်ပါက auction ရဲ့ forwarded post ကို တိုက်ရိုက် reply လုပ်ပြီး bid ဆွဲပါ။

- ပထမ bid သည် start amount ထက်မနည်းရပါ။ နောက် bid သည် highest bid + increment ထက်မနည်းရပါ။
- `5`, `5.5`, `5.50` လက်ခံသည်။ `5coin`, `1,000`, negative values၊ decimal ၂ နေရာထက်ပိုခြင်းကို လက်မခံပါ။
- Bid ကို edit လုပ်ခြင်းက ပမာဏမပြောင်းစေပါ။ ပမာဏအသစ်ဖြင့် `/bid` အသစ်ပို့ပါ။
- Bid ဖျက်ခြင်း/ပြန်ရုပ်သိမ်းခြင်း မပါ။ Telegram message delete လုပ်လည်း database မှတ်တမ်း မပျက်ပါ။
- Owner နှင့် user နှစ်မျိုးလုံး ကိုယ်ပိုင် Telegram account ဖြင့် မူရင်း post Comments မှာ bid ဆွဲနိုင်သည်။ Owner ကိုလည်း balance လုံလောက်မှု၊ minimum bid၊ wallet hold နှင့် deadline စည်းမျဉ်းများ အတူတူသတ်မှတ်သည်။ Anonymous admin / channel identity / bot accounts သည် bid မဆွဲနိုင်ပါ။
- Comment thread ထဲ `/rules` နဲ့ owner သတ်မှတ်ထားသောစည်းကမ်းကို ဖတ်နိုင်သည်။

## Owner panel — လုပ်ဆောင်ချက် ၂၈

`/panel` ခလုတ်တွေက ချက်ချင်းလုပ်ဆောင်ပေးခြင်း သို့မဟုတ် လိုအပ်သော command parameters ကိုပြပေးခြင်း ဖြစ်သည်။

| # | Command | လုပ်ဆောင်ချက် |
| --- | --- | --- |
| 1 | `/new` | Card creation wizard |
| 2 | `/auctions` | နောက်ဆုံး auctions 30 |
| 3 | `/view ID` | Card နှင့်လက်ရှိအခြေအနေ |
| 4 | `/bids ID` | နောက်ဆုံး bids 30 နှင့် bidder IDs |
| 5 | `/close ID` | လေလံကိုစောပိတ်ပြီး highest bidder ကို winner သတ်မှတ် |
| 6 | `/cancelauction ID` | Winner မရှိဘဲ လေလံဖျက်သိမ်း၊ bid audit ဆက်သိမ်း |
| 7 | `/extend ID minutes` | Active auction end time တိုး (1–10080 minutes) |
| 8 | `/pause` | Bid အားလုံးခဏရပ်; deadlines ဆက်သွားသည် |
| 9 | `/resume` | Bid ပြန်လက်ခံ |
| 10 | `/setchannel -100…` | Post တင်မည့် channel |
| 11 | `/setgroup -100…` | အလုပ်လုပ်မည့် linked discussion supergroup |
| 12 | `/increment 1.00` | **နောက်တင်မည့် auctions** အတွက် minimum increment |
| 13 | `/ban USER_ID` | နောက် bid များကိုပိတ် |
| 14 | `/unban USER_ID` | Bid ပြန်ခွင့်ပြု |
| 15 | `/banned` | ပထမ banned IDs 100 |
| 16 | `/stats` | Auction counts၊ bid counts၊ winning bid totals |
| 17 | `/export ID` | Auction တစ်ခု၏ bid audit CSV အပြည့် |
| 18 | `/rules စာသား` | စည်းကမ်းပြင်; argument မပါလျှင် လက်ရှိစာသားပြ |
| 19 | `/settings` | Channel/group၊ increment၊ pause နှင့် owner IDs |
| 20 | `/check` | Telegram linked chats နှင့် admin permissions စစ် |
| 21 | `/welcome` | User `/start` ပုံ၊ စာ၊ အရောင်ပါ link buttons ပြင် |
| 22 | `/credit USER_ID $100 note` သို့ user ကို reply လုပ်ပြီး `+$100` ပို့ | USD ကို coin ပြောင်းထည့်; user bot DM ဖွင့်ထားလျှင် receipt ပို့ |
| 23 | `/debit USER_ID $10 note` သို့ reply လုပ်ပြီး `-$10` ပို့ | USD ပမာဏအတိုင်း coin နုတ် |
| 24 | `/wallet USER_ID` | User wallet ကို owner စစ် |
| 25 | `/walletmode on` / `/walletmode off` | လေလံအသစ်များတွင် wallet လို/မလို သတ်မှတ် |
| 26 | `/auth` | User ကို reply သို့ numeric ID ဖြင့် USD ပမာဏပေးပြီး coin ပြောင်းထည့်/နုတ် |
| 27 | `/setpvpgp -100…` | PvP ကစားမည့် supergroup သတ်မှတ်ရန် (owner DM မှသာ) |
| 28 | `/zip` | Bot source code ZIP ကို owner DM သို့ပို့ရန် |

`/start`, `/help`, `/panel`, `/draftcancel` ကိုလည်းသုံးနိုင်သည်။ ID ဆိုသည်မှာ post ထိပ်က `#1` ကဲ့သို့ auction ID ဖြစ်သည်; command မှာ `1` ဟုသာရေးပါ။

Ban လုပ်ခြင်းသည် ယခင် bids များကို မဖျက်ပါ။ Winner နှင့်ပတ်သက်ပြီး လေလံဖျက်သိမ်းလိုလျှင် active ဖြစ်နေချိန် `/cancelauction` သုံးပါ။ `/close` နှင့် `/cancelauction` ပြီးသော auction ကို ပြန်ဖွင့်ခြင်းမပါ။ Active/publishing auctions ရှိနေစဉ် channel/group ပြောင်းမရပါ။ Running/Pending PvP ရှိနေချိန် PvP group ကို မပြောင်းနိုင်ပါ။

## PvP coin game

Owner သည် bot private chat မှ `/setpvpgp -100…` ဖြင့် သီးခြား supergroup သတ်မှတ်ပါ။ Bot ကို အဲဒီ group ထဲထည့်ပါ။ Owner သည် game group မှ user message ကို reply လုပ်ပြီး `+$100` / `-$5` ပို့ကာ coin ထည့်/နုတ်နိုင်သည်။ Reply ကို bot လက်ခံရရန် bot ကို group admin ခန့်ပါ၊ သို့မဟုတ် @BotFather တွင် Group Privacy ကိုပိတ်ပါ။ PvP group command menu မှာ `/pvp`, `/bal`, `/bcoin` သုံးခုသာ ပေါ်မည်။ `/bal` သည် ကိုယ့်လက်ကျန်ကိုပြပြီး `/bcoin` ကို PvP game group ထဲမှာသာ coin gift ပို့ရန်သုံးပါ။

ပြိုင်ဘက်၏ group message ကို reply လုပ်ပြီး `/pvp 250` သို့မဟုတ် 250 coin ထက်များသောပမာဏ ပို့ပါ။ Requester မှာ လောင်းကြေးပြည့်ရှိမှ request တင်နိုင်သည်။ ဖိတ်ခေါ်ခံရသူက **Confirm** လုပ်သည့်အချိန်တွင် နှစ်ဖက်စလုံး၏ လက်ကျန်နှင့် game slot ကိုပြန်စစ်ပြီး တစ်ယောက်စီ၏ wager ကိုဖယ်ထားသည်။ ဖိတ်ခေါ်ခံရသူက **Cancel** လုပ်နိုင်ပြီး requester ကလည်း pending request ကို cancel လုပ်နိုင်သည်။

အတည်ပြုပြီးနောက် 50/50 ရလဒ်အတွက် animation bar ကို တစ်စက္ကန့်တစ်ကြိမ်၊ ၅ ကြိမ် update လုပ်သည်။ နောက်ဆုံးမှာ ဥပမာ 60/40 ပြလျှင် 60% ဘက်ကနိုင်သည်။ အနိုင်ရသူကို နှစ်ဖက် wager စုစုပေါင်း ပြန်ပေါင်းပေးပြီး ရှုံးသူ wager ကိုဆုံးရှုံးသည်။ Group တစ်ခုတွင် တစ်ချိန်တည်း running ပွဲ ၅ ပွဲအထိသာ ကစားနိုင်ပြီး user တစ်ယောက်သည် တစ်ပွဲတည်းသာ ဝင်နိုင်သည်။ ပွဲပြီး၍ slot လွတ်တိုင်း `1Round လူရှင်းပါပီ` အသိပေးစာတစ်စောင်ပို့သည်။

PvP game group ထဲမှာသာ အခြား user ရဲ့ message ကို reply လုပ်ပြီး `/bcoin 100` ပို့လျှင် ကိုယ့် available balance မှ 100 coin ကို သူ့ balance ထဲ တစ်ခါတည်းပြောင်းပေးသည်။ ပမာဏသည် coin ဖြစ်ပြီး decimal ၂ နေရာအထိရသည်။ ကိုယ့်ကိုယ်ကို၊ bot ကို၊ anonymous/channel message ကို gift မပို့နိုင်ပါ။ Gift မပို့မီ sender ၏ available coin နှင့် receiver ၏ wallet limit ကိုစစ်သည်; လက်ကျန်စစ်ရန် `/bal` သုံးပါ။

Auction discussion group တွင် `/auther` ပို့လျှင် နောက်ဆုံး auction ပုံအောက်တွင် Inline search ခလုတ်တပ်ပေးသည်။

## User account / history / wallet

User က bot private chat မှာ `/start` ပို့ပြီး **My account** နှိပ်ပါ။ Owner ပြင်ထားသော welcome link buttons များအောက်မှာ account ခလုတ် သီးသန့်ပါသည်။ `/menu` နဲ့လည်းဖွင့်နိုင်သည်။ History နှင့် wallet history commands ကို group ထဲမပြပါ။ PvP game group မှာ `/bal` က လက်ကျန်စစ်ပြီး `/bcoin` က reply လုပ်ထားသူကို coin gift ပို့သည်။

| Command | မြင်ရမည့်အရာ |
| --- | --- |
| `/history` | ပါဝင်ခဲ့သော နောက်ဆုံးလေလံ **10 ခု** (လေလံတစ်ခုကိုတစ်ကြိမ်)၊ ကိုယ့်အမြင့်ဆုံး bid၊ နောက်ဆုံး highest bid၊ နိုင်/ရှုံး/ဦးဆောင်/ကျော်ခံရ/ဖျက်သိမ်း အခြေအနေ |
| `/wins` | ကိုယ်နိုင်ခဲ့သော နောက်ဆုံးကဒ် **10 ခု** နှင့် post links |
| `/auctions` | Active auctions အရေအတွက်နှင့် Inline search/Back ခလုတ်များ |
| `/balance` သို့ `/bal` (private chat မှာ `/bcoin` လည်း balance alias) | Available / Held / Total coin နှင့် ကိုယ့် user ID |
| `/transactions` | ကိုယ့် coin အဝင်/အထွက်မှတ်တမ်း နောက်ဆုံး **10 ခု** |

History ကို နောက်ဆုံး bid ပါဝင်ခဲ့သည့်အစီအစဉ်ဖြင့် စီသည်။ Cancelled လေလံကို lost ဟု မတွက်ပါ။ ကိုယ့် user ID ကို Telegram မှစစ်သဖြင့် အခြားသူရဲ့ history/balance ကို parameter ပြောင်းပြီးကြည့်မရပါ။ Private channel links ကိုဖွင့်ရန် channel membership လိုနိုင်သည်။ Wallet history note ရှည်လျှင် preview ကိုသာပြပြီး note အပြည့်ကို database ထဲသိမ်းသည်။

### Owner USD ထည့်/နုတ်ပြီး coin ပြောင်းခြင်း

**Owner ID စာရင်းထဲရှိသူပဲ** `/auth`, `/credit`, `/debit` ကို သုံးနိုင်သည် — group admin ဖြစ်ရုံနှင့် မသုံးနိုင်ပါ။ Owner သတ်မှတ်တဲ့ ပမာဏတွေက USD ဖြစ်ပြီး `$100 = 2500 coin` နှုန်းဖြင့် balance ထဲ coin ပြောင်းထည့်/နုတ်ပေးသည်။ ဥပမာ user ၏ message ကို reply လုပ်ပြီး `+100` ပို့လျှင် 2500 coin ထည့်ပေးမည်။ ID ဖြင့် သို့မဟုတ် reply ဖြင့် ပြင်နိုင်ပြီး ကိုယ့် message ကို reply လုပ်ခြင်းဖြင့် owner ကိုယ်တိုင်လည်း wallet ပြင်နိုင်သည်။ Positive credit ရလျှင် bot က user ၏ bot DM သို့ USD နဲ့ coin နှစ်မျိုးလုံးပြသော receipt ပို့မည်; DM ကို အရင် `/start` လုပ်ထားရန်လိုနိုင်သည်။ `/auth` သည် သတ်မှတ်ထားသော discussion group မှာလည်း အသုံးပြုနိုင်သည်။ ဥပမာ:

```text
# user message ကို reply လုပ်ပြီး raw amount ပို့ရန်
+100
-5
# command နဲ့ reply ပို့ရန်
/auth +$100
/auth -$5
/credit +$100 welcome
/debit -$5 correction
# ID ဖြင့်
/credit 123456789 $100 welcome
/debit 123456789 $5 correction
```

Numeric user ID ဖြင့် owner private chat သို့မဟုတ် သတ်မှတ်ထားသော group မှာ:

```text
/auth 123456789 +$100
/auth 123456789 -$5
/auth 123456789 +$20.50 deposit confirmed
```

`+` ကထည့်၊ `-` ကနုတ် ဖြစ်သည်။ ID ပါသော command သည် ထို ID ကိုပဲပြင်သည်။ ID မပါလျှင် reply လုပ်ထားသော message ရဲ့ **ပို့သူ** ကိုပြင်သည်; forwarded content ရဲ့ original author ကို မရွေးပါ။ Bot/channel/anonymous message ကို reply လုပ်ပြီး credit ထည့်မရပါ။ Owner က ကိုယ့် numeric ID သို့မဟုတ် ကိုယ့် message ကို reply လုပ်၍ ကိုယ့် wallet ကိုလည်း ထည့်/နုတ်နိုင်သည်။ Private chat ထဲ forwarded user message ကိုသုံးမည့်အစား numeric ID ပုံစံကိုသုံးပါ။

- Owner ပေးသော USD ပမာဏသည် 0 ထက်ကြီးပြီး decimal ၂ နေရာအထိသာ; လက်ကျန်ကို fixed rate အတိုင်း coin ပြောင်းတွက်သည်။ Note ကို စာလုံး 200 အထိထည့်နိုင်သည်။
- Available balance ထက်ပိုမနုတ်နိုင်သလို bid အတွက် held ငွေကိုလည်း နုတ်မရပါ။
- တူညီသော Telegram command update ထပ်ရောက်လျှင် နှစ်ခါမထည့်/မနုတ်ပါ။ Message edit လုပ်လျှင် ငွေမပြောင်းပါ; command အသစ်ပို့ပါ။
- Group မှာ ပြင်သည့်ပမာဏနဲ့ user ID ကိုအတည်ပြုပေးပြီး user ရဲ့လက်ကျန်အပြည့်ကို မဖော်ပြပါ။
- `/credit` နှင့် `/debit` private commands များလည်း ဆက်သုံးနိုင်သည်။
- **Bid ကျော်ခံရလျှင် private notification မပို့ပါ။** `/history` မှာ လက်ရှိအခြေအနေကြည့်နိုင်သည်။

### Balance စနစ်

**Wallet hold အမြဲဖွင့်ထားသည်။** Bid လက်ခံသည့်အချိန် available balance မှ ယာယီထိန်းထားငွေသို့ ပြောင်းသည်။ ငွေမလုံလောက်လျှင် bid ကိုငြင်းပယ်ပြီး လက်ရှိ winner နှင့် holds မပြောင်းပါ။ `/walletmode off` ဖြင့် ပိတ်၍မရပါ။

Owner က coin ထည့်ပြီး လေလံတင်ရန်:

```text
/auth 123456789 + 20.00 deposit confirmed
/walletmode on
/new
```

- User balance 20 coin ရှိပြီး wallet လေလံမှာ 5 coin bid ဆွဲလျှင် **Available 15 / Held 5 / Total 20 coin** ဖြစ်သည်။
- အခြားသူကျော်ဆွဲလျှင် မူလ bidder ၏ 5 coin hold ကိုပြန်လွှတ်သည်။ Cancel လုပ်လျှင်လည်း hold ကိုပြန်လွှတ်သည်။
- မူလ bidder က 6 coin ထပ်တင်လျှင် hold စုစုပေါင်း 6 coin ဖြစ်သည်။ နှစ်ခါပေါင်း 11 coin မဖြစ်ပါ။
- 5 coin နဲ့နိုင်သွားလျှင် wallet ကိုတစ်ကြိမ်သာဖြတ်ပြီး **Available 15 / Held 0 / Total 15 coin** ဖြစ်သည်။
- လေလံအများကြီးမှာ ဦးဆောင်နေပါက hold အားလုံးကိုပေါင်းပြီး available balance ကိုတွက်သည်။ လက်ကျန်ထက်ပို bid မဆွဲနိုင်ပါ။ Owner က held ငွေကို `/debit` နဲ့နုတ်မရပါ။
- `/debit 123456789 2.00 correction` ဖြင့် available balance မှနုတ်နိုင်သည်။ Note ကိုစာလုံး 200 အထိရေးနိုင်သည်။
- လေလံအသစ်တိုင်း wallet လိုအပ်သည်။ MongoDB migration မှာ open legacy auctions ကိုလည်း funded hold ဖြင့် ပြောင်းပေးသည်။ Balance မလုံလောက်သော legacy leader ရှိလျှင် migration ကိုငြင်းပယ်သည်။
- Ban သည် ယခင် bids/holds ကိုမဖျက်ပါ။ အနိုင်ရပြီး settled ဖြစ်သော payment ကိုပြန်ပြင်ရန် owner က reason ပါသော `/credit` လုပ်ရသည်။ ဒါသည် card အနိုင်ရမှတ်တမ်းကို ပြန်မဖျက်ပါ။

Owner credit/debit requests၊ holds၊ winner settlement များကို MongoDB replica-set transactions (snapshot reads, majority commit) ဖြင့်လုပ်သည်။ Transaction write conflicts ကို PyMongo က transaction အစမှ retry လုပ်သည်။ Request IDs နှင့် unique indexes ကြောင့် replay update မှ ထပ်မံ credit/charge မဖြစ်ပါ။ Restart ပြီးလည်း holds နှင့် ledger ဆက်ရှိသည်။ Bot service အတွက် `/data` Volume မလိုတော့ပါ။

## User `/start` welcome ပြင်ခြင်း

Owner private chat မှာ `/welcome` ပို့ပါ၊ သို့မဟုတ် `/panel` → **welcome** နှိပ်ပါ။ User တွေ bot private chat မှာ `/start` ပို့ရင် သိမ်းထားသော welcome ကိုမြင်ရသည်။ Owner `/start` က admin panel ကိုဖွင့်ပြီး user welcome ကို **Preview** နဲ့ကြည့်နိုင်သည်။ အခြား groups မှာ welcome မပို့ပါ။

- **Photo** — Telegram photo ပို့ပါ။ Caption ပါလျှင် အဲဒီ formatted caption ကို welcome text အဖြစ်ပါသိမ်းသည်။ Caption မပါလျှင် လက်ရှိစာသားကိုဆက်သုံးသည်။
- **Formatted text** — Telegram editor ရဲ့ bold, italic, underline, strikethrough, spoiler, quote, expandable quote, code, preformatted block, text link format များသုံးပြီး စာပို့ပါ။
- **HTML text** — အောက်က HTML ပုံစံအတိုင်း စာပို့နိုင်သည်။ Markdown `**bold**` ကို အလိုအလျောက် HTML မပြောင်းပါ; Telegram editor နဲ့ bold လုပ်ပါ သို့မဟုတ် HTML mode ကိုသုံးပါ။
- **Buttons / colours** — Link buttons အမည်၊ URL၊ အရောင်ကိုပြင်ပါ။
- **Preview**, **Remove photo**, **Clear buttons**, **Help**, **Cancel edit** ပါသည်။ `/welcomehelp` ဖြင့် အသုံးပြုပုံကြည့်၊ `/welcomecancel` ဖြင့် edit ရပ်နိုင်သည်။

ပို့လိုက်သည့်ပြင်ဆင်ချက်ကို owner ဆီ preview ပို့သည်။ Telegram က preview လက်ခံပြီးမှ database ထဲသိမ်းသည်။ မှားနေသော format/URL ဖြစ်လျှင် ယခင် setting ကိုဆက်သုံးပြီး edit ကို ပြန်ပို့နိုင်သည်။ သိမ်းထားသော welcome သည် restart ပြီးလည်း မပျောက်ပါ။ မသိမ်းရသေးသော editor state သည် restart ဖြစ်လျှင် `/welcome` မှ ပြန်စရသည်။

### User placeholders

| Placeholder | အဓိပ္ပာယ် |
| --- | --- |
| `{mention}` | `/start` ပို့သော user ကိုနှိပ်နိုင်သော mention |
| `{first_name}` / `{last_name}` | User အမည် |
| `{full_name}` | အမည်အပြည့် |
| `{username}` | `@username`; မရှိလျှင် အမည်အပြည့် |
| `{user_id}` | Telegram numeric user ID |
| `{bot_name}` / `{bot_username}` | Bot အမည် / `@username` |

Placeholder များကို မြင်ရသောစာသားထဲမှာပဲသုံးပါ။ HTML attribute နှင့် URL ထဲ မထည့်ပါနှင့်။ User အမည်များကို HTML escape လုပ်ပေးသည်။ Code block သို့မဟုတ် link တစ်ခုအတွင်း `{mention}` သုံးပါက အမည်စာသားအဖြစ်ပြသည်။

**HTML text** ဥပမာ:

```html
<b>မင်္ဂလာပါ {mention} 👋</b>
<i>{bot_name} မှ ကြိုဆိုပါတယ်။</i>
<blockquote>Channel ထဲက card ရဲ့ Comments မှာ bid ဆွဲနိုင်ပါတယ်။</blockquote>
<blockquote expandable>လေလံစည်းကမ်းအရှည်ကို ဒီမှာရေးနိုင်ပါတယ်။</blockquote>
<tg-spoiler>အထူးကြေညာချက်</tg-spoiler>
<code>/bid 5.00</code>
<a href="https://t.me/example">Channel ကိုသွားရန်</a>
```

Bold (`b/strong`), italic (`i/em`), underline (`u/ins`), strike (`s/strike/del`), spoiler (`tg-spoiler`/`span class="tg-spoiler"`), `blockquote`, `blockquote expandable`, `code`, `pre`, `pre` + `code class="language-python"`, `a href`, `tg-emoji emoji-id` များကိုသုံးနိုင်သည်။ Telegram က လက်ခံသော nesting နှင့် account/client ကခွင့်ပြုသော emoji များသာ အသုံးပြုနိုင်သည်။ Format နှင့် placeholders ဖြေပြီး စာလုံး 4096 အထိ; user name အရှည်အတွက် နေရာချန်ရန် စစ်ပေးသည်။ ပုံနှင့်စာတွဲပါက 1024 ကျော်လျှင် ပုံတစ်ခု၊ စာနှင့်buttons တစ်ခု ခွဲပို့ပြီး စာမဖြတ်ပါ။

### အရောင်ပါ link buttons

```text
Channel | https://t.me/example | blue
Support | https://t.me/example_support | green && Owner | tg://user?id=123456789 | red
```

တစ်ကြောင်းက row တစ်ခု၊ တစ်တန်းတည်းခလုတ်များကို `&&` နဲ့ခြားပါ။ တစ်တန်း 3 ခု၊ စုစုပေါင်း 12 ခုအထိ။ `red`, `green`, `blue` (သို့မဟုတ် `danger`, `success`, `primary`) သုံးပါ။ HTTPS/HTTP links နှင့် `tg://user?id=...` profile links ကိုလက်ခံသည်။ ဒီခလုတ်များသည် link ဖွင့်ပေးသော buttons ဖြစ်သည်။

## Restart / request failure

- Channel post ကို edit မရလျှင် database bid ကိုဆက်သိမ်းပြီး worker က retry လုပ်သည်။ Telegram rate limit ရှိပါက server ပြောသည့်ကြာချိန်ကိုစောင့်သည်။ Admin rights ပျောက်ခြင်း/ဖျက်ထားသော post ကိုပြင်မရခြင်းရှိပါက log မှ auction ID ကိုကြည့်ပြီး permissions စစ်ပါ။
- `sendPhoto` request timeout ဖြစ်သောအခါ post တကယ်တင်ပြီး/မပြီး မသေချာနိုင်သဖြင့် bot က ထပ်မတင်ပါ။ Auction ကို `publishing` အဖြစ်သိမ်းထားပြီး Telegram automatic discussion forward ရလာလျှင် ပြန်ချိတ်ပေးသည်။
- `/auctions` မှာ `publishing` ကျန်နေလျှင် channel ကိုစစ်ပါ။ Post ရှိပါက အဲဒီ post ရဲ့ discussion root ကို reply လုပ်သော message ပို့ပြီး bot ပြန်ချိတ်နိုင်အောင်လုပ်ပါ။ Telegram ရဲ့ pending updates က အကန့်အသတ်ရှိသဖြင့် bot ကို အချိန်ကြာကြာပိတ်ထားလျှင် root reply လိုနိုင်သည်။
- Recovery မဖြစ်ပါက `/cancelauction ID` ဖြင့် database record ပိတ်ပြီး channel မှ မသုံးတော့သော post ကို owner က manually ဖျက်ပါ။ ထို့နောက် `/new` ဖြင့် ပြန်တင်ပါ။
- `/pause` က end time ကို မရပ်ပါ။ လိုလျှင် မကုန်သေးသော auction တစ်ခုချင်းကို `/extend` လုပ်ပါ။
- MongoDB backup/snapshot ကို provider မှစီစဉ်ပါ။ SQLite အဟောင်းကို MongoDB သို့ရွှေ့ရန် အောက်ပါ migration လမ်းညွှန်ကိုသုံးပါ။

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q auction_bot main.py run.py tests
.venv/bin/python -m pip check
```

Legacy compatibility tests တွေက temporary SQLite databases သုံးသည်။ `TEST_MONGODB_URI` ပါလျှင် real MongoDB replica set ပေါ် synthetic databases ဖန်တီးပြီး စမ်းသပ်ကာ ပြန်ဖျက်သည်။ Telegram transport သည် synthetic ဖြစ်သည်။ ငွေအစစ်၊ user data အစစ်နှင့် Telegram API အစစ်ကို မထိပါ။ Real Telegram group/channel smoke test အတွက် valid token၊ configured chats နှင့် bot permissions လိုအပ်သည်။

## Project structure

```text
auction_bot/config.py   Environment validation
auction_bot/domain.py   Shared database-agnostic rules and currency helpers
auction_bot/mongo_store.py MongoDB transactions and persistent data
auction_bot/store.py    Test-only legacy SQLite adapter (not used at runtime)
auction_bot/migrate.py  Atomic SQLite-to-MongoDB migration
auction_bot/bot.py      Owner wizard, panel, comments, periodic updates
auction_bot/welcome.py  Welcome templates, placeholders, link buttons
auction_bot/account.py  Personal history, wins, active listings and wallet views
main.py / run.py        Polling entry points
tests/                 Auction engine and Telegram boundary tests
```

Telegram behavior references: [Bot API message and forward fields](https://core.telegram.org/bots/api#message), [linked chat information](https://core.telegram.org/bots/api#chatfullinfo), [caption editing](https://core.telegram.org/bots/api#editmessagecaption), [official button styles](https://core.telegram.org/bots/api#inlinekeyboardbutton).

## Auction post နှင့် bid edit timing

- Post ကို CHARACTER / STARTING BID / MIN. INCREMENT / CURRENT BID / HIGHEST BIDDER / TIME LEFT ပုံစံဖြင့် ပြသည်။ ကျန်ချိန်သည် `4min 50sec` ပုံစံဖြစ်သည်။
- ပထမ bid သို့မဟုတ် ၂ စက္ကန့်ကျော်ခြားသော bid ကို လက်ခံပြီးလျှင် post ချက်ချင်း edit လုပ်သည်။ ၂ စက္ကန့်အတွင်း bids ဆက်တက်နေပါက နောက်ဆုံး bid မှ ၂ စက္ကန့်ငြိမ်သည့်အခါ နောက်ဆုံးဈေးနှင့် bidder ကို တစ်ခါတည်းပြသည်။ Invalid/replayed bid က စောင့်ချိန်ကို မတိုးပါ။ Edit ပြီးပြီးချင်း ၂ စက္ကန့်အတွင်း bid အသစ်လာလျှင်လည်း ၂ စက္ကန့်ငြိမ်သည်အထိ ထပ်စောင့်သည်။
- Worker က 0.25 စက္ကန့်ခြား pending updates နှင့် deadline များစစ်သည်။ အပြောင်းအလဲမရှိလျှင် Telegram edit မပို့ပါ။ ပိတ်သွားသောလေလံက bid စောင့်ချိန်ကိုကျော်၍ final result ပြသည်။ Telegram RetryAfter/network backoff ရှိပါက ထိုကန့်သတ်ချိန်ကို ဆက်လိုက်နာသည်။
- PAYMENT တွင် owner တောင်းဆိုသော `Guess harem ထဲ 3min အတွင်း ကဒ်ဝင်လာပါလိမ့်မယ်` စာသားပြသည်။ ဤစာသားသည် owner ၏ပေးပို့မည့်အစီအစဉ်ဖြစ်သည်။ Guess harem API/ကဒ်ပေးပို့မှု automation မပါဝင်ပါ။ ပိတ်ပြီး winner မရှိသော/ဖျက်သိမ်းသောလေလံမှာ ကဒ်ပေးပို့မှုမရှိကြောင်း ပြသည်။

## Owner ကိုယ့် wallet နှင့် Telegram command menu

- Owner က `/auth OWNER_ID + 20` ဖြင့် ကိုယ့် wallet ထဲထည့်နိုင်သည်။ `/auth OWNER_ID - 5` ဖြင့် နုတ်နိုင်သည်။ Allowed group မှာ ကိုယ့် message ကို reply လုပ်ပြီး `/auth + 20` လည်း သုံးနိုင်သည်။ `/credit`, `/debit` မှာလည်း owner ID လက်ခံသည်။ `/bal` ဖြင့် ကိုယ့်လက်ကျန်ကြည့်ပါ။
- Private Telegram menu: `/start`, `/menu`, `/history`, `/wins`, `/auctions`, `/bal`, `/transactions`။ Group menu: `/bid`, `/rules`။ Owner commands ကို menu မှာမပြဘဲ owner က `/panel` ဖြင့် ဆက်သုံးနိုင်သည်။
- Startup မှာ default/private/group/admin scopes နှင့် owner private chat scopes အတွက် default, Myanmar, English command lists ကို user commands များဖြင့် သတ်မှတ်သည်။ Menu setup ယာယီမအောင်မြင်လျှင် polling ဆက်လည်ပြီး retry လုပ်သည်။ Menu ပြင်ခြင်းက command permissions ကိုမပြောင်းပါ။

## Post ကျန်ချိန်နှင့် owner permissions

- Preview/publish မှာ `5min` ကဲ့သို့ owner သတ်မှတ်သောကြာချိန်ပြသည်။ Active post ကို bid ကြောင့် edit လုပ်ချိန်မှာ database ပိတ်ချိန်မှ လက်ရှိအချိန်နုတ်၍ `4min 50sec` လိုပြသည်။ Burst စောင့်ပြီး edit/retry လုပ်လျှင်လည်း တကယ် edit ပို့ချိန်မှ ပြန်တွက်သည်။
- ကျန်ချိန်တစ်ခုတည်းပြောင်းခြင်းကြောင့် post ကို edit မလုပ်ပါ။ ထို့ကြောင့် bid မတက်လျှင် မြင်ရသောကျန်ချိန်က နောက်ဆုံး edit ချိန်ကတန်ဖိုးအတိုင်းရှိသည်။ ပိတ်ချိန်ရောက်လျှင် bid မရှိလည်း ပိတ်ပြီး final result နှင့် `0sec` ပြသည်။ Owner close/cancel လုပ်လျှင်လည်း `0sec` ဖြစ်သည်။
- Owner-only commands နှင့် owner buttons ကို user က command ရိုက်ခြင်း၊ bot username ထည့်ခြင်း၊ callback data အတုလုပ်ခြင်းဖြင့် မသုံးနိုင်ပါ။ User `/auctions` သည် active listing ကြည့်ရန်သာ၊ `/rules` သည် စည်းကမ်းဖတ်ရန်သာဖြစ်ပြီး settings/ငွေစာရင်း/card များကို မပြင်နိုင်ပါ။

## User menu navigation

- Wallet တွင် ID၊ သုံးနိုင်ငွေ၊ Bid အတွက်ထိန်းထားငွေ၊ စုစုပေါင်းနှင့် Deposit/withdrawal အတွက် owner ဆက်သွယ်ရန်စာသာ ပြသည်။
- User inline buttons က လက်ရှိ message ကို edit လုပ်သည်။ History/Wins/Wallet/Transactions/Active auctions မှ `⬅️ Back` နှိပ်ပြီး account menu ပြန်ရောက်သည်။ Account menu ကို `✖️ Close` ဖြင့်ပိတ်ပြီး `Open menu` ဖြင့် ပြန်ဖွင့်နိုင်သည်။
- Welcome photo ပေါ်က account button နှိပ်လျှင် ပုံမပြောင်းဘဲ caption ကို edit လုပ်သည်။ Caption ထက်ရှည်သောစာရင်းများကို `Previous text`/`Next text` ဖြင့် စာမျက်နှာခွဲပြသည်။ Active auctions မှာ အရေအတွက်နှင့် Search ညွှန်ကြားချက်သာပြပြီး ကဒ်များကို Inline search မှာကြည့်နိုင်သည်။
- Slash commands သည် အသစ်ဝင်ရန် message တစ်ခု ပို့ပေးသည်။ မပြောင်းသောခလုတ်ကို ထပ်နှိပ်လျှင် message မပွားပါ။ Edit မရသော menu တွင် popup က `/menu` ပြန်ဝင်ရန်ပြောသည်။

## SQLite အဟောင်းမှ MongoDB ပြောင်းရွှေ့ရန်

1. Bot အဟောင်းကိုရပ်ပြီး SQLite online backup ဖန်တီးပါ။ Data ပါသော MongoDB target ကို မသုံးပါနှင့်။
2. `.env` မှာ MONGODB_URI နှင့် MONGODB_DATABASE ထည့်ပါ။
3. `python -m auction_bot.migrate --sqlite data/auctions.sqlite3` ဖြင့် dry run လုပ်ပါ။ Source schema၊ holds၊ available funds ကိုစစ်သည်။ Open legacy leader တွင်ငွေမလုံလောက်လျှင် owner က credit ထည့် သို့မဟုတ် လေလံပိတ်/ဖျက်ပြီးမှ ပြန်လုပ်ပါ။
4. `python -m auction_bot.migrate --sqlite data/auctions.sqlite3 --apply` ဖြင့် empty target ထဲ transaction တစ်ခုတည်းနှင့် import လုပ်ပါ။ Source SQLite ကိုမပြင်ပါ။ Target တွင် data/settings ရှိပြီးသား၊ import လုပ်ပြီးသားဆို မထပ်ရေးပါ။ Failed import တွင် partial data မကျန်ပါ။
5. Auctions/bids၊ wallet/holds/events၊ settings/welcome၊ bans၊ comment mappings နှင့် ID counters အားလုံးရွှေ့ပြီးမှ `python run.py` ဖြင့် bot တစ်ခုတည်းဖွင့်ပါ။ MongoDB အသစ်ထဲ credit/bid မလုပ်ခင်မှသာ SQLite backup ကို rollback source အဖြစ်ပြန်သုံးနိုင်သည်။

Migration source က လက်ရှိ schema ဖြစ်ရမည်။ SQLite ကို one-time migration source နှင့် compatibility tests အတွက်သာသုံးသည်; production `run.py` သည် MongoDB တစ်ခုတည်းသုံးပြီး MongoDB URI မရှိလျှင် မစပါ။

MongoDB integration tests:

```bash
TEST_MONGODB_URI='mongodb://127.0.0.1:27027/?replicaSet=authbid' .venv/bin/python -m unittest discover -s tests -v
```

Test URI သည် disposable replica set သာဖြစ်ရမည်။ Production URI ကို TEST_MONGODB_URI အဖြစ် မသုံးပါနှင့်။

## Inline search နှင့် သီးခြားလေလံများ

- BotFather → `/setinline` → bot ကိုရွေး → `Search waifu auctions` ပို့ပြီး Inline mode ဖွင့်ပါ။ Bot API မှ ဒီ setting ကိုဖွင့်၍မရပါ။
- User `/auctions` စာရင်းအောက်မှ `🔎 Inline search` နှိပ်လျှင် လက်ရှိ chat ရဲ့စာရိုက်နေရာမှာ `@botusername` ဝင်လာသည်။ Card name၊ anime၊ rarity သို့ `#ID` ရိုက်၍ ဖွင့်ထားသောလေလံများရှာနိုင်သည်။ Query ဗလာဆို active cards အားလုံးကို စာမျက်နှာခွဲပြသည်။
- Inline result သည် ကဒ်ပုံနှင့် နောက် bid ပမာဏပါတဲ့ snapshot ဖြစ်သည်။ `Open original post · Comments` နှိပ်ပြီး မူရင်း post ရဲ့ Comments မှာသာ `/bid amount` တင်ပါ။ ရှာဖွေပြီးပို့ထားသောကဒ်မိတ္တူမှာ bids မတင်နိုင်ပါ။ မူရင်း post သာ live update ရသည်။ Inline mode မှ personal wallet/history ကိုမဖော်ပြပါ။
- Owner က `/new` → Publish ကို ထပ်လုပ်ပြီး လေလံအများကြီး တစ်ပြိုင်နက်ဖွင့်နိုင်သည်။ လေလံတစ်ခုစီတွင် သီးခြား ID၊ channel post ID၊ discussion root၊ bids၊ holds နှင့် deadline ရှိသည်။
- Comment thread နဲ့ reply က မတူသောလေလံကိုညွှန်လျှင် bid ကိုငြင်းပယ်သည်။ မသိသော thread၊ inline/forward မိတ္တူ၊ bot private chat၊ အခြား group နှင့် group ပုံမှန်စာရိုက်နေရာတွင် bid မလက်ခံပါ။ တစ်ခုကိုပိတ်/ဖျက်ခြင်းသည် တခြားလေလံများ၏ bids/holds ကိုမပြောင်းပါ။

Inline မှပို့သောကဒ် caption သည် `🌸 Name`, `📺 Anime`, `💎 ⚜️ Rarity`, `🆔 Auction ID`, `🎴Start Bid - amount coin` သာပါသည်။ မူရင်း post Comments link ခလုတ်ကို ဆက်ထားသည်။

Default minimum increment is 250coin. The first bid may equal the starting price; subsequent bids must be at least 250coin above the current bid (larger increases are accepted). Owner /increment applies to future auctions.

Winner notices are sent to the original card's discussion thread, with a linked display name and a View Win Card button. Successful sends are recorded persistently; failed sends retry with backoff. Historical closed auctions are not announced on upgrade. Telegram cannot guarantee exactly-once delivery if a send succeeds but its response or the following database write is lost. The "Payment Deadline: 5 Min" line is display text; wallet settlement still happens at auction close and no additional payment timer is started.

Owner DM command /zip sends the running source code and Railway deployment files as auth-bot-code.zip. Includes .env.example; excludes actual credentials, databases, logs, Git metadata and dependencies. It is not listed in the public Telegram command menu.
