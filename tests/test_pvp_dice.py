"""Dice rendering and Telegram boundaries; ledger checks use a disposable replica set."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from pymongo.errors import PyMongoError
from telegram.error import TimedOut

from auction_bot.bot import AuctionBot, pvp_animation_text, pvp_payouts
from auction_bot.domain import RuleError, cents
from auction_bot.mongo_store import MongoStore, PVP_DICE_TIMEOUT_SECONDS


def bot_for(store):
    bot = object.__new__(AuctionBot)
    bot.store = store
    bot.pvp_group_id = "-100123"
    bot.button_cooldown_until = {}
    bot.pvp_render_retry = {}
    bot.pvp_dice_retry_after = {}
    bot.pvp_slot_retry_after = {}
    bot.pvp_edit_after = 0
    return bot


def dice_game(value=5):
    return dict(id="dice", mode="dice", status="finished", amount=cents("1000"),
                requester_id=1, requester_name="Marcus <3", target_id=2,
                target_name="Osamu", winner_id=1 if value <= 3 else 2, dice_value=value)


class DicePresentationTests(unittest.TestCase):
    def test_result_matches_requested_format_for_each_face(self):
        for value in range(1, 7):
            with self.subTest(value=value):
                game = dice_game(value)
                text = pvp_animation_text(game)
                self.assertIn("PvP · 1000coin each", text)
                self.assertIn("Marcus &lt;3", text)
                self.assertIn("— 1 , 2 , 3", text)
                self.assertIn("— 4 , 5 , 6", text)
                self.assertIn(f"🎲Result : {value}", text)
                self.assertIn("🪙 Prize: 2000coin", text)
                winner_line = text.split("🏆 Winner: ")[1].split("\n")[0]
                self.assertIn(f'id={game["winner_id"]}', winner_line)
                self.assertNotIn("%", text)
                self.assertNotIn("Refund", text)
                self.assertEqual(pvp_payouts(game), (cents("2000"), 0))

    def test_confirm_reserves_stakes_without_picking_a_local_result(self):
        store = Mock()
        game = dict(dice_game(), status="running", winner_id=None, dice_value=None)
        store.accept_pvp.return_value = game
        bot = bot_for(store)
        query = SimpleNamespace(data="pvp:confirm:dice", answer=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=2, is_bot=False),
                                 effective_chat=SimpleNamespace(id=-100123, type="supergroup"))
        context = SimpleNamespace(bot=Mock())
        asyncio.run(bot.callback(update, context))
        store.accept_pvp.assert_called_once_with("dice", 2)
        context.bot.send_dice.assert_not_called()
        self.assertIn("1 , 2 , 3", query.edit_message_text.call_args.args[0])


@unittest.skipUnless(os.environ.get("TEST_MONGODB_URI"), "TEST_MONGODB_URI requires a disposable replica set")
class DiceLedgerTests(unittest.TestCase):
    def setUp(self):
        self.database = "test_pvp_dice_" + uuid4().hex
        self.store = MongoStore(os.environ["TEST_MONGODB_URI"], self.database)
        self.store.set_pvp_group(-100123)
        self.bot = bot_for(self.store)

    def tearDown(self):
        self.store.client.drop_database(self.database)
        self.store.close()

    def request(self, game_id="dice", requester=1, target=2, amount="1000", now=100):
        for uid in (requester, target):
            self.store.adjust_wallet(uid, cents("5000"), 99, f"credit:{uid}")
        game = self.store.create_pvp(game_id, -100123, requester, f"Player {requester}",
                                     target, f"Player {target}", cents(amount), now=now)
        self.store.set_pvp_message(game_id, 55)
        return game

    def accept(self, game_id="dice", requester=1, target=2):
        self.request(game_id, requester, target)
        return self.store.accept_pvp(game_id, target, now=100)

    def roll(self, value=5):
        self.accept()
        self.store.claim_pvp_dice("dice", now=100)
        self.store.record_pvp_dice("dice", value, 777, now=101)
        return self.store.advance_pvp("dice", now=105)

    def context(self, value=5):
        return SimpleNamespace(bot=SimpleNamespace(
            send_dice=AsyncMock(return_value=SimpleNamespace(message_id=777, dice=SimpleNamespace(value=value))),
            send_message=AsyncMock(), edit_message_text=AsyncMock()))

    def test_each_face_pays_full_pot_exactly_once(self):
        for value in range(1, 7):
            with self.subTest(value=value):
                requester, target = value * 2, value * 2 + 1
                game_id = f"face-{value}"
                game = self.accept(game_id, requester, target)
                self.assertEqual(game["mode"], "dice")
                self.store.claim_pvp_dice(game_id, now=100)
                self.store.record_pvp_dice(game_id, value, 700 + value, now=101)
                self.assertEqual(self.store.advance_pvp(game_id, now=104)["status"], "running")
                game = self.store.advance_pvp(game_id, now=105)
                winner = requester if value <= 3 else target
                loser = target if winner == requester else requester
                self.assertEqual(game["winner_id"], winner)
                self.assertEqual(self.store.wallet_balance(winner)["total"], cents("6000"))
                self.assertEqual(self.store.wallet_balance(loser)["total"], cents("4000"))
                self.assertEqual(self.store.advance_pvp(game_id, now=106), game)
                self.assertEqual(self.store.db.wallet_events.count_documents({"event_key": f"pvp:{game_id}:prize"}), 1)
                self.assertEqual(self.store.db.wallet_events.count_documents({"kind": "pvp_refund"}), 0)

    def test_confirm_checks_actor_funds_and_duplicate_acceptance(self):
        self.request()
        with self.assertRaises(RuleError):
            self.store.accept_pvp("dice", 1, now=100)
        self.store.adjust_wallet(2, -cents("4500"), 99, "reduce:2")
        with self.assertRaises(RuleError):
            self.store.accept_pvp("dice", 2, now=100)
        self.assertEqual(self.store.wallet_balance(1)["total"], cents("5000"))
        self.assertEqual(self.store.db.wallet_events.count_documents({"kind": "pvp_stake"}), 0)
        self.store.adjust_wallet(2, cents("500"), 99, "topup:2")
        self.store.accept_pvp("dice", 2, now=100)
        with self.assertRaises(RuleError):
            self.store.accept_pvp("dice", 2, now=100)
        self.assertEqual(self.store.db.wallet_events.count_documents({"kind": "pvp_stake"}), 2)

    def test_concurrent_workers_claim_one_roll_and_reject_changed_results(self):
        self.accept()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.store.claim_pvp_dice("dice", now=100), range(2)))
        self.assertEqual(sum(row is not None for row in results), 1)
        for value in (0, 7, True):
            with self.assertRaises(RuleError):
                self.store.record_pvp_dice("dice", value, 777, now=101)
        recorded = self.store.record_pvp_dice("dice", 5, 777, now=101)
        self.assertEqual(self.store.record_pvp_dice("dice", 5, 777, now=103), recorded)
        with self.assertRaises(RuleError):
            self.store.record_pvp_dice("dice", 2, 778, now=103)

    def test_missing_roll_refunds_both_players_once_after_timeout(self):
        self.accept()
        self.store.claim_pvp_dice("dice", now=100)
        self.assertEqual(self.store.advance_pvp("dice", now=189)["status"], "running")
        game = self.store.advance_pvp("dice", now=100 + PVP_DICE_TIMEOUT_SECONDS)
        self.assertEqual(game["status"], "cancelled")
        self.store.advance_pvp("dice", now=200)
        self.assertEqual(self.store.wallet_balance(1)["total"], cents("5000"))
        self.assertEqual(self.store.wallet_balance(2)["total"], cents("5000"))
        self.assertEqual(self.store.db.wallet_events.count_documents({"kind": "pvp_refund"}), 2)
        with self.assertRaises(RuleError):
            self.store.record_pvp_dice("dice", 6, 777, now=201)
        context = self.context()
        asyncio.run(self.bot.announce_pvp_dice_results(context))
        self.assertEqual(context.bot.send_message.call_args.kwargs["reply_parameters"].message_id, 55)
        self.assertNotIn("Winner:", context.bot.send_message.call_args.kwargs["text"])

    def test_tick_uses_telegram_value_then_replies_to_the_dice_after_restart(self):
        self.accept()
        context = self.context(value=5)
        asyncio.run(self.bot.tick_pvp(context))
        context.bot.send_dice.assert_awaited_once()
        stored = self.store._pvp_game("dice")
        self.assertEqual((stored["dice_value"], stored["dice_message_id"]), (5, 777))
        context.bot.send_message.assert_not_awaited()
        self.store.close()
        self.store = MongoStore(os.environ["TEST_MONGODB_URI"], self.database)
        self.bot = bot_for(self.store)
        self.store.db.pvp_games.update_one({"_id": "dice"}, {"$set": {"next_at": 0}})
        asyncio.run(self.bot.tick_pvp(context))
        context.bot.send_dice.assert_awaited_once()
        context.bot.send_message.assert_awaited_once()
        kwargs = context.bot.send_message.call_args.kwargs
        self.assertEqual(kwargs["reply_parameters"].message_id, 777)
        self.assertIn("🎲Result : 5", kwargs["text"])
        self.assertIn("🪙 Prize: 2000coin", kwargs["text"])
        self.assertEqual(self.store.wallet_balance(2)["total"], cents("6000"))
        asyncio.run(self.bot.tick_pvp(context))
        context.bot.send_message.assert_awaited_once()

    def test_send_failure_does_not_reroll_and_recovers_stakes(self):
        game = self.accept()
        context = self.context()
        context.bot.send_dice.side_effect = TimedOut()
        asyncio.run(self.bot.tick_pvp_dice(game, context))
        restarted_bot = bot_for(self.store)
        asyncio.run(restarted_bot.tick_pvp_dice(game, context))
        context.bot.send_dice.assert_awaited_once()
        deadline = self.store._pvp_game("dice")["next_at"]
        self.store.advance_pvp("dice", now=deadline)
        self.assertEqual(self.store.wallet_balance(1)["total"], cents("5000"))
        self.assertEqual(self.store.wallet_balance(2)["total"], cents("5000"))

    def test_database_failure_after_send_does_not_generate_another_roll(self):
        game = self.accept()
        context = self.context()
        with patch.object(self.store, "record_pvp_dice", side_effect=PyMongoError("synthetic outage")):
            asyncio.run(self.bot.tick_pvp_dice(game, context))
        asyncio.run(bot_for(self.store).tick_pvp_dice(game, context))
        context.bot.send_dice.assert_awaited_once()
        deadline = self.store._pvp_game("dice")["next_at"]
        self.assertEqual(self.store.advance_pvp("dice", now=deadline)["status"], "cancelled")

    def test_result_delivery_retries_without_repaying(self):
        self.roll()
        context = self.context()
        context.bot.send_message.side_effect = TimedOut()
        asyncio.run(self.bot.announce_pvp_dice_results(context))
        self.assertEqual(len(self.store.pending_pvp_dice_results()), 1)
        context.bot.send_message.side_effect = None
        asyncio.run(bot_for(self.store).announce_pvp_dice_results(context))
        self.assertEqual(self.store.pending_pvp_dice_results(), [])
        self.assertEqual(self.store.db.wallet_events.count_documents({"event_key": "pvp:dice:prize"}), 1)
        self.assertEqual(self.store.wallet_balance(2)["total"], cents("6000"))

    def test_existing_percentage_rounds_keep_their_original_payout(self):
        self.request()
        self.store.accept_pvp("dice", 2, 80, now=100)
        self.store.advance_pvp("dice", now=102)
        self.store.advance_pvp("dice", now=104)
        self.assertEqual(self.store.wallet_balance(1)["total"], cents("5600"))
        self.assertEqual(self.store.wallet_balance(2)["total"], cents("4400"))
        self.assertEqual(self.store.pending_pvp_dice_results(), [])


if __name__ == "__main__":
    unittest.main()
