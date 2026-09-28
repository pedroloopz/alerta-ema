import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("MODO", "teste")
os.environ.pop("TELEGRAM_TOKEN", None)
os.environ.pop("TELEGRAM_CHAT_ID", None)
