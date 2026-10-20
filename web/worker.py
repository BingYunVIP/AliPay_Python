import argparse
import signal
import time

from .wsgi import app
from .webhooks import deliver_one

running = True
parser = argparse.ArgumentParser(description='ByPay background jobs')
parser.add_argument('--pricing-only', action='store_true', help='Only sync real market data, without payment/callback jobs')
args = parser.parse_args()


def stop(*_):
    global running
    running = False


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
while running:
    app.extensions["store"].heartbeat("pricing" if args.pricing_only else "callbacks")
    jobs = [("callbacks", lambda: deliver_one(app.extensions["store"], app.extensions["webhook_signer"],
                  app.extensions["trace"], app.extensions["callback_hosts"]))]
    jobs.extend((name, app.extensions[name].tick) for name in ("pricing", "reconciler", "crypto_scanner", "refunds", "operations"))
    if app.extensions['operations'].rules.read()['jobs_mail']:
        jobs.append(("mailer", app.extensions["mailer"].tick))
    if args.pricing_only:
        jobs = [("pricing", app.extensions["pricing"].tick)]
    has_job = False
    for name, action in jobs:
        try:
            has_job = action() or has_job
        except Exception as exc:
            app.logger.error("Worker %s failed: %s", name, type(exc).__name__)
    if not has_job:
        time.sleep(3)
