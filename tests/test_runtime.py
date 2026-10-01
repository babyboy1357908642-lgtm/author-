"""Routing and scheduling regressions with synthetic Telegram messages."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

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
        callbacks = [job.callback for job in app.job_queue.jobs()]
        self.assertIn(bot.tick, callbacks)
        self.assertIn(bot.tick_games, callbacks)

    def test_mobile_signed_amounts_preserve_usd_syntax(self):
        for text, expected in (("+ ၁၀၀", ["+100"]), ("−$5", ["-$5"]),
                               ("＋100 gift", ["+100", "gift"]), ("hello", None)):
            self.assertEqual(signed_owner_message_args(text), expected)


if __name__ == "__main__":
    unittest.main()
