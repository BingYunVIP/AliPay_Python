import signal
import time

from .wsgi import app
from .webhooks import deliver_one

running = True


def stop(*_):
    global running
    running = False


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
while running:
    try:
        has_job = deliver_one(app.extensions["store"], app.extensions["webhook_signer"],
                              app.extensions["trace"], app.extensions["callback_hosts"])
    except Exception as exc:
        app.logger.error("Callback worker error: %s", type(exc).__name__)
        has_job = False
    if not has_job:
        time.sleep(3)
