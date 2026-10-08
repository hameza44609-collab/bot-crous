import os
import unittest
from unittest.mock import call, patch


os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:test-token-for-unit-tests")

import crous_bot as bot


class SubscriberRegistryTests(unittest.TestCase):
    def test_encrypted_registry_round_trip_does_not_expose_chat_ids(self):
        state = bot.default_state()

        bot.save_subscribers(state, {"123456789", "-987654321"})

        encrypted = state["subscribers_encrypted"]
        self.assertIsInstance(encrypted, str)
        self.assertNotIn("123456789", encrypted)
        self.assertNotIn("987654321", encrypted)
        self.assertEqual(
            bot.load_subscribers(state), {"123456789", "-987654321"}
        )

    def test_registry_cannot_be_decrypted_with_another_bot_token(self):
        state = bot.default_state()
        bot.save_subscribers(state, {"123456789"})

        with patch.object(bot, "TELEGRAM_BOT_TOKEN", "different-token"):
            with self.assertRaisesRegex(RuntimeError, "Cannot decrypt"):
                bot.load_subscribers(state)


class SubscriptionCommandTests(unittest.TestCase):
    def test_start_and_stop_commands_update_registry_and_offset(self):
        state = bot.default_state()
        state["legacy_chat_ids_migrated"] = True
        updates = [
            {"update_id": 10, "message": {"text": "/start", "chat": {"id": 1}}},
            {
                "update_id": 11,
                "message": {"text": "/start payload", "chat": {"id": 2}},
            },
            {
                "update_id": 12,
                "message": {"text": "/stop@crous_bot", "chat": {"id": 1}},
            },
        ]

        with patch.object(bot, "fetch_telegram_updates", return_value=updates), patch.object(
            bot, "_send_telegram_single", return_value=True
        ) as send:
            subscribers = bot.sync_telegram_subscribers(state, set())

        self.assertEqual(subscribers, {"2"})
        self.assertEqual(bot.load_subscribers(state), {"2"})
        self.assertEqual(state["telegram_update_offset"], 13)
        self.assertEqual(send.call_count, 3)

    def test_non_command_messages_do_not_subscribe(self):
        state = bot.default_state()
        state["legacy_chat_ids_migrated"] = True
        updates = [
            {"update_id": 20, "message": {"text": "hello", "chat": {"id": 5}}},
            {"update_id": 21, "edited_message": {"text": "/start"}},
        ]

        with patch.object(bot, "fetch_telegram_updates", return_value=updates), patch.object(
            bot, "_send_telegram_single"
        ) as send:
            subscribers = bot.sync_telegram_subscribers(state, set())

        self.assertEqual(subscribers, set())
        self.assertEqual(state["telegram_update_offset"], 22)
        send.assert_not_called()

    def test_legacy_chat_ids_are_migrated_only_once(self):
        state = bot.default_state()
        with patch.object(bot, "LEGACY_TELEGRAM_CHAT_IDS", ["7"]), patch.object(
            bot, "fetch_telegram_updates", return_value=[]
        ):
            subscribers = bot.sync_telegram_subscribers(state, set())
            subscribers.clear()
            bot.save_subscribers(state, subscribers)
            subscribers = bot.sync_telegram_subscribers(state, subscribers)

        self.assertEqual(subscribers, set())
        self.assertTrue(state["legacy_chat_ids_migrated"])


class BroadcastTests(unittest.TestCase):
    def test_broadcast_attempts_every_subscriber_and_tolerates_partial_failure(self):
        def deliver(chat_id, text, disable_preview=True):
            if chat_id == "2":
                raise RuntimeError("blocked")
            return True

        with patch.object(bot, "_send_telegram_single", side_effect=deliver) as send:
            delivered = bot.send_telegram("message", {"1", "2"})

        self.assertEqual(delivered, 1)
        send.assert_has_calls(
            [
                call("1", "message", disable_preview=True),
                call("2", "message", disable_preview=True),
            ],
            any_order=True,
        )
        self.assertEqual(send.call_count, 2)

    def test_empty_registry_skips_broadcast(self):
        with patch.object(bot, "_send_telegram_single") as send:
            self.assertEqual(bot.send_telegram("message", set()), 0)
        send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
