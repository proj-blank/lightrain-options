# Telegram helper - imports from main trading folder using full path
import importlib.util
import sys

# Load telegram_bot directly from absolute path
spec = importlib.util.spec_from_file_location(
    'trading_telegram_bot', 
    '/home/ubuntu/trading/scripts/telegram_bot.py'
)
telegram_module = importlib.util.module_from_spec(spec)

try:
    spec.loader.exec_module(telegram_module)
    send_telegram_message = telegram_module.send_telegram_message
    TELEGRAM_ENABLED = True
except Exception as e:
    TELEGRAM_ENABLED = False
    _error = str(e)
    def send_telegram_message(msg, parse_mode=None):
        print(f'[TELEGRAM DISABLED: {_error}] {msg}')
