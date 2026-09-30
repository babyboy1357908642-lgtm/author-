# Railway deployment — MongoDB

## Variables

Private ZIP မှ `.env` ကို Railway Service → Variables → Raw Editor ထဲထည့်ပါ။

```dotenv
BOT_TOKEN=your_botfather_token
OWNER_IDS=1735522859
CHANNEL_ID=-1004335666883
GROUP_ID=your_linked_group_id
MONGODB_URI=mongodb+srv://USERNAME:PASSWORD@YOUR_CLUSTER/?retryWrites=true&w=majority
MONGODB_DATABASE=authbid_bot
```

MongoDB URI မပေးရသေးလျှင် ZIP ရဲ့ MONGODB_URI ဗလာဖြစ်သည်။ ကိုယ့် connection string ဖြည့်ပြီးမှ deploy လုပ်ပါ။ `.env` ကို GitHub ထဲမတင်ပါနှင့်။

MongoDB Atlas သို့ transactions ရသော replica set/sharded cluster လိုသည်။ Standalone MongoDB template တစ်ခုတည်းကို transactions မရဘဲ သုံး၍မရပါ။ Database user ကို သတ်မှတ် database အတွက် read/write ခွင့်ပေးပြီး bot service မှချိတ်ဆက်နိုင်အောင် network access စီစဉ်ပါ။ URI password မှ special characters ကို URL-encode လုပ်ပါ။

Bot container က data ကို MongoDB ထဲသိမ်းသည်။ ယခင် SQLite `/data` volume ကို bot အသစ်အတွက် မလိုပါ။ MongoDB ကို ကိုယ်တိုင် host လုပ်ပါက database service ၏ persistent storage/replica set/backup ကို သီးခြားစီစဉ်ရမည်။

## Existing data

ZIP မှာ production database/backups မပါပါ။ Sandbox က settings/welcome/wallets/bids ကို ဆက်သုံးမည်ဆို bot အဟောင်းကိုရပ်ပြီး README ရဲ့ SQLite → MongoDB migration ကို အရင်လုပ်ပါ။

```bash
python -m auction_bot.migrate --sqlite /path/to/old/auctions.sqlite3
python -m auction_bot.migrate --sqlite /path/to/old/auctions.sqlite3 --apply
```

Empty MongoDB target သို့သာ import လုပ်သည်။ Active legacy bid အတွက်ငွေမလုံလောက်လျှင် migration ရပ်သည်။ Source ကိုမပြင်ပါ။ ထပ်ခါ import လုပ်လျှင် existing data ကိုမရေးပါ။

## Deploy

1. GitHub repo ချိတ်ခြင်း သို့ Railway CLI `railway up` ဖြင့် source တင်ပါ။ ZIP ကိုဖြည်ပြီး project directory မှ run ပါ။
2. Variables ကိုအရင်ဖြည့်ပါ။ Railway က Dockerfile ဖြင့် Python 3.11 image build လုပ်သည်။ `python -u run.py` သည် worker entry point ဖြစ်သည်။
3. Token တစ်ခုအတွက် polling process တစ်ခုတည်းထားပါ။ Sandbox bot အဟောင်းရှိပါက Railway စမတိုင်မီ ရပ်ပါ။ railway.json က replica 1/overlap 0 သတ်မှတ်ထားသည်။
4. Logs မှာ `Application started` စစ်ပါ။ MongoDB မချိတ်နိုင်/replica set မဟုတ်လျှင် startup ရပ်ပြီး SQLite သို့ fallback မလုပ်ပါ။
5. Owner `/settings`, `/check`, `/bal` စစ်ပြီး `/auth USER_ID + 20` ဖြင့် user ကိုငွေထည့်ပါ။ Bid တင်လျှင် available မှ held သို့ပြောင်း၊ outbid/cancel မှာပြန်လွှတ်၊ winner ကိုအပြီးဖြတ်သည်။

Bot သည် outbound polling worker ဖြစ်ပြီး HTTP domain/port မလိုပါ။ MongoDB transactions နှင့် URI guidance: [MongoDB PyMongo transactions](https://www.mongodb.com/docs/languages/python/pymongo-driver/current/crud/transactions/).
