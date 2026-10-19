"""Monthly public archives; a disposable SQLite lookup keeps collection incremental."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

from . import briefings
from .core import normalized_title, validate_dataset

KST = ZoneInfo('Asia/Seoul')
MONTH = re.compile(r'^\d{4}-(?:0[1-9]|1[0-2])$')


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()


def atomic_bytes(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix='.tmp')
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_json(path, value):
    atomic_bytes(path, encoded(value))


def month_of(stamp):
    value = datetime.fromisoformat(stamp) if isinstance(stamp, str) else stamp
    return value.astimezone(KST).strftime('%Y-%m')


def digest(content):
    return hashlib.sha256(content).hexdigest()


def empty_catalog():
    return {'version': 2, 'updated_at': None, 'collection_slot': None, 'news': {}, 'briefings': {}, 'editorial': {'hidden_ids': [], 'corrections': {}}}


def public_path(relative):
    return relative in ('catalog.json', 'sources.json', 'news.json', 'briefings.json') or bool(re.fullmatch(r'(news|briefings)/\d{4}-(?:0[1-9]|1[0-2])\.json', relative))


class Archive:
    def __init__(self, directory, runtime=None):
        self.root = Path(directory).resolve()
        self.runtime = Path(runtime or self.root.parent / '.runtime').resolve()
        self.journal = self.runtime / 'archive-transaction'
        self.recover()
        path = self.root / 'catalog.json'
        if not path.exists() and any(p for kind in ('news', 'briefings') for p in (self.root / kind).glob('*.json')):
            raise ValueError('Monthly files exist without their catalog; restore the complete archive')
        self.catalog = json.loads(path.read_text()) if path.exists() else empty_catalog()
        if self.catalog.get('version') != 2:
            raise ValueError('Unsupported archive catalog')
        for kind in ('news', 'briefings'):
            if not isinstance(self.catalog.get(kind), dict) or any(not MONTH.fullmatch(m) for m in self.catalog[kind]):
                raise ValueError('Invalid archive month')
        source_path = self.root / 'sources.json'
        source_data = json.loads(source_path.read_text()) if source_path.exists() else {'version': 1, 'sources': []}
        if source_data.get('version') != 1 or not isinstance(source_data.get('sources'), list):
            raise ValueError('Invalid source catalog')
        self.sources = source_data['sources']
        if path.exists() and not source_path.exists():
            raise ValueError('Missing source catalog')
        validate_dataset({'version': 1, 'updated_at': self.catalog['updated_at'], 'articles': [], 'sources': self.sources})
        for entries in (self.catalog['news'], self.catalog['briefings']):
            for entry in entries.values():
                if not isinstance(entry.get('count'), int) or entry['count'] < 0 or not re.fullmatch(r'[a-f0-9]{64}', entry.get('sha256', '')):
                    raise ValueError('Invalid archive catalog entry')
        self.cache = {}
        self._db = None
        # Legacy input is accepted only for explicit migration, never silently mixed.
        self.legacy = not path.exists() and (self.root / 'news.json').exists()
        if path.exists() and any((self.root / name).exists() for name in ('news.json', 'briefings.json')):
            raise ValueError('Mixed legacy and monthly archive; finish migration first')

    def recover(self):
        manifest = self.journal / 'manifest.json'
        if not manifest.exists():
            return
        record = json.loads(manifest.read_text())
        if record['root'] != str(self.root):
            raise ValueError('Recovery journal belongs to another data directory')
        if record.get('complete'):
            shutil.rmtree(self.journal)
            return
        for entry in record['files']:
            name = entry['path']
            if not public_path(name):
                raise ValueError('Unsafe recovery path')
            current = self.root / name
            current_hash = digest(current.read_bytes()) if current.exists() else None
            if current_hash not in (entry['before'], entry['after']):
                raise ValueError('Public data edited after interrupted transaction; inspect before recovery')
            if entry['before'] is not None:
                backup = self.journal / 'before' / name
                if not backup.exists() or digest(backup.read_bytes()) != entry['before']:
                    raise ValueError('Recovery backup corrupted; inspect before recovery')
        for entry in record['files']:
            path = self.root / entry['path']
            if entry['before'] is None:
                path.unlink(missing_ok=True)
            else:
                atomic_bytes(path, (self.journal / 'before' / entry['path']).read_bytes())
        shutil.rmtree(self.journal)

    def _transaction(self, changes):
        changes = {
            name: value for name, value in changes.items()
            if ((self.root / name).read_bytes() if (self.root / name).exists() else None) != value}
        if not changes:
            return False
        if any(not public_path(name) for name in changes):
            raise ValueError('Unexpected public output path')
        self.journal.mkdir(parents=True, exist_ok=True)
        records = []
        for name, value in changes.items():
            path = self.root / name
            before = path.read_bytes() if path.exists() else None
            if before is not None:
                atomic_bytes(self.journal / 'before' / name, before)
            records.append({'path': name, 'before': digest(before) if before is not None else None,
                            'after': digest(value) if value is not None else None})
        manifest = {'root': str(self.root), 'files': records, 'complete': False}
        atomic_json(self.journal / 'manifest.json', manifest)
        try:
            # The catalog is the final pointer to the complete set of monthly files.
            for name in sorted(changes, key=lambda n: (n == 'catalog.json', n)):
                if changes[name] is None:
                    (self.root / name).unlink(missing_ok=True)
                else:
                    atomic_bytes(self.root / name, changes[name])
            manifest['complete'] = True
            atomic_json(self.journal / 'manifest.json', manifest)
        except BaseException:
            self.recover()
            raise
        shutil.rmtree(self.journal)
        self.cache.clear()
        self._close_index()
        return True

    def read(self, kind, month):
        if not MONTH.fullmatch(month):
            raise ValueError('Invalid month')
        key = (kind, month)
        if key not in self.cache:
            item = self.catalog[kind].get(month)
            records_key = 'articles' if kind == 'news' else 'editions'
            if item is None:
                return []
            content = (self.root / kind / f'{month}.json').read_bytes()
            value = json.loads(content)
            if digest(content) != item['sha256'] or value.get('version') != 1 or value.get('month') != month or len(value[records_key]) != item['count']:
                raise ValueError(f'Archive integrity check failed: {kind}/{month}')
            records = value[records_key]
            if kind == 'news':
                validate_dataset({'version': 1, 'updated_at': None, 'articles': records, 'sources': self.sources})
                if any(month_of(a['published_at']) != month for a in records):
                    raise ValueError('Article stored in wrong KST month')
                if sorted({datetime.fromisoformat(a['published_at']).astimezone(KST).date().isoformat() for a in records}) != item['days']:
                    raise ValueError('Incorrect archive day index')
            else:
                briefings.validate_history({'version': 1, 'editions': records})
                if any(e['day'][:7] != month for e in records):
                    raise ValueError('Briefing stored in wrong month')
            self.cache[key] = records
        return self.cache[key]

    def all(self, kind):
        return [entry for month in sorted(self.catalog[kind], reverse=True) for entry in self.read(kind, month)]

    def validate(self, incremental=False):
        if self.legacy:
            raise ValueError('Legacy archive requires migration')
        seen = {'news': set(), 'briefings': set()}
        for kind in seen:
            files = {p.stem for p in (self.root / kind).glob('*.json')}
            if files != set(self.catalog[kind]):
                raise ValueError(f'Missing or unlisted {kind} month files')
            if incremental: continue
            for month in self.catalog[kind]:
                for record in self.read(kind, month):
                    if record['id'] in seen[kind]:
                        raise ValueError('Duplicate archive ID across months')
                    seen[kind].add(record['id'])
        if incremental: self.index()
        return self

    def save(self, *, news=None, editions=None, sources=None, updated_at=None, collection_slot=None, editorial=None, remove_legacy=False):
        catalog = json.loads(json.dumps(self.catalog))
        changes = {}
        for kind, batches in (('news', news or {}), ('briefings', editions or {})):
            for month, records in batches.items():
                if not MONTH.fullmatch(month):
                    raise ValueError('Invalid month')
                key = 'articles' if kind == 'news' else 'editions'
                records = sorted(records, key=lambda a: (a['published_at'], a['id']) if kind == 'news' else (a['day'], a['id']), reverse=True)
                if kind == 'news':
                    validate_dataset({'version': 1, 'updated_at': None, 'articles': records, 'sources': sources if sources is not None else self.sources})
                    if any(month_of(a['published_at']) != month for a in records): raise ValueError('Incorrect news month')
                else:
                    briefings.validate_history({'version': 1, 'editions': records})
                    if any(e['day'][:7] != month for e in records): raise ValueError('Incorrect briefing month')
                content = encoded({'version': 1, 'month': month, key: records})
                entry = {'count': len(records), 'sha256': digest(content)}
                if kind == 'news':
                    entry['days'] = sorted({datetime.fromisoformat(a['published_at']).astimezone(KST).date().isoformat() for a in records})
                catalog[kind][month] = entry
                changes[f'{kind}/{month}.json'] = content
        if sources is not None or not (self.root / 'sources.json').exists():
            changes['sources.json'] = encoded({'version': 1, 'sources': sources if sources is not None else self.sources})
        if updated_at is not None: catalog['updated_at'] = updated_at
        if collection_slot is not None: catalog['collection_slot'] = collection_slot
        if editorial is not None: catalog['editorial'] = editorial
        changes['catalog.json'] = encoded(catalog)
        if remove_legacy:
            changes.update({'news.json': None, 'briefings.json': None})
        changed = self._transaction(changes)
        self.catalog = catalog
        if sources is not None: self.sources = sources
        self.legacy = False
        return changed

    def migrate(self):
        if not self.legacy:
            return False
        data = json.loads((self.root / 'news.json').read_text())
        history_path = self.root / 'briefings.json'
        history = json.loads(history_path.read_text()) if history_path.exists() else {'version': 1, 'editions': []}
        validate_dataset(data)
        briefings.validate_history(history)
        news, editions = {}, {}
        for a in data['articles']: news.setdefault(month_of(a['published_at']), []).append(a)
        for e in history['editions']: editions.setdefault(e['day'][:7], []).append(e)
        self.save(news=news, editions=editions, sources=data['sources'], updated_at=data['updated_at'], collection_slot=data.get('collection_slot'), remove_legacy=True)
        self.validate()
        return True

    def _close_index(self):
        if self._db is not None:
            self._db.close()
            self._db = None

    def close(self):
        self._close_index()

    def index(self, _retry=True):
        if self._db is not None: return self._db
        self.runtime.mkdir(parents=True, exist_ok=True)
        path = self.runtime / f'archive-index-{digest(str(self.root).encode())[:12]}.sqlite3'
        try:
            db = sqlite3.connect(path)
            db.execute('CREATE TABLE IF NOT EXISTS files (kind TEXT, month TEXT, hash TEXT, PRIMARY KEY(kind, month))')
            db.execute('CREATE TABLE IF NOT EXISTS articles (id TEXT PRIMARY KEY, month TEXT, title TEXT, published TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS article_title ON articles(title, published)')
            db.execute('CREATE TABLE IF NOT EXISTS urls (url TEXT PRIMARY KEY, article_id TEXT, month TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS refs (article_id TEXT, month TEXT, PRIMARY KEY(article_id,month))')
        except sqlite3.DatabaseError as exc:
            if 'db' in locals(): db.close()
            if _retry and getattr(exc, 'sqlite_errorcode', 0) in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
                path.unlink(missing_ok=True)
                return self.index(_retry=False)
            raise
        try:
            with db:
                known = {(k, m): h for k, m, h in db.execute('SELECT kind, month, hash FROM files')}
                target = {(k, m): e['sha256'] for k in ('news', 'briefings') for m, e in self.catalog[k].items()}
                changed = [key for key in set(known) | set(target) if known.get(key) != target.get(key)]
                # Delete every changed partition before inserting records that moved months.
                for kind, month in changed:
                    if kind == 'news':
                        db.execute('DELETE FROM urls WHERE month=?', (month,))
                        db.execute('DELETE FROM articles WHERE month=?', (month,))
                    else: db.execute('DELETE FROM refs WHERE month=?', (month,))
                    db.execute('DELETE FROM files WHERE kind=? AND month=?', (kind, month))
                for kind, month in changed:
                    if (kind, month) not in target: continue
                    for record in self.read(kind, month):
                        if kind == 'news':
                            db.execute('INSERT INTO articles VALUES (?,?,?,?)', (record['id'], month, normalized_title(record['original_title']), record['published_at']))
                            for ref in record['sources']:
                                previous = db.execute('SELECT article_id FROM urls WHERE url=?', (ref['url'],)).fetchone()
                                if previous and previous[0] != record['id']: raise ValueError('Source URL assigned to multiple article IDs')
                                db.execute('INSERT OR IGNORE INTO urls VALUES (?,?,?)', (ref['url'], record['id'], month))
                        else:
                            for a in record['articles']:
                                db.execute('INSERT OR IGNORE INTO refs VALUES (?,?)', (a['id'], month))
                    db.execute('INSERT INTO files VALUES (?,?,?)', (kind, month, target[(kind, month)]))
        except Exception as exc:
            db.close()
            if _retry and isinstance(exc, sqlite3.DatabaseError) and getattr(exc, 'sqlite_errorcode', 0) in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
                path.unlink(missing_ok=True)
                self.cache.clear()
                return self.index(_retry=False)
            raise
        self._db = db
        # Do not retain all full records when the disposable index is first built.
        self.cache.clear()
        return db

    def relevant_news_months(self, candidates, start, end, changed_ids=()):
        months = {m for m in self.catalog['news'] if month_of(start) <= m <= month_of(end)}
        db = self.index()
        for a in candidates:
            months.add(month_of(a['published_at']))
            months.update(r[0] for r in db.execute('SELECT month FROM urls WHERE url=?', (a['url'],)))
            months.update(r[0] for r in db.execute('SELECT month FROM articles WHERE id=?', (a['id'],)))
            title = normalized_title(a['original_title'])
            if len(title) >= 24:
                for month, stamp in db.execute('SELECT month,published FROM articles WHERE title=?', (title,)):
                    if abs((datetime.fromisoformat(stamp)-datetime.fromisoformat(a['published_at'])).total_seconds()) < 72*3600:
                        months.add(month)
        for aid in changed_ids:
            months.update(r[0] for r in db.execute('SELECT month FROM articles WHERE id=?', (aid,)))
        return months

    def editorial_changes(self, editorial):
        before = self.catalog.get('editorial', {'hidden_ids': [], 'corrections': {}})
        ids = set(before['hidden_ids']) ^ set(editorial['hidden_ids'])
        for aid in before['corrections'].keys() | editorial['corrections'].keys():
            if before['corrections'].get(aid) != editorial['corrections'].get(aid): ids.add(aid)
        months = set()
        db = self.index()
        for aid in ids:
            months.update(r[0] for r in db.execute('SELECT month FROM refs WHERE article_id=?', (aid,)))
        return ids, {m: briefings.apply_editorial({'version': 1, 'editions': self.read('briefings', m)}, editorial)['editions'] for m in months}


def read_news(directory):
    store = Archive(directory)
    if store.legacy:
        return json.loads((Path(directory) / 'news.json').read_text())
    return {'version': 1, 'updated_at': store.catalog['updated_at'], 'collection_slot': store.catalog['collection_slot'], 'sources': store.sources, 'articles': store.all('news')}


def read_history(directory):
    store = Archive(directory)
    legacy = Path(directory) / 'briefings.json'
    if store.legacy:
        return json.loads(legacy.read_text()) if legacy.exists() else {'version': 1, 'editions': []}
    return {'version': 1, 'editions': store.all('briefings')}
