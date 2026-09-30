"""MongoDB replica-set storage. Every ledger mutation commits atomically."""
import time
import re
import random
import secrets
from pymongo import MongoClient, ReturnDocument
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern
from .domain import BOOM_TURN_TIMEOUT_SECONDS, MAX_ACTIVE_PVP_GAMES, MAX_PVP_WAGER, MIN_PVP_WAGER, PVP_REQUEST_TIMEOUT_SECONDS, PVP_DICE_PAYOUT_RULE, RuleError, money, pvp_dice_outcome

PVP_ANIMATION_INTERVAL_SECONDS = 1.2
PVP_DICE_ANIMATION_SECONDS = 4
PVP_DICE_TIMEOUT_SECONDS = 90


class MongoStore:
    def __init__(self, uri, database):
        self.client = MongoClient(uri, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000,
                                  socketTimeoutMS=20000, appname="authbid-bot")
        self.db = self.client[database]
        hello = self.client.admin.command("hello")
        if not hello.get("setName") and hello.get("msg") != "isdbgrid":
            self.close()
            raise ValueError("MongoDB replica set or Atlas is required for wallet transactions")
        self.db.bids.create_index([("chat_id", 1), ("message_id", 1)], unique=True)
        self.db.bids.create_index([("user_id", 1), ("auction_id", 1), ("id", -1)])
        self.db.auctions.create_index([("status", 1), ("ends", 1)])
        self.db.auctions.update_many(
            {"winner_notified": {"$exists": False}, "status": {"$in": ["closed", "cancelled"]}},
            {"$set": {"winner_notified": 1}})
        self.db.auctions.update_many({"winner_notified": {"$exists": False}},
                                     {"$set": {"winner_notified": 0}})
        self.db.holds.create_index("user_id")
        self.db.wallet_events.create_index("event_key", unique=True)
        self.db.wallet_events.create_index([("user_id", 1), ("id", -1)])
        self.db.messages.create_index([("chat_id", 1), ("message_id", 1)], unique=True)
        self.db.pvp_games.create_index([("group_id", 1), ("status", 1), ("next_at", 1)])
        self.db.pvp_games.create_index([("status", 1), ("requester_id", 1), ("target_id", 1)])
        self.db.coord.update_one({"_id":"ledger"}, {"$setOnInsert":{"version":0}}, upsert=True)
        # Upgrade only the untouched legacy default; preserve an owner's custom setting.
        self.db.settings.update_one({"_id":"increment","value":"5000"}, {"$set":{"value":"25000"}})
        for key, value in (("wallet_mode","1"),("increment","25000"),("paused","0"),
                           ("rules","Bid ငွေကို ယာယီထိန်းထားပြီး winner ကို လေလံပိတ်ချိန် ငွေဖြတ်ပါမယ်။")):
            self.db.settings.update_one({"_id":key},{"$setOnInsert":{"value":value}},upsert=True)

    def close(self):
        self.client.close()

    def _tx(self, operation):
        def execute(session):
            # A shared write serializes ledger decisions, including cross-auction holds.
            # Driver retries write conflicts by rerunning the entire callback.
            self.db.coord.update_one({"_id":"ledger"},{"$inc":{"version":1}},session=session)
            return operation(session)
        with self.client.start_session() as session:
            return session.with_transaction(execute, read_concern=ReadConcern("snapshot"),
                                            write_concern=WriteConcern("majority"), max_commit_time_ms=10000)

    def _next(self, name, session):
        return self.db.counters.find_one_and_update({"_id":name},{"$inc":{"value":1}},upsert=True,
                   return_document=ReturnDocument.AFTER,session=session)["value"]

    @staticmethod
    def _clean(row):
        return {k:v for k,v in row.items() if k!="_id"} if row else None

    def _record_streak(self, session, category, group_id, user_id, won, now):
        key=f"{category}:{group_id}:{user_id}"
        row=self.db.streaks.find_one({"_id":key},session=session)
        streak=(row.get("streak",0) if row else 0) + 1 if won else 0
        reward=50000 if streak == 3 else 150000 if streak == 6 else 0
        self.db.streaks.update_one({"_id":key},{"$set":{"category":category,"group_id":group_id,
            "user_id":user_id,"streak":streak,"updated":now}},upsert=True,session=session)
        if reward:
            balance=self.wallet_balance(user_id,session)
            if balance["total"] + reward > 99999999999:
                raise RuleError("Streak reward ထည့်လျှင် wallet limit ကျော်နိုင်ပါတယ်။")
            self.db.wallets.update_one({"_id":user_id},{"$inc":{"balance":reward}},session=session)
            eid=self._next("wallet_events",session)
            self.db.wallet_events.insert_one(dict(_id=eid,id=eid,user_id=user_id,delta=reward,
                kind="streak_reward",note=f"{category} {streak}-win streak reward",
                actor_id=None,auction_id=None,event_key=f"streak:{category}:{group_id}:{user_id}:{streak}",created=now),session=session)
        return {"streak":streak,"reward":reward}

    def get(self, key, default="", session=None):
        row=self.db.settings.find_one({"_id":key},session=session)
        return row["value"] if row else default

    def set(self, key, value):
        if key=="wallet_mode" and str(value)!="1":
            raise RuleError("Wallet hold ကို ပိတ်၍မရပါ။ Bid အတွက် balance လိုအပ်ပါတယ်။")
        self._tx(lambda s:self.db.settings.update_one({"_id":key},{"$set":{"value":str(value)}},upsert=True,session=s))

    def claim_new_user(self, user_id):
        result = self.db.new_users.update_one(
            {"_id": user_id}, {"$setOnInsert": {"created": int(time.time())}}, upsert=True)
        return result.upserted_id is not None

    def target(self, key, value):
        def change(s):
            if self.get(key,session=s)==str(value): return
            if self.db.auctions.find_one({"status":{"$in":["active","publishing"]}},session=s):
                raise RuleError("Channel/group ပြောင်းမယ်ဆို active/publishing လေလံတွေကို အရင်ပိတ်ပါ။")
            self.db.settings.update_one({"_id":key},{"$set":{"value":str(value)}},upsert=True,session=s)
        self._tx(change)

    def set_pvp_group(self, group_id):
        def configure(s):
            if str(group_id) != str(self.get("pvp_group_id", session=s)) and self.db.pvp_games.find_one(
                    {"status":{"$in":["pending","running"]}}, session=s):
                raise RuleError("PvP group ပြောင်းရန် pending/running ပွဲများကို အရင်ရှင်းပါ။")
            self.db.settings.update_one({"_id":"pvp_group_id"},{"$set":{"value":str(group_id)}},upsert=True,session=s)
        self._tx(configure)

    def auction(self, auction_id, session=None):
        row=self.db.auctions.find_one({"_id":auction_id},session=session)
        if not row: raise RuleError("လေလံ ID မတွေ့ပါ။")
        return self._clean(row)

    def create(self, card, now=None):
        def create(s):
            created=int(time.time()) if now is None else now
            duration=card.get("duration_seconds")
            if duration is not None and (type(duration) is not int or not 1<=duration<=604800):
                raise RuleError("ကြာချိန်ကို 1sec မှ 7days အတွင်း သတ်မှတ်ပါ။")
            ends=created+duration if duration is not None else card["ends"]
            if ends<=created: raise RuleError("End time က အနာဂတ်ဖြစ်ရပါမယ်။")
            channel,group=self.get("channel_id",session=s),self.get("group_id",session=s)
            if not channel or not group: raise RuleError("/setchannel နှင့် /setgroup အရင်သတ်မှတ်ပါ။")
            aid=self._next("auctions",s)
            row={k:card[k] for k in ("photo","name","anime","rarity","start")}
            row["card_type"] = card.get("card_type", "")
            row["card_id"] = card.get("card_id", "")
            row["media_type"] = card.get("media_type", "photo")
            row.update(_id=aid,id=aid,increment=int(self.get("increment",session=s)),ends=ends,
                       channel_id=int(channel),group_id=int(group),post_id=None,root_id=None,
                       status="publishing",highest=None,winner_id=None,winner_name=None,
                       winner_notified=0,version=1,rendered=0,created=created,duration_seconds=duration,wallet_required=1)
            self.db.auctions.insert_one(row,session=s)
            return aid
        return self._tx(create)

    def _published(self, aid, post_id, published_at, s):
        row=self.auction(aid,s)
        if row["status"]!="publishing": return
        ends=published_at+row["duration_seconds"] if row.get("duration_seconds") else row["ends"]
        self.db.auctions.update_one({"_id":aid},{"$set":{"post_id":post_id,"status":"active","ends":ends},"$inc":{"version":1}},session=s)

    def published(self, auction_id, post_id, published_at=None):
        at=int(time.time()) if published_at is None else published_at
        self._tx(lambda s:self._published(auction_id,post_id,at,s))

    def attach(self, auction_id, post_id, root_id, published_at=None):
        def attach(s):
            row=self.auction(auction_id,s)
            if row["post_id"] not in (None,post_id) or row["root_id"] not in (None,root_id): return False
            self.db.auctions.update_one({"_id":auction_id},{"$set":{"post_id":post_id,"root_id":root_id}},session=s)
            self._published(auction_id,post_id,int(time.time()) if published_at is None else published_at,s)
            self._map(row["group_id"],root_id,auction_id,s)
            return True
        return self._tx(attach)

    def _map(self, chat_id, message_id, auction_id, session=None):
        self.db.messages.update_one({"_id":f"{chat_id}:{message_id}"},
            {"$setOnInsert":{"chat_id":chat_id,"message_id":message_id,"auction_id":auction_id}},upsert=True,session=session)

    def map_message(self, chat_id, message_id, auction_id):
        self._map(chat_id,message_id,auction_id)

    def thread_auction(self, chat_id, root_id):
        row=self.db.auctions.find_one({"group_id":chat_id,"root_id":root_id},{"_id":1})
        return row["_id"] if row else None

    def resolve(self, chat_id, message_ids):
        matches=set()
        for mid in message_ids:
            if mid:
                row=self.db.messages.find_one({"_id":f"{chat_id}:{mid}"})
                if row:matches.add(row["auction_id"])
        return matches.pop() if len(matches)==1 else None

    def search_active(self, query="", offset=0, limit=20, now=None):
        if not 0<=offset<=1000000 or not 1<=limit<=50 or len(query)>256:
            raise RuleError("Search parameter မမှန်ပါ။")
        at=int(time.time()) if now is None else now
        filters=dict(status="active",ends={"$gt":at},post_id={"$ne":None},
                     channel_id=int(self.get("channel_id") or 0),group_id=int(self.get("group_id") or 0))
        query=query.strip()
        if query:
            literal={"$regex":re.escape(query),"$options":"i"}
            filters["$or"]=[{field:literal} for field in ("name","anime","rarity")]
            number=query.lstrip('#')
            if number.isascii() and number.isdigit() and len(number)<19:
                filters["$or"].append({"_id":int(number)})
        rows=[self._clean(r) for r in self.db.auctions.find(filters).sort("id",-1).skip(offset).limit(limit+1)]
        return rows[:limit],len(rows)>limit

    def find_post(self, channel_id, post_id, group_id):
        return self._clean(self.db.auctions.find_one(dict(channel_id=channel_id,post_id=post_id,group_id=group_id)))

    def recoverable(self, auction_id, channel_id, group_id):
        return self._clean(self.db.auctions.find_one(dict(_id=auction_id,channel_id=channel_id,group_id=group_id,status="publishing",post_id=None)))

    def wallet_balance(self, user_id, session=None):
        if session is None:
            return self._tx(lambda s:self.wallet_balance(user_id,s))
        row=self.db.wallets.find_one({"_id":user_id},session=session)
        total=row["balance"] if row else 0
        held=sum(r["amount"] for r in self.db.holds.find({"user_id":user_id},session=session))
        return {"total":total,"held":held,"available":total-held}

    def bid(self, auction_id, user_id, user_name, amount, chat_id, message_id, now=None):
        if type(amount) is not int or not 0<amount<=99999999999: raise RuleError("Bid ပမာဏ မမှန်ပါ။")
        def bid(s):
            at=int(time.time()) if now is None else now
            row=self.auction(auction_id,s)
            allowed_groups = {str(self.get("group_id", session=s)),
                              str(self.get("pvp_group_id", session=s))}
            if row["group_id"] != int(self.get("group_id", session=s) or 0) or str(chat_id) not in allowed_groups:
                raise RuleError("သတ်မှတ်ထားတဲ့ group မှာပဲ bid ဆွဲနိုင်ပါတယ်။")
            if self.db.bids.find_one(dict(chat_id=chat_id,message_id=message_id),session=s):return False
            if self.get("paused",session=s)=="1":raise RuleError("Owner က bidding ခဏရပ်ထားပါတယ်။")
            if self.db.banned.find_one({"_id":user_id},session=s):raise RuleError("သင့်အကောင့်ကို bid ဆွဲခွင့်ပိတ်ထားပါတယ်။")
            if row["status"]!="active" or at>=row["ends"]:raise RuleError("ဒီလေလံ ပိတ်သွားပါပြီ။")
            minimum=row["start"] if row["highest"] is None else row["highest"]+row["increment"]
            if amount<minimum:raise RuleError(f"အနည်းဆုံး {money(minimum)} bid ဆွဲရပါမယ်။")
            balance=self.wallet_balance(user_id,s)
            hold=self.db.holds.find_one({"_id":auction_id,"user_id":user_id},session=s)
            usable=balance["available"]+(hold["amount"] if hold else 0)
            if amount>usable:raise RuleError(f"လက်ကျန်မလုံလောက်ပါ။ ဒီ bid အတွက် သုံးနိုင်ငွေ {money(usable)}။ /bal ကိုစစ်ပါ။")
            self.db.holds.replace_one({"_id":auction_id},dict(_id=auction_id,auction_id=auction_id,user_id=user_id,amount=amount),upsert=True,session=s)
            bid_id=self._next("bids",s)
            self.db.bids.insert_one(dict(_id=bid_id,id=bid_id,auction_id=auction_id,user_id=user_id,user_name=user_name,amount=amount,created=at,chat_id=chat_id,message_id=message_id),session=s)
            self.db.auctions.update_one({"_id":auction_id},{"$set":{"highest":amount,"winner_id":user_id,"winner_name":user_name,"wallet_required":1},"$inc":{"version":1}},session=s)
            return True
        return self._tx(bid)

    def is_banned(self,user_id):return bool(self.db.banned.find_one({"_id":user_id}))

    def ban(self,user_id,blocked):
        def change(s):
            if blocked:self.db.banned.update_one({"_id":user_id},{"$set":{"user_id":user_id}},upsert=True,session=s)
            else:self.db.banned.delete_one({"_id":user_id},session=s)
        self._tx(change)

    def banned(self):return [r["user_id"] for r in self.db.banned.find().sort("user_id",1).limit(100)]

    def _finish(self, aid, status, s):
        if status not in ("closed","cancelled"):raise RuleError("လေလံ status မမှန်ပါ။")
        row=self.auction(aid,s)
        allowed=("active","publishing") if status=="cancelled" else ("active",)
        if row["status"] not in allowed:raise RuleError("ဒီလေလံကို ယခုလုပ်ဆောင်ချက် မလုပ်နိုင်ပါ။")
        if status=="closed" and row["wallet_required"] and row["highest"] is not None:
            hold=self.db.holds.find_one({"_id":aid},session=s)
            if not hold or hold["user_id"]!=row["winner_id"] or hold["amount"]!=row["highest"]:
                raise RuleError("Wallet hold မကိုက်ညီပါ။ Owner က စစ်ဆေးရန်လိုပါတယ်။")
            result=self.db.wallets.update_one({"_id":hold["user_id"],"balance":{"$gte":hold["amount"]}}, {"$inc":{"balance":-hold["amount"]}},session=s)
            if result.modified_count!=1:raise RuleError("Winner balance မလုံလောက်ပါ။")
            eid=self._next("wallet_events",s)
            self.db.wallet_events.insert_one(dict(_id=eid,id=eid,user_id=hold["user_id"],delta=-hold["amount"],kind="win",note="Auction purchase",actor_id=None,auction_id=aid,event_key=f"settle:{aid}",created=int(time.time())),session=s)
        self.db.holds.delete_one({"_id":aid},session=s)
        self.db.auctions.update_one({"_id":aid},{"$set":{"status":status},"$inc":{"version":1}},session=s)

    def finish(self,auction_id,status):self._tx(lambda s:self._finish(auction_id,status,s))

    def close_due(self,now=None):
        at=int(time.time()) if now is None else now
        # Avoid starting write transactions on every idle UI tick.
        for row in self.db.auctions.find({"status":"active","ends":{"$lte":at}}, {"_id":1}):
            def close(s,aid=row["_id"]):
                current=self.auction(aid,s)
                if current["status"]=="active" and current["ends"]<=at:self._finish(aid,"closed",s)
            self._tx(close)

    def extend(self,auction_id,minutes):
        if type(minutes) is not int or not 1<=minutes<=10080:raise RuleError("1–10080 minutes သာရေးပါ။")
        def extend(s):
            row=self.auction(auction_id,s)
            if row["status"]!="active" or row["ends"]<=time.time():raise RuleError("Active လေလံကိုပဲ အချိန်တိုးနိုင်ပါတယ်။")
            self.db.auctions.update_one({"_id":auction_id},{"$inc":{"ends":minutes*60,"version":1}},session=s)
        self._tx(extend)

    def pending_winners(self):
        return list(self.db.auctions.find({"status": "closed", "winner_id": {"$ne": None},
                    "winner_notified": 0, "root_id": {"$ne": None}, "post_id": {"$ne": None}}).sort("id", 1))

    def winner_notified(self, auction_id):
        self.db.auctions.update_one({"_id": auction_id}, {"$set": {"winner_notified": 1}})

    def dirty(self):return [self._clean(r) for r in self.db.auctions.find({"post_id":{"$ne":None},"$expr":{"$gt":["$version","$rendered"]}}).sort("id",1)]
    def rendered(self,auction_id,version):self.db.auctions.update_one({"_id":auction_id},{"$max":{"rendered":version}})
    def listing(self,limit=30):return [self._clean(r) for r in self.db.auctions.find().sort("id",-1).limit(limit)]
    def history(self,auction_id,limit=30):
        self.auction(auction_id)
        return [self._clean(r) for r in self.db.bids.find({"auction_id":auction_id}).sort("id",-1).limit(limit)]
    def export_bids(self,auction_id):return (self._clean(r) for r in self.db.bids.find({"auction_id":auction_id}).sort("id",1))

    def user_history(self,user_id,wins=False,now=None):
        at=int(time.time()) if now is None else now
        pipeline=[{"$match":{"user_id":user_id}},{"$group":{"_id":"$auction_id","my_bid":{"$max":"$amount"},"last_bid":{"$max":"$id"}}},{"$sort":{"last_bid":-1}},
                  {"$lookup":{"from":"auctions","localField":"_id","foreignField":"_id","as":"auction"}},{"$unwind":"$auction"}]
        if wins:pipeline.append({"$match":{"auction.winner_id":user_id,"$or":[{"auction.status":"closed"},{"auction.status":"active","auction.ends":{"$lte":at}}]}})
        pipeline.append({"$limit":10})
        return [dict(self._clean(r["auction"]),my_bid=r["my_bid"],last_bid=r["last_bid"]) for r in self.db.bids.aggregate(pipeline)]

    def active_auctions(self,page=0,now=None):
        at=int(time.time()) if now is None else now
        query=dict(status="active",ends={"$gt":at},channel_id=int(self.get("channel_id") or 0),group_id=int(self.get("group_id") or 0))
        count=self.db.auctions.count_documents(query)
        page=min(page,max(0,(count-1)//5))
        rows=self.db.auctions.find(query).sort([("ends",1),("id",1)]).skip(page*5).limit(5)
        return count,[self._clean(r) for r in rows],page

    def adjust_wallet(self,user_id,delta,actor_id,event_key,note=""):
        if type(user_id) is not int or not 0<user_id<2**63 or type(delta) is not int or not 0<abs(delta)<=99999999999 or len(note)>200:
            raise RuleError("User ID၊ ပမာဏ သို့မဟုတ် note (200 စာလုံးအထိ) မမှန်ပါ။")
        def adjust(s):
            existing=self.db.wallet_events.find_one({"event_key":event_key},session=s)
            if existing:
                if (existing["user_id"],existing["delta"],existing["actor_id"])!=(user_id,delta,actor_id):raise RuleError("ဒီ request ကို ပြင်ပြီးပြန်သုံးလို့မရပါ။")
                return False
            balance=self.wallet_balance(user_id,s)
            if balance["available"]+delta<0:raise RuleError("ထိန်းထားတဲ့ bid ငွေကို နုတ်လို့မရပါ။ Available balance မလုံလောက်ပါ။")
            if balance["total"]+delta>99999999999:raise RuleError("Wallet ပမာဏအများဆုံး ကျော်နေပါတယ်။")
            self.db.wallets.update_one({"_id":user_id},{"$inc":{"balance":delta},"$set":{"user_id":user_id}},upsert=True,session=s)
            eid=self._next("wallet_events",s)
            self.db.wallet_events.insert_one(dict(_id=eid,id=eid,user_id=user_id,delta=delta,kind="credit" if delta>0 else "debit",note=note,actor_id=actor_id,auction_id=None,event_key=event_key,created=int(time.time())),session=s)
            return True
        return self._tx(adjust)

    def wallet_history(self,user_id):return [self._clean(r) for r in self.db.wallet_events.find({"user_id":user_id}).sort("id",-1).limit(10)]

    def payout_total(self):
        kinds = ("pvp_win", "pvp_refund", "boom_win", "streak_reward")
        rows = list(self.db.wallet_events.aggregate([
            {"$match": {"kind": {"$in": list(kinds)}, "delta": {"$gt": 0}}},
            {"$group": {"_id": "$kind", "total": {"$sum": "$delta"}}},
        ]))
        totals = {kind: 0 for kind in kinds}
        for row in rows:
            totals[row["_id"]] = row["total"]
        totals["total"] = sum(totals.values())
        return totals
    def claim_dailycoin(self, user_id, now=None):
        if type(user_id) is not int or not 0 < user_id < 2**63:
            raise RuleError("User ID မမှန်ပါ။")
        at = int(time.time()) if now is None else int(now)
        def claim(s):
            previous = self.db.daily_claims.find_one({"_id": user_id}, session=s)
            if previous:
                next_at = previous["claimed_at"] + 86400
                if at < next_at:
                    return {"claimed": False, "remaining": next_at - at}
            reward = random.randint(500, 2000) * 100
            balance = self.wallet_balance(user_id, s)
            if balance["total"] + reward > 99999999999:
                raise RuleError("Wallet ပမာဏအများဆုံး ကျော်နေပါတယ်။")
            self.db.wallets.update_one({"_id": user_id}, {"$inc": {"balance": reward}, "$set": {"user_id": user_id}}, upsert=True, session=s)
            self.db.daily_claims.update_one({"_id": user_id}, {"$set": {"claimed_at": at, "reward": reward}}, upsert=True, session=s)
            eid = self._next("wallet_events", s)
            self.db.wallet_events.insert_one(dict(_id=eid, id=eid, user_id=user_id, delta=reward,
                kind="dailycoin", note="Daily coin reward", actor_id=None, auction_id=None,
                event_key=f"dailycoin:{user_id}:{at}", created=at), session=s)
            return {"claimed": True, "reward": reward, "available": balance["available"] + reward}
        return self._tx(claim)

    def transfer_coins(self,group_id,sender_id,recipient_id,amount,event_key):
        if (type(sender_id) is not int or type(recipient_id) is not int
                or not 0<sender_id<2**63 or not 0<recipient_id<2**63
                or sender_id==recipient_id or type(amount) is not int
                or not 0<amount<=99999999999 or not event_key):
            raise RuleError("Coin gift ပမာဏ သို့မဟုတ် user ID မမှန်ပါ။")
        sent_key,received_key=f"{event_key}:sent",f"{event_key}:received"
        def transfer(s):
            if str(group_id)!=str(self.get("pvp_group_id",session=s)):
                raise RuleError("Coin gift ကို သတ်မှတ်ထားတဲ့ PvP game group မှာပဲ ပို့နိုင်ပါတယ်။")
            rows=list(self.db.wallet_events.find({"event_key":{"$in":[sent_key,received_key]}},session=s))
            if rows:
                existing={r["event_key"]:(r["user_id"],r["delta"],r["actor_id"]) for r in rows}
                expected={sent_key:(sender_id,-amount,sender_id),received_key:(recipient_id,amount,sender_id)}
                if existing==expected:return False
                raise RuleError("ဒီ gift request ကို ပြင်ပြီးပြန်သုံးလို့မရပါ။")
            sender=self.wallet_balance(sender_id,s)
            if sender["available"]<amount:raise RuleError(f"Coin မလုံလောက်ပါ။ လက်ရှိသုံးနိုင်တာ {money(sender['available'])} ပါ။")
            recipient=self.wallet_balance(recipient_id,s)
            if recipient["total"]+amount>99999999999:raise RuleError("လက်ခံသူ၏ wallet ပမာဏအများဆုံး ကျော်နေပါတယ်။")
            self.db.wallets.update_one({"_id":sender_id},{"$inc":{"balance":-amount},"$set":{"user_id":sender_id}},upsert=True,session=s)
            self.db.wallets.update_one({"_id":recipient_id},{"$inc":{"balance":amount},"$set":{"user_id":recipient_id}},upsert=True,session=s)
            created=int(time.time())
            for uid,delta,kind,note,key in (
                (sender_id,-amount,"gift_sent",f"Gift to user {recipient_id}",sent_key),
                (recipient_id,amount,"gift_received",f"Gift from user {sender_id}",received_key)):
                eid=self._next("wallet_events",s)
                self.db.wallet_events.insert_one(dict(_id=eid,id=eid,user_id=uid,delta=delta,kind=kind,
                    note=note,actor_id=sender_id,auction_id=None,event_key=key,created=created),session=s)
            return True
        return self._tx(transfer)

    def _pvp_game(self, game_id, session=None):
        row=self.db.pvp_games.find_one({"_id":game_id},session=session)
        if not row:raise RuleError("PvP request မတွေ့ပါ။")
        return self._clean(row)
    def _player_locked(self, group_id, user_id, session=None):
        query={"group_id":group_id,"status":{"$in":["pending","running"]},
               "$or":[{"requester_id":user_id},{"target_id":user_id}]}
        return bool(self.db.pvp_games.find_one(query,session=session) or
                    self.db.boom_games.find_one(query,session=session))
    def _active_group_games(self, group_id, session=None):
        return (self.db.pvp_games.count_documents({"group_id":group_id,"status":"running"},session=session)
                + self.db.boom_games.count_documents({"group_id":group_id,"status":"running"},session=session))

    def _finish_ready_player_dice(self, group_id, player_ids, now=None):
        """Allow rematches when the animation is over but the worker is delayed."""
        at = time.time() if now is None else now
        rows = self.db.pvp_games.find({
            "group_id": group_id, "status": "running", "mode": "dice",
            "dice_value": {"$in": list(range(1, 7))}, "next_at": {"$lte": at},
            "$or": [{"requester_id": {"$in": player_ids}},
                    {"target_id": {"$in": player_ids}}]}, {"_id": 1})
        for row in rows:
            # Use the same atomic, idempotent settlement as the worker. Commit it
            # separately so a rejected new wager cannot roll back the old prize.
            self.advance_pvp(row["_id"], now=at)

    def create_pvp(self,game_id,group_id,requester_id,requester_name,target_id,target_name,amount,now=None):
        if type(amount) is not int or not MIN_PVP_WAGER<=amount<=MAX_PVP_WAGER:raise RuleError("PvP လောင်းကြေးကို 250 မှ 30000 coin အတွင်းထားပါ။")
        if requester_id==target_id:raise RuleError("ကိုယ့်ကိုယ်ကို PvP request လုပ်လို့မရပါ။")
        self._finish_ready_player_dice(group_id, [requester_id, target_id], now)
        # Pick once before transaction retries, and reveal the assignment before Confirm.
        low_player_id = secrets.choice((requester_id, target_id))
        def create(s):
            at=time.time() if now is None else now
            if str(group_id)!=str(self.get("pvp_group_id",session=s)):
                raise RuleError("သတ်မှတ်ထားတဲ့ PvP group မှာပဲ ကစားနိုင်ပါတယ်။")
            if self._active_group_games(group_id, s) >= MAX_ACTIVE_PVP_GAMES:
                raise RuleError("လက်ရှိ game ၃ ပွဲ ပြည့်နေပါပြီ။ တစ်ပွဲပြီးမှ ပွဲအသစ် စနိုင်ပါမည်။")
            balance=self.wallet_balance(requester_id,s)
            if balance["available"]<amount:raise RuleError(f"Coin မလုံလောက်ပါ။ လက်ရှိသုံးနိုင်တာ {money(balance['available'])} ပါ။")
            self.db.pvp_games.insert_one(dict(_id=game_id,id=game_id,group_id=group_id,requester_id=requester_id,
                requester_name=requester_name[:64],target_id=target_id,target_name=target_name[:64],amount=amount,
                status="pending",message_id=0,created=at,next_at=None,step=0,final_percent=None,winner_id=None,
                slot_notified=0, low_player_id=low_player_id,
                dice_payout_rule=PVP_DICE_PAYOUT_RULE),session=s)
            return self._pvp_game(game_id,s)
        return self._tx(create)

    def set_pvp_message(self,game_id,message_id):
        self.db.pvp_games.update_one({"_id":game_id,"message_id":0,"status":{"$in":["pending","running"]}},
                                     {"$set":{"message_id":message_id}})

    def start_solo_pvp(self, game_id, group_id, user_id, user_name, amount, choice, result, now=None):
        if type(amount) is not int or not MIN_PVP_WAGER <= amount <= MAX_PVP_WAGER:
            raise RuleError("PvP လောင်းကြေးကို 250 မှ 30000 coin အတွင်းထားပါ။")
        if choice not in {"heads", "tails"} or result not in {"heads", "tails"}:
            raise RuleError("Solo PvP result မမှန်ပါ။ h=Heads, t=Tails ကိုသုံးပါ။")
        def start(s):
            at = time.time() if now is None else now
            if str(group_id) != str(self.get("pvp_group_id", session=s)):
                raise RuleError("သတ်မှတ်ထားတဲ့ PvP group မှာပဲ ကစားနိုင်ပါတယ်။")
            active = self._active_group_games(group_id, s)
            if active >= MAX_ACTIVE_PVP_GAMES:
                raise RuleError("လက်ရှိ game ၃ ပွဲ ပြည့်နေပါပြီ။ တစ်ပွဲပြီးမှ ပွဲအသစ် စနိုင်ပါမည်။")
            if self._player_locked(group_id, user_id, s):
                raise RuleError("ဒီ user က PvP/Boom game တစ်ခုမှာ ပါဝင်နေပြီးသားပါ။ ပွဲပြီးမှ ထပ်ကစားနိုင်ပါတယ်။")
            balance = self.wallet_balance(user_id, s)
            if balance["available"] < amount:
                raise RuleError(f"Coin မလုံလောက်ပါ။ လက်ရှိသုံးနိုင်တာ {money(balance['available'])} ပါ။")
            if result == choice and balance["total"] - amount + amount * 2 > 99999999999:
                raise RuleError("အနိုင်ရလျှင် wallet limit ကျော်နိုင်ပါတယ်။")
            self.db.wallets.update_one({"_id":user_id,"balance":{"$gte":amount}}, {"$inc":{"balance":-amount}}, session=s)
            eid=self._next("wallet_events",s)
            self.db.wallet_events.insert_one(dict(_id=eid,id=eid,user_id=user_id,delta=-amount,kind="pvp_stake",
                note=f"Solo PvP stake · {game_id}",actor_id=user_id,auction_id=None,
                event_key=f"pvp:{game_id}:stake",created=at),session=s)
            self.db.pvp_games.insert_one(dict(_id=game_id,id=game_id,group_id=group_id,
                requester_id=user_id,requester_name=user_name[:64],target_id=0,target_name="House",
                amount=amount,status="running",message_id=0,created=at,next_at=at+1,step=0,
                final_percent=random.randint(1,99),winner_id=None,
                slot_notified=0 if active == 2 else 1,
                mode="solo",choice=choice,result=result),session=s)
            return self._pvp_game(game_id,s)
        return self._tx(start)

    def play_solo_pvp(self, game_id, group_id, user_id, user_name, amount, choice, result, now=None):
        if type(amount) is not int or not MIN_PVP_WAGER <= amount <= MAX_PVP_WAGER:
            raise RuleError("PvP လောင်းကြေးကို 250 မှ 30000 coin အတွင်းထားပါ။")
        if choice not in {"heads", "tails"} or result not in {"heads", "tails"}:
            raise RuleError("Solo PvP result မမှန်ပါ။ h=Heads, t=Tails ကိုသုံးပါ။")
        def play(s):
            at = time.time() if now is None else now
            if str(group_id) != str(self.get("pvp_group_id", session=s)):
                raise RuleError("သတ်မှတ်ထားတဲ့ PvP group မှာပဲ ကစားနိုင်ပါတယ်။")
            if self._active_group_games(group_id, s) >= MAX_ACTIVE_PVP_GAMES:
                raise RuleError("လက်ရှိ game ၃ ပွဲ ပြည့်နေပါပြီ။ တစ်ပွဲပြီးမှ ပွဲအသစ် စနိုင်ပါမည်။")
            if self._player_locked(group_id, user_id, s):
                raise RuleError("ဒီ user က PvP/Boom game တစ်ခုမှာ ပါဝင်နေပြီးသားပါ။ ပွဲပြီးမှ ထပ်ကစားနိုင်ပါတယ်။")
            balance = self.wallet_balance(user_id, s)
            if balance["available"] < amount:
                raise RuleError(f"Coin မလုံလောက်ပါ။ လက်ရှိသုံးနိုင်တာ {money(balance['available'])} ပါ။")
            won = choice == result
            if won and balance["total"] - amount + amount * 2 > 99999999999:
                raise RuleError("အနိုင်ရလျှင် wallet limit ကျော်နိုင်ပါတယ်။")
            self.db.wallets.update_one({"_id": user_id}, {"$inc": {"balance": -amount}}, upsert=True, session=s)
            eid = self._next("wallet_events", s)
            self.db.wallet_events.insert_one(dict(_id=eid,id=eid,user_id=user_id,delta=-amount,kind="pvp_stake",
                note=f"Solo PvP stake · {game_id}",actor_id=user_id,auction_id=None,event_key=f"pvp:{game_id}:stake",created=at),session=s)
            if won:
                prize = amount * 2
                self.db.wallets.update_one({"_id": user_id}, {"$inc": {"balance": prize}}, session=s)
                eid = self._next("wallet_events", s)
                self.db.wallet_events.insert_one(dict(_id=eid,id=eid,user_id=user_id,delta=prize,kind="pvp_win",
                    note=f"Solo PvP prize · {game_id}",actor_id=None,auction_id=None,event_key=f"pvp:{game_id}:prize",created=at),session=s)
            self.db.pvp_games.insert_one(dict(_id=game_id,id=game_id,group_id=group_id,requester_id=user_id,
                requester_name=user_name[:64],target_id=0,target_name="House",amount=amount,status="finished",
                message_id=0,created=at,next_at=None,step=5,final_percent=100 if result == "heads" else 0,
                winner_id=user_id if won else 0,slot_notified=1,mode="solo",choice=choice,result=result),session=s)
            return self._pvp_game(game_id, s)
        return self._tx(play)

    def create_solo_boom(self, game_id, group_id, requester_id, requester_name, amount, now=None):
        if type(amount) is not int or not MIN_PVP_WAGER <= amount <= MAX_PVP_WAGER:
            raise RuleError("Boom လောင်းကြေးကို 250 မှ 30000 coin အတွင်းထားပါ။")
        def create(s):
            at = time.time() if now is None else now
            if str(group_id) != str(self.get("pvp_group_id", session=s)):
                raise RuleError("Boom ကို သတ်မှတ်ထားတဲ့ game group မှာပဲ ကစားနိုင်ပါတယ်။")
            if self._active_group_games(group_id, s) >= MAX_ACTIVE_PVP_GAMES:
                raise RuleError("လက်ရှိ game ၃ ပွဲ ပြည့်နေပါပြီ။ တစ်ပွဲပြီးမှ ပွဲအသစ် စနိုင်ပါမည်။")
            if self._player_locked(group_id, requester_id, s):
                raise RuleError("ဒီ user က PvP/Boom request သို့ game တစ်ခုမှာ ပါဝင်နေပြီးသားပါ။ ပွဲပြီးမှ ထပ်ကစားနိုင်ပါတယ်။")
            balance = self.wallet_balance(requester_id, s)
            if balance["available"] < amount:
                raise RuleError(f"Coin မလုံလောက်ပါ။ လက်ရှိသုံးနိုင်တာ {money(balance['available'])} ပါ။")
            self.db.wallets.update_one({"_id":requester_id,"balance":{"$gte":amount}}, {"$inc":{"balance":-amount}}, session=s)
            eid=self._next("wallet_events",s)
            self.db.wallet_events.insert_one(dict(_id=eid,id=eid,user_id=requester_id,delta=-amount,
                kind="boom_stake",note=f"Solo Boom stake · {game_id}",actor_id=requester_id,
                auction_id=None,event_key=f"boom:{game_id}:stake:{requester_id}",created=at),session=s)
            positions=random.sample(range(1,13),4)
            self.db.boom_games.insert_one(dict(_id=game_id,id=game_id,group_id=group_id,
                requester_id=requester_id,requester_name=requester_name[:64],target_id=0,target_name="BOOM",
                amount=amount,status="running",message_id=0,created=at,board_size=12,
                boom_positions=positions,boom_owners={},revealed=[],turn_id=requester_id,
                turn_at=at,last_click=None,mode="solo",winner_id=None), session=s)
            return self._clean(self.db.boom_games.find_one({"_id":game_id}, session=s))
        return self._tx(create)

    def create_boom(self, game_id, group_id, requester_id, requester_name, target_id, target_name, amount, now=None):
        if type(amount) is not int or not MIN_PVP_WAGER <= amount <= MAX_PVP_WAGER:
            raise RuleError("Boom လောင်းကြေးကို 250 မှ 30000 coin အတွင်းထားပါ။")
        if requester_id == target_id:
            raise RuleError("ကိုယ့်ကိုယ်ကို Boom request လုပ်လို့မရပါ။")
        self._finish_ready_player_dice(group_id, [requester_id, target_id], now)
        def create(s):
            at = time.time() if now is None else now
            if str(group_id) != str(self.get("pvp_group_id", session=s)):
                raise RuleError("Boom ကို သတ်မှတ်ထားတဲ့ game group မှာပဲ ကစားနိုင်ပါတယ်။")
            if self._active_group_games(group_id, s) >= MAX_ACTIVE_PVP_GAMES:
                raise RuleError("လက်ရှိ game ၃ ပွဲ ပြည့်နေပါပြီ။ တစ်ပွဲပြီးမှ ပွဲအသစ် စနိုင်ပါမည်။")
            balance = self.wallet_balance(requester_id, s)
            if balance["available"] < amount:
                raise RuleError(f"Coin မလုံလောက်ပါ။ လက်ရှိသုံးနိုင်တာ {money(balance['available'])} ပါ။")
            self.db.boom_games.insert_one(dict(_id=game_id,id=game_id,group_id=group_id,
                requester_id=requester_id,requester_name=requester_name[:64],target_id=target_id,
                target_name=target_name[:64],amount=amount,status="pending",message_id=0,created=at,
                board_size=None,boom_positions=[],revealed=[],turn_id=None,winner_id=None), session=s)
            return self._clean(self.db.boom_games.find_one({"_id":game_id}, session=s))
        return self._tx(create)

    def set_boom_message(self, game_id, message_id):
        self.db.boom_games.update_one({"_id":game_id,"status":"pending"},{"$set":{"message_id":message_id}})

    def cancel_boom(self, game_id, actor_id):
        def cancel(s):
            row = self.db.boom_games.find_one({"_id":game_id}, session=s)
            if not row or actor_id not in (row["requester_id"], row["target_id"]):
                raise RuleError("ဒီ Boom request ကို cancel လုပ်ခွင့်မရှိပါ။")
            if row["status"] != "pending":
                raise RuleError("ဒီ Boom request ကို ယခု cancel မလုပ်နိုင်ပါ။")
            self.db.boom_games.update_one({"_id":game_id,"status":"pending"},{"$set":{"status":"cancelled"}},session=s)
            row["status"] = "cancelled"
            return self._clean(row)
        return self._tx(cancel)

    def expire_boom(self, now=None):
        at = time.time() if now is None else now
        cutoff = at - PVP_REQUEST_TIMEOUT_SECONDS
        def expire(s):
            rows = list(self.db.boom_games.find({"status":"pending","created":{"$lte":cutoff}},session=s))
            expired=[]
            for row in rows:
                result=self.db.boom_games.update_one({"_id":row["_id"],"status":"pending"},{"$set":{"status":"cancelled"}},session=s)
                if result.modified_count:
                    row["status"]="cancelled"; expired.append(self._clean(row))
            return expired
        return self._tx(expire)

    def accept_boom(self, game_id, actor_id, now=None):
        def accept(s):
            at=time.time() if now is None else now
            row=self.db.boom_games.find_one({"_id":game_id},session=s)
            if not row or actor_id != row["target_id"]: raise RuleError("Boom ကို ဖိတ်ခေါ်ခံရသူပဲ Confirm လုပ်နိုင်ပါတယ်။")
            if row["status"] != "pending": raise RuleError("ဒီ Boom request ကို အရင်ဖြေပြီးပါပြီ။")
            if str(row["group_id"]) != str(self.get("pvp_group_id",session=s)): raise RuleError("ဒီ group မှာ Boom မကစားနိုင်တော့ပါ။")
            if self._active_group_games(row["group_id"], s) >= MAX_ACTIVE_PVP_GAMES:
                raise RuleError("လက်ရှိ game ၃ ပွဲ ပြည့်နေပါပြီ။ တစ်ပွဲပြီးမှ ပွဲအသစ် စနိုင်ပါမည်။")
            for uid in (row["requester_id"],row["target_id"]):
                balance=self.wallet_balance(uid,s)
                if balance["available"] < row["amount"]: raise RuleError(f"User {uid} မှာ coin မလုံလောက်ပါ။")
            for uid in (row["requester_id"],row["target_id"]):
                self.db.wallets.update_one({"_id":uid,"balance":{"$gte":row["amount"]}},{"$inc":{"balance":-row["amount"]}},session=s)
                eid=self._next("wallet_events",s)
                self.db.wallet_events.insert_one(dict(_id=eid,id=eid,user_id=uid,delta=-row["amount"],kind="boom_stake",note=f"Boom stake · {game_id}",actor_id=actor_id,auction_id=None,event_key=f"boom:{game_id}:stake:{uid}",created=at),session=s)
            size=random.choice((6,9)); positions=random.sample(range(1,size+1),2)
            owners={str(positions[0]):row["requester_id"],str(positions[1]):row["target_id"]}
            self.db.boom_games.update_one({"_id":game_id,"status":"pending"},{"$set":{"status":"running","board_size":size,"boom_positions":positions,"boom_owners":owners,"revealed":[],"turn_id":row["requester_id"],"turn_at":at}},session=s)
            return self._clean(self.db.boom_games.find_one({"_id":game_id},session=s))
        return self._tx(accept)

    def pick_boom(self, game_id, actor_id, number, now=None):
        def pick(s):
            row=self.db.boom_games.find_one({"_id":game_id},session=s)
            if not row or row["status"] != "running": raise RuleError("ဒီ Boom game မကစားနိုင်တော့ပါ။")
            solo = row.get("mode") == "solo"
            if solo and actor_id != row["requester_id"]:
                raise RuleError("ဒီ Solo Boom ကို စတင်ကစားသူပဲ နှိပ်နိုင်ပါတယ်။")
            if not solo and actor_id not in (row["requester_id"],row["target_id"]): raise RuleError("ဒီ game ထဲက player ၂ ယောက်ပဲ နှိပ်နိုင်ပါတယ်။")
            if not solo and actor_id != row["turn_id"]: raise RuleError("အခု သင့်အလှည့်မဟုတ်ပါ။")
            if number in row["revealed"]: raise RuleError("ဒီ button ကို နှိပ်ပြီးသားပါ။")
            if number < 1 or number > row["board_size"]: raise RuleError("Button မမှန်ပါ။")
            at=time.time() if now is None else now
            if number in row["boom_positions"]:
                if solo:
                    streak=self._record_streak(s,"boom",row["group_id"],row["requester_id"],False,at)
                    self.db.boom_games.update_one({"_id":game_id,"status":"running"},
                        {"$set":{"status":"finished","winner_id":0,"last_click":number,
                                  "streak":streak["streak"],"streak_reward":streak["reward"]},"$addToSet":{"revealed":number}},session=s)
                    return self._clean(self.db.boom_games.find_one({"_id":game_id},session=s))
                owner=row.get("boom_owners",{}).get(str(number))
                winner=actor_id if owner == actor_id else (row["target_id"] if actor_id==row["requester_id"] else row["requester_id"])
                pot=row["amount"]*2
                self.db.wallets.update_one({"_id":winner},{"$inc":{"balance":pot}},session=s)
                winner_streak=self._record_streak(s,"boom",row["group_id"],winner,True,at)
                loser=row["target_id"] if winner==row["requester_id"] else row["requester_id"]
                self._record_streak(s,"boom",row["group_id"],loser,False,at)
                eid=self._next("wallet_events",s)
                self.db.wallet_events.insert_one(dict(_id=eid,id=eid,user_id=winner,delta=pot,kind="boom_win",note=f"Boom prize · {game_id}",actor_id=None,auction_id=None,event_key=f"boom:{game_id}:prize",created=at),session=s)
                self.db.boom_games.update_one({"_id":game_id,"status":"running"},{"$set":{"status":"finished","winner_id":winner,"last_click":number,
                    "streak":winner_streak["streak"],"streak_reward":winner_streak["reward"]},"$addToSet":{"revealed":number}},session=s)
            else:
                update={"$addToSet":{"revealed":number},"$set":{"last_click":number,"turn_at":at}}
                if not solo:
                    update["$set"]["turn_id"] = row["target_id"] if actor_id==row["requester_id"] else row["requester_id"]
                self.db.boom_games.update_one({"_id":game_id,"status":"running","revealed":{"$ne":number}},update,session=s)
            return self._clean(self.db.boom_games.find_one({"_id":game_id},session=s))
        return self._tx(pick)

    @staticmethod
    def solo_boom_prize(amount, safe_count):
        # Solo Boom uses the wager as base: Safe 1 = 1.2x, Safe 2 = 1.4x.
        return (amount * (10 + 2 * safe_count) + 5) // 10

    def cash_boom(self, game_id, actor_id, now=None):
        def cash(s):
            at=time.time() if now is None else now
            row=self.db.boom_games.find_one({"_id":game_id},session=s)
            if not row or row.get("mode") != "solo" or row["status"] != "running":
                raise RuleError("ဒီ Solo Boom မှာ Cash ထုတ်လို့မရတော့ပါ။")
            if actor_id != row["requester_id"]:
                raise RuleError("ဒီ Solo Boom ကို စတင်ကစားသူပဲ Cash ထုတ်နိုင်ပါတယ်။")
            safe_count=len(row.get("revealed",[]))
            prize=self.solo_boom_prize(row["amount"], safe_count)
            self.db.wallets.update_one({"_id":actor_id},{"$inc":{"balance":prize}},session=s)
            streak=self._record_streak(s,"boom",row["group_id"],actor_id,safe_count >= 4,at)
            eid=self._next("wallet_events",s)
            self.db.wallet_events.insert_one(dict(_id=eid,id=eid,user_id=actor_id,delta=prize,
                kind="boom_win",note=f"Solo Boom cash prize · {game_id}",actor_id=None,
                auction_id=None,event_key=f"boom:{game_id}:prize",created=at),session=s)
            self.db.boom_games.update_one({"_id":game_id,"status":"running"},
                {"$set":{"status":"finished","winner_id":actor_id,"cash_prize":prize,"cashed":1,
                          "streak":streak["streak"],"streak_reward":streak["reward"]}},session=s)
            return self._clean(self.db.boom_games.find_one({"_id":game_id},session=s))
        return self._tx(cash)

    def replay_user(self, group_id, user_id, now=None):
        """Release only this user's stale solo rounds; never touch a duel."""
        def replay(s):
            at = time.time() if now is None else now
            query = {"group_id": group_id, "requester_id": user_id,
                     "status": {"$in": ["pending", "running"]}, "mode": "solo"}
            games = [(self.db.pvp_games, row) for row in self.db.pvp_games.find(query, session=s)]
            games += [(self.db.boom_games, row) for row in self.db.boom_games.find(query, session=s)]
            cleared = []
            for collection, row in games:
                game_id = row["_id"]
                refund_key = f"replay:{row.get('mode', 'game')}:{game_id}:refund"
                if not self.db.wallet_events.find_one({"event_key": refund_key}, session=s):
                    wallet = self.wallet_balance(user_id, s)
                    if wallet["total"] + row["amount"] > 99999999999:
                        raise RuleError("Replay refund ထည့်လျှင် wallet limit ကျော်နိုင်ပါတယ်။")
                    self.db.wallets.update_one({"_id": user_id}, {"$inc": {"balance": row["amount"]}}, session=s)
                    eid = self._next("wallet_events", s)
                    self.db.wallet_events.insert_one(dict(
                        _id=eid, id=eid, user_id=user_id, delta=row["amount"],
                        kind="game_refund", note=f"Replay stale game · {game_id}",
                        actor_id=user_id, auction_id=None, event_key=refund_key, created=at), session=s)
                result = collection.update_one(
                    {"_id": game_id, "status": {"$in": ["pending", "running"]}},
                    {"$set": {"status": "cancelled", "winner_id": 0, "replay": 1,
                              "next_at": None, "turn_at": None}}, session=s)
                if result.modified_count:
                    row["status"] = "cancelled"; row["winner_id"] = 0; row["replay"] = 1
                    cleared.append(self._clean(row))
            return cleared
        return self._tx(replay)

    def timeout_boom(self, now=None):
        at=time.time() if now is None else now
        def settle(s):
            rows=list(self.db.boom_games.find({"status":"running","turn_at":{"$lte":at-BOOM_TURN_TIMEOUT_SECONDS}},session=s))
            finished=[]
            for row in rows:
                if row.get("mode") == "solo":
                    streak=self._record_streak(s,"boom",row["group_id"],row["requester_id"],False,at)
                    result=self.db.boom_games.update_one(
                        {"_id":row["_id"],"status":"running"},
                        {"$set":{"status":"finished","winner_id":0,"timeout":1,
                                  "streak":streak["streak"],"streak_reward":streak["reward"]}}, session=s)
                    if result.modified_count:
                        row["status"]="finished"; row["winner_id"]=0; row["timeout"]=1
                        row["streak"]=streak["streak"]; row["streak_reward"]=streak["reward"]
                        finished.append(self._clean(row))
                    continue
                winner=row["target_id"] if row["turn_id"]==row["requester_id"] else row["requester_id"]
                pot=row["amount"]*2
                winner_streak=self._record_streak(s,"boom",row["group_id"],winner,True,at)
                loser=row["target_id"] if winner==row["requester_id"] else row["requester_id"]
                self._record_streak(s,"boom",row["group_id"],loser,False,at)
                result=self.db.boom_games.update_one({"_id":row["_id"],"status":"running"},{"$set":{"status":"finished","winner_id":winner,"timeout":1,"streak":winner_streak["streak"],"streak_reward":winner_streak["reward"]}},session=s)
                if not result.modified_count: continue
                self.db.wallets.update_one({"_id":winner},{"$inc":{"balance":pot}},session=s)
                eid=self._next("wallet_events",s)
                self.db.wallet_events.insert_one(dict(_id=eid,id=eid,user_id=winner,delta=pot,kind="boom_win",note=f"Boom timeout prize · {row['_id']}",actor_id=None,auction_id=None,event_key=f"boom:{row['_id']}:prize",created=at),session=s)
                row["status"]="finished"; row["winner_id"]=winner; row["timeout"]=1
                row["streak"]=winner_streak["streak"]; row["streak_reward"]=winner_streak["reward"]
                finished.append(self._clean(row))
            return finished
        return self._tx(settle)

    def boom_top(self, limit=10):
        rows=list(self.db.wallets.find().sort("balance",-1).limit(limit))
        return [{"user_id":r["_id"],"balance":r.get("balance",0)} for r in rows]

    def cancel_pvp(self,game_id,actor_id):
        def cancel(s):
            row=self._pvp_game(game_id,s)
            if actor_id not in (row["requester_id"],row["target_id"]):raise RuleError("ဒီ PvP request ကို cancel လုပ်ခွင့်မရှိပါ။")
            if row["status"]!="pending":raise RuleError("ဒီ PvP request ကို ယခု cancel မလုပ်နိုင်ပါ။")
            self.db.pvp_games.update_one({"_id":game_id,"status":"pending"},{"$set":{"status":"cancelled"}},session=s)
            return self._pvp_game(game_id,s)
        return self._tx(cancel)

    def expire_pvp(self, now=None):
        at = time.time() if now is None else now
        cutoff = at - PVP_REQUEST_TIMEOUT_SECONDS
        def expire(s):
            rows = list(self.db.pvp_games.find(
                {"status": "pending", "created": {"$lte": cutoff}}, session=s))
            expired = []
            for row in rows:
                result = self.db.pvp_games.update_one(
                    {"_id": row["_id"], "status": "pending"},
                    {"$set": {"status": "cancelled"}}, session=s)
                if result.modified_count:
                    row["status"] = "cancelled"
                    expired.append(self._clean(row))
            return expired
        return self._tx(expire)

    def accept_pvp(self,game_id,actor_id,final_percent=None,now=None):
        if final_percent is not None and (type(final_percent) is not int or not 1<=final_percent<=100):raise RuleError("PvP result မမှန်ပါ။")
        def accept(s):
            at=time.time() if now is None else now
            row=self._pvp_game(game_id,s)
            if actor_id!=row["target_id"]:raise RuleError("Request လက်ခံနိုင်သူက ဖိတ်ခေါ်ခံရသူတစ်ဦးတည်းပါ။")
            if row["status"]!="pending":raise RuleError("ဒီ PvP request ကို အရင်ဖြေပြီးပါပြီ။")
            if str(row["group_id"])!=str(self.get("pvp_group_id",session=s)):raise RuleError("ဒီ group မှာ PvP မကစားနိုင်တော့ပါ။")
            active_rounds = self._active_group_games(row["group_id"], s)
            if active_rounds >= MAX_ACTIVE_PVP_GAMES:
                raise RuleError("လက်ရှိ game ၃ ပွဲ ပြည့်နေပါပြီ။ တစ်ပွဲပြီးမှ ပွဲအသစ် စနိုင်ပါမည်။")
            for uid in (row["requester_id"],row["target_id"]):
                balance=self.wallet_balance(uid,s)
                if balance["available"]<row["amount"]:raise RuleError(f"User {uid} မှာ လိုအပ်တဲ့ coin မလုံလောက်ပါ။")
                if balance["total"]+row["amount"]>99999999999:raise RuleError("လောင်းကြေးအနိုင်ရလျှင် wallet limit ကျော်နိုင်ပါတယ်။")
            for uid in (row["requester_id"],row["target_id"]):
                self.db.wallets.update_one({"_id":uid,"balance":{"$gte":row["amount"]}},{"$inc":{"balance":-row["amount"]}},session=s)
                eid=self._next("wallet_events",s)
                self.db.wallet_events.insert_one(dict(_id=eid,id=eid,user_id=uid,delta=-row["amount"],kind="pvp_stake",
                    note=f"PvP stake · {game_id}",actor_id=actor_id,auction_id=None,event_key=f"pvp:{game_id}:stake:{uid}",created=at),session=s)
            state = {"status": "running", "next_at": at + PVP_ANIMATION_INTERVAL_SECONDS,
                     "step": 0, "final_percent": final_percent,
                     # Only the third concurrent round frees a slot from a full set.
                     "slot_notified": 0 if active_rounds == 2 else 1}
            if final_percent is None:
                state.update(mode="dice", next_at=at, dice_started_at=None,
                             dice_value=None, dice_message_id=None, result_notified=False)
            self.db.pvp_games.update_one({"_id":game_id,"status":"pending"},
                                        {"$set":state},session=s)
            return self._pvp_game(game_id,s)
        return self._tx(accept)

    def claim_pvp_dice(self, game_id, now=None):
        """Claim one Telegram roll; an uncertain send is refunded, never rerolled."""
        def claim(s):
            at = time.time() if now is None else now
            row = self._pvp_game(game_id, s)
            if (row["status"] != "running" or row.get("mode") != "dice"
                    or row.get("dice_started_at") is not None):
                return None
            self.db.pvp_games.update_one({"_id": game_id}, {"$set": {
                "dice_started_at": at, "next_at": at + PVP_DICE_TIMEOUT_SECONDS}}, session=s)
            return self._pvp_game(game_id, s)
        return self._tx(claim)

    def record_pvp_dice(self, game_id, value, message_id, now=None):
        if type(value) is not int or not 1 <= value <= 6 or type(message_id) is not int or message_id <= 0:
            raise RuleError("PvP အံစာရလဒ် မမှန်ပါ။")
        def record(s):
            at = time.time() if now is None else now
            row = self._pvp_game(game_id, s)
            if row.get("dice_value") is not None:
                if (row["dice_value"], row["dice_message_id"]) != (value, message_id):
                    raise RuleError("ဒီပွဲရဲ့ အံစာရလဒ်ကို ပြောင်းလို့မရပါ။")
                return row
            if (row["status"] != "running" or row.get("mode") != "dice"
                    or row.get("dice_started_at") is None):
                raise RuleError("ဒီ PvP ပွဲမှာ အံစာရလဒ် သိမ်းလို့မရပါ။")
            self.db.pvp_games.update_one({"_id": game_id}, {"$set": {
                "dice_value": value, "dice_message_id": message_id,
                "next_at": at + PVP_DICE_ANIMATION_SECONDS}}, session=s)
            return self._pvp_game(game_id, s)
        return self._tx(record)

    def pending_pvp_dice_results(self):
        return [self._clean(row) for row in self.db.pvp_games.find({
            "mode": "dice", "status": {"$in": ["finished", "cancelled"]},
            "result_notified": False}).sort([("created", 1), ("_id", 1)])]

    def mark_pvp_dice_result(self, game_id):
        self.db.pvp_games.update_one({"_id": game_id, "mode": "dice",
            "status": {"$in": ["finished", "cancelled"]}}, {"$set": {"result_notified": True}})

    def due_pvp(self,now=None):
        at=time.time() if now is None else now
        return [self._clean(r) for r in self.db.pvp_games.find({"status":"running","next_at":{"$lte":at}}).sort([("next_at",1),("created",1)])]

    def advance_pvp(self,game_id,now=None):
        def advance(s):
            at=time.time() if now is None else now
            row=self._pvp_game(game_id,s)
            if row["status"]!="running" or row["next_at"] is None or row["next_at"]>at:return row
            if row.get("mode") == "dice" and row.get("dice_value") is None:
                if row.get("dice_started_at") is None:
                    return row
                # A lost Telegram response or process crash must not leave stakes locked.
                for uid in (row["requester_id"], row["target_id"]):
                    self.db.wallets.update_one({"_id": uid}, {"$inc": {"balance": row["amount"]}}, session=s)
                    eid = self._next("wallet_events", s)
                    self.db.wallet_events.insert_one(dict(_id=eid, id=eid, user_id=uid,
                        delta=row["amount"], kind="pvp_refund", note=f"PvP dice unavailable · {game_id}",
                        actor_id=None, auction_id=None, event_key=f"pvp:{game_id}:timeout:{uid}",
                        created=at), session=s)
                self.db.pvp_games.update_one({"_id": game_id}, {"$set": {
                    "status": "cancelled", "next_at": None, "slot_notified": 1}}, session=s)
                return self._pvp_game(game_id, s)
            step = 2 if row.get("mode") == "dice" else row["step"] + 1
            if step<2:
                self.db.pvp_games.update_one({"_id":game_id,"status":"running"},{"$set":{"step":step,"next_at":at+PVP_ANIMATION_INTERVAL_SECONDS}},session=s)
                return self._pvp_game(game_id,s)
            requester_percent = row["final_percent"]
            target_percent = 100 - requester_percent if requester_percent is not None else None
            if row.get("mode") == "solo":
                won = row.get("choice") == row.get("result")
                pot = row["amount"] * 2
                winning_percent = requester_percent
                losing_percent = 100 - winning_percent
                share = pot * losing_percent // 100 if losing_percent <= 25 else 0
                winner = row["requester_id"] if won else 0
                prize = pot - share if won else 0
                refund = 0
                if won:
                    self.db.wallets.update_one({"_id":winner},{"$inc":{"balance":prize}},session=s)
                    eid=self._next("wallet_events",s)
                    self.db.wallet_events.insert_one(dict(_id=eid,id=eid,user_id=winner,delta=prize,kind="pvp_win",
                        note=f"Solo PvP prize · {game_id}",actor_id=None,auction_id=None,
                        event_key=f"pvp:{game_id}:prize",created=at),session=s)
                    streak=self._record_streak(s,"pvp",row["group_id"],winner,True,at)
                else:
                    player_percent = 100 - winning_percent
                    refund = pot * player_percent // 100 if player_percent <= 25 else 0
                    if refund:
                        self.db.wallets.update_one({"_id":row["requester_id"]},{"$inc":{"balance":refund}},session=s)
                        eid=self._next("wallet_events",s)
                        self.db.wallet_events.insert_one(dict(_id=eid,id=eid,user_id=row["requester_id"],delta=refund,kind="pvp_refund",
                            note=f"Solo PvP share refund · {game_id}",actor_id=None,auction_id=None,
                            event_key=f"pvp:{game_id}:refund",created=at),session=s)
                    streak=self._record_streak(s,"pvp",row["group_id"],row["requester_id"],False,at)
                self.db.pvp_games.update_one({"_id":game_id,"status":"running"},
                    {"$set":{"status":"finished","winner_id":winner,"step":2,"next_at":None,
                              "prize":prize,"refund":refund,"streak":streak["streak"],"streak_reward":streak["reward"]}},session=s)
                return self._pvp_game(game_id,s)
            pot = row["amount"] * 2
            if row.get("mode") == "dice":
                winner, winner_payout = pvp_dice_outcome(row)
                loser_payout = 0
            else:
                winner = row["requester_id"] if requester_percent > 50 else row["target_id"]
                loser_percent = target_percent if winner == row["requester_id"] else requester_percent
                loser_payout = 0 if loser_percent > 25 else pot * loser_percent // 100
                winner_payout = pot - loser_payout
            loser = row["target_id"] if winner == row["requester_id"] else row["requester_id"]
            for uid in (winner, loser):
                if not self.db.wallets.find_one({"_id":uid},session=s):
                    raise RuleError("Winner/loser wallet မတွေ့ပါ။ Owner က စစ်ဆေးရန်လိုပါတယ်။")
            self.db.wallets.update_one({"_id":winner},{"$inc":{"balance":winner_payout}},session=s)
            eid=self._next("wallet_events",s)
            self.db.wallet_events.insert_one(dict(_id=eid,id=eid,user_id=winner,delta=winner_payout,kind="pvp_win",
                note=f"PvP prize · {game_id}",actor_id=None,auction_id=None,event_key=f"pvp:{game_id}:prize",created=at),session=s)
            if loser_payout:
                self.db.wallets.update_one({"_id":loser},{"$inc":{"balance":loser_payout}},session=s)
                eid=self._next("wallet_events",s)
                self.db.wallet_events.insert_one(dict(_id=eid,id=eid,user_id=loser,delta=loser_payout,kind="pvp_refund",
                    note=f"PvP refund · {game_id}",actor_id=None,auction_id=None,event_key=f"pvp:{game_id}:refund",created=at),session=s)
            winner_streak=self._record_streak(s,"pvp",row["group_id"],winner,True,at)
            self._record_streak(s,"pvp",row["group_id"],loser,False,at)
            settled = {"status": "finished", "winner_id": winner, "step": 2, "next_at": None,
                       "streak": winner_streak["streak"], "streak_reward": winner_streak["reward"]}
            if row.get("mode") == "dice":
                settled.update(prize=winner_payout, refund=0, retained=pot-winner_payout)
            self.db.pvp_games.update_one({"_id":game_id,"status":"running"}, {"$set":settled}, session=s)
            return self._pvp_game(game_id,s)
        return self._tx(advance)

    def pending_pvp_slot_notifications(self):
        return [self._clean(r) for r in self.db.pvp_games.find({"status":"finished","slot_notified":0}).sort([("created",1),("_id",1)])]

    def mark_pvp_slot_notified(self,game_id):
        self.db.pvp_games.update_one({"_id":game_id,"status":"finished"},{"$set":{"slot_notified":1}})

    def stats(self):
        counts=[(r["_id"],r["count"]) for r in self.db.auctions.aggregate([{"$group":{"_id":"$status","count":{"$sum":1}}}])]
        sales=list(self.db.auctions.aggregate([{"$match":{"status":"closed"}},{"$group":{"_id":None,"total":{"$sum":"$highest"}}}]))
        return counts,self.db.bids.count_documents({}),sales[0]["total"] if sales else 0
