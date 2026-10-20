"""Current payment precision, matching and display terms shared by all adapters."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal('0.01')
MODES = {'exact', 'prefix', 'round'}
NETWORK_LABELS = {'BSC':'BSC链','TRON':'Tron链','APTOS':'Aptos链','ETH':'Ethereum链',
    'PLASMA':'Plasma链','SOL':'Solana链','ARBITRUM':'Arbitrum One链',
    'POLYGON':'Polygon PoS链','AVAXC':'Avalanche C链'}
CHANNEL_LABELS = {'alipay':'支付宝','wechat':'微信','crypto':'Crypto','paypal':'PayPal','stripe':'Stripe'}
EVM_NETWORKS = {'BSC','ETH','PLASMA','ARBITRUM','POLYGON','AVAXC'}

def decimal(value):
    try:result=Decimal(str(value))
    except (InvalidOperation,TypeError,ValueError):raise ValueError('invalid_decimal') from None
    if not result.is_finite():raise ValueError('invalid_decimal')
    return result

def two(value):
    try:return format(decimal(value).quantize(CENT,rounding=ROUND_HALF_UP),'.2f')
    except InvalidOperation:raise ValueError('invalid_decimal') from None

def payment_amount(cny,rate):
    r=decimal(rate)
    if r<=0:raise ValueError('invalid_rate')
    value=two(decimal(cny)/r)
    if decimal(value)<=0:raise ValueError('crypto_amount_too_small')
    return value

def matches(actual,expected,mode='exact'):
    a,e=decimal(actual),decimal(expected)
    if a<=0 or e<=0 or mode not in MODES:return False
    if a==e:return True
    if mode=='round':return a.quantize(CENT,rounding=ROUND_HALF_UP)==e
    if mode=='prefix':return e<=a<e+CENT
    return False

def interval(value,mode):
    e=decimal(value)
    if mode=='prefix':return e,e+CENT,False
    if mode=='round':return e-Decimal('.005'),e+Decimal('.005'),False
    return e,e,True

def overlaps(first,first_mode,second,second_mode):
    a,b,point_a=interval(first,first_mode);c,d,point_b=interval(second,second_mode)
    if point_a:return matches(a,second,second_mode)
    if point_b:return matches(c,first,first_mode)
    return max(a,c)<min(b,d)

def address_key(network,address):
    return str(address).lower() if network in EVM_NETWORKS or network=='APTOS' else str(address)

def method_label(channel,network=''):
    return NETWORK_LABELS.get(network,network) if channel=='crypto' else CHANNEL_LABELS.get(channel,channel)
