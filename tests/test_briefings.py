import argparse
import copy
from contextlib import redirect_stdout
from datetime import datetime, timedelta
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collector import briefings
from collector.__main__ import ROOT, run, run_daily
from collector.core import UTC, article_id


def response(inputs):
    return {"headline": "새로운 AI 개발 도구와 연구 발표를 살펴봅니다",
            "overview": [{"text": "여러 출처에서 AI 개발 도구의 새로운 기능과 적용 방법을 소개했습니다.", "issue_indexes": [0]}],
            "issues": [{"title": "AI 개발 도구의 새로운 기능 발표", "category": "개발·도구",
                        "facts": [{"text": "제공된 자료에서는 AI 개발 도구의 새로운 기능을 발표했다고 설명합니다.",
                                   "evidence": [{"article_id": i["article_id"], "quote": "A new AI tool is available."} for i in inputs[:2]]}],
                        "why_it_matters": "개발자는 새로운 도구의 적용 가능성을 원문에서 확인할 수 있습니다."}]}


class BriefingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = argparse.Namespace(data_dir=self.root, runtime_dir=self.root / 'runtime', days=3, no_llm=False, dry_run=False)
        self.sources = json.loads((ROOT / 'config/sources.json').read_text())
        self.now = datetime.now(UTC).replace(microsecond=0)

    def fetch(self, source):
        return [{"url": f'https://example.com/{source["id"]}', "title": f'AI tool announcement by {source["id"]}',
                 "published_at": (briefings.daily_window(self.now)[2] - timedelta(hours=1)).isoformat(), "body": 'A new AI tool is available. ' * 35}], ''

    def run_batch(self, generate=response, **kwargs):
        def call(inputs, *_): return generate(inputs)
        fetch = kwargs.pop('fetch_source', self.fetch)
        with redirect_stdout(io.StringIO()), patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-only', 'OPENROUTER_MODEL': 'test/model', 'MAX_SUMMARIES_PER_RUN': '1'}):
            collection_args = argparse.Namespace(**vars(self.args))
            collection_args.no_llm, collection_args.dry_run = True, False
            with patch('collector.__main__.datetime', wraps=datetime) as clock:
                clock.now.return_value = self.now
                run(collection_args, fetch_source=fetch)
            path = self.root / 'news.json'
            data = json.loads(path.read_text())
            data['collection_slot'] = briefings.daily_window(self.now)[2].isoformat()
            path.write_text(json.dumps(data))
            return run_daily(self.args, fetch_source=fetch, generate_briefing=call, now=self.now)

    def test_edition_synthesizes_multiple_sources_and_reuses_identical_inputs(self):
        first = self.run_batch()
        self.assertEqual(first['llm_requests'], 1)
        self.assertEqual(first['briefing']['result'], 'created')
        path = self.root / 'briefings.json'
        before = path.read_bytes()
        history = json.loads(before)
        briefings.validate_history(history)
        edition = history['editions'][0]
        self.assertEqual(len(edition['issues'][0]['article_ids']), 2)
        self.assertNotIn('source_text', before.decode())
        self.assertNotIn('A new AI tool is available.', before.decode())
        self.assertEqual(edition['day'], briefings.daily_window(self.now)[0])
        second = self.run_batch(generate=lambda _: self.fail('Duplicate paid briefing'))
        self.assertEqual(second['briefing']['result'], 'already_published')
        self.assertEqual(second['llm_requests'], 0)
        self.assertEqual(path.read_bytes(), before)

    def test_new_batch_appends_snapshot_without_rewriting_previous(self):
        self.run_batch()
        path = self.root / 'briefings.json'
        original = json.loads(path.read_text())['editions'][0]
        def changed(source):
            entries, note = self.fetch(source)
            entries[0]['url'] += '/next'
            entries[0]['title'] += ' for the next release'
            return entries, note
        same_day = self.run_batch(fetch_source=changed)
        self.assertEqual(same_day['briefing']['result'], 'already_published')
        self.assertEqual(json.loads(path.read_text())['editions'][0], original)
        self.now += timedelta(days=1)
        def next_day(source):
            entries, note = changed(source)
            entries[0]['url'] += '/tomorrow'
            entries[0]['title'] += ' tomorrow'
            return entries, note
        self.run_batch(fetch_source=next_day)
        history = json.loads(path.read_text())
        self.assertEqual(len(history['editions']), 2)
        self.assertEqual(history['editions'][1], original)
        self.assertNotEqual(history['editions'][0]['id'], original['id'])

    def test_failed_briefing_preserves_history_and_blocks_same_day_retry(self):
        self.run_batch()
        path = self.root / 'briefings.json'
        before = path.read_bytes()
        self.now += timedelta(days=1)
        def changed(source):
            entries, note = self.fetch(source)
            entries[0]['url'] += '/next-day'
            entries[0]['title'] += ' next-day release'
            entries[0]['body'] += ' Additional verified information.'
            return entries, note
        result = self.run_batch(generate=lambda _: (_ for _ in ()).throw(ValueError('bad JSON')), fetch_source=changed)
        self.assertEqual(result['llm_requests'], 1)
        self.assertEqual(result['briefing']['result'], 'failed_previous_preserved')
        self.assertEqual(path.read_bytes(), before)
        result = self.run_batch(generate=lambda _: self.fail('Paid retry during cooldown'), fetch_source=changed)
        self.assertEqual(result['briefing']['result'], 'cooldown')

    def test_unknown_article_fabricated_quote_and_wrong_issue_reference_rejected(self):
        for kind in ('article', 'quote', 'overview'):
            with self.subTest(kind=kind):
                for path in (self.root/'news.json', self.root/'briefings.json', self.root/'runtime/summary-attempts.json'):
                    path.unlink(missing_ok=True)
                def invalid(inputs):
                    value = response(inputs)
                    if kind == 'article': value['issues'][0]['facts'][0]['evidence'][0]['article_id'] = '0' * 20
                    if kind == 'quote': value['issues'][0]['facts'][0]['evidence'][0]['quote'] = 'Invented source claim is not allowed.'
                    if kind == 'overview': value['overview'][0]['issue_indexes'] = [99]
                    return value
                def case_source(source):
                    entries, note = self.fetch(source)
                    entries[0]['body'] += f' Case marker: {kind}.'
                    return entries, note
                result = self.run_batch(generate=invalid, fetch_source=case_source)
                self.assertEqual(result['briefing']['result'], 'failed_previous_preserved')
                self.assertFalse((self.root / 'briefings.json').exists())

    def test_withdrawal_scrubs_entire_historical_snapshot_and_correction_is_explicit(self):
        self.run_batch()
        history = json.loads((self.root / 'briefings.json').read_text())
        aid = history['editions'][0]['articles'][0]['id']
        corrected = briefings.apply_editorial(history, {'hidden_ids': [], 'corrections': {aid: '원문 정정: 발표 날짜를 확인하세요.'}})
        self.assertIn('correction', corrected['editions'][0]['articles'][0])
        self.assertNotIn('correction', history['editions'][0]['articles'][0])
        withdrawn = briefings.apply_editorial(history, {'hidden_ids': [aid], 'corrections': {}})
        briefings.validate_history(withdrawn)
        self.assertEqual(withdrawn['editions'][0]['id'], history['editions'][0]['id'])
        self.assertEqual(withdrawn['editions'][0]['status'], 'withdrawn')
        self.assertEqual(withdrawn['editions'][0]['articles'], [])

    def test_kst_day_and_window_validation(self):
        self.run_batch()
        history = json.loads((self.root / 'briefings.json').read_text())
        edition = history['editions'][0]
        # UTC 21:00 is the next Korean date at 06:00.
        end = datetime(2026, 10, 17, 21, 0, tzinfo=UTC)
        edition.update(id='2026-10-18', window_start=(end - timedelta(hours=24)).isoformat(), window_end=end.isoformat(), scheduled_at=(end+timedelta(minutes=30)).isoformat(), day='2026-10-18')
        for article in edition['articles']:
            article['published_at'] = (end - timedelta(hours=1)).isoformat()
        briefings.validate_history(history)
        edition['day'] = '2026-10-17'
        with self.assertRaises(ValueError): briefings.validate_history(history)

    def test_no_llm_and_dry_run_do_not_generate_or_invent_history(self):
        self.args.no_llm = True
        result = self.run_batch(generate=lambda _: self.fail('no_llm'))
        self.assertEqual(result['llm_requests'], 0)
        self.assertFalse((self.root / 'briefings.json').exists())
        self.args.no_llm, self.args.dry_run = False, True
        result = self.run_batch(generate=lambda _: self.fail('dry_run'))
        self.assertEqual(result['llm_requests'], 0)
        self.assertFalse((self.root / 'briefings.json').exists())

    def test_inputs_exclude_link_only_sources_old_and_future_articles(self):
        articles, evidence = [], {}
        for s in self.sources:
            entries, _ = self.fetch(s)
            for offset in (0, -48, 48):
                entry = entries[0]
                url = entry['url'] + f'/{offset}'
                aid = article_id(url)
                articles.append({'id': aid, 'url': url, 'original_title': entry['title'], 'published_at': (self.now + timedelta(hours=offset, minutes=-1)).isoformat(), 'sources': [{'url': url}]})
                evidence[aid] = (entry['body'], s)
        inputs = briefings.prepare_inputs(articles, evidence, self.now)
        denied = {s['id'] for s in self.sources if not s['summarize']}
        self.assertTrue(inputs)
        self.assertTrue(all(i['source_id'] not in denied for i in inputs))
        self.assertTrue(all(self.now - timedelta(hours=24) <= datetime.fromisoformat(i['published_at']) <= self.now for i in inputs))

    def test_daily_cutoff_and_early_manual_run_have_fixed_days(self):
        before = datetime(2026, 10, 17, 21, 29, tzinfo=UTC)
        after = before + timedelta(minutes=1)
        self.assertEqual(briefings.daily_window(before)[0], '2026-10-17')
        day, start, end, scheduled = briefings.daily_window(after)
        self.assertEqual(day, '2026-10-18')
        self.assertEqual(end-start, timedelta(hours=24))
        self.assertEqual(end.astimezone(briefings.KST).hour, 6)
        self.assertEqual(scheduled, after)
        self.assertEqual(briefings.daily_window(after+timedelta(hours=8))[2], end)

    def test_merged_link_uses_allowed_body_and_exact_window_boundaries(self):
        allowed = next(s for s in self.sources if s['summarize'])
        denied = next(s for s in self.sources if not s['summarize'])
        official_url, discovery_url = 'https://example.com/official', 'https://example.com/discovery'
        aid = article_id(discovery_url)
        article = {'id': aid, 'url': discovery_url, 'original_title': 'AI release',
                   'published_at': (self.now-timedelta(hours=24)).isoformat(),
                   'sources': [{'source_id': denied['id'], 'url': discovery_url},
                               {'source_id': allowed['id'], 'url': official_url}]}
        evidence = {aid: ('Unapproved text. '*100, denied),
                    article_id(official_url): ('Allowed source body. '*40, allowed)}
        inputs = briefings.prepare_inputs([article], evidence, self.now)
        self.assertEqual(len(inputs), 1)  # Start boundary is inclusive.
        self.assertEqual(inputs[0]['source_id'], allowed['id'])
        self.assertNotIn('Unapproved', inputs[0]['source_text'])
        article['published_at'] = self.now.isoformat()
        self.assertEqual(briefings.prepare_inputs([article], evidence, self.now), [])

    def test_scheduled_collection_never_generates_a_briefing(self):
        with redirect_stdout(io.StringIO()), patch.dict(os.environ, {'OPENROUTER_API_KEY':'test-only', 'OPENROUTER_MODEL':'test/model'}):
            result = run(self.args, fetch_source=self.fetch, generate=lambda *_: self.fail('Collection spent LLM budget'))
        self.assertEqual(result['llm_requests'], 0)
        self.assertFalse((self.root/'briefings.json').exists())
        data = json.loads((self.root/'news.json').read_text())
        self.assertEqual(datetime.fromisoformat(data['collection_slot']).astimezone(briefings.KST).hour % 6, 0)

    def test_daily_job_recovers_missing_morning_collection_before_generation(self):
        (self.root/'news.json').write_text(json.dumps({'version':1, 'updated_at':None, 'articles':[], 'sources':[]}))
        with redirect_stdout(io.StringIO()), patch.dict(os.environ, {'OPENROUTER_API_KEY':'test-only', 'OPENROUTER_MODEL':'test/model', 'MAX_SUMMARIES_PER_RUN':'1'}):
            result = run_daily(self.args, fetch_source=self.fetch, generate_briefing=lambda inputs,*_:response(inputs), now=self.now)
        self.assertTrue(result['collection_recovered'])
        self.assertEqual(result['briefing']['result'], 'created')
        self.assertEqual(result['llm_requests'], 1)

    @patch('collector.briefings.request')
    def test_truncated_provider_output_is_not_an_edition(self, request):
        request.return_value = b'{"choices":[{"finish_reason":"length","message":{"content":"{}"}}]}'
        with self.assertRaisesRegex(ValueError, 'output_token_limit'):
            briefings.generate([], 'test-only', 'test/model')


if __name__ == '__main__': unittest.main()
