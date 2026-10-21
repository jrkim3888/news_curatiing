"""Archive persistence and lookup regressions; no network or paid model calls."""
from datetime import datetime
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collector import storage
from collector.storage import Archive, atomic_json, digest, encoded, month_of, read_news
from collector.core import merge_candidates, UTC
from test_collector import fixture_article

SOURCE = {'id': 'openai', 'name': 'OpenAI', 'homepage': 'https://openai.com/', 'region': 'global', 'role': 'official', 'enabled': True}


def article(key, stamp='2026-10-15T00:00:00+00:00', title=None):
    a = fixture_article(f'https://example.com/{key}', title or f'AI research announcement number {key}')
    a['published_at'] = stamp
    return a


def edition(a, day='2026-10-16'):
    end = datetime.fromisoformat(f'{day}T06:00:00+09:00')
    from datetime import timedelta
    return {'id': day, 'day': day, 'window_end': end.isoformat(), 'window_start': (end-timedelta(days=1)).isoformat(),
            'scheduled_at': (end+timedelta(minutes=30)).isoformat(), 'generated_at': (end+timedelta(hours=1)).isoformat(),
            'status': 'published', 'headline': '새로운 인공지능 연구 발표', 'overview': [{'text': '새로운 인공지능 연구 결과와 도구에 관한 발표를 살펴보았습니다.', 'issue_ids': ['issue-1']}],
            'issues': [{'id': 'issue-1', 'title': '새로운 인공지능 연구 발표', 'category': '모델·연구', 'facts': [{'text': '연구팀은 새로운 인공지능 연구 결과를 공개했습니다.', 'article_ids': [a['id']]}], 'article_ids': [a['id']], 'why_it_matters': '새로운 연구의 결과를 실제 적용 환경에서 살펴볼 수 있습니다.'}],
            'articles': [{'id': a['id'], 'title': a['title'], 'published_at': a['published_at'], 'sources': [{**a['sources'][0], 'name': 'OpenAI', 'role': 'official'}]}],
            'reviewed_articles': 1, 'model': 'test/model', 'prompt_version': 'daily-brief-v1', 'input_hash': 'a'*64}


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'data'
        self.runtime = Path(self.temp.name) / 'runtime'
        self.store = Archive(self.root, self.runtime)
        self.addCleanup(self.store.close)

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*.json')}

    def test_migration_preserves_all_records_and_is_idempotent(self):
        first = article('first', '2026-09-30T14:59:59+00:00')
        second = article('second', '2026-09-30T15:00:00+00:00')
        legacy = {'version': 1, 'updated_at': second['published_at'], 'collection_slot': '2026-10-15T21:00:00+00:00', 'sources': [SOURCE], 'articles': [first, second]}
        e = edition(article('brief'))
        atomic_json(self.root/'news.json', legacy)
        atomic_json(self.root/'briefings.json', {'version': 1, 'editions': [e]})
        self.store = Archive(self.root, self.runtime)
        self.assertTrue(self.store.migrate())
        self.assertFalse((self.root/'news.json').exists())
        self.assertFalse((self.root/'briefings.json').exists())
        self.assertEqual(self.store.read('news', '2026-09'), [first])
        self.assertEqual(self.store.read('news', '2026-10'), [second])
        self.assertEqual(self.store.all('briefings'), [e])
        self.assertEqual(self.store.catalog['collection_slot'], legacy['collection_slot'])
        before = self.snapshot()
        self.assertFalse(self.store.migrate())
        self.assertEqual(self.snapshot(), before)

    def test_invalid_migration_does_not_remove_originals(self):
        atomic_json(self.root/'news.json', {'version': 1, 'updated_at': None, 'sources': [SOURCE], 'articles': [article('bad', 'invalid')]})
        self.store = Archive(self.root, self.runtime)
        before = self.snapshot()
        with self.assertRaises((AssertionError, ValueError)):
            self.store.migrate()
        self.assertEqual(self.snapshot(), before)

    def test_kst_boundary_and_month_validation(self):
        self.assertEqual(month_of('2026-09-30T14:59:59+00:00'), '2026-09')
        self.assertEqual(month_of('2026-09-30T15:00:00+00:00'), '2026-10')
        with self.assertRaises(ValueError):
            self.store.save(news={'2026-09': [article('wrong', '2026-09-30T15:00:00+00:00')]}, sources=[SOURCE])
        self.assertEqual(self.snapshot(), {})

    def test_old_month_is_not_rewritten_and_warm_lookup_reads_no_shards(self):
        old = article('old', '2026-09-15T00:00:00+00:00')
        self.store.save(news={'2026-09': [old], '2026-10': [article('current')]}, sources=[SOURCE])
        self.store.index()
        self.store.close()
        path = self.root/'news/2026-09.json'
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        self.store.save(news={'2026-10': [article('current'), article('new')]})
        self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)
        self.store.index()
        self.store.close()
        with patch.object(self.store, 'read', side_effect=AssertionError('Warm lookup loaded an archive')):
            months = self.store.relevant_news_months([], datetime(2026,10,15,tzinfo=UTC), datetime(2026,10,16,tzinfo=UTC))
        self.assertEqual(months, {'2026-10'})

    def test_previous_month_duplicates_keep_original_id_and_bookmark_target(self):
        old = article('old', '2026-09-30T14:00:00+00:00', 'An identical long title about an AI model release')
        self.store.save(news={'2026-09': [old]}, sources=[SOURCE])
        candidate = article('new-url', '2026-10-01T00:00:00+00:00', old['original_title'])
        months = self.store.relevant_news_months([candidate], datetime(2026,10,1,tzinfo=UTC), datetime(2026,10,2,tzinfo=UTC))
        self.assertEqual(months, {'2026-09', '2026-10'})
        merged = merge_candidates([a for m in months for a in self.store.read('news', m)], [candidate])
        self.assertEqual([a['id'] for a in merged], [old['id']])
        self.assertEqual(len(merged[0]['sources']), 2)
        # An old exact URL is found even after its feed publication date changes months later.
        candidate.update(url=old['url'], published_at='2027-01-02T00:00:00+00:00', original_title='Changed title')
        self.assertIn('2026-09', self.store.relevant_news_months([candidate], datetime(2027,1,1,tzinfo=UTC), datetime(2027,1,3,tzinfo=UTC)))

    def test_used_briefing_articles_match_all_months_and_source_aliases(self):
        old = article('old', '2026-09-29T00:00:00+00:00')
        snapshot = edition(old, '2026-09-30')
        alias = 'https://example.com/original?idxno=7'
        snapshot['articles'][0]['sources'].append({**snapshot['articles'][0]['sources'][0], 'url': alias})
        # A snapshot can outlive a news shard; matching must use the archived source links.
        self.store.save(editions={'2026-09': [snapshot]}, sources=[SOURCE])
        same_id = article('old', '2026-11-01T00:00:00+00:00')
        same_id['sources'][0]['url'] = same_id['url'] = 'https://example.com/moved'
        merged = article('different-id', '2026-11-01T00:00:00+00:00')
        merged['sources'].append({**merged['sources'][0], 'url': alias + '&utm_source=rss#section'})
        fresh = article('new', '2026-11-01T00:00:00+00:00')
        self.store.index()
        self.store.close()
        with patch.object(self.store, 'read', side_effect=AssertionError('Warm index should not reread history')):
            self.assertEqual(self.store.previously_briefed_ids([same_id, merged, fresh]), {same_id['id'], merged['id']})
        self.store.close()
        next(self.runtime.glob('archive-index-*.sqlite3')).unlink()
        self.assertEqual(self.store.previously_briefed_ids([merged]), {merged['id']})

    def test_recent_coverage_crosses_months_but_excludes_old_future_and_withdrawn(self):
        def snapshot(day):
            end = datetime.fromisoformat(f'{day}T06:00:00+09:00')
            from datetime import timedelta
            return edition(article(day, (end-timedelta(hours=1)).isoformat()), day)
        withdrawn = snapshot('2026-10-31')
        withdrawn.update(status='withdrawn', articles=[], issues=[], overview=[])
        self.store.save(editions={
            '2026-09': [snapshot('2026-09-30')],
            '2026-10': [snapshot('2026-10-24'), snapshot('2026-10-25'), withdrawn],
            '2026-11': [snapshot('2026-11-01'), snapshot('2026-11-02')]}, sources=[SOURCE])
        with patch.object(self.store, 'read', wraps=self.store.read) as read:
            context = self.store.recent_briefing_issues('2026-11-01')
        self.assertEqual([item['id'] for item in context], ['2026-10-25/issue-1'])
        self.assertNotIn(('briefings', '2026-09'), [call.args for call in read.call_args_list])
        self.assertEqual(context[0]['facts'], ['연구팀은 새로운 인공지능 연구 결과를 공개했습니다.'])

    def test_disposable_index_is_rebuilt_when_missing_or_corrupt(self):
        self.store.save(news={'2026-10': [article('first')]}, sources=[SOURCE])
        self.store.index(); self.store.close()
        path = next(self.runtime.glob('archive-index-*.sqlite3'))
        path.write_bytes(b'corrupt sqlite file')
        self.assertEqual(self.store.index().execute('SELECT COUNT(*) FROM articles').fetchone()[0], 1)
        self.store.close(); path.unlink()
        self.assertEqual(self.store.index().execute('SELECT COUNT(*) FROM articles').fetchone()[0], 1)

    def test_archive_beyond_former_5000_limit_retains_every_article(self):
        september = [article(str(i), '2026-09-20T00:00:00+00:00') for i in range(2600)]
        october = [article(str(i)) for i in range(2600, 5201)]
        self.store.save(news={'2026-09': september, '2026-10': october}, sources=[SOURCE])
        self.store.validate()
        self.assertEqual(self.store.index().execute('SELECT COUNT(*) FROM articles').fetchone()[0], 5201)
        self.assertEqual(len(read_news(self.root)['articles']), 5201)

    def test_old_editorial_changes_find_and_withdraw_past_briefing(self):
        a = article('old')
        self.store.save(news={'2026-10': [a]}, editions={'2026-10': [edition(a)]}, sources=[SOURCE])
        editorial = {'hidden_ids': [a['id']], 'corrections': {}}
        ids, months = self.store.editorial_changes(editorial)
        self.assertEqual(ids, {a['id']})
        self.assertEqual(months['2026-10'][0]['status'], 'withdrawn')
        self.assertEqual(months['2026-10'][0]['articles'], [])
        self.assertIn('2026-10', self.store.relevant_news_months([], datetime(2027,1,1,tzinfo=UTC), datetime(2027,1,2,tzinfo=UTC), ids))
        self.store.save(news={'2026-10': []}, editions=months, editorial=editorial)
        self.assertEqual(self.store.editorial_changes(editorial), (set(), {}))

    def test_failed_multi_file_write_rolls_back_and_removes_new_month(self):
        self.store.save(news={'2026-09': [article('old', '2026-09-01T00:00:00+00:00')]}, sources=[SOURCE])
        before = self.snapshot()
        real = storage.atomic_bytes
        def fail(path, content):
            if Path(path) == self.root/'catalog.json' and b'2026-10' in content:
                raise OSError('Simulated full disk while committing catalog')
            return real(path, content)
        with patch('collector.storage.atomic_bytes', side_effect=fail), self.assertRaises(OSError):
            self.store.save(news={'2026-10': [article('new')]})
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.runtime/'archive-transaction').exists())

    def test_interrupted_transaction_recovers_but_preserves_operator_edits(self):
        self.store.save(news={'2026-10': [article('first')]}, sources=[SOURCE])
        before = (self.root/'news/2026-10.json').read_bytes()
        journal = self.runtime/'archive-transaction'
        atomic_json(journal/'manifest.json', {'root': str(self.root), 'complete': False, 'files': [{'path': 'news/2026-10.json', 'before': digest(before), 'after': digest(b'partial')}]})
        storage.atomic_bytes(journal/'before/news/2026-10.json', before)
        (self.root/'news/2026-10.json').write_bytes(b'operator changes')
        with self.assertRaisesRegex(ValueError, 'edited'):
            Archive(self.root, self.runtime)
        self.assertEqual((self.root/'news/2026-10.json').read_bytes(), b'operator changes')
        (self.root/'news/2026-10.json').write_bytes(b'partial')
        recovered = Archive(self.root, self.runtime)
        self.assertEqual((self.root/'news/2026-10.json').read_bytes(), before)
        recovered.validate(); recovered.close()

    def test_missing_unlisted_and_tampered_months_fail_validation(self):
        self.store.save(news={'2026-10': [article('first')]}, sources=[SOURCE])
        path = self.root/'news/2026-10.json'
        before = path.read_bytes()
        path.unlink()
        with self.assertRaises(ValueError): self.store.validate()
        path.write_bytes(before+b' ')
        with self.assertRaises(ValueError): self.store.validate()
        path.write_bytes(before)
        atomic_json(self.root/'news/2026-09.json', {})
        with self.assertRaises(ValueError): self.store.validate(incremental=True)

    def test_duplicate_ids_across_months_are_rejected(self):
        a = article('same')
        b = {**a, 'published_at': '2026-09-15T00:00:00+00:00'}
        self.store.save(news={'2026-09': [b], '2026-10': [a]}, sources=[SOURCE])
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.store.validate()

    def test_missing_catalog_never_replaces_existing_monthly_data(self):
        self.store.save(news={'2026-10': [article('first')]}, sources=[SOURCE])
        (self.root/'catalog.json').unlink()
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'without their catalog'):
            Archive(self.root, self.runtime)
        self.assertEqual(self.snapshot(), before)
