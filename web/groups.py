"""Billing groups are independent of authentication roles."""
import time
import json
from decimal import Decimal, InvalidOperation


class MerchantGroups:
    def __init__(self, store):
        self.store=store
        with store.connect() as db:
            db.execute("INSERT OR IGNORE INTO merchant_groups(id,name,service_rate,cost_rate,billing_mode,version) VALUES (1,'普通商户组','1.50','1.00','merchant_fee',1)")
            db.execute("INSERT OR IGNORE INTO merchant_groups(id,name,service_rate,cost_rate,billing_mode,version) VALUES (2,'管理员计费组','0.00','1.00','admin_cost_only',1)")

    def all(self):
        with self.store.connect() as db:
            return [dict(row) for row in db.execute('SELECT * FROM merchant_groups ORDER BY id')]

    def for_user(self, user, db):
        row = db.execute('SELECT g.* FROM merchant_groups g JOIN users u ON u.group_id=g.id WHERE u.id=?', (user,)).fetchone()
        if row is None:
            raise ValueError('merchant_group_missing')
        return dict(row)

    def assign(self, user, group, actor):
        if type(group) is not int:
            raise ValueError('invalid_merchant_group')
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM merchant_groups WHERE id=?', (group,)).fetchone():
                raise ValueError('invalid_merchant_group')
            row = db.execute('SELECT role FROM users WHERE id=?', (user,)).fetchone()
            if not row:
                raise ValueError('merchant_not_found')
            if row['role'] == 'admin' and group != 2:
                raise ValueError('admin_group_protected')
            db.execute('UPDATE users SET group_id=? WHERE id=?', (group,user))
            db.execute("INSERT INTO events(accepted,reason,created_at,stage,details,user_id) VALUES (1,'merchant_group_assigned',?,'merchant.group',?,?)",
                       (int(time.time()), json.dumps({'merchant':user,'group_id':group}), actor))

    def create(self,payload):
        if not isinstance(payload,dict) or set(payload)!={'name','service_rate','cost_rate'}: raise ValueError('invalid_merchant_group')
        name=payload['name']
        if not isinstance(name,str) or not 2<=len(name.strip())<=48 or any(ord(c)<32 for c in name): raise ValueError('invalid_merchant_group')
        rates=[]
        for key in ('service_rate','cost_rate'):
            try:
                value=Decimal(str(payload[key]))
                if not value.is_finite() or value<0 or value>100 or value.as_tuple().exponent < -2: raise ValueError()
                rates.append(format(value,'.2f'))
            except (ValueError,InvalidOperation): raise ValueError('invalid_group_rate') from None
        if Decimal(rates[0])+Decimal(rates[1])>100: raise ValueError('invalid_group_rate')
        try:
            with self.store.connect() as db:
                identity=db.execute("INSERT INTO merchant_groups(name,service_rate,cost_rate,billing_mode) VALUES (?,?,?,'merchant_fee')",(name.strip(),*rates)).lastrowid
        except Exception as exc:
            if 'UNIQUE' in str(exc): raise ValueError('merchant_group_name_exists') from None
            raise
        return next(row for row in self.all() if row['id']==identity)

    def save(self, identity, payload):
        if not isinstance(payload,dict) or set(payload) != {'version','service_rate','cost_rate'}:
            raise ValueError('invalid_merchant_group')
        rates = []
        for key in ('service_rate','cost_rate'):
            try:
                value = Decimal(str(payload[key]))
                if not value.is_finite() or value < 0 or value > 100 or value.as_tuple().exponent < -2:
                    raise ValueError()
                rates.append(format(value,'.2f'))
            except (ValueError, InvalidOperation):
                raise ValueError('invalid_group_rate') from None
        if sum(map(Decimal,rates)) > 100 or (identity == 2 and Decimal(rates[0]) != 0):
            raise ValueError('invalid_group_rate')
        with self.store.connect() as db:
            changed = db.execute('UPDATE merchant_groups SET service_rate=?,cost_rate=?,version=version+1 WHERE id=? AND version=?', (*rates,identity,payload['version'])).rowcount
            if not changed:
                raise ValueError('merchant_group_conflict')
        return next(g for g in self.all() if g['id'] == identity)
