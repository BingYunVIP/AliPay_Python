"""The administrator identity policy is shared by HTTP and management commands."""
import re
import string
from .mailing import email_address

def validate_admin_identity(email,password):
    if not isinstance(email,str) or not email_address(email.strip().lower()) or email.strip().lower()=='admin@pay.com':raise ValueError('invalid_new_email')
    if not isinstance(password,str) or not 12<=len(password)<=128 or any(c.isspace() for c in password):
        raise ValueError('invalid_admin_password')
    categories=(bool(re.search('[A-Z]',password)),bool(re.search('[a-z]',password)),
                bool(re.search('[0-9]',password)),any(c in string.punctuation for c in password))
    if sum(categories)<3:
        raise ValueError('invalid_admin_password')
