import argparse
import copy
from contextlib import redirect_stdout
from datetime import datetime, timedelta
import io
import json
import os
from pathlib import Path
import tempfile
import shutil
import unittest
from unittest.mock import patch

from collector.storage import Archive, encoded, read_news, read_history
from collector import briefings
from collector.__main__ import ROOT, run, run_daily
from collector.core import UTC, article_id
from collector.llm import SummaryError


def response(inputs):
    return {"headline": "새 개발 도구를 고르는 기준",
            "takeaway": "새 도구를 도입하기 전에 필요한 기능을 원문에서 확인할 수 있습니다.",
            "overview": [{"text": "여러 출처에서 새로운 AI 개발 도구를 사용할 수 있다고 발표했습니다. 개발자는 도구를 도입하기 전에 각 발표의 원문에서 자신에게 필요한 기능을 제공하는지 확인할 수 있습니다.", "issue_indexes": [0]}],
            "issues": [{"title": "AI 개발 도구의 새로운 기능 발표", "category": "개발·도구",
                        "facts": [{"text": "제공된 자료에서는 AI 개발 도구의 새로운 기능을 발표했다고 설명합니다.",
                                   "evidence": [{"article_id": i["article_id"], "quote": "A new AI tool is available."} for i in inputs[:2]],
                                   "links": [{"text": phrase, "article_id": i["article_id"]} for phrase, i in zip(['AI 개발 도구', '새로운 기능'], inputs[:2])]}],
                        "why_it_matters": "개발자는 새로운 도구의 적용 가능성을 원문에서 확인할 수 있습니다."}],
            "article_summaries": [
                {"article_id": i["article_id"], "title": "AI 개발 도구의 새로운 기능 발표",
                 "summary": "새로운 AI 개발 도구를 사용할 수 있다고 발표했습니다. 제공된 원문에서 도구의 기능과 적용 가능성을 확인할 수 있습니다.",
                 "why_it_matters": "개발자는 새로운 도구의 적용 가능성을 원문에서 확인할 수 있습니다.",
                 "category": "개발·도구", "evidence": ["A new AI tool is available."]}
                for i in inputs[:2]]}


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
        def call(inputs, *_, **kwargs): return generate(inputs)
        fetch = kwargs.pop('fetch_source', self.fetch)
        with redirect_stdout(io.StringIO()), patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-only', 'OPENROUTER_MODEL': 'test/model', 'MAX_SUMMARIES_PER_RUN': '1'}):
            collection_args = argparse.Namespace(**vars(self.args))
            collection_args.no_llm, collection_args.dry_run = True, False
            with patch('collector.__main__.datetime', wraps=datetime) as clock:
                clock.now.return_value = self.now
                run(collection_args, fetch_source=fetch)
            store = Archive(self.root, self.args.runtime_dir)
            store.save(collection_slot=briefings.daily_window(self.now)[2].isoformat())
            store.close()
            return run_daily(self.args, fetch_source=fetch, generate_briefing=call, now=self.now)

    def test_edition_synthesizes_multiple_sources_and_reuses_identical_inputs(self):
        first = self.run_batch()
        self.assertEqual(first['llm_requests'], 1)
        self.assertEqual(first['briefing']['result'], 'created')
        before = encoded(read_history(self.root))
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
        self.assertEqual(encoded(read_history(self.root)), before)
        articles = read_news(self.root)['articles']
        selected = set(edition['issues'][0]['article_ids'])
        self.assertEqual(first['briefing']['articles_summarized'], len(selected))
        self.assertEqual({a['id'] for a in articles if a['status'] == 'summarized'}, selected)
        for article in articles:
            if article['id'] in selected:
                self.assertEqual(article['summary_meta']['briefing_day'], edition['day'])
                self.assertTrue(article['summary_meta']['evidence_verified'])
                self.assertNotIn('evidence', article)
        self.assertNotIn('A new AI tool is available.', json.dumps([a['summary'] for a in articles if a['id'] in selected]))

    def test_invalid_article_summaries_do_not_partially_publish(self):
        for kind in ('missing', 'duplicate', 'unknown', 'evidence'):
            with self.subTest(kind=kind):
                shutil.rmtree(self.root)
                self.root.mkdir()
                def invalid(inputs):
                    value = response(inputs)
                    items = value['article_summaries']
                    if kind == 'missing': items.pop()
                    if kind == 'duplicate': items[1] = copy.deepcopy(items[0])
                    if kind == 'unknown': items[1]['article_id'] = '0' * 20
                    if kind == 'evidence': items[1]['evidence'] = ['An invented statement from another source.']
                    return value
                result = self.run_batch(generate=invalid)
                self.assertEqual(result['briefing']['result'], 'failed_previous_preserved')
                self.assertEqual(result['briefing']['stage'], 'article_summary_validation')
                self.assertEqual(result['briefing']['reason'], 'validation_failed')
                self.assertTrue(result['briefing']['validation_error'])
                self.assertFalse(read_history(self.root)['editions'])
                self.assertTrue(all(a['status'] == 'link' for a in read_news(self.root)['articles']))

    def test_article_summary_cannot_borrow_another_articles_evidence(self):
        def different_sources(source):
            entries, note = self.fetch(source)
            entries[0]['body'] += f' This statement belongs only to {source["id"]}.'
            return entries, note
        def wrong_source(inputs):
            value = response(inputs)
            value['article_summaries'][0]['evidence'] = [f'This statement belongs only to {inputs[1]["source_id"]}.']
            return value
        result = self.run_batch(generate=wrong_source, fetch_source=different_sources)
        self.assertEqual(result['briefing']['stage'], 'article_summary_validation')
        self.assertEqual(result['briefing']['result'], 'failed_previous_preserved')

    def test_existing_article_summary_and_other_dates_are_preserved(self):
        self.now = datetime(2026, 10, 20, 0, 0, tzinfo=UTC)
        selected = []
        def fail_after_selection(inputs):
            selected.append(inputs[0]['article_id'])
            raise SummaryError('output_token_limit')
        self.run_batch(generate=fail_after_selection)
        store = Archive(self.root, self.args.runtime_dir)
        month = next(iter(store.catalog['news']))
        items = store.read('news', month)
        chosen = next(a for a in items if a['id'] == selected[0])
        chosen.update(status='summarized', title='기존 검증된 기사 제목',
                      summary='이 기사의 요약은 이전 실행에서 생성됐습니다. 기존 검증된 내용을 유지해야 합니다.',
                      why_it_matters='이전 실행에서 검증을 마친 요약을 계속 읽을 수 있습니다.',
                      summary_meta={'model':'old/model', 'prompt_version':'old',
                                    'generated_at':self.now.isoformat(), 'input_hash':'a'*64,
                                    'evidence_verified':True})
        older = copy.deepcopy(items[-1])
        older.update(id=article_id('https://example.com/older'), url='https://example.com/older',
                     published_at=(briefings.daily_window(self.now)[2]-timedelta(days=2)).isoformat())
        older['sources'] = [{**older['sources'][0], 'url': older['url']}]
        items.append(older)
        store.save(news={month: items})
        store.close()
        saved = copy.deepcopy(chosen)
        self.args.briefing_date = briefings.daily_window(self.now)[0]
        self.args.retry_failed_briefing = True
        self.run_batch()
        actual = {a['id']:a for a in read_news(self.root)['articles']}
        self.assertEqual(actual[saved['id']], saved)
        self.assertEqual(actual[older['id']], older)

    def test_new_batch_appends_snapshot_without_rewriting_previous(self):
        self.run_batch()
        original = read_history(self.root)['editions'][0]
        def changed(source):
            entries, note = self.fetch(source)
            entries[0]['url'] += '/next'
            entries[0]['title'] += ' for the next release'
            return entries, note
        same_day = self.run_batch(fetch_source=changed)
        self.assertEqual(same_day['briefing']['result'], 'already_published')
        self.assertEqual(read_history(self.root)['editions'][0], original)
        self.now += timedelta(days=1)
        def next_day(source):
            entries, note = changed(source)
            entries[0]['url'] += '/tomorrow'
            entries[0]['title'] += ' tomorrow'
            return entries, note
        self.run_batch(fetch_source=next_day)
        history = read_history(self.root)
        self.assertEqual(len(history['editions']), 2)
        self.assertEqual(history['editions'][1], original)
        self.assertNotEqual(history['editions'][0]['id'], original['id'])

    def test_failed_briefing_preserves_history_and_blocks_same_day_retry(self):
        self.run_batch()
        before = encoded(read_history(self.root))
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
        self.assertEqual(encoded(read_history(self.root)), before)
        result = self.run_batch(generate=lambda _: self.fail('Paid retry during cooldown'), fetch_source=changed)
        self.assertEqual(result['briefing']['result'], 'cooldown')

    def test_targeted_retry_preserves_other_cooldowns_and_published_editions(self):
        self.run_batch(generate=lambda _: (_ for _ in ()).throw(SummaryError('output_token_limit')))
        path = self.args.runtime_dir / 'summary-attempts.json'
        attempts = json.loads(path.read_text())
        attempts['daily:2020-01-01'] = '2020-01-01T00:00:00+00:00'
        attempts['article-test-id'] = self.now.isoformat()
        path.write_text(json.dumps(attempts))
        self.args.briefing_date = briefings.daily_window(self.now)[0]
        self.args.retry_failed_briefing = True
        retried = self.run_batch()
        self.assertEqual(retried['briefing']['result'], 'created')
        self.assertEqual(retried['llm_requests'], 1)
        self.assertIn('previous_attempt_at', retried['briefing'])
        after = json.loads(path.read_text())
        self.assertEqual(after['daily:2020-01-01'], attempts['daily:2020-01-01'])
        self.assertEqual(after['article-test-id'], attempts['article-test-id'])
        again = self.run_batch(generate=lambda _: self.fail('Published edition overwritten by retry'))
        self.assertEqual(again['briefing']['result'], 'already_published')
        self.assertEqual(again['llm_requests'], 0)

    def test_retry_still_respects_disable_dry_run_and_future_date(self):
        self.run_batch(generate=lambda _: (_ for _ in ()).throw(SummaryError('output_token_limit')))
        path = self.args.runtime_dir / 'summary-attempts.json'
        before = path.read_bytes()
        self.args.briefing_date = briefings.daily_window(self.now)[0]
        self.args.retry_failed_briefing = True
        for flag, expected in [('no_llm', 'disabled'), ('dry_run', 'dry_run')]:
            setattr(self.args, flag, True)
            result = self.run_batch(generate=lambda _: self.fail('Disabled retry spent money'))
            self.assertEqual(result['briefing']['result'], expected)
            self.assertEqual(path.read_bytes(), before)
            setattr(self.args, flag, False)
        with patch.dict(os.environ, {'MAX_SUMMARIES_PER_RUN':'0'}):
            result = run_daily(self.args, now=self.now, generate_briefing=lambda *_: self.fail('Zero budget retry'))
        self.assertEqual(result['briefing']['result'], 'disabled')
        self.args.briefing_date = None
        with self.assertRaisesRegex(ValueError, 'explicit'):
            run_daily(self.args, now=self.now)
        self.args.briefing_date = (self.now + timedelta(days=2)).date().isoformat()
        with self.assertRaisesRegex(ValueError, 'scheduled time'):
            run_daily(self.args, now=self.now)

    def test_explicit_past_date_keeps_its_fixed_window(self):
        now = datetime(2026, 10, 22, 0, 0, tzinfo=UTC)
        day, start, end, scheduled = briefings.daily_window(now, '2026-10-20')
        self.assertEqual(day, '2026-10-20')
        self.assertEqual(end.isoformat(), '2026-10-19T21:00:00+00:00')
        self.assertEqual(end-start, timedelta(days=1))
        self.assertEqual(scheduled-end, timedelta(minutes=30))
        for invalid in ['2026-02-30', '20261020', 'bad;date']:
            with self.assertRaises(ValueError):
                briefings.daily_window(now, invalid)

    def test_unknown_article_fabricated_quote_and_wrong_issue_reference_rejected(self):
        for kind in ('article', 'quote', 'overview'):
            with self.subTest(kind=kind):
                shutil.rmtree(self.root)
                self.root.mkdir()
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
                self.assertFalse(bool(read_history(self.root)['editions']))

    def test_withdrawal_scrubs_entire_historical_snapshot_and_correction_is_explicit(self):
        self.run_batch()
        history = read_history(self.root)
        aid = history['editions'][0]['articles'][0]['id']
        corrected = briefings.apply_editorial(history, {'hidden_ids': [], 'corrections': {aid: '원문 정정: 발표 날짜를 확인하세요.'}})
        self.assertIn('correction', corrected['editions'][0]['articles'][0])
        self.assertNotIn('correction', history['editions'][0]['articles'][0])
        withdrawn = briefings.apply_editorial(history, {'hidden_ids': [aid], 'corrections': {}})
        briefings.validate_history(withdrawn)
        self.assertEqual(withdrawn['editions'][0]['id'], history['editions'][0]['id'])
        self.assertEqual(withdrawn['editions'][0]['status'], 'withdrawn')
        self.assertEqual(withdrawn['editions'][0]['articles'], [])
        self.assertNotIn('takeaway', withdrawn['editions'][0])

    def test_kst_day_and_window_validation(self):
        self.run_batch()
        history = read_history(self.root)
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
        self.assertFalse(bool(read_history(self.root)['editions']))
        self.args.no_llm, self.args.dry_run = False, True
        result = self.run_batch(generate=lambda _: self.fail('dry_run'))
        self.assertEqual(result['llm_requests'], 0)
        self.assertFalse(bool(read_history(self.root)['editions']))

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

    def test_headline_lists_and_single_paragraph_roundups_are_rejected(self):
        paragraph = {'text': response([])['overview'][0]['text'], 'issue_ids': ['issue-1']}
        title = '새 AI 개발 도구의 적용 가능성을 살펴봅니다'
        briefings.validate_narrative(title, [paragraph], 1)
        briefings.validate_narrative(title, [paragraph, {**paragraph, 'issue_ids': ['issue-2']}], 2)
        for headline, paragraphs, count in [
            ('모델 공개·AI 코딩 혁신…기업 보안 이슈 동시 부상', [paragraph], 1),
            (title, [paragraph], 5),
            (title, [{**paragraph, 'text': 'AI 도구 출시와 기업 보안 논의 등 새로운 기술의 활용 범위를 넓히고 결과를 검증하려는 다양한 시도가 여러 분야에서 함께 주목받으며 시장의 새로운 흐름으로 부상'}], 1),
            (title, [{**paragraph, 'issue_ids': ['issue-1', 'issue-2', 'issue-3', 'issue-4']}, paragraph], 4),
        ]:
            with self.subTest(headline=headline, count=count), self.assertRaises(ValueError):
                briefings.validate_narrative(headline, paragraphs, count)

    def test_v1_history_remains_readable_and_overview_revisions_are_validated(self):
        self.run_batch()
        history = read_history(self.root)
        e = history['editions'][0]
        original_overview = copy.deepcopy(e['overview'])
        e.pop('takeaway')
        for issue in e['issues']:
            for fact in issue['facts']: fact.pop('links', None)
        e.update(prompt_version='daily-brief-v1', headline='모델 발표·코딩 도구…새로운 소식 모음',
                 overview=[{'text': '여러 AI 도구와 기업의 발표가 함께 주요 소식으로 전해졌습니다.', 'issue_ids': ['issue-1']}])
        briefings.validate_history(history)
        e.update(headline='새로운 AI 개발 도구의 적용 가능성을 살펴봅니다', overview=original_overview,
                 overview_revision={'revised_at': e['generated_at'], 'note': '상단 요약을 설명하는 문단으로 다듬었습니다.'})
        briefings.validate_history(history)
        e['overview_revision']['revised_at'] = e['window_start']
        with self.assertRaises(ValueError): briefings.validate_history(history)

    def test_title_and_takeaway_are_distinct_and_required_for_new_editions(self):
        self.run_batch()
        history = read_history(self.root)
        e = history['editions'][0]
        self.assertEqual(e['headline'], response([])['headline'])
        for takeaway in [None, e['headline'], '한 문장입니다. 이어지는 두 번째 문장도 있습니다.', '짧은 한 줄']:
            with self.subTest(takeaway=takeaway), self.assertRaises(ValueError):
                e['takeaway'] = takeaway
                briefings.validate_history(history)

    def test_bad_model_link_annotations_do_not_discard_grounded_facts(self):
        def generated(inputs):
            value = response(inputs)
            fact = value['issues'][0]['facts'][0]
            fact['links'] += [
                {'text': '발표했다고', 'article_id': '0' * 20},
                {'text': '존재하지 않는 구절', 'article_id': inputs[0]['article_id']},
                {'text': '설명합니다', 'article_id': inputs[0]['article_id'], 'url': 'https://untrusted.example'},
            ]
            return value
        result = self.run_batch(generate=generated)
        self.assertEqual(result['briefing']['result'], 'created')
        history = read_history(self.root)
        fact = history['editions'][0]['issues'][0]['facts'][0]
        self.assertEqual([link['text'] for link in fact['links']], ['AI 개발 도구', '새로운 기능'])
        self.assertEqual([link['article_id'] for link in fact['links']], fact['article_ids'])
        briefings.validate_history(history)
        fact['links'][0]['article_id'] = '0' * 20
        with self.assertRaises(ValueError): briefings.validate_history(history)

    def test_keyword_links_are_short_unambiguous_nonoverlapping_and_grounded(self):
        aid = 'a' * 20
        def link(text, **extra): return {'text': text, 'article_id': aid, **extra}
        text = '새 AI 개발 도구가 코드 작성과 검토를 지원한다고 발표했습니다.'
        briefings.validate_keyword_links(text, [link('AI 개발 도구'), link('검토')], [aid])
        for content, links in [
            (text, [link(text)]),
            (text, [link('없는 단어')]),
            (text, [link('AI 개발 도구', article_id='b' * 20)]),
            (text, [link('AI 개발 도구'), link('개발 도구')]),
            (text, [link('AI 개발 도구', url='https://untrusted.example')]),
            (text, [link('AI'), link('개발'), link('도구'), link('검토')]),
            ('AI 도구와 AI 도구가 서로 다른 결과를 보여줬습니다.', [link('AI 도구')]),
            ('aaa라는 반복 문자열을 검사하는 예제입니다.', [link('aa')]),
            ('새로운 모델이 출시됐습니다.', [link('새로운 모델이 출시')]),
        ]:
            with self.subTest(content=content, links=links), self.assertRaises(ValueError):
                briefings.validate_keyword_links(content, links, [aid])
        self.assertEqual(briefings.clean_keyword_links(text, [link('없는 단어')], [aid]), [])

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
        self.assertFalse(bool(read_history(self.root)['editions']))
        data = read_news(self.root)
        self.assertEqual(datetime.fromisoformat(data['collection_slot']).astimezone(briefings.KST).hour % 6, 0)

    def test_daily_job_recovers_missing_morning_collection_before_generation(self):
        with redirect_stdout(io.StringIO()), patch.dict(os.environ, {'OPENROUTER_API_KEY':'test-only', 'OPENROUTER_MODEL':'test/model', 'MAX_SUMMARIES_PER_RUN':'1'}):
            result = run_daily(self.args, fetch_source=self.fetch, generate_briefing=lambda inputs,*_,**kwargs:response(inputs), now=self.now)
        self.assertTrue(result['collection_recovered'])
        self.assertEqual(result['briefing']['result'], 'created')
        self.assertEqual(result['llm_requests'], 1)

    def test_first_day_briefing_reads_both_news_months(self):
        self.now = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)
        def across_months(source):
            entries, note = self.fetch(source)
            # The fixed 24h window includes Oct 31 KST and Nov 1 KST.
            entries[0]['published_at'] = '2026-10-31T10:00:00+00:00' if source['id'] == 'openai' else '2026-10-31T20:00:00+00:00'
            return entries, note
        seen = []
        def generated(inputs):
            seen.extend(i['published_at'] for i in inputs)
            return response(inputs)
        result = self.run_batch(generate=generated, fetch_source=across_months)
        self.assertEqual(result['briefing']['result'], 'created')
        self.assertIn('2026-10-31T10:00:00+00:00', seen)
        self.assertIn('2026-10-31T20:00:00+00:00', seen)
        self.assertTrue((self.root/'news/2026-10.json').exists())
        self.assertTrue((self.root/'news/2026-11.json').exists())
        self.assertTrue((self.root/'briefings/2026-11.json').exists())
        articles = read_news(self.root)['articles']
        edition = read_history(self.root)['editions'][0]
        selected = {a['id'] for a in edition['articles']}
        self.assertEqual({a['id'] for a in articles if a['status'] == 'summarized'}, selected)
        store = Archive(self.root, self.args.runtime_dir).validate()
        store.close()

    @patch('collector.briefings.request')
    def test_truncated_provider_output_is_not_an_edition(self, request):
        request.return_value = json.dumps({
            'id':'gen-test', 'model':'test/provider-model',
            'choices':[{'finish_reason':'length','message':{'content':'private partial text','reasoning':'private reasoning'}}],
            'usage':{'prompt_tokens':1200,'completion_tokens':18432,'total_tokens':19632,
                     'completion_tokens_details':{'reasoning_tokens':9000},'cost':0.01,'private':'do not retain'}
        }).encode()
        diagnostics = {}
        with self.assertRaisesRegex(ValueError, 'output_token_limit'):
            briefings.generate([], 'test-only', 'test/model', diagnostics=diagnostics)
        self.assertEqual(diagnostics['finish_reason'], 'length')
        self.assertEqual(diagnostics['usage']['reasoning_tokens'], 9000)
        self.assertEqual(diagnostics['response_model'], 'test/provider-model')
        self.assertNotIn('private', json.dumps(diagnostics))
        self.assertNotIn('test-only', json.dumps(diagnostics))
        self.assertEqual(request.call_count, 1)

    @patch('collector.briefings.request')
    def test_provider_schema_bounds_issues_and_known_article_ids(self, request):
        request.return_value = b'{"choices":[{"finish_reason":"stop","message":{"content":"{}"}}]}'
        briefings.generate([{'article_id':'a'*20}, {'article_id':'b'*20}], 'test-only', 'test/model')
        payload = json.loads(request.call_args.kwargs['payload'])
        response_format = payload['response_format']
        self.assertEqual(response_format['type'], 'json_schema')
        self.assertTrue(response_format['json_schema']['strict'])
        schema = response_format['json_schema']['schema']
        fields = schema['properties']
        self.assertEqual(fields['issues']['maxItems'], 5)
        self.assertEqual(fields['overview']['maxItems'], 3)
        sentences = fields['overview']['items']['properties']['sentences']
        self.assertEqual((sentences['minItems'], sentences['maxItems']), (2, 3))
        self.assertEqual(fields['article_summaries']['items']['properties']['article_id']['enum'], ['a'*20, 'b'*20])
        self.assertIn('article_summaries', schema['required'])
        self.assertFalse(schema['additionalProperties'])
        self.assertTrue(payload['provider']['require_parameters'])

    @patch('collector.briefings.request')
    def test_deepseek_budget_is_reserved_for_visible_summaries(self, request):
        request.return_value = b'{"choices":[{"finish_reason":"stop","message":{"content":"{}"}}]}'
        diagnostics = {}
        with patch.dict(os.environ, {'BRIEFING_MAX_TOKENS':'18432'}):
            briefings.generate([], 'test-only', 'deepseek/deepseek-v4.1-flash', diagnostics=diagnostics)
        payload = json.loads(request.call_args.kwargs['payload'])
        self.assertEqual(payload['reasoning'], {'effort':'none'})
        self.assertEqual(payload['max_tokens'], 18432)
        self.assertEqual(diagnostics['reasoning'], {'effort':'none'})
        self.assertEqual(request.call_count, 1)

    def test_structured_sentences_normalize_spacing_and_keep_all_facts(self):
        def generated(inputs):
            value = response(inputs)
            text = value['overview'][0].pop('text')
            value['overview'][0]['sentences'] = text.rstrip('.').split('. ')
            value['overview'][0]['sentences'][0] = value['overview'][0]['sentences'][0].replace('새로운 AI', '새로운\nAI')
            return value
        result = self.run_batch(generate=generated)
        self.assertEqual(result['briefing']['result'], 'created')
        edition = read_history(self.root)['editions'][0]
        self.assertEqual(edition['prompt_version'], 'daily-brief-v5')
        self.assertEqual(edition['overview'][0]['text'], response([])['overview'][0]['text'])

    def test_prose_handles_abbreviations_versions_quotes_and_rejects_fragments(self):
        paragraph = {'text': '미국 U.S. 연구팀은 모델 4.1의 평가 결과를 새로운 보고서에 공개했습니다. 개발자는 원문에 적힌 평가 조건과 사용 환경을 함께 확인해야 합니다.', 'issue_ids': ['issue-1']}
        briefings.validate_narrative('새 도구의 평가 조건을 확인합니다', [paragraph], 1)
        self.assertEqual(briefings.prose_shape(paragraph['text'])['sentence_count'], 2)
        quoted = paragraph['text'].replace('공개했습니다.', '공개했습니다。”')
        self.assertEqual(briefings.prose_shape(quoted)['sentence_count'], 2)
        briefings.validate_narrative('새 도구의 평가 조건', [paragraph], 1,
                                     '미국 U.S. 연구팀의 모델 4.1 평가 조건을 함께 확인해야 합니다.')
        parts = ['연구팀은 새로운 도구의 평가 결과를 공개했습니다”',
                 '개발자는 원문의 평가 조건과 적용 범위를 함께 확인해야 합니다']
        normalized = briefings.overview_text({'sentences': parts}, 0)
        self.assertEqual(briefings.prose_shape(normalized)['sentence_count'], 2)
        for text in [
            '새로운 AI 모델 공개와 도구의 사용 확대, 다양한 기업의 연구 결과 발표를 통해 확인하는 성능 향상과 검증 방법의 변화 및 관련 기술의 최신 동향 정리.',
            paragraph['text'] + ' 아직 다른 조건의 결과는 확인되지 않았습니다. 사용 시 주의해야 합니다.',
            paragraph['text'].replace('공개했습니다.', '공개했습니다…'),
        ]:
            with self.subTest(text=text), self.assertRaises(briefings.BriefingValidationError) as error:
                briefings.validate_narrative('새 도구의 평가 조건을 확인합니다', [{**paragraph, 'text': text}], 1)
            self.assertEqual(error.exception.details['field'], 'overview[0].text')

    def test_invalid_sentence_shape_reports_only_safe_metrics(self):
        private_text = 'PRIVATE_RESPONSE 원문에 없는 제목 형태의 표현과 나열을 반복하는 구절 ' * 2
        def generated(inputs):
            value = response(inputs)
            value['overview'][0]['text'] = private_text
            return value
        result = self.run_batch(generate=generated)
        self.assertEqual(result['briefing']['result'], 'failed_previous_preserved')
        details = result['briefing']['validation_details']
        self.assertEqual(details['field'], 'overview[0].text')
        self.assertEqual(details['sentence_count'], 1)
        self.assertFalse(details['complete_endings'])
        self.assertNotIn('PRIVATE_RESPONSE', json.dumps(result))
        self.assertFalse(read_history(self.root)['editions'])

    def test_sentence_array_does_not_accept_lists_or_multiple_sentences_per_item(self):
        for parts in [[], ['첫 번째 문장을 완결된 형태로 설명합니다.'],
                      ['첫 번째 문장을 완결된 형태로 설명합니다.'] * 4,
                      ['새 도구의 발표와 새로운 모델 출시 및 사용 범위 확대', '추가 사항을 원문에서 확인할 수 있습니다.'],
                      ['새로운 도구가 공개됐습니다. 자세한 정보도 있습니다.', '추가 사항을 원문에서 확인할 수 있습니다.']]:
            with self.subTest(parts=parts), self.assertRaises(briefings.BriefingValidationError):
                briefings.overview_text({'sentences': parts}, 0)

    @patch('collector.briefings.request')
    def test_generation_budget_and_safe_diagnostics_on_invalid_json(self, request):
        request.return_value = b'{"choices":[{"finish_reason":"stop","message":{"content":"not JSON"}}],"usage":{"completion_tokens":12}}'
        for budget in ('18432', '24576'):
            diagnostics = {}
            with patch.dict(os.environ, {'BRIEFING_MAX_TOKENS':budget}), self.assertRaisesRegex(ValueError, 'invalid_response_json'):
                briefings.generate([], 'test-only', 'z-ai/glm-5.3-flash', diagnostics=diagnostics)
            payload = json.loads(request.call_args.kwargs['payload'])
            self.assertEqual(payload['max_tokens'], int(budget))
            self.assertEqual(payload['reasoning'], {'effort':'low'})
            self.assertEqual(request.call_args.kwargs['timeout'], 180)
            self.assertEqual(diagnostics['usage']['completion_tokens'], 12)
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(briefings.max_tokens(), 6144 * 3)
        request.reset_mock()
        for invalid in ('0', 'abc', '999999'):
            with patch.dict(os.environ, {'BRIEFING_MAX_TOKENS':invalid}), self.assertRaises(ValueError):
                briefings.generate([], 'test-only', 'test/model')
        request.assert_not_called()

    @patch('collector.briefings.request')
    def test_failure_reason_and_usage_reach_run_report_and_console(self, request):
        request.return_value = b'{"choices":[{"finish_reason":"length"}],"usage":{"completion_tokens":18432}}'
        self.run_batch(generate=lambda _: (_ for _ in ()).throw(SummaryError('output_token_limit')))
        self.args.briefing_date = briefings.daily_window(self.now)[0]
        self.args.retry_failed_briefing = True
        output = io.StringIO()
        with redirect_stdout(output), patch.dict(os.environ, {'OPENROUTER_API_KEY':'secret-test', 'OPENROUTER_MODEL':'test/model', 'MAX_SUMMARIES_PER_RUN':'1', 'GITHUB_ACTIONS':'true'}):
            result = run_daily(self.args, fetch_source=self.fetch, now=self.now)
        report = json.loads((self.args.runtime_dir / 'last-run.json').read_text())
        self.assertEqual(report, result)
        self.assertEqual(report['llm']['usage']['completion_tokens'], 18432)
        self.assertIn('output_token_limit', output.getvalue())
        self.assertIn('::error title=Daily briefing failed::', output.getvalue())
        self.assertIn('18432', output.getvalue())
        self.assertNotIn('secret-test', output.getvalue())
        self.assertEqual(request.call_count, 1)


if __name__ == '__main__': unittest.main()
