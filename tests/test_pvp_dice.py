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
from auction_bot.domain import PVP_DICE_PAYOUT_RULE, RuleError, cents, pvp_dice_outcome
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

    def test_tiered_result_names_random_sides_and_shows_actual_prize(self):
        for value, prize in ((1, "1500"), (2, "1700"), (3, "2000"),
                             (4, "1500"), (5, "1700"), (6, "2000")):
            with self.subTest(value=value):
                game = dict(dice_game(value), dice_payout_rule=PVP_DICE_PAYOUT_RULE,
                            low_player_id=2, winner_id=2 if value <= 3 else 1)
                text = pvp_animation_text(game)
                low_line, high_line = text.split("\n\n")[1:3]
                self.assertTrue(low_line.startswith("🟥"))
                self.assertIn("Osamu", low_line)
                self.assertIn("1 , 2 , 3", low_line)
                self.assertTrue(high_line.startswith("🟩"))
                self.assertIn("Marcus &lt;3", high_line)
                self.assertIn("4 , 5 , 6", high_line)
                self.assertIn(f"🪙 Prize: {prize}coin", text)
                self.assertEqual(pvp_payouts(game), (cents(prize), 0))

    def test_fractional_prizes_round_down_in_coin_subunits(self):
        for value, expected in ((1, 37501), (2, 42501), (3, 50002),
                                (4, 37501), (5, 42501), (6, 50002)):
            game = dict(dice_game(value), amount=cents("250.01"),
                        dice_payout_rule=PVP_DICE_PAYOUT_RULE)
            self.assertEqual(pvp_dice_outcome(game)[1], expected)

    def test_confirm_reserves_stakes_without_picking_a_local_result(self):
        store = Mock()
        game = dict(dice_game(), status="running", winner_id=None, dice_value=None)
        store.accept_pvp.return_value = game
        bot = bot_for(store)
        query = SimpleNamespace(data="pvp:confirm:dice", answer=AsyncMock(),
                                delete_message=AsyncMock(), edit_message_text=AsyncMock())
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=2, is_bot=False),
                                 effective_chat=SimpleNamespace(id=-100123, type="supergroup"))
        context = SimpleNamespace(bot=Mock())
        asyncio.run(bot.callback(update, context))
        store.accept_pvp.assert_called_once_with("dice", 2)
        context.bot.send_dice.assert_not_called()
        query.delete_message.assert_awaited_once()
        query.edit_message_text.assert_not_awaited()


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

    def request(self, game_id="dice", requester=1, target=2, amount="1000", now=100, low_player_id=None):
        for uid in (requester, target):
            self.store.adjust_wallet(uid, cents("5000"), 99, f"credit:{uid}")
        low = requester if low_player_id is None else low_player_id
        with patch("auction_bot.mongo_store.secrets.choice", return_value=low) as choose:
            game = self.store.create_pvp(game_id, -100123, requester, f"Player {requester}",
                                         target, f"Player {target}", cents(amount), now=now)
        choose.assert_called_once_with((requester, target))
        self.store.set_pvp_message(game_id, 55)
        return game

    def accept(self, game_id="dice", requester=1, target=2, low_player_id=None):
        self.request(game_id, requester, target, low_player_id=low_player_id)
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

    def test_each_face_pays_tiered_prize_once_with_no_loser_refund_on_either_side(self):
        for swapped in (False, True):
            for value, prize in ((1, 1500), (2, 1700), (3, 2000),
                                 (4, 1500), (5, 1700), (6, 2000)):
                with self.subTest(value=value, swapped=swapped):
                    requester, target = value * 2 + 100 * swapped, value * 2 + 1 + 100 * swapped
                    low = target if swapped else requester
                    high = requester if swapped else target
                    game_id = f"face-{value}-{swapped}"
                    game = self.accept(game_id, requester, target, low_player_id=low)
                    self.assertEqual(game["mode"], "dice")
                    self.assertEqual(game["low_player_id"], low)
                    self.store.claim_pvp_dice(game_id, now=100)
                    self.store.record_pvp_dice(game_id, value, 700 + value, now=101)
                    self.assertEqual(self.store.advance_pvp(game_id, now=104)["status"], "running")
                    game = self.store.advance_pvp(game_id, now=105)
                    winner, loser = (low, high) if value <= 3 else (high, low)
                    self.assertEqual(game["winner_id"], winner)
                    self.assertEqual(game["prize"], cents(str(prize)))
                    self.assertEqual(game["retained"], cents("2000") - cents(str(prize)))
                    self.assertEqual(game["refund"], 0)
                    self.assertEqual(self.store.wallet_balance(winner)["total"], cents(str(4000 + prize)))
                    self.assertEqual(self.store.wallet_balance(loser)["total"], cents("4000"))
                    self.assertEqual(self.store.advance_pvp(game_id, now=106), game)
                    event = self.store.db.wallet_events.find_one({"event_key": f"pvp:{game_id}:prize"})
                    self.assertEqual(event["user_id"], winner)
                    self.assertEqual(event["delta"], cents(str(prize)))
                    self.assertEqual(self.store.db.wallet_events.count_documents({"event_key": f"pvp:{game_id}:prize"}), 1)
                    self.assertEqual(self.store.db.wallet_events.count_documents({"kind": "pvp_refund"}), 0)

    def test_random_sides_are_shown_before_confirm_and_preserved_through_restart(self):
        for uid in (1, 2):
            self.store.adjust_wallet(uid, cents("5000"), 99, f"credit:{uid}")
        self.bot.game_cooldown_until = {}
        message = SimpleNamespace(
            chat_id=-100123,
            reply_to_message=SimpleNamespace(sender_chat=None,
                from_user=SimpleNamespace(id=2, full_name="Osamu", is_bot=False)),
            reply_text=AsyncMock(return_value=SimpleNamespace(message_id=55)))
        user = SimpleNamespace(id=1, full_name="Marcus", is_bot=False)
        with patch("auction_bot.mongo_store.secrets.choice", return_value=2) as choose:
            asyncio.run(self.bot.pvp_request(["1000"], message, user))
        choose.assert_called_once_with((1, 2))
        text = message.reply_text.call_args.args[0]
        self.assertIn('🟥 <a href="tg://user?id=2">Osamu</a> — 1 , 2 , 3', text)
        self.assertIn('🟩 <a href="tg://user?id=1">Marcus</a> — 4 , 5 , 6', text)
        self.assertIn("2/5 → 1.7x", text)
        game = self.store.db.pvp_games.find_one({})
        self.store.close()
        self.store = MongoStore(os.environ["TEST_MONGODB_URI"], self.database)
        with self.assertRaises(RuleError):
            self.store.accept_pvp(game["id"], 1)
        accepted = self.store.accept_pvp(game["id"], 2)
        self.assertEqual(accepted["low_player_id"], 2)
        self.assertEqual(accepted["dice_payout_rule"], PVP_DICE_PAYOUT_RULE)

    def test_older_dice_request_keeps_its_original_sides_and_full_prize(self):
        self.request()
        self.store.db.pvp_games.update_one({"_id": "dice"},
            {"$unset": {"low_player_id": "", "dice_payout_rule": ""}})
        self.store.accept_pvp("dice", 2, now=100)
        self.store.claim_pvp_dice("dice", now=100)
        self.store.record_pvp_dice("dice", 5, 777, now=101)
        game = self.store.advance_pvp("dice", now=105)
        self.assertEqual(game["winner_id"], 2)
        self.assertEqual(game["prize"], cents("2000"))
        self.assertEqual(game["retained"], 0)
        self.assertEqual(self.store.wallet_balance(2)["total"], cents("6000"))
        self.assertIn("🟦", pvp_animation_text(game))
        self.assertIn("🪙 Prize: 2000coin", pvp_animation_text(game))

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
        self.assertIn("🪙 Prize: 1700coin", kwargs["text"])
        self.assertEqual(self.store.wallet_balance(2)["total"], cents("5700"))
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

    def test_next_request_settles_ready_dice_even_when_worker_is_delayed(self):
        for kind, first, second in (("pvp", 1, 2), ("boom", 3, 4)):
            with self.subTest(kind=kind):
                game_id = f"previous-{kind}"
                self.accept(game_id, first, second)
                self.store.claim_pvp_dice(game_id, now=100)
                self.store.record_pvp_dice(game_id, 5, 777, now=101)
                create = getattr(self.store, f"create_{kind}")
                # Both players stay locked while the native dice is still rolling.
                with self.assertRaises(RuleError):
                    create(f"early-{kind}", -100123, second, "Second", first,
                           "First", cents("1000"), now=104)
                game = create(f"next-{kind}", -100123, second, "Second", first,
                              "First", cents("1000"), now=105)
                self.assertEqual(game["status"], "pending")
                previous = self.store._pvp_game(game_id)
                self.assertEqual(previous["status"], "finished")
                self.assertFalse(previous["result_notified"])
                self.assertEqual(self.store.wallet_balance(second)["total"], cents("5700"))
                self.store.advance_pvp(game_id, now=106)
                self.assertEqual(self.store.db.wallet_events.count_documents(
                    {"event_key": f"pvp:{game_id}:prize"}), 1)
                # The new, unanswered challenge must still prevent overlapping play.
                with self.assertRaises(RuleError):
                    create(f"overlap-{kind}", -100123, first, "First", second,
                           "Second", cents("1000"), now=106)

    def test_both_players_can_request_again_as_soon_as_result_is_sent(self):
        self.roll()
        context = self.context()

        async def result_sent(**kwargs):
            self.assertIn("🎲Result : 5", kwargs["text"])
            game = await self.bot.store_call(self.store.create_pvp, "rematch", -100123,
                2, "Second", 1, "First", cents("1000"), now=106)
            self.assertEqual(game["status"], "pending")

        context.bot.send_message.side_effect = result_sent
        asyncio.run(self.bot.announce_pvp_dice_results(context))
        context.bot.send_message.assert_awaited_once()
        self.assertEqual(self.store.pending_pvp_dice_results(), [])
        self.assertEqual(self.store.db.wallet_events.count_documents(
            {"event_key": "pvp:dice:prize"}), 1)

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
        self.assertEqual(self.store.wallet_balance(2)["total"], cents("5700"))

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
