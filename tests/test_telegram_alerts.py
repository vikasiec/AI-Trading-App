from jev_indstocks_trader.config import load_config
from jev_indstocks_trader.telegram_alerts import TelegramAlertNotifier


def _make_message(chat_id: int):
    msg = type("Msg", (), {})()
    msg.chat = type("Chat", (), {"id": chat_id})()
    return msg


def test_unauthorized_chat_cannot_trigger_kill_switch(mocker):
    mocker.patch("jev_indstocks_trader.telegram_alerts.telebot.TeleBot")
    cfg = load_config()
    kill_switch = mocker.Mock()
    notifier = TelegramAlertNotifier(cfg.telegram, kill_switch_callback=kill_switch)

    # Grab the handler registered for /halt via the mocked decorator call
    handler = notifier.bot.message_handler.call_args_list[0]
    # Simpler: call the closure directly by re-registering and capturing it
    register_calls = notifier.bot.message_handler.return_value.call_args_list
    handle_emergency = register_calls[0][0][0]

    stranger_message = _make_message(chat_id=111111)  # not the owner
    handle_emergency(stranger_message)

    kill_switch.assert_not_called()


def test_authorized_chat_triggers_kill_switch(mocker):
    mocker.patch("jev_indstocks_trader.telegram_alerts.telebot.TeleBot")
    cfg = load_config()
    kill_switch = mocker.Mock()
    notifier = TelegramAlertNotifier(cfg.telegram, kill_switch_callback=kill_switch)

    register_calls = notifier.bot.message_handler.return_value.call_args_list
    handle_emergency = register_calls[0][0][0]

    owner_message = _make_message(chat_id=cfg.telegram.owner_chat_id)
    handle_emergency(owner_message)

    kill_switch.assert_called_once()


def test_start_background_polling_starts_daemon_thread(mocker):
    mocker.patch("jev_indstocks_trader.telegram_alerts.telebot.TeleBot")
    mocker.patch(
        "jev_indstocks_trader.telegram_alerts.TelegramAlertNotifier.start_polling",
        autospec=False,
    )
    # Don't actually block on infinity_polling
    cfg = load_config()
    notifier = TelegramAlertNotifier(cfg.telegram, kill_switch_callback=lambda: None)
    notifier.bot.infinity_polling.side_effect = lambda **kw: None
    thread = notifier.start_background_polling()
    thread.join(timeout=2)
    assert thread.daemon is True
    assert thread.name == "telegram-poll"
    notifier.bot.infinity_polling.assert_called()
