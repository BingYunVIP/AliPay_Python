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
        app.extensions["store"].heartbeat("callbacks")
        has_job = deliver_one(app.extensions["store"], app.extensions["webhook_signer"],
                              app.extensions["trace"], app.extensions["callback_hosts"], local_origins=app.extensions["callback_local_origins"])
        has_job = app.extensions["reconciler"].tick() or has_job
        has_job = app.extensions["refunds"].tick() or has_job
        has_job = app.extensions['operations'].tick() or has_job
        if app.extensions['operations'].rules.read()['jobs_mail']:has_job = app.extensions['mailer'].tick() or has_job
    except Exception as exc:
        app.logger.error("Payment worker error: %s", type(exc).__name__)
        has_job = False
    if not has_job:
        time.sleep(3)
