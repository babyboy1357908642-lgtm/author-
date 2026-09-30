import unittest
import tempfile
import asyncio
import threading
from pathlib import Path
from types import SimpleNamespace

from auction_bot.store import RuleError, Store, cents, money, usd_to_coins
from auction_bot.bot import (AuctionBot, auth_adjustment, pvp_animation_text, pvp_payouts,
                             signed_owner_message_args, usd_equivalent,
                             UPDATE_CONCURRENCY, WORKER_TICK_INTERVAL_SECONDS)


class PvPStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.temp.name) / "bot.sqlite3"))
        self.store.set_pvp_group(-100123)
        self.amount = cents("500")

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def credit(self, user_id, amount=100000):
        self.store.adjust_wallet(user_id, amount, 99, f"credit:{user_id}", "test coin")

    def request(self, game_id, first, second, now=100):
        return self.store.create_pvp(game_id, -100123, first, f"Player {first}",
                                     second, f"Player {second}", self.amount, now=now)

    def test_request_needs_funds_and_only_target_can_confirm(self):
        self.credit(1, 25000)
        with self.assertRaisesRegex(RuleError, "Coin မလုံလောက်"):
            self.request("short", 1, 2)
        self.store.adjust_wallet(1, 75000, 99, "credit:1:extra", "top up")
        self.credit(2, 25000)
        game = self.request("challenge", 1, 2)
        with self.assertRaisesRegex(RuleError, "ဖိတ်ခေါ်ခံရသူ"):
            self.store.accept_pvp(game["id"], 1, 60, now=100)
        with self.assertRaisesRegex(RuleError, "လိုအပ်တဲ့ coin"):
            self.store.accept_pvp(game["id"], 2, 60, now=100)
        self.store.adjust_wallet(2, 75000, 99, "credit:2:extra", "top up")
        accepted = self.store.accept_pvp(game["id"], 2, 60, now=100)
        self.assertEqual(accepted["status"], "running")
        self.assertEqual(self.store.wallet_balance(1)["available"], 50000)
        self.assertEqual(self.store.wallet_balance(2)["available"], 50000)
        with self.assertRaisesRegex(RuleError, "This User Playing"):
            self.request("busy", 3, 1)

    def test_pending_request_locks_requester_and_target(self):
        self.credit(1)
        self.credit(2)
        self.credit(3)
        self.request("pending", 1, 2)
        with self.assertRaisesRegex(RuleError, "ပွဲပြီး သို့မဟုတ် cancel"):
            self.request("requester-busy", 1, 3)
        with self.assertRaisesRegex(RuleError, "ပွဲပြီး သို့မဟုတ် cancel"):
            self.request("target-busy", 3, 2)
        self.store.cancel_pvp("pending", 1)
        self.request("after-cancel", 1, 3)

    def test_winner_receives_pot_and_slot_notice_is_emitted_once(self):
        self.credit(1)
        self.credit(2)
        game = self.request("payout", 1, 2)
        self.store.accept_pvp(game["id"], 2, 60, now=100)
        self.assertEqual(self.store.due_pvp(now=100), [])
        for tick in range(1, 3):
            game = self.store.advance_pvp(game["id"], now=100 + tick)
            self.assertEqual(game["step"], tick)
        self.assertEqual(game["status"], "finished")
        self.assertEqual(game["winner_id"], 1)
        self.assertEqual(self.store.wallet_balance(1)["total"], 150000)
        self.assertEqual(self.store.wallet_balance(2)["total"], 50000)
        notifications = self.store.pending_pvp_slot_notifications()
        self.assertEqual([row["id"] for row in notifications], ["payout"])
        self.store.mark_pvp_slot_notified("payout")
        self.assertEqual(self.store.pending_pvp_slot_notifications(), [])

    def test_pvp_payout_uses_redeem_percentage_until_loser_exceeds_25(self):
        self.credit(1)
        self.credit(2)
        game = self.request("split", 1, 2)
        self.store.accept_pvp(game["id"], 2, 80, now=100)
        for tick in range(1, 3):
            game = self.store.advance_pvp(game["id"], now=100 + tick)
        self.assertEqual(self.store.wallet_balance(1)["total"], 130000)
        self.assertEqual(self.store.wallet_balance(2)["total"], 70000)

        game = self.request("full-pot", 1, 2)
        self.store.accept_pvp(game["id"], 2, 65, now=200)
        for tick in range(1, 3):
            game = self.store.advance_pvp(game["id"], now=200 + tick)
        self.assertEqual(self.store.wallet_balance(1)["total"], 180000)
        self.assertEqual(self.store.wallet_balance(2)["total"], 20000)

    def test_cannot_start_a_fourth_simultaneous_round(self):
        for user_id in range(1, 13):
            self.credit(user_id, 50000)
        for index in range(3):
            game = self.request(f"game-{index}", index * 2 + 1, index * 2 + 2)
            self.store.accept_pvp(game["id"], game["target_id"], 60, now=100)
        with self.assertRaisesRegex(RuleError, "game ၃ ပွဲ"):
            self.request("game-3", 7, 8)

    def test_coin_amount_display(self):
        self.assertEqual(cents("10000"), 1_000_000)
        self.assertEqual(money(1_500_000), "15000coin")
        self.assertEqual(money(250_000), "2500coin")
        self.assertEqual(money(250_050), "2500.5coin")
        self.assertEqual(money(250_005), "2500.05coin")

    def test_pvp_wager_maximum_is_30000_coins(self):
        self.credit(1, cents("30001"))
        with self.assertRaisesRegex(RuleError, "30000"):
            self.store.play_solo_pvp("too-large", -100123, 1, "Player 1", cents("30001"), "heads", "tails", now=100)

    def test_dailycoin_rewards_500_to_2000_and_locks_for_24_hours(self):
        first = self.store.claim_dailycoin(77, now=1000)
        self.assertTrue(first["claimed"])
        self.assertGreaterEqual(first["reward"], cents("500"))
        self.assertLessEqual(first["reward"], cents("2000"))
        retry = self.store.claim_dailycoin(77, now=1000 + 86399)
        self.assertFalse(retry["claimed"])
        self.assertEqual(retry["remaining"], 1)
        second = self.store.claim_dailycoin(77, now=1000 + 86400)
        self.assertTrue(second["claimed"])
        self.assertEqual(self.store.wallet_balance(77)["total"], first["reward"] + second["reward"])

    def test_responsiveness_settings_and_local_sqlite_thread_safety(self):
        self.assertEqual(UPDATE_CONCURRENCY, 16)
        self.assertEqual(WORKER_TICK_INTERVAL_SECONDS, 0.5)
        bot = object.__new__(AuctionBot)
        bot.store = self.store
        current_thread = threading.get_ident()
        self.assertEqual(asyncio.run(bot.store_call(threading.get_ident)), current_thread)

    def test_bot_requires_mongodb_and_has_no_sqlite_fallback(self):
        with self.assertRaisesRegex(ValueError, "MONGODB_URI is required"):
            AuctionBot(SimpleNamespace(mongodb_uri="", mongodb_database="authbid_bot"))

    def test_unrelated_group_chat_uses_cached_routing_and_skips_database_work(self):
        class NoDatabaseAccess:
            def __getattr__(self, name):
                raise AssertionError(f"unexpected database access: {name}")

        bot = object.__new__(AuctionBot)
        bot.store = NoDatabaseAccess()
        bot.config = SimpleNamespace(owners=set())
        bot.group_id = "-100123"
        bot.pvp_group_id = "-100456"
        message = SimpleNamespace(sender_chat=None, text="ordinary group message", chat_id=-100123)
        update = SimpleNamespace(
            message=message,
            effective_chat=SimpleNamespace(id=-100123, type="supergroup"),
            effective_user=SimpleNamespace(id=7, is_bot=False),
        )
        asyncio.run(bot.message(update, SimpleNamespace()))

    def test_pvp_group_can_be_the_auction_group(self):
        self.store.target("group_id", -100123)
        self.assertEqual(self.store.get("group_id"), "-100123")
        self.assertEqual(self.store.get("pvp_group_id"), "-100123")

    def test_solo_pvp_heads_tails_settles_immediately(self):
        self.credit(7, cents("500"))
        game = self.store.play_solo_pvp("solo-1", -100123, 7, "Player 7", cents("250"), "tails", "tails", now=100)
        self.assertEqual(game["mode"], "solo")
        self.assertEqual(game["winner_id"], 7)
        self.assertEqual(self.store.wallet_balance(7)["total"], cents("750"))

        text = pvp_animation_text(game)
        self.assertIn("🪙", text)
        self.assertIn("Tails", text)
        self.assertIn("Your Choice", text)
        self.assertIn("Prize: 500coin", text)

    def test_separate_pvp_group_can_place_an_auction_bid(self):
        self.store.target("channel_id", -100555)
        self.store.target("group_id", -100999)
        self.store.set("wallet_mode", 1)
        auction_id = self.store.create({
            "photo": "photo", "name": "Card", "anime": "Anime", "rarity": "SSR",
            "start": cents("100"), "ends": 200,
        }, now=100)
        self.store.published(auction_id, 10, published_at=100)
        self.credit(7, cents("100"))
        self.assertTrue(self.store.bid(auction_id, 7, "Player 7", cents("100"), -100123, 55, now=101))
        self.assertEqual(self.store.auction(auction_id)["highest"], cents("100"))

    def test_final_animation_shows_odds_and_winner(self):
        text = pvp_animation_text({
            "status": "finished", "final_percent": 60, "step": 5,
            "requester_id": 1, "requester_name": "Alice",
            "target_id": 2, "target_name": "Bob", "winner_id": 1,
            "amount": cents("500"),
        })
        self.assertIn("60%", text)
        self.assertIn("40%", text)
        self.assertIn("Winner:", text)
        self.assertIn("Prize: 1000coin", text)

    def test_final_animation_shows_percentage_based_prize_and_refund(self):
        game = {
            "status": "finished", "final_percent": 79, "step": 5,
            "requester_id": 1, "requester_name": "Marcus",
            "target_id": 2, "target_name": "Osamu", "winner_id": 1,
            "amount": cents("500"),
        }
        self.assertEqual(pvp_payouts(game), (79000, 21000))
        text = pvp_animation_text(game)
        self.assertIn("Prize: 790coin", text)
        self.assertIn("Refund: 210coin", text)

    def test_owner_can_credit_or_debit_by_reply_and_by_id(self):
        reply = SimpleNamespace(
            sender_chat=None,
            from_user=SimpleNamespace(id=77, is_bot=False),
        )
        message = SimpleNamespace(reply_to_message=reply)
        owner_bot = object.__new__(AuctionBot)
        self.assertEqual(auth_adjustment(["+", "$100", "gift"], message),
                         (77, cents("2500"), "gift"))
        self.assertEqual(auth_adjustment(["-$5"], message),
                         (77, -cents("125"), ""))
        self.assertEqual(owner_bot.owner_wallet_adjustment("credit", ["77", "$100", "gift"], message),
                         (77, cents("2500"), "gift"))
        self.assertEqual(owner_bot.owner_wallet_adjustment("debit", ["-$5"], message),
                         (77, -cents("125"), ""))

    def test_raw_reply_plus_100_converts_at_rate_100_usd_to_2500_coins(self):
        self.assertEqual(usd_to_coins("$100"), cents("2500"))
        self.assertEqual(usd_to_coins("100.50"), cents("2512.50"))
        self.assertEqual(signed_owner_message_args("+100"), ["+100"])
        self.assertEqual(signed_owner_message_args("-$5 correction"), ["-$5", "correction"])
        self.assertEqual(usd_equivalent(cents("2500")), "$100")
        self.assertEqual(usd_equivalent(cents("2512.50")), "$100.50")

    def test_owner_can_credit_and_debit_by_reply_in_the_pvp_group(self):
        class FakeBot:
            def __init__(self):
                self.sent = []

            async def send_message(self, **kwargs):
                self.sent.append(kwargs)

        class FakeMessage:
            sender_chat = None

            def __init__(self, text, message_id):
                self.text = text
                self.chat = SimpleNamespace(type="supergroup")
                self.chat_id = -100123
                self.message_id = message_id
                self.reply_to_message = SimpleNamespace(
                    sender_chat=None,
                    from_user=SimpleNamespace(id=77, is_bot=False, full_name="Player"),
                )
                self.sent = []

            async def reply_text(self, text):
                self.sent.append(text)

        bot = object.__new__(AuctionBot)
        bot.store = self.store
        bot.config = SimpleNamespace(owners={99})
        bot.group_id = "-100999"
        bot.pvp_group_id = "-100123"
        user = SimpleNamespace(id=99, is_bot=False)
        chat = SimpleNamespace(id=-100123, type="supergroup")
        fake_bot = FakeBot()
        context = SimpleNamespace(bot=fake_bot, user_data={})

        for message_id, text in enumerate(("+100", "-$20", "/auth +$20"), start=1):
            message = FakeMessage(text, message_id)
            update = SimpleNamespace(message=message, effective_chat=chat, effective_user=user)
            asyncio.run(bot.message(update, context))

        self.assertEqual(self.store.wallet_balance(77)["available"], cents("2500"))
        self.assertEqual(len(fake_bot.sent), 2)
        self.assertIn("USD $100", fake_bot.sent[0]["text"])
        self.assertIn("2500coin", fake_bot.sent[0]["text"])

    def test_pvp_coin_gift_is_atomic_idempotent_and_group_limited(self):
        self.credit(1, cents("1000"))
        self.assertTrue(self.store.transfer_coins(-100123, 1, 2, cents("250"), "gift:group:message"))
        self.assertEqual(self.store.wallet_balance(1)["available"], cents("750"))
        self.assertEqual(self.store.wallet_balance(2)["available"], cents("250"))
        self.assertFalse(self.store.transfer_coins(-100123, 1, 2, cents("250"), "gift:group:message"))
        with self.assertRaisesRegex(RuleError, "Coin မလုံလောက်"):
            self.store.transfer_coins(-100123, 1, 3, cents("800"), "gift:insufficient")
        with self.assertRaisesRegex(RuleError, "PvP game group"):
            self.store.transfer_coins(-100999, 1, 3, cents("100"), "gift:wrong-group")
        self.assertEqual(self.store.wallet_balance(1)["available"], cents("750"))
        self.assertEqual(self.store.wallet_balance(3)["total"], 0)

    def test_bcoin_reply_sends_coin_gift_to_replied_user(self):
        class FakeMessage:
            chat_id = -100123
            message_id = 55
            reply_to_message = SimpleNamespace(
                sender_chat=None,
                from_user=SimpleNamespace(id=8, is_bot=False, full_name="Receiver"),
            )

            def __init__(self):
                self.sent = []

            async def reply_text(self, text):
                self.sent.append(text)

        self.credit(7, cents("200"))
        bot = object.__new__(AuctionBot)
        bot.store = self.store
        message = FakeMessage()
        asyncio.run(bot.pvp_gift_command(["100"], message,
                                         SimpleNamespace(id=7, is_bot=False)))
        self.assertEqual(self.store.wallet_balance(7)["available"], cents("100"))
        self.assertEqual(self.store.wallet_balance(8)["available"], cents("100"))
        self.assertIn("100coin", message.sent[0])
        self.assertIn("Receiver", message.sent[0])

    def test_button_cooldown_is_six_seconds(self):
        owner_bot = object.__new__(AuctionBot)
        owner_bot.config = SimpleNamespace(owners=set())
        owner_bot.button_cooldown_until = {}
        user = SimpleNamespace(id=77, is_bot=False)
        self.assertEqual(owner_bot.button_cooldown(user), 0.0)
        self.assertAlmostEqual(owner_bot.button_cooldown(user), 6.0, delta=0.01)
        owner_bot.button_cooldown_until = {}
        owner_bot.config.owners = {99}
        owner = SimpleNamespace(id=99, is_bot=False)
        self.assertEqual(owner_bot.button_cooldown(owner), 0.0)
        self.assertAlmostEqual(owner_bot.button_cooldown(owner), 6.0, delta=0.01)

    def test_credit_notification_is_sent_to_users_bot_dm(self):
        class FakeBot:
            def __init__(self):
                self.sent = []

            async def send_message(self, **kwargs):
                self.sent.append(kwargs)

        owner_bot = object.__new__(AuctionBot)
        fake = FakeBot()
        delivered = asyncio.run(owner_bot.wallet_credit_notification(
            fake, 77, cents("500"), cents("600")))
        self.assertTrue(delivered)
        self.assertEqual(fake.sent[0]["chat_id"], 77)
        self.assertIn("USD $20", fake.sent[0]["text"])
        self.assertIn("500coin", fake.sent[0]["text"])
        self.assertIn("600coin", fake.sent[0]["text"])

    def test_pvp_wager_below_250_coins_is_rejected(self):
        self.credit(1)
        self.credit(2)
        with self.assertRaisesRegex(RuleError, "30000"):
            self.store.create_pvp("too-small", -100123, 1, "Player 1", 2, "Player 2", cents("249"))


if __name__ == "__main__":
    unittest.main()
