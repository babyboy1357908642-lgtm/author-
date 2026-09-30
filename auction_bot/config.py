import os
from dataclasses import dataclass
from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    token: str
    owners: frozenset
    channel_id: str = ""
    group_id: str = ""
    mongodb_uri: str = ""
    mongodb_database: str = "authbid_bot"

    @classmethod
    def from_env(cls):
        load_dotenv()
        token = os.getenv("BOT_TOKEN", "").strip()
        owners = os.getenv("OWNER_IDS", "").split(",")
        if not token or token == "replace_with_BotFather_token":
            raise ValueError("Set BOT_TOKEN in .env (get it from @BotFather).")
        if not all(owner.strip().isdigit() and int(owner) > 0 for owner in owners):
            raise ValueError("OWNER_IDS must contain comma-separated positive Telegram user IDs.")
        for key in ("CHANNEL_ID", "GROUP_ID"):
            value = os.getenv(key, "").strip()
            if value and (not value.lstrip("-").isdigit() or int(value) >= 0):
                raise ValueError(f"{key} must be a negative numeric Telegram chat ID.")
        uri = os.getenv("MONGODB_URI", "").strip()
        if not uri.startswith(("mongodb://", "mongodb+srv://")):
            raise ValueError("Set MONGODB_URI to a MongoDB replica set or Atlas connection string")
        database = os.getenv("MONGODB_DATABASE", "authbid_bot").strip()
        if not database or any(c in database for c in '/\\. "$*<>:|?') or len(database.encode())>63:
            raise ValueError("MONGODB_DATABASE is invalid")
        return cls(
            token=token,
            owners=frozenset(int(owner) for owner in owners),
            channel_id=os.getenv("CHANNEL_ID", "").strip(),
            group_id=os.getenv("GROUP_ID", "").strip(),
            mongodb_uri=uri,
            mongodb_database=database,
        )
