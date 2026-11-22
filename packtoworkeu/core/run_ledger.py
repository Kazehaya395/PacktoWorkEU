"""Run-local metadata map. SQLite's anonymous file is deleted on close.

Callers own the lock. Only small JSON metadata/proof strings belong here,
never descriptions. No changes to the user's database or persistent schema.
"""
import json
import sqlite3
from collections.abc import MutableMapping


class RunLedger(MutableMapping):
    def __init__(self, threshold=4096):
        self.ram = {}; self.conn = None; self.threshold = threshold; self.size = 0

    def __len__(self):
        return len(self.ram) if self.conn is None else self.size

    def __getitem__(self, key):
        if self.conn is None:
            return self.ram[key]
        row = self.conn.execute('select v from entries where k=?',(key,)).fetchone()
        if row is None: raise KeyError(key)
        return json.loads(row[0])

    def __setitem__(self, key, value):
        if self.conn is None:
            if len(self.ram)<self.threshold or key in self.ram:
                self.ram[key]=value; return
            c=sqlite3.connect('',check_same_thread=False)
            c.execute('create table entries(k text primary key, v text not null) without rowid')
            c.executemany('insert into entries values(?,?)',((k,json.dumps(v)) for k,v in self.ram.items()))
            self.size=len(self.ram); self.conn=c; self.ram.clear()
        inserted=self.conn.execute('insert or ignore into entries values(?,?)',(key,json.dumps(value))).rowcount
        if inserted: self.size+=1
        else: self.conn.execute('update entries set v=? where k=?',(json.dumps(value),key))

    def __delitem__(self,key):
        if self.conn is None: del self.ram[key]; return
        if not self.conn.execute('delete from entries where k=?',(key,)).rowcount: raise KeyError(key)
        self.size-=1

    def __iter__(self):
        if self.conn is None: yield from self.ram
        else: yield from (k for k, in self.conn.execute('select k from entries'))

    def items(self):
        if self.conn is None: yield from self.ram.items()
        else: yield from ((k,json.loads(v)) for k,v in self.conn.execute('select k,v from entries'))

    def values(self):
        for _,v in self.items(): yield v
