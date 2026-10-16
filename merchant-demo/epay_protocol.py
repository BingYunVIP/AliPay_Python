"""Epay Payment.php signing contract, ported for V1 MD5 and V2 RSA-SHA256.

Reference: Epay/includes/lib/Payment.php::getSignContent/makeSign/verifySign.
The same independent module is included in the merchant demonstration archive.
"""
import base64
import hashlib
import hmac
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def canonical(parameters):
    fields = []
    for key in sorted(parameters):
        value = parameters[key]
        if key in {'sign', 'sign_type'} or value is None or isinstance(value, (dict, list, tuple)):
            continue
        if isinstance(value, bool): value = '1' if value else ''
        value = str(value)
        if not value.strip(): continue
        fields.append(str(key) + '=' + value)
    return '&'.join(fields).encode('utf-8')


def pem(value, private=False):
    if not isinstance(value, str) or len(value) > 12000: raise ValueError('invalid_rsa_key')
    value = value.strip().replace('\\n', '\n')
    if not value.startswith('-----BEGIN '):
        raw = ''.join(value.split())
        label = 'PRIVATE KEY' if private else 'PUBLIC KEY'
        value = f'-----BEGIN {label}-----\n' + '\n'.join(raw[i:i+64] for i in range(0, len(raw), 64)) + f'\n-----END {label}-----\n'
    return value.encode()


def private_key(value):
    key = serialization.load_pem_private_key(pem(value, True), password=None)
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048: raise ValueError('invalid_rsa_key')
    return key


def public_key(value):
    key = serialization.load_pem_public_key(pem(value))
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048: raise ValueError('invalid_rsa_key')
    return key


def public_pem(value):
    key = value.public_key() if isinstance(value, rsa.RSAPrivateKey) else public_key(value)
    return key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def sign(parameters, secret, algorithm='MD5'):
    data = canonical(parameters)
    if algorithm == 'MD5': return hashlib.md5(data + secret.encode('utf-8')).hexdigest()
    if algorithm != 'RSA': raise ValueError('invalid_sign_type')
    key = private_key(secret) if isinstance(secret, str) else secret
    return base64.b64encode(key.sign(data, padding.PKCS1v15(), hashes.SHA256())).decode()


def verify(parameters, secret, algorithm=None, timestamp=False, now=None):
    algorithm = algorithm or parameters.get('sign_type', 'MD5')
    signature = parameters.get('sign')
    if not isinstance(signature, str): return False
    try:
        if timestamp and abs((time.time() if now is None else now) - int(parameters.get('timestamp', ''))) > 300: return False
        if algorithm == 'MD5': return hmac.compare_digest(sign(parameters, secret, algorithm), signature)
        if algorithm != 'RSA': return False
        key = public_key(secret) if isinstance(secret, str) else secret
        key.verify(base64.b64decode(signature, validate=True), canonical(parameters), padding.PKCS1v15(), hashes.SHA256())
        return True
    except (ValueError, TypeError, InvalidSignature): return False


def signed(parameters, secret, algorithm='MD5', timestamp=None):
    result = dict(parameters, sign_type=algorithm)
    if algorithm == 'RSA': result['timestamp'] = str(int(time.time()) if timestamp is None else timestamp)
    result['sign'] = sign(result, secret, algorithm)
    return result
