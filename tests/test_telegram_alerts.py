from jev_indstocks_trader.config import load_config
from jev_indstocks_trader.telegram_alerts import TelegramAlertNotifier, authorized_kill_command


def _msg(chat_id, user_id=None, text="/halt CONFIRM", forwarded=False):
    m = type("Msg", (), {})()
    m.chat = type("Chat", (), {"id": chat_id})()
    m.from_user = type("User", (), {"id": user_id if user_id is not None else chat_id})()
    m.text = text
    m.forward_from = object() if forwarded else None
    m.forward_from_chat = None
    m.forward_origin = None
    m.forward_date = None
    return m


def test_authorized_requires_confirm_and_user():
    ok, why = authorized_kill_command(_msg(999999, text="/halt"), 999999, 999999)
    assert ok is False and why == "need_confirm"
    ok, why = authorized_kill_command(_msg(111, 111, "/halt CONFIRM"), 999999, 999999)
    assert ok is False and why == "bad_chat"
    ok, why = authorized_kill_command(_msg(999999, 111, "/halt CONFIRM"), 999999, 999999)
    assert ok is False and why == "bad_user"
    ok, why = authorized_kill_command(_msg(999999, 999999, "/halt CONFIRM", forwarded=True), 999999, 999999)
    assert ok is False and why == "forwarded"
    ok, why = authorized_kill_command(_msg(999999, 999999, "/halt CONFIRM"), 999999, 999999)
    assert ok is True


def test_unauthorized_chat_cannot_trigger_kill_switch(mocker):
    mocker.patch("jev_indstocks_trader.telegram_alerts.telebot.TeleBot")
    cfg = load_config()
    kill_switch = mocker.Mock()
    notifier = TelegramAlertNotifier(cfg.telegram, kill_switch_callback=kill_switch)
    handle_emergency = notifier.bot.message_handler.return_value.call_args_list[0][0][0]
    handle_emergency(_msg(111111, 111111, "/halt CONFIRM"))
    kill_switch.assert_not_called()


def test_authorized_chat_triggers_kill_switch(mocker):
    mocker.patch("jev_indstocks_trader.telegram_alerts.telebot.TeleBot")
    cfg = load_config()
    kill_switch = mocker.Mock()
    notifier = TelegramAlertNotifier(cfg.telegram, kill_switch_callback=kill_switch)
    handle_emergency = notifier.bot.message_handler.return_value.call_args_list[0][0][0]
    handle_emergency(_msg(cfg.telegram.owner_chat_id, cfg.telegram.owner_user_id, "/halt CONFIRM"))
    kill_switch.assert_called_once()


def test_start_background_polling_starts_daemon_thread(mocker):
    mocker.patch("jev_indstocks_trader.telegram_alerts.telebot.TeleBot")
    cfg = load_config()
    notifier = TelegramAlertNotifier(cfg.telegram, kill_switch_callback=lambda: None)
    notifier.bot.infinity_polling.side_effect = lambda **kw: None
    thread = notifier.start_background_polling()
    thread.join(timeout=2)
    assert thread.daemon is True
    assert thread.name == "telegram-poll"
    notifier.bot.infinity_polling.assert_called()
