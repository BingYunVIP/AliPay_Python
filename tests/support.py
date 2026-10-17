"""Shared test setup: new merchants now start behind review, a paid fee, a deposit and a domain allowlist."""


def open_gates(app):
    """Turn the merchant admission gates off so a test can exercise its own rule.

    The shipped defaults require review, a paid registration fee, a deposit and an approved payment domain.
    Tests that are not about those gates open them here instead of pretending the merchant is approved.
    """
    rules = app.extensions["operations"].rules
    rules.save({"registration_review": False, "registration_paid": False, "deposit_enabled": False,
                "risk_enabled": False, "domain_add": True, "domain_enforce": False,
                "refund_fee_payer": "platform", "version": rules.read()["version"]})
