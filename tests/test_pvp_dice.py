"""Dice rendering and Telegram boundaries; ledger checks use a disposable replica set."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from pymongo.errors import OperationFailure, PyMongoError
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

    def test_database_error_logs_safe_diagnostics_without_repeating_a_write(self):
        bot = bot_for(Mock(spec=MongoStore))
        error = OperationFailure("synthetic-private-payload", code=11000)
        operation = Mock(__name__="adjust_wallet", side_effect=error)
        with self.assertLogs("auction_bot.bot", level="WARNING") as logs:
            with self.assertRaises(OperationFailure) as raised:
                asyncio.run(bot.store_call(operation, 1, 100))
        self.assertIs(raised.exception, error)
        operation.assert_called_once_with(1, 100)
        self.assertIn("adjust_wallet failed (OperationFailure, code=11000)", logs.output[0])
        self.assertNotIn("synthetic-private-payload", logs.output[0])

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
                early = create(f"early-{kind}", -100123, second, "Second", first,
                               "First", cents("1000"), now=104)
                self.assertEqual(early["status"], "pending")
                self.assertEqual(self.store._pvp_game(game_id)["status"], "running")
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
                overlap = create(f"overlap-{kind}", -100123, first, "First", second,
                                 "Second", cents("1000"), now=106)
                self.assertEqual(overlap["status"], "pending")

    def test_shared_players_can_confirm_pvp_and_boom_up_to_group_limit(self):
        self.request("first")
        self.store.create_boom("second", -100123, 2, "Second", 1, "First", cents("1000"), now=100)
        self.store.create_boom("fourth", -100123, 1, "First", 2, "Second", cents("1000"), now=100)
        self.store.accept_pvp("first", 2, now=100)
        self.assertEqual(self.store.accept_boom("second", 1, now=100)["status"], "running")
        self.store.create_pvp("third", -100123, 2, "Second", 1, "First", cents("1000"), now=100)
        self.assertEqual(self.store.accept_pvp("third", 1, now=100)["status"], "running")
        with self.assertRaisesRegex(RuleError, "game ၃ ပွဲ"):
            self.store.accept_boom("fourth", 2, now=100)
        for uid in (1, 2):
            self.assertEqual(self.store.wallet_balance(uid)["total"], cents("2000"))
        self.assertEqual(self.store.db.wallet_events.count_documents(
            {"kind": {"$in": ["pvp_stake", "boom_stake"]}}), 6)
        with self.assertRaises(RuleError):
            self.store.accept_boom("second", 1, now=100)
        self.assertEqual(self.store.wallet_balance(1)["total"], cents("2000"))

    def test_concurrent_shared_player_confirms_cannot_overspend(self):
        self.request("pvp", amount="3000")
        self.store.create_boom("boom", -100123, 1, "First", 2, "Second", cents("3000"), now=100)

        def confirm(kind):
            try:
                return getattr(self.store, f"accept_{kind}")(kind, 2, now=100)["status"]
            except RuleError:
                return "insufficient"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(confirm, ("pvp", "boom")))
        self.assertCountEqual(results, ["running", "insufficient"])
        for uid in (1, 2):
            self.assertEqual(self.store.wallet_balance(uid)["total"], cents("2000"))
        self.assertEqual(self.store.db.wallet_events.count_documents(
            {"kind": {"$in": ["pvp_stake", "boom_stake"]}}), 2)

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

    def test_repeated_streak_rewards_settle_with_legacy_reward_history(self):
        for category in ("pvp", "boom"):
            with self.subTest(category=category):
                for uid in (1, 2):
                    self.store.adjust_wallet(uid, cents("10000"), 99, f"fund:{category}:{uid}")
                for index in range(13):
                    game_id = f"streak-{category}-{index}"
                    at = 100 + index * 10
                    won = index != 6  # Six wins, one loss, another six wins.
                    create = getattr(self.store, f"create_{category}")
                    with patch("auction_bot.mongo_store.secrets.choice", return_value=1):
                        create(game_id, -100123, 1, "First", 2, "Second", cents("250"), now=at)
                    game = getattr(self.store, f"accept_{category}")(game_id, 2, now=at)
                    if category == "pvp":
                        self.store.claim_pvp_dice(game_id, now=at)
                        self.store.record_pvp_dice(game_id, 3 if won else 6, 777 + index, now=at)
                        game = self.store.advance_pvp(game_id, now=at + 4)
                        self.assertEqual(self.store.advance_pvp(game_id, now=at + 5), game)
                    else:
                        number = next(int(n) for n, uid in game["boom_owners"].items()
                                      if uid == (1 if won else 2))
                        game = self.store.pick_boom(game_id, 1, number, now=at)
                        with self.assertRaises(RuleError):
                            self.store.pick_boom(game_id, 1, number, now=at + 1)
                    self.assertEqual(game["status"], "finished")
                    self.assertEqual(game["winner_id"], 1 if won else 2)
                    if index in (2, 5):
                        # Existing deployments already contain these old unique keys.
                        event = self.store.db.wallet_events.find_one({
                            "kind": "streak_reward", "note": f"{category} {index + 1}-win streak reward"})
                        self.store.db.wallet_events.update_one({"_id": event["_id"]},
                            {"$set": {"event_key": f"streak:{category}:-100123:1:{index + 1}"}})
                rewards = list(self.store.db.wallet_events.find({
                    "kind": "streak_reward", "note": {"$regex": f"^{category} "}}))
                self.assertEqual(len(rewards), 4)
                self.assertEqual(sum(r["delta"] for r in rewards), cents("4000"))

    def test_balance_read_does_not_write_to_shared_ledger(self):
        self.store.adjust_wallet(1, cents("1000"), 99, "balance-fund")
        self.store.db.holds.insert_one({"_id": 123, "user_id": 1, "amount": cents("250")})
        before = self.store.db.coord.find_one({"_id": "ledger"})["version"]
        self.assertEqual(self.store.wallet_balance(1),
                         {"total": cents("1000"), "held": cents("250"), "available": cents("750")})
        self.assertEqual(self.store.db.coord.find_one({"_id": "ledger"})["version"], before)

    def test_target_changes_survive_restart_and_sync_game_settings(self):
        self.store.set("channel_id", "-100111")
        self.store.set("group_id", "-100222")
        self.store.configure_targets("-100333", "-100444")
        self.assertEqual(self.store.get("channel_id"), "-100333")
        self.assertEqual(self.store.get("pvp_group_id"), "-100444")
        self.store.target("channel_id", "-100555")
        self.store.target("group_id", "-100666")
        self.assertEqual(self.store.get("pvp_group_id"), "-100666")
        self.store.close()
        self.store = MongoStore(os.environ["TEST_MONGODB_URI"], self.database)
        config = SimpleNamespace(mongodb_uri=os.environ["TEST_MONGODB_URI"],
            mongodb_database=self.database, channel_id="-100333", group_id="-100444")
        with patch("auction_bot.bot.MongoStore", return_value=self.store):
            bot = AuctionBot(config)
        self.assertEqual((bot.channel_id, bot.group_id, bot.pvp_group_id),
                         ("-100555", "-100666", "-100666"))
        # A later deployment with different IDs takes effect as well.
        changed = self.store.configure_targets("-100777", "-100888")
        self.assertEqual(changed, {"channel_id": "-100777", "group_id": "-100888"})
        self.assertEqual(self.store.get("pvp_group_id"), "-100888")
        self.assertEqual(self.store.configure_targets("", ""), changed)

    def test_idle_game_checks_skip_writes_but_expire_and_timeout_due_games(self):
        before = self.store.db.coord.find_one({"_id": "ledger"})["version"]
        self.assertEqual(self.store.expire_pvp(now=100), [])
        self.assertEqual(self.store.expire_boom(now=100), [])
        self.assertEqual(self.store.timeout_boom(now=100), [])
        self.assertEqual(self.store.db.coord.find_one({"_id": "ledger"})["version"], before)
        self.request("expires", now=100)
        self.store.create_boom("boom-expires", -100123, 1, "First", 2, "Second", cents("250"), now=100)
        self.assertEqual(self.store.expire_pvp(now=114), [])
        self.assertEqual(self.store.expire_boom(now=114), [])
        self.assertEqual(self.store.expire_pvp(now=115)[0]["status"], "cancelled")
        self.assertEqual(self.store.expire_boom(now=115)[0]["status"], "cancelled")
        self.store.create_boom("timeout", -100123, 1, "First", 2, "Second", cents("250"), now=120)
        self.store.accept_boom("timeout", 2, now=120)
        self.assertEqual(self.store.timeout_boom(now=120), [])
        games = self.store.timeout_boom(now=1000)
        self.assertEqual(games[0]["status"], "finished")
        self.assertEqual(self.store.timeout_boom(now=1001), [])
        self.assertEqual(self.store.db.wallet_events.count_documents(
            {"event_key": "boom:timeout:prize"}), 1)

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
