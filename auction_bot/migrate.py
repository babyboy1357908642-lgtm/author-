"""One-time SQLite -> MongoDB import. Stop polling before taking the snapshot."""
import argparse
import sqlite3
from pathlib import Path
from .config import Config
from .mongo_store import MongoStore
from .domain import RuleError
from pymongo.errors import PyMongoError

TABLES=("settings","auctions","bids","wallets","holds","wallet_events","banned","messages")


def read_snapshot(path):
    source=Path(path).resolve()
    with sqlite3.connect(source.as_uri()+"?mode=ro",uri=True) as db:
        db.row_factory=sqlite3.Row
        db.execute("BEGIN")
        result={name:[dict(r) for r in db.execute(f'SELECT * FROM {name}')] for name in TABLES}
    auctions={r["id"]:r for r in result["auctions"]}
    wallets={r["user_id"]:r["balance"] for r in result["wallets"]}
    holds={r["auction_id"]:r for r in result["holds"]}
    held={}
    for row in holds.values():
        auction=auctions.get(row["auction_id"])
        if (not auction or auction["status"]!="active" or row["amount"]<=0
                or auction["winner_id"]!=row["user_id"] or auction["highest"]!=row["amount"]):
            raise RuleError("Source wallet holds do not match auction leaders")
        held[row["user_id"]]=held.get(row["user_id"],0)+row["amount"]
    for row in result["auctions"]:
        row.setdefault("winner_notified", int(row["status"] in ("closed", "cancelled")))
        if row["status"] not in ("active","publishing"):continue
        if row.get("wallet_required") and row["highest"] is not None and row["id"] not in holds:
            raise RuleError("Source wallet auction is missing its hold")
        if row["highest"] is not None and row["id"] not in holds:
            uid=row["winner_id"]
            held[uid]=held.get(uid,0)+row["highest"]
            result["holds"].append(dict(auction_id=row["id"],user_id=uid,amount=row["highest"]))
        row["wallet_required"]=1
        row["version"]+=1
    if any(balance<0 or balance>99999999999 for balance in wallets.values()):
        raise RuleError("Source wallet totals are invalid")
    if any(amount>wallets.get(uid,0) for uid,amount in held.items()):
        raise RuleError("Open legacy bids have insufficient wallet funds. Credit their owners or close/cancel those auctions before migrating.")
    for row in result["bids"]+result["messages"]:
        if row["auction_id"] not in auctions:raise RuleError("Source auction references are invalid")
    for row in result["wallet_events"]:
        if row["user_id"] not in wallets:raise RuleError("Source wallet event references are invalid")
    settings={r["key"]:r["value"] for r in result["settings"]}
    settings["wallet_mode"]="1"
    result["settings"]=[dict(key=k,value=v) for k,v in settings.items()]
    return result


def import_snapshot(store, snapshot):
    def apply(session):
        if store.db.migrations.find_one({"_id":"sqlite-import"},session=session):
            raise RuleError("SQLite import already completed; no data overwritten")
        for name in TABLES[1:]+("counters",):
            if store.db[name].find_one({},session=session):raise RuleError("MongoDB target is not empty; import refused")
        allowed={"wallet_mode":"1","increment":"5000","paused":"0",
                 "rules":"Bid ငွေကို ယာယီထိန်းထားပြီး winner ကို လေလံပိတ်ချိန် ငွေဖြတ်ပါမယ်။"}
        for row in store.db.settings.find({},session=session):
            if allowed.get(row["_id"])!=row["value"]:raise RuleError("MongoDB target has existing settings; import refused")
        store.db.settings.delete_many({},session=session)
        for name,rows in snapshot.items():
            docs=[]
            for row in rows:
                row=dict(row)
                if name=="settings":key=row.pop("key")
                elif name in ("wallets","banned"):key=row["user_id"]
                elif name=="holds":key=row["auction_id"]
                elif name=="messages":key=f'{row["chat_id"]}:{row["message_id"]}'
                else:key=row["id"]
                row["_id"]=key
                docs.append(row)
            if docs:store.db[name].insert_many(docs,session=session)
        for name in ("auctions","bids","wallet_events"):
            highest=max((r["id"] for r in snapshot[name]),default=0)
            store.db.counters.insert_one({"_id":name,"value":highest},session=session)
        store.db.migrations.insert_one({"_id":"sqlite-import","counts":{k:len(v) for k,v in snapshot.items()}},session=session)
    store._tx(apply)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sqlite",required=True)
    parser.add_argument("--apply",action="store_true",help="Import into an empty MongoDB database after stopping the bot")
    args=parser.parse_args()
    store=None
    try:
        snapshot=read_snapshot(args.sqlite)
        print("Validated snapshot:",", ".join(f"{name}={len(rows)}" for name,rows in snapshot.items()))
        if not args.apply:
            print("Dry run only. Stop the bot, set MONGODB_URI/MONGODB_DATABASE, then rerun with --apply.")
            return
        config=Config.from_env()
        store=MongoStore(config.mongodb_uri,config.mongodb_database)
        import_snapshot(store,snapshot)
        print("Migration committed. Source SQLite file unchanged; wallet holds enabled.")
    except (RuleError,ValueError,sqlite3.Error,PyMongoError) as exc:
        print(str(exc) if isinstance(exc,RuleError) else "Migration failed; check source schema and MongoDB replica-set access. No partial import committed.")
        raise SystemExit(1) from None
    finally:
        if store:store.close()


if __name__=="__main__":main()
