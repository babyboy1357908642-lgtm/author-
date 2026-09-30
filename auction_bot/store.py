"""Legacy SQLite adapter retained only for import compatibility tests; production uses MongoDB."""
import sqlite3
import time
import random
from contextlib import contextmanager
from pathlib import Path
from .domain import MAX_ACTIVE_PVP_GAMES, MAX_PVP_WAGER, MIN_PVP_WAGER, USD_TO_COIN_RATE, RuleError, cents, money, usd_to_coins


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS auctions(
          id INTEGER PRIMARY KEY, photo TEXT NOT NULL, name TEXT NOT NULL,
          anime TEXT NOT NULL, card_type TEXT NOT NULL DEFAULT '', card_id TEXT NOT NULL DEFAULT '',
          rarity TEXT NOT NULL, start INTEGER NOT NULL,
          increment INTEGER NOT NULL, ends INTEGER NOT NULL,
          channel_id INTEGER NOT NULL, group_id INTEGER NOT NULL,
          post_id INTEGER, root_id INTEGER, status TEXT NOT NULL DEFAULT 'publishing',
          highest INTEGER, winner_id INTEGER, winner_name TEXT,
          version INTEGER NOT NULL DEFAULT 1, rendered INTEGER NOT NULL DEFAULT 0,
          created INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS bids(
          id INTEGER PRIMARY KEY, auction_id INTEGER NOT NULL REFERENCES auctions(id),
          user_id INTEGER NOT NULL, user_name TEXT NOT NULL, amount INTEGER NOT NULL,
          created INTEGER NOT NULL, chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
          UNIQUE(chat_id, message_id));
        CREATE INDEX IF NOT EXISTS bids_by_user ON bids(user_id,auction_id,id);
        CREATE INDEX IF NOT EXISTS active_by_deadline ON auctions(status,ends);
        CREATE TABLE IF NOT EXISTS wallets(
          user_id INTEGER PRIMARY KEY, balance INTEGER NOT NULL DEFAULT 0 CHECK(balance>=0));
        CREATE TABLE IF NOT EXISTS daily_claims(
          user_id INTEGER PRIMARY KEY, claimed_at INTEGER NOT NULL, reward INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS holds(
          auction_id INTEGER PRIMARY KEY REFERENCES auctions(id),
          user_id INTEGER NOT NULL REFERENCES wallets(user_id), amount INTEGER NOT NULL CHECK(amount>0));
        CREATE INDEX IF NOT EXISTS holds_by_user ON holds(user_id);
        CREATE TABLE IF NOT EXISTS wallet_events(
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES wallets(user_id),
          delta INTEGER NOT NULL, kind TEXT NOT NULL, note TEXT NOT NULL,
          actor_id INTEGER, auction_id INTEGER REFERENCES auctions(id),
          event_key TEXT NOT NULL UNIQUE, created INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS wallet_events_by_user ON wallet_events(user_id,id);
        CREATE TABLE IF NOT EXISTS banned(user_id INTEGER PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS messages(
          chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
          auction_id INTEGER NOT NULL REFERENCES auctions(id),
          PRIMARY KEY(chat_id, message_id));
        CREATE TABLE IF NOT EXISTS pvp_games(
          id TEXT PRIMARY KEY, group_id INTEGER NOT NULL,
          requester_id INTEGER NOT NULL, requester_name TEXT NOT NULL,
          target_id INTEGER NOT NULL, target_name TEXT NOT NULL,
          amount INTEGER NOT NULL CHECK(amount>0), status TEXT NOT NULL,
          message_id INTEGER NOT NULL DEFAULT 0, created INTEGER NOT NULL,
          next_at INTEGER, step INTEGER NOT NULL DEFAULT 0,
          final_percent INTEGER, winner_id INTEGER,
          slot_notified INTEGER NOT NULL DEFAULT 0,
          mode TEXT NOT NULL DEFAULT 'duel', choice TEXT, result TEXT);
        CREATE INDEX IF NOT EXISTS pvp_games_by_group_status ON pvp_games(group_id,status,next_at);
        CREATE INDEX IF NOT EXISTS pvp_games_by_players_status ON pvp_games(status,requester_id,target_id);
        """)
        # Nullable column preserves deadlines of auctions created by older versions.
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(auctions)")}
        if "winner_notified" not in columns:
            with self.transaction():
                self.db.execute("ALTER TABLE auctions ADD COLUMN winner_notified INTEGER NOT NULL DEFAULT 0")
                # Do not announce historical sales when upgrading.
                self.db.execute("UPDATE auctions SET winner_notified=1 WHERE status IN ('closed','cancelled')")
        if "duration_seconds" not in columns:
            self.db.execute("ALTER TABLE auctions ADD COLUMN duration_seconds INTEGER")
        if "wallet_required" not in columns:
            self.db.execute("ALTER TABLE auctions ADD COLUMN wallet_required INTEGER NOT NULL DEFAULT 0")
        if "card_type" not in columns:
            self.db.execute("ALTER TABLE auctions ADD COLUMN card_type TEXT NOT NULL DEFAULT ''")
        if "card_id" not in columns:
            self.db.execute("ALTER TABLE auctions ADD COLUMN card_id TEXT NOT NULL DEFAULT ''")
        pvp_columns = {row[1] for row in self.db.execute("PRAGMA table_info(pvp_games)")}
        for name, definition in (("mode", "TEXT NOT NULL DEFAULT 'duel'"),
                                 ("choice", "TEXT"), ("result", "TEXT")):
            if name not in pvp_columns:
                with self.transaction():
                    self.db.execute(f"ALTER TABLE pvp_games ADD COLUMN {name} {definition}")
        for key, value in [("wallet_mode", "0"), ("increment", "5000"), ("paused", "0"), ("rules", "Winner ကို owner က ဆက်သွယ်ပါမယ်။ Payment ကို owner နှင့် တိုက်ရိုက်ညှိပါ။")]:
            self.db.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (key, value))

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def get(self, key, default=""):
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, str(value)))

    def target(self, key, value):
        if self.get(key) == str(value):
            return
        if self.db.execute("SELECT 1 FROM auctions WHERE status IN ('active','publishing') LIMIT 1").fetchone():
            raise RuleError("Channel/group ပြောင်းမယ်ဆို active/publishing လေလံတွေကို အရင်ပိတ်ပါ။")
        self.set(key, value)

    def set_pvp_group(self, group_id):
        with self.transaction():
            if str(group_id) != self.get("pvp_group_id") and self.db.execute(
                    "SELECT 1 FROM pvp_games WHERE status IN ('pending','running') LIMIT 1").fetchone():
                raise RuleError("PvP group ပြောင်းရန် pending/running ပွဲများကို အရင်ရှင်းပါ။")
            self.set("pvp_group_id", group_id)

    def auction(self, auction_id):
        row = self.db.execute("SELECT * FROM auctions WHERE id=?", (auction_id,)).fetchone()
        if not row:
            raise RuleError("လေလံ ID မတွေ့ပါ။")
        return dict(row)

    def create(self, card, now=None):
        now = int(time.time()) if now is None else now
        duration = card.get("duration_seconds")
        if duration is not None and (type(duration) is not int or not 1 <= duration <= 604800):
            raise RuleError("ကြာချိန်ကို 1sec မှ 7days အတွင်း သတ်မှတ်ပါ။")
        ends = now + duration if duration is not None else card["ends"]
        if ends <= now:
            raise RuleError("End time က အနာဂတ်ဖြစ်ရပါမယ်။")
        if not self.get("channel_id") or not self.get("group_id"):
            raise RuleError("/setchannel နှင့် /setgroup အရင်သတ်မှတ်ပါ။")
        cur = self.db.execute("""INSERT INTO auctions
          (photo,name,anime,card_type,card_id,rarity,start,increment,ends,channel_id,group_id,created,duration_seconds,wallet_required)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            card["photo"], card["name"], card["anime"], card.get("card_type", ""), card.get("card_id", ""), card["rarity"], card["start"],
            int(self.get("increment")), ends, int(self.get("channel_id")),
            int(self.get("group_id")), now, duration, int(self.get("wallet_mode"))))
        return cur.lastrowid

    def published(self, auction_id, post_id, published_at=None):
        published_at = int(time.time()) if published_at is None else published_at
        # Telegram's original post date is authoritative, including timeout recovery.
        # The status guard prevents repeated forwards from resetting the countdown.
        self.db.execute("""UPDATE auctions SET post_id=?, status='active',
          ends=CASE WHEN duration_seconds IS NULL THEN ends ELSE ?+duration_seconds END,
          version=version+1 WHERE id=? AND status='publishing'""",
          (post_id, published_at, auction_id))

    def attach(self, auction_id, post_id, root_id, published_at=None):
        row = self.auction(auction_id)
        if row["post_id"] not in (None, post_id) or row["root_id"] not in (None, root_id):
            return False
        self.db.execute("UPDATE auctions SET post_id=?,root_id=? WHERE id=?", (post_id, root_id, auction_id))
        self.published(auction_id, post_id, published_at)
        self.map_message(row["group_id"], root_id, auction_id)
        return True

    def map_message(self, chat_id, message_id, auction_id):
        self.db.execute("INSERT OR IGNORE INTO messages VALUES (?,?,?)", (chat_id, message_id, auction_id))

    def thread_auction(self, chat_id, root_id):
        row=self.db.execute("SELECT id FROM auctions WHERE group_id=? AND root_id=?",(chat_id,root_id)).fetchone()
        return row[0] if row else None

    def resolve(self, chat_id, message_ids):
        matches=set()
        for message_id in message_ids:
            if message_id:
                row=self.db.execute("SELECT auction_id FROM messages WHERE chat_id=? AND message_id=?",(chat_id,message_id)).fetchone()
                if row:matches.add(row[0])
        return matches.pop() if len(matches)==1 else None

    def search_active(self, query="", offset=0, limit=20, now=None):
        if not 0<=offset<=1000000 or not 1<=limit<=50 or len(query)>256:
            raise RuleError("Search parameter မမှန်ပါ။")
        now=int(time.time()) if now is None else now
        terms="status='active' AND ends>? AND post_id IS NOT NULL AND channel_id=? AND group_id=?"
        args=[now,self.get("channel_id"),self.get("group_id")]
        query=query.strip()
        if query:
            terms+=" AND (instr(lower(name),lower(?))>0 OR instr(lower(anime),lower(?))>0 OR instr(lower(rarity),lower(?))>0 OR id=?)"
            number=query.lstrip('#')
            aid=int(number) if number.isascii() and number.isdigit() and len(number)<19 else -1
            args.extend([query,query,query,aid])
        rows=[dict(r) for r in self.db.execute(f"SELECT * FROM auctions WHERE {terms} ORDER BY id DESC LIMIT ? OFFSET ?",args+[limit+1,offset])]
        return rows[:limit],len(rows)>limit

    def bid(self, auction_id, user_id, user_name, amount, chat_id, message_id, now=None):
        now = int(time.time()) if now is None else now
        with self.transaction():
            row = self.auction(auction_id)
            allowed_groups = {self.get("group_id"), self.get("pvp_group_id")}
            if row["group_id"] != int(self.get("group_id") or 0) or str(chat_id) not in allowed_groups:
                raise RuleError("သတ်မှတ်ထားတဲ့ group မှာပဲ bid ဆွဲနိုင်ပါတယ်။")
            if self.db.execute("SELECT 1 FROM bids WHERE chat_id=? AND message_id=?", (chat_id, message_id)).fetchone():
                return False
            if self.get("paused") == "1":
                raise RuleError("Owner က bidding ခဏရပ်ထားပါတယ်။")
            if self.is_banned(user_id):
                raise RuleError("သင့်အကောင့်ကို bid ဆွဲခွင့်ပိတ်ထားပါတယ်။")
            if row["status"] != "active" or now >= row["ends"]:
                raise RuleError("ဒီလေလံ ပိတ်သွားပါပြီ။")
            minimum = row["start"] if row["highest"] is None else row["highest"] + row["increment"]
            if amount < minimum:
                raise RuleError(f"အနည်းဆုံး {money(minimum)} bid ဆွဲရပါမယ်။")
            if row["wallet_required"]:
                self.db.execute("INSERT OR IGNORE INTO wallets(user_id) VALUES (?)", (user_id,))
                balance = self.wallet_balance(user_id)
                previous = self.db.execute("SELECT amount FROM holds WHERE auction_id=? AND user_id=?", (auction_id,user_id)).fetchone()
                usable = balance["available"] + (previous[0] if previous else 0)
                if amount > usable:
                    raise RuleError(f"လက်ကျန်မလုံလောက်ပါ။ ဒီ bid အတွက် သုံးနိုင်ငွေ {money(usable)}။ /balance ကို private chat မှာစစ်ပါ။")
                self.db.execute("DELETE FROM holds WHERE auction_id=?", (auction_id,))
                self.db.execute("INSERT INTO holds VALUES (?,?,?)", (auction_id,user_id,amount))
            self.db.execute("INSERT INTO bids(auction_id,user_id,user_name,amount,created,chat_id,message_id) VALUES (?,?,?,?,?,?,?)", (auction_id,user_id,user_name,amount,now,chat_id,message_id))
            self.db.execute("UPDATE auctions SET highest=?,winner_id=?,winner_name=?,version=version+1 WHERE id=?", (amount,user_id,user_name,auction_id))
            return True

    def is_banned(self, user_id):
        return bool(self.db.execute("SELECT 1 FROM banned WHERE user_id=?", (user_id,)).fetchone())

    def ban(self, user_id, blocked):
        if blocked:
            self.db.execute("INSERT OR IGNORE INTO banned VALUES (?)", (user_id,))
        else:
            self.db.execute("DELETE FROM banned WHERE user_id=?", (user_id,))

    def finish(self, auction_id, status):
        with self.transaction():
            self._finish(auction_id, status)

    def _finish(self, auction_id, status):
        if status not in ("closed", "cancelled"):
            raise RuleError("လေလံ status မမှန်ပါ။")
        row = self.auction(auction_id)
        allowed = ("active", "publishing") if status == "cancelled" else ("active",)
        if row["status"] not in allowed:
            raise RuleError("ဒီလေလံကို ယခုလုပ်ဆောင်ချက် မလုပ်နိုင်ပါ။")
        if status == "closed" and row["wallet_required"] and row["highest"] is not None:
            hold = self.db.execute("SELECT * FROM holds WHERE auction_id=?", (auction_id,)).fetchone()
            if not hold or hold["user_id"] != row["winner_id"] or hold["amount"] != row["highest"]:
                raise RuleError("Wallet hold မကိုက်ညီပါ။ Owner က စစ်ဆေးရန်လိုပါတယ်။")
            self.db.execute("UPDATE wallets SET balance=balance-? WHERE user_id=?", (hold["amount"],hold["user_id"]))
            self.db.execute("""INSERT INTO wallet_events(user_id,delta,kind,note,auction_id,event_key,created)
              VALUES (?,?,'win',?,?,?,?)""", (hold["user_id"],-hold["amount"],"Auction purchase",auction_id,f"settle:{auction_id}",int(time.time())))
        self.db.execute("DELETE FROM holds WHERE auction_id=?", (auction_id,))
        self.db.execute("UPDATE auctions SET status=?,version=version+1 WHERE id=?", (status, auction_id))

    def extend(self, auction_id, minutes):
        row = self.auction(auction_id)
        if row["status"] != "active" or row["ends"] <= time.time():
            raise RuleError("Active လေလံကိုပဲ အချိန်တိုးနိုင်ပါတယ်။")
        if not 1 <= minutes <= 10080:
            raise RuleError("1–10080 minutes သာရေးပါ။")
        self.db.execute("UPDATE auctions SET ends=ends+?,version=version+1 WHERE id=?", (minutes * 60, auction_id))

    def close_due(self, now=None):
        now = int(time.time()) if now is None else now
        with self.transaction():
            due = self.db.execute("SELECT id FROM auctions WHERE status='active' AND ends<=? ORDER BY id", (now,)).fetchall()
            for row in due:
                self._finish(row["id"], "closed")

    def pending_winners(self):
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM auctions WHERE status='closed' AND winner_id IS NOT NULL "
            "AND winner_notified=0 AND root_id IS NOT NULL AND post_id IS NOT NULL ORDER BY id")]

    def winner_notified(self, auction_id):
        self.db.execute("UPDATE auctions SET winner_notified=1 WHERE id=?", (auction_id,))

    def dirty(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM auctions WHERE post_id IS NOT NULL AND version>rendered ORDER BY id")]

    def rendered(self, auction_id, version):
        self.db.execute("UPDATE auctions SET rendered=? WHERE id=?", (version, auction_id))

    def listing(self, limit=30):
        return [dict(r) for r in self.db.execute("SELECT * FROM auctions ORDER BY id DESC LIMIT ?", (limit,))]

    def history(self, auction_id, limit=30):
        self.auction(auction_id)
        return [dict(r) for r in self.db.execute("SELECT * FROM bids WHERE auction_id=? ORDER BY id DESC LIMIT ?", (auction_id,limit))]

    def user_history(self, user_id, wins=False, now=None):
        now = int(time.time()) if now is None else now
        clause = ""
        params = [user_id]
        if wins:
            clause = " AND a.winner_id=? AND (a.status='closed' OR (a.status='active' AND a.ends<=?))"
            params.extend([user_id, now])
        return [dict(row) for row in self.db.execute("""SELECT a.*, b.my_bid, b.last_bid
          FROM auctions a JOIN (
            SELECT auction_id,MAX(amount) AS my_bid,MAX(id) AS last_bid
            FROM bids WHERE user_id=? GROUP BY auction_id
          ) b ON b.auction_id=a.id WHERE 1=1""" + clause + " ORDER BY b.last_bid DESC LIMIT 10", params)]

    def active_auctions(self, page=0, now=None):
        now = int(time.time()) if now is None else now
        params = (now, self.get("channel_id"), self.get("group_id"))
        where = "status='active' AND ends>? AND channel_id=? AND group_id=?"
        count = self.db.execute("SELECT COUNT(*) FROM auctions WHERE " + where, params).fetchone()[0]
        page = min(page, max(0, (count - 1) // 5))
        rows = self.db.execute("SELECT * FROM auctions WHERE " + where + " ORDER BY ends,id LIMIT 5 OFFSET ?", params + (page * 5,))
        return count, [dict(row) for row in rows], page

    def wallet_balance(self, user_id):
        row = self.db.execute("""SELECT
          COALESCE((SELECT balance FROM wallets WHERE user_id=?),0) AS total,
          COALESCE((SELECT SUM(amount) FROM holds WHERE user_id=?),0) AS held""", (user_id,user_id)).fetchone()
        return {"total": row["total"], "held": row["held"], "available": row["total"]-row["held"]}

    def adjust_wallet(self, user_id, delta, actor_id, event_key, note=""):
        if type(user_id) is not int or not 0 < user_id < 2**63 or type(delta) is not int or not 0 < abs(delta) <= 99999999999 or len(note)>200:
            raise RuleError("User ID၊ ပမာဏ သို့မဟုတ် note (200 စာလုံးအထိ) မမှန်ပါ။")
        with self.transaction():
            existing = self.db.execute("SELECT user_id,delta,actor_id FROM wallet_events WHERE event_key=?", (event_key,)).fetchone()
            if existing:
                if tuple(existing) != (user_id,delta,actor_id):
                    raise RuleError("ဒီ request ကို ပြင်ပြီးပြန်သုံးလို့မရပါ။")
                return False
            self.db.execute("INSERT OR IGNORE INTO wallets(user_id) VALUES (?)", (user_id,))
            balance = self.wallet_balance(user_id)
            if balance["available"]+delta < 0:
                raise RuleError("ထိန်းထားတဲ့ bid ငွေကို နုတ်လို့မရပါ။ Available balance မလုံလောက်ပါ။")
            if balance["total"]+delta > 99999999999:
                raise RuleError("Wallet ပမာဏအများဆုံး ကျော်နေပါတယ်။")
            self.db.execute("UPDATE wallets SET balance=balance+? WHERE user_id=?", (delta,user_id))
            self.db.execute("""INSERT INTO wallet_events(user_id,delta,kind,note,actor_id,event_key,created)
              VALUES (?,?,?,?,?,?,?)""", (user_id,delta,"credit" if delta>0 else "debit",note,actor_id,event_key,int(time.time())))
            return True

    def wallet_history(self, user_id):
        return [dict(row) for row in self.db.execute("SELECT * FROM wallet_events WHERE user_id=? ORDER BY id DESC LIMIT 10", (user_id,))]
    def claim_dailycoin(self, user_id, now=None):
        if type(user_id) is not int or not 0 < user_id < 2**63:
            raise RuleError("User ID မမှန်ပါ။")
        now = int(time.time()) if now is None else int(now)
        with self.transaction():
            previous = self.db.execute("SELECT claimed_at FROM daily_claims WHERE user_id=?", (user_id,)).fetchone()
            if previous:
                next_at = previous["claimed_at"] + 86400
                if now < next_at:
                    return {"claimed": False, "remaining": next_at - now}
            reward = random.randint(500, 2000) * 100
            balance = self.wallet_balance(user_id)
            if balance["total"] + reward > 99999999999:
                raise RuleError("Wallet ပမာဏအများဆုံး ကျော်နေပါတယ်။")
            self.db.execute("INSERT OR IGNORE INTO wallets(user_id) VALUES (?)", (user_id,))
            self.db.execute("UPDATE wallets SET balance=balance+? WHERE user_id=?", (reward, user_id))
            self.db.execute("INSERT INTO daily_claims(user_id,claimed_at,reward) VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET claimed_at=excluded.claimed_at,reward=excluded.reward", (user_id, now, reward))
            self.db.execute("INSERT INTO wallet_events(user_id,delta,kind,note,actor_id,event_key,created) VALUES (?,?,?,?,?,?,?)",
                            (user_id, reward, "dailycoin", "Daily coin reward", None, f"dailycoin:{user_id}:{now}", now))
            return {"claimed": True, "reward": reward, "available": balance["available"] + reward}

    def transfer_coins(self, group_id, sender_id, recipient_id, amount, event_key):
        if (type(sender_id) is not int or type(recipient_id) is not int
                or not 0 < sender_id < 2**63 or not 0 < recipient_id < 2**63
                or sender_id == recipient_id or type(amount) is not int
                or not 0 < amount <= 99999999999 or not event_key):
            raise RuleError("Coin gift ပမာဏ သို့မဟုတ် user ID မမှန်ပါ။")
        sent_key, received_key = f"{event_key}:sent", f"{event_key}:received"
        with self.transaction():
            if str(group_id) != self.get("pvp_group_id"):
                raise RuleError("Coin gift ကို သတ်မှတ်ထားတဲ့ PvP game group မှာပဲ ပို့နိုင်ပါတယ်။")
            rows = self.db.execute(
                "SELECT event_key,user_id,delta,actor_id FROM wallet_events WHERE event_key IN (?,?)",
                (sent_key, received_key)).fetchall()
            if rows:
                existing = {row["event_key"]: (row["user_id"],row["delta"],row["actor_id"]) for row in rows}
                expected = {sent_key: (sender_id,-amount,sender_id),
                            received_key: (recipient_id,amount,sender_id)}
                if existing == expected:
                    return False
                raise RuleError("ဒီ gift request ကို ပြင်ပြီးပြန်သုံးလို့မရပါ။")
            self.db.execute("INSERT OR IGNORE INTO wallets(user_id) VALUES (?)", (sender_id,))
            self.db.execute("INSERT OR IGNORE INTO wallets(user_id) VALUES (?)", (recipient_id,))
            sender = self.wallet_balance(sender_id)
            if sender["available"] < amount:
                raise RuleError(f"Coin မလုံလောက်ပါ။ လက်ရှိသုံးနိုင်တာ {money(sender['available'])} ပါ။")
            recipient = self.wallet_balance(recipient_id)
            if recipient["total"] + amount > 99999999999:
                raise RuleError("လက်ခံသူ၏ wallet ပမာဏအများဆုံး ကျော်နေပါတယ်။")
            self.db.execute("UPDATE wallets SET balance=balance-? WHERE user_id=?", (amount,sender_id))
            self.db.execute("UPDATE wallets SET balance=balance+? WHERE user_id=?", (amount,recipient_id))
            created = int(time.time())
            self.db.execute("""INSERT INTO wallet_events(user_id,delta,kind,note,actor_id,event_key,created)
              VALUES (?,?, 'gift_sent', ?,?,?,?)""",
              (sender_id,-amount,f"Gift to user {recipient_id}",sender_id,sent_key,created))
            self.db.execute("""INSERT INTO wallet_events(user_id,delta,kind,note,actor_id,event_key,created)
              VALUES (?,?, 'gift_received', ?,?,?,?)""",
              (recipient_id,amount,f"Gift from user {sender_id}",sender_id,received_key,created))
            return True

    def _pvp_game(self, game_id):
        row = self.db.execute("SELECT * FROM pvp_games WHERE id=?", (game_id,)).fetchone()
        if not row:
            raise RuleError("PvP request မတွေ့ပါ။")
        return dict(row)

    def create_pvp(self, game_id, group_id, requester_id, requester_name,
                   target_id, target_name, amount, now=None):
        now = int(time.time()) if now is None else int(now)
        if type(amount) is not int or not MIN_PVP_WAGER <= amount <= MAX_PVP_WAGER:
            raise RuleError("PvP လောင်းကြေးကို 250 မှ 30000 coin အတွင်းထားပါ။")
        if requester_id == target_id:
            raise RuleError("ကိုယ့်ကိုယ်ကို PvP request လုပ်လို့မရပါ။")
        with self.transaction():
            if str(group_id) != self.get("pvp_group_id"):
                raise RuleError("သတ်မှတ်ထားတဲ့ PvP group မှာပဲ ကစားနိုင်ပါတယ်။")
            if self.db.execute("SELECT COUNT(*) FROM pvp_games WHERE group_id=? AND status='running'", (group_id,)).fetchone()[0] >= MAX_ACTIVE_PVP_GAMES:
                raise RuleError("လက်ရှိ game ၃ ပွဲ ပြည့်နေပါပြီ။ တစ်ပွဲပြီးမှ ပွဲအသစ် စနိုင်ပါမည်။")
            balance = self.wallet_balance(requester_id)
            if balance["available"] < amount:
                raise RuleError(f"Coin မလုံလောက်ပါ။ လက်ရှိသုံးနိုင်တာ {money(balance['available'])} ပါ။")
            self.db.execute("INSERT INTO pvp_games(id,group_id,requester_id,requester_name,target_id,target_name,amount,status,created) VALUES (?,?,?,?,?,?,?,'pending',?)",
                            (game_id,group_id,requester_id,requester_name[:64],target_id,target_name[:64],amount,now))
            return self._pvp_game(game_id)

    def set_pvp_message(self, game_id, message_id):
        self.db.execute("UPDATE pvp_games SET message_id=? WHERE id=? AND status='pending'", (message_id,game_id))

    def play_solo_pvp(self, game_id, group_id, user_id, user_name, amount, choice, result, now=None):
        now = int(time.time()) if now is None else int(now)
        if type(amount) is not int or not MIN_PVP_WAGER <= amount <= MAX_PVP_WAGER:
            raise RuleError("PvP လောင်းကြေးကို 250 မှ 30000 coin အတွင်းထားပါ။")
        if choice not in {"heads", "tails"} or result not in {"heads", "tails"}:
            raise RuleError("Solo PvP result မမှန်ပါ။ h=Heads, t=Tails ကိုသုံးပါ။")
        with self.transaction():
            if str(group_id) != self.get("pvp_group_id"):
                raise RuleError("သတ်မှတ်ထားတဲ့ PvP group မှာပဲ ကစားနိုင်ပါတယ်။")
            if self.db.execute("SELECT COUNT(*) FROM pvp_games WHERE group_id=? AND status='running'", (group_id,)).fetchone()[0] >= MAX_ACTIVE_PVP_GAMES:
                raise RuleError("လက်ရှိ game ၃ ပွဲ ပြည့်နေပါပြီ။ တစ်ပွဲပြီးမှ ပွဲအသစ် စနိုင်ပါမည်။")
            if self.db.execute("SELECT 1 FROM pvp_games WHERE group_id=? AND status IN ('pending','running') AND (requester_id=? OR target_id=?) LIMIT 1", (group_id,user_id,user_id)).fetchone():
                raise RuleError("ဒီ user က PvP/Boom game တစ်ခုမှာ ပါဝင်နေပြီးသားပါ။ ပွဲပြီးမှ ထပ်ကစားနိုင်ပါတယ်။")
            self.db.execute("INSERT OR IGNORE INTO wallets(user_id) VALUES (?)", (user_id,))
            balance = self.wallet_balance(user_id)
            if balance["available"] < amount:
                raise RuleError(f"Coin မလုံလောက်ပါ။ လက်ရှိသုံးနိုင်တာ {money(balance['available'])} ပါ။")
            won = choice == result
            if won and balance["total"] - amount + amount * 2 > 99999999999:
                raise RuleError("အနိုင်ရလျှင် wallet limit ကျော်နိုင်ပါတယ်။")
            self.db.execute("UPDATE wallets SET balance=balance-? WHERE user_id=?", (amount, user_id))
            self.db.execute("INSERT INTO wallet_events(user_id,delta,kind,note,actor_id,event_key,created) VALUES (?,?,?,?,?,?,?)",
                            (user_id, -amount, "pvp_stake", f"Solo PvP stake · {game_id}", user_id, f"pvp:{game_id}:stake", now))
            if won:
                prize = amount * 2
                self.db.execute("UPDATE wallets SET balance=balance+? WHERE user_id=?", (prize, user_id))
                self.db.execute("INSERT INTO wallet_events(user_id,delta,kind,note,actor_id,event_key,created) VALUES (?,?,?,?,?,?,?)",
                                (user_id, prize, "pvp_win", f"Solo PvP prize · {game_id}", None, f"pvp:{game_id}:prize", now))
            self.db.execute("""INSERT INTO pvp_games
              (id,group_id,requester_id,requester_name,target_id,target_name,amount,status,created,next_at,step,final_percent,winner_id,slot_notified,mode,choice,result)
              VALUES (?,?,?,?,?,?,?,'finished',?,?,?,?,?,?,?, ?,?)""",
              (game_id, group_id, user_id, user_name[:64], 0, "House", amount, now, None, 5,
               100 if result == "heads" else 0, user_id if won else 0, 1, "solo", choice, result))
            return self._pvp_game(game_id)

    def cancel_pvp(self, game_id, actor_id):
        with self.transaction():
            row = self._pvp_game(game_id)
            if actor_id not in (row["requester_id"],row["target_id"]):
                raise RuleError("ဒီ PvP request ကို cancel လုပ်ခွင့်မရှိပါ။")
            if row["status"] != "pending":
                raise RuleError("ဒီ PvP request ကို ယခု cancel မလုပ်နိုင်ပါ။")
            self.db.execute("UPDATE pvp_games SET status='cancelled' WHERE id=?", (game_id,))
            return self._pvp_game(game_id)

    def accept_pvp(self, game_id, actor_id, final_percent, now=None):
        now = time.time() if now is None else now
        if type(final_percent) is not int or not 1 <= final_percent <= 100:
            raise RuleError("PvP result မမှန်ပါ။")
        with self.transaction():
            row = self._pvp_game(game_id)
            if actor_id != row["target_id"]:
                raise RuleError("Request လက်ခံနိုင်သူက ဖိတ်ခေါ်ခံရသူတစ်ဦးတည်းပါ။")
            if row["status"] != "pending":
                raise RuleError("ဒီ PvP request ကို အရင်ဖြေပြီးပါပြီ။")
            if str(row["group_id"]) != self.get("pvp_group_id"):
                raise RuleError("ဒီ group မှာ PvP မကစားနိုင်တော့ပါ။")
            active = self.db.execute("SELECT COUNT(*) FROM pvp_games WHERE group_id=? AND status='running'", (row["group_id"],)).fetchone()[0]
            if active >= MAX_ACTIVE_PVP_GAMES:
                raise RuleError("လက်ရှိ game ၃ ပွဲ ပြည့်နေပါပြီ။ တစ်ပွဲပြီးမှ ပွဲအသစ် စနိုင်ပါမည်။")
            for user_id in (row["requester_id"],row["target_id"]):
                balance = self.wallet_balance(user_id)
                if balance["available"] < row["amount"]:
                    raise RuleError(f"User {user_id} မှာ လိုအပ်တဲ့ coin မလုံလောက်ပါ။")
                if balance["total"] + row["amount"] > 99999999999:
                    raise RuleError("လောင်းကြေးအနိုင်ရလျှင် wallet limit ကျော်နိုင်ပါတယ်။")
            for user_id in (row["requester_id"],row["target_id"]):
                self.db.execute("UPDATE wallets SET balance=balance-? WHERE user_id=?", (row["amount"],user_id))
                self.db.execute("INSERT INTO wallet_events(user_id,delta,kind,note,actor_id,event_key,created) VALUES (?,?,'pvp_stake',?,?,?,?)",
                                (user_id,-row["amount"],f"PvP stake · {game_id}",actor_id,f"pvp:{game_id}:stake:{user_id}",now))
            self.db.execute("UPDATE pvp_games SET status='running',next_at=?,step=0,final_percent=? WHERE id=?", (now+1,final_percent,game_id))
            return self._pvp_game(game_id)

    def due_pvp(self, now=None):
        now = time.time() if now is None else now
        return [dict(r) for r in self.db.execute("SELECT * FROM pvp_games WHERE status='running' AND next_at<=? ORDER BY next_at,id", (now,))]

    def advance_pvp(self, game_id, now=None):
        now = time.time() if now is None else now
        with self.transaction():
            row = self._pvp_game(game_id)
            if row["status"] != "running" or row["next_at"] is None or row["next_at"] > now:
                return row
            step = row["step"] + 1
            if step < 2:
                self.db.execute("UPDATE pvp_games SET step=?,next_at=? WHERE id=? AND status='running'", (step,now+1,game_id))
                return self._pvp_game(game_id)
            requester_percent = row["final_percent"]
            target_percent = 100 - requester_percent
            winner_id = row["requester_id"] if requester_percent > 50 else row["target_id"]
            loser_id = row["target_id"] if winner_id == row["requester_id"] else row["requester_id"]
            loser_percent = target_percent if winner_id == row["requester_id"] else requester_percent
            pot = row["amount"] * 2
            loser_payout = 0 if loser_percent > 25 else pot * loser_percent // 100
            winner_payout = pot - loser_payout
            for user_id in (winner_id, loser_id):
                if not self.db.execute("SELECT 1 FROM wallets WHERE user_id=?", (user_id,)).fetchone():
                    raise RuleError("Winner/loser wallet မတွေ့ပါ။ Owner က စစ်ဆေးရန်လိုပါတယ်။")
            self.db.execute("UPDATE wallets SET balance=balance+? WHERE user_id=?", (winner_payout,winner_id))
            self.db.execute("INSERT INTO wallet_events(user_id,delta,kind,note,actor_id,event_key,created) VALUES (?,?,'pvp_win',?,?,?,?)",
                            (winner_id,winner_payout,f"PvP prize · {game_id}",None,f"pvp:{game_id}:prize",now))
            if loser_payout:
                self.db.execute("UPDATE wallets SET balance=balance+? WHERE user_id=?", (loser_payout,loser_id))
                self.db.execute("INSERT INTO wallet_events(user_id,delta,kind,note,actor_id,event_key,created) VALUES (?,?,'pvp_refund',?,?,?,?)",
                                (loser_id,loser_payout,f"PvP refund · {game_id}",None,f"pvp:{game_id}:refund",now))
            self.db.execute("UPDATE pvp_games SET status='finished',winner_id=?,step=2,next_at=NULL WHERE id=?", (winner_id,game_id))
            return self._pvp_game(game_id)

    def pending_pvp_slot_notifications(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM pvp_games WHERE status='finished' AND slot_notified=0 ORDER BY created,id")]

    def mark_pvp_slot_notified(self, game_id):
        self.db.execute("UPDATE pvp_games SET slot_notified=1 WHERE id=? AND status='finished'", (game_id,))

    def close(self):
        self.db.close()

    def find_post(self, channel_id, post_id, group_id):
        row=self.db.execute("SELECT * FROM auctions WHERE channel_id=? AND post_id=? AND group_id=?",(channel_id,post_id,group_id)).fetchone()
        return dict(row) if row else None

    def recoverable(self, auction_id, channel_id, group_id):
        row=self.db.execute("SELECT * FROM auctions WHERE id=? AND channel_id=? AND group_id=? AND status='publishing' AND post_id IS NULL",(auction_id,channel_id,group_id)).fetchone()
        return dict(row) if row else None

    def banned(self):
        return [r[0] for r in self.db.execute("SELECT user_id FROM banned ORDER BY user_id LIMIT 100")]

    def export_bids(self, auction_id):
        return self.db.execute("SELECT * FROM bids WHERE auction_id=? ORDER BY id",(auction_id,))

    def stats(self):
        counts=self.db.execute("SELECT status,COUNT(*) FROM auctions GROUP BY status").fetchall()
        total=self.db.execute("SELECT COUNT(*) FROM bids").fetchone()[0]
        sales=self.db.execute("SELECT COALESCE(SUM(highest),0) FROM auctions WHERE status='closed'").fetchone()[0]
        return counts,total,sales

    def enable_wallet(self):
        """Convert only open legacy auctions; refuse to invent unfunded holds."""
        with self.transaction():
            for row in self.db.execute("SELECT * FROM auctions WHERE status IN ('active','publishing') AND wallet_required=0").fetchall():
                if row["highest"] is not None:
                    if self.wallet_balance(row["winner_id"])["available"] < row["highest"]:
                        raise RuleError(f'Legacy auction #{row["id"]} has an unfunded leader; close/cancel it before migration.')
                    self.db.execute("INSERT INTO holds VALUES (?,?,?)",(row["id"],row["winner_id"],row["highest"]))
                self.db.execute("UPDATE auctions SET wallet_required=1,version=version+1 WHERE id=?",(row["id"],))
            self.set("wallet_mode",1)
