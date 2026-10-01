"""Routing and scheduling regressions with synthetic Telegram messages."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from apscheduler.events import EVENT_JOB_MAX_INSTANCES, EVENT_JOB_MISSED
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from auction_bot.bot import AuctionBot, signed_owner_message_args


def runtime_bot():
    bot = object.__new__(AuctionBot)
    bot.config = SimpleNamespace(owners={99}, token="123:synthetic")
    bot.group_id = bot.pvp_group_id = "-100123"
    bot.channel_id = "-100456"
    bot.store = Mock()
    bot.tick_lock = asyncio.Lock()
    bot.game_tick_lock = asyncio.Lock()
    bot.global_edit_after = 0
    bot.edit_after = {}
    bot.bid_edit_due = {}
    bot.last_caption_at = {}
    return bot


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_group_reply_works_with_private_draft_and_welcome_state(self):
        bot = runtime_bot()
        bot.auth_command = AsyncMock()
        message = SimpleNamespace(sender_chat=None, text="− ၁၀ correction",
                                  reply_to_message=object())
        context = SimpleNamespace(bot=Mock(), user_data={"draft": {"step": "photo"},
                                                       "welcome_edit": "text"})
        update = SimpleNamespace(message=message,
            effective_chat=SimpleNamespace(id=-100123, type="supergroup"),
            effective_user=SimpleNamespace(id=99, is_bot=False))
        await bot.message(update, context)
        bot.auth_command.assert_awaited_once_with(["-10", "correction"], message, 99, context.bot)
        bot.auth_command.reset_mock()
        update.effective_user.id = 77
        await bot.message(update, context)
        bot.auth_command.assert_not_awaited()

    async def test_setgroup_updates_both_routes_immediately(self):
        bot = runtime_bot()
        message = SimpleNamespace(reply_text=AsyncMock())
        context = SimpleNamespace(bot=SimpleNamespace(get_chat=AsyncMock(
            return_value=SimpleNamespace(type="supergroup"))))
        await bot.owner_command("setgroup", ["-100789"], message, context)
        bot.store.target.assert_called_once_with("group_id", -100789)
        new = SimpleNamespace(effective_chat=SimpleNamespace(id=-100789, type="supergroup"))
        old = SimpleNamespace(effective_chat=SimpleNamespace(id=-100123, type="supergroup"))
        self.assertTrue(bot.group(new))
        self.assertTrue(bot.pvp_group(new))
        self.assertFalse(bot.group(old))
        self.assertFalse(bot.pvp_group(old))

    async def test_owner_confirmation_precedes_slow_user_dm(self):
        bot = runtime_bot()
        bot.store.adjust_wallet.return_value = True
        bot.store.wallet_balance.return_value = {"available": 250000}
        message = SimpleNamespace(chat_id=-100123, message_id=9,
            chat=SimpleNamespace(type="supergroup"), reply_text=AsyncMock(),
            reply_to_message=SimpleNamespace(sender_chat=None,
                from_user=SimpleNamespace(id=77, is_bot=False)))
        async def notify(**kwargs):
            message.reply_text.assert_awaited_once()
        telegram = SimpleNamespace(send_message=AsyncMock(side_effect=notify))
        await bot.auth_command(["+100"], message, 99, telegram)
        telegram.send_message.assert_awaited_once()
        self.assertIn("2500coin", message.reply_text.call_args.args[0])

    async def test_games_run_while_channel_caption_request_is_blocked(self):
        bot = runtime_bot()
        bot.store.dirty.return_value = [dict(id=1, status="active", channel_id=-100456,
                                            post_id=1, version=1)]
        bot.tick_pvp = AsyncMock()
        bot.announce_winners = AsyncMock()
        editing, release = asyncio.Event(), asyncio.Event()
        async def slow_edit(**kwargs):
            editing.set()
            await release.wait()
        context = SimpleNamespace(bot=SimpleNamespace(edit_message_caption=AsyncMock(side_effect=slow_edit)))
        with patch("auction_bot.bot.caption", return_value="synthetic card"):
            auction = asyncio.create_task(bot.tick(context))
            try:
                await asyncio.wait_for(editing.wait(), 1)
                await asyncio.wait_for(bot.tick_games(context), 1)
                bot.tick_pvp.assert_awaited_once_with(context)
                self.assertFalse(auction.done())
            finally:
                release.set()
                await auction

    async def test_application_schedules_auction_and_game_workers_separately(self):
        bot = runtime_bot()
        app = bot.application()
        jobs = app.job_queue.jobs()
        self.assertCountEqual([job.data for job in jobs], ["auctions", "games"])
        self.assertTrue(all(job.callback == bot.run_worker for job in jobs))
        self.assertTrue(all(type(job.job.trigger).__name__ == "DateTrigger" for job in jobs))
        self.assertTrue(all(job.job.misfire_grace_time is None for job in jobs))

    async def test_slow_worker_only_schedules_next_pass_after_completion(self):
        bot = runtime_bot()
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow_tick(context):
            entered.set()
            await release.wait()
        bot.tick_games = AsyncMock(side_effect=slow_tick)
        context = SimpleNamespace(job=SimpleNamespace(data="games"), job_queue=Mock(),
                                  application=SimpleNamespace(running=True))
        task = asyncio.create_task(bot.run_worker(context))
        try:
            await asyncio.wait_for(entered.wait(), 1)
            context.job_queue.run_once.assert_not_called()
            bot.tick_games.assert_awaited_once()
        finally:
            release.set()
            await task
        context.job_queue.run_once.assert_called_once()
        args = context.job_queue.run_once.call_args
        self.assertEqual(args.args[0], bot.run_worker)
        self.assertEqual(args.kwargs["data"], "games")
        self.assertGreaterEqual(args.kwargs["when"], 0.05)

    async def test_actual_scheduler_handles_late_start_and_slow_passes_without_skips(self):
        bot = runtime_bot()
        scheduler = AsyncIOScheduler()
        warnings, active, maximum, passes = [], 0, 0, 0
        finished = asyncio.Event()
        context = SimpleNamespace(job=SimpleNamespace(data="games"),
                                  application=SimpleNamespace(running=True))
        def run_once(callback, when, **options):
            scheduler.add_job(callback, "date",
                run_date=datetime.now(timezone.utc) + timedelta(seconds=when),
                args=[context], name=options["name"], **options["job_kwargs"])
        context.job_queue = SimpleNamespace(run_once=run_once)
        async def slow_tick(context):
            nonlocal active, maximum, passes
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.03)
            active -= 1
            passes += 1
            if passes == 3:
                context.application.running = False
                finished.set()
        bot.tick_games = AsyncMock(side_effect=slow_tick)
        scheduler.add_listener(warnings.append, EVENT_JOB_MAX_INSTANCES | EVENT_JOB_MISSED)
        run_once(bot.run_worker, -20, name="worker_games", job_kwargs={"misfire_grace_time": None})
        with patch("auction_bot.bot.WORKER_TICK_INTERVAL_SECONDS", 0.01):
            scheduler.start()
            try:
                await asyncio.wait_for(finished.wait(), 3)
                self.assertEqual((passes, maximum), (3, 1))
                self.assertEqual(warnings, [])
            finally:
                context.application.running = False
                scheduler.shutdown(wait=False)

    async def test_failed_worker_retries_but_shutdown_does_not_reschedule(self):
        bot = runtime_bot()
        bot.tick = AsyncMock(side_effect=RuntimeError("synthetic failure"))
        context = SimpleNamespace(job=SimpleNamespace(data="auctions"), job_queue=Mock(),
                                  application=SimpleNamespace(running=True))
        with self.assertRaises(RuntimeError):
            await bot.run_worker(context)
        context.job_queue.run_once.assert_called_once()
        context.job_queue.reset_mock()
        context.application.running = False
        with self.assertRaises(RuntimeError):
            await bot.run_worker(context)
        context.job_queue.run_once.assert_not_called()

    async def test_due_rounds_are_processed_before_expired_message_edits(self):
        bot = runtime_bot()
        seen = []
        async def rounds(context):
            seen.append("rounds")
        async def expire(context):
            self.assertEqual(seen, ["rounds"])
        bot.tick_pvp_rounds = AsyncMock(side_effect=rounds)
        bot.tick_game_expirations = AsyncMock(side_effect=expire)
        await bot.tick_pvp(SimpleNamespace())
        bot.tick_game_expirations.assert_awaited_once()

    def test_mobile_signed_amounts_preserve_usd_syntax(self):
        for text, expected in (("+ ၁၀၀", ["+100"]), ("−$5", ["-$5"]),
                               ("＋100 gift", ["+100", "gift"]), ("hello", None)):
            self.assertEqual(signed_owner_message_args(text), expected)


if __name__ == "__main__":
    unittest.main()
