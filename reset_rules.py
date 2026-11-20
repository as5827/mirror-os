# -*- coding: utf-8 -*-
"""把风控阈值恢复成出厂默认。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import db  # noqa: E402

c = db.connect()
for key, name, descr, value, unit, enabled in db.DEFAULT_RULES:
    c.execute("UPDATE rules SET value=?, enabled=? WHERE key=?", (value, enabled, key))
c.commit()
print("阈值已恢复默认：")
for r in c.execute("SELECT key,value,unit,enabled FROM rules ORDER BY key"):
    print(f"  {r['key']:24s} {r['value']:>8g} {r['unit']}  {'开' if r['enabled'] else '关'}")
