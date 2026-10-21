import argparse
from contextlib import redirect_stdout
from datetime import datetime, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collector.__main__ import ROOT, run
from collector.article_summaries import is_english
from collector.core import UTC, iso_now, validate_dataset
from collector.storage import read_news


class EnglishSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        config = self.root / 'config'
        config.mkdir()
        self.sources = [s for s in json.loads((ROOT / 'config/sources.json').read_text())
                        if s['id'] in ('techcrunch', 'aitimes', 'openai', 'google_ai')]
        (config / 'sources.json').write_text(json.dumps(self.sources))
        (config / 'editorial.json').write_text('{"hidden_ids":[],"corrections":{}}')
        for mock in (patch('collector.__main__.ROOT', self.root),
                     patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-only', 'OPENROUTER_MODEL': 'test/model',
                                             'MAX_SUMMARIES_PER_RUN': '1', 'ENGLISH_SUMMARIES_PER_RUN': '2'})):
            mock.start()
            self.addCleanup(mock.stop)
        self.args = argparse.Namespace(data_dir=self.root / 'data', runtime_dir=self.root / 'runtime',
                                       days=3, no_llm=False, dry_run=False, summarize_english=True)
        self.stamp = (datetime.now(UTC) - timedelta(hours=1)).replace(microsecond=0).isoformat()
        self.calls = []

    def fetch(self, source):
        body = ('한국의 연구팀은 새로운 인공지능 도구의 기능과 평가 결과를 설명했습니다. ' * 30
                if source['id'] == 'aitimes' else 'The research team has released a new AI tool for developers. ' * 25)
        return [{'url': f'https://example.com/{source["id"]}', 'title': f'New AI tool announced by {source["id"]}',
                 'published_at': self.stamp, 'body': body}], ''

    def generate(self, article, body, key, model):
        self.calls.append((article['id'], article['url']))
        attempts = json.loads((self.args.runtime_dir / 'summary-attempts.json').read_text())
        self.assertIn(article['id'], attempts)  # Durably recorded before sending the request.
        return ({'title': '연구팀의 새로운 AI 개발 도구 발표',
                 'summary': '연구팀이 개발자를 위한 새로운 AI 도구를 발표했습니다. 실제 적용 범위와 제공 기능은 원문에서 확인할 수 있습니다.',
                 'why_it_matters': '개발자는 도구를 도입하기 전에 실제 제공 기능을 확인할 수 있습니다.',
                 'category': '개발·도구'},
                {'model': model, 'prompt_version': 'test-ko', 'generated_at': iso_now(),
                 'input_hash': hashlib.sha256(body.encode()).hexdigest(), 'evidence_verified': True})

    def batch(self, **kwargs):
        with redirect_stdout(io.StringIO()):
            return run(self.args, fetch_source=kwargs.get('fetch_source', self.fetch), generate=kwargs.get('generate', self.generate))

    def test_english_body_gets_korean_title_and_summary_once(self):
        result = self.batch()
        self.assertEqual(result['llm_requests'], 2)
        self.assertEqual(result['article_summaries']['generated'], 2)
        news = read_news(self.args.data_dir)
        validate_dataset(news)
        summarized = [a for a in news['articles'] if a['status'] == 'summarized']
        self.assertEqual({a['sources'][0]['source_id'] for a in summarized}, {'openai', 'google_ai'})
        self.assertTrue(all(a['original_title'].startswith('New AI') and '연구팀' in a['title'] for a in summarized))
        self.assertEqual(result['article_summaries']['skipped'].get('not_english'), 1)
        self.assertEqual(result['article_summaries']['skipped'].get('no_permitted_evidence'), 1)
        again = self.batch()
        self.assertEqual(again['llm_requests'], 0)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(news['articles'], read_news(self.args.data_dir)['articles'])

    def test_pending_articles_are_processed_on_the_next_collection(self):
        with patch.dict(os.environ, {'ENGLISH_SUMMARIES_PER_RUN': '1'}):
            first = self.batch()
            second = self.batch()
        self.assertEqual(first['llm_requests'], 1)
        self.assertGreater(first['article_summaries']['remaining_candidates'], 0)
        self.assertEqual(second['llm_requests'], 1)
        self.assertNotEqual(self.calls[0][0], self.calls[1][0])

    def test_failure_keeps_links_and_cools_down_without_discarding_other_summaries(self):
        def generate(article, *args):
            if article['url'].endswith('/openai'):
                raise RuntimeError('private provider response')
            return self.generate(article, *args)
        result = self.batch(generate=generate)
        self.assertEqual(result['llm_requests'], 2)
        self.assertEqual(result['summary_failures'], 1)
        self.assertEqual(result['article_summaries']['generated'], 1)
        self.assertNotIn('private provider response', json.dumps(result))
        self.assertEqual(len(read_news(self.args.data_dir)['articles']), 4)
        again = self.batch(generate=generate)
        self.assertEqual(again['llm_requests'], 0)
        self.assertEqual(again['article_summaries']['skipped']['cooldown'], 1)

    def test_no_llm_dry_run_and_both_zero_budgets_make_no_paid_calls(self):
        for setting in ('no_llm', 'dry_run', 'MAX_SUMMARIES_PER_RUN', 'ENGLISH_SUMMARIES_PER_RUN'):
            with self.subTest(setting=setting):
                self.args.no_llm = setting == 'no_llm'
                self.args.dry_run = setting == 'dry_run'
                with patch.dict(os.environ, {setting: '0'} if setting.endswith('PER_RUN') else {}):
                    result = self.batch(generate=lambda *_: self.fail('disabled mode made a paid call'))
                self.assertEqual(result['llm_requests'], 0)
                self.assertIn('disabled_reason', result['article_summaries'])
        self.assertFalse((self.args.runtime_dir / 'summary-attempts.json').exists())

    def test_merged_discovery_uses_the_permitted_source_url_and_body(self):
        def fetch(source):
            if source['id'] not in ('techcrunch', 'openai'):
                return [], ''
            entry = self.fetch(source)[0][0]
            entry.update(title='A new AI tool release reported by multiple sources',
                         body='Private disallowed source body. ' * 30 if source['id'] == 'techcrunch' else '')
            return [entry], ''
        with patch('collector.article_summaries.Robots.allows', return_value=True), \
             patch('collector.article_summaries.request', return_value=b'<article><p>' + b'The team has released a new AI tool for developers. ' * 30 + b'</p></article>') as fetch_body:
            result = self.batch(fetch_source=fetch)
        self.assertEqual(result['article_summaries']['generated'], 1)
        self.assertEqual(fetch_body.call_args.args[0], 'https://example.com/openai')
        self.assertEqual(self.calls[0][1], 'https://example.com/openai')
        article = read_news(self.args.data_dir)['articles'][0]
        self.assertEqual(article['sources'][0]['source_id'], 'openai')
        self.assertEqual(len(article['sources']), 2)

    def test_short_text_and_unavailable_robots_rules_are_not_summarized(self):
        def fetch(source):
            entry = self.fetch(source)[0][0]
            entry['body'] = 'A short English title is not enough evidence.'
            return [entry], ''
        with patch('collector.article_summaries.Robots.allows', return_value=False), \
             patch('collector.article_summaries.request') as fetch_body:
            result = self.batch(fetch_source=fetch)
        self.assertEqual(result['llm_requests'], 0)
        self.assertEqual(len(read_news(self.args.data_dir)['articles']), 4)
        fetch_body.assert_not_called()

    def test_pending_article_can_be_summarized_after_leaving_the_feed(self):
        with patch.dict(os.environ, {'ENGLISH_SUMMARIES_PER_RUN': '1'}):
            self.batch()
            with patch('collector.article_summaries.Robots.allows', return_value=True), \
                 patch('collector.article_summaries.request', return_value=b'<article><p>' + b'The team has released a new AI tool for developers. ' * 30 + b'</p></article>'):
                result = self.batch(fetch_source=lambda _: ([], ''))
        self.assertEqual(result['llm_requests'], 1)
        self.assertEqual(result['article_summaries']['generated'], 1)
        self.assertNotEqual(self.calls[0][0], self.calls[1][0])

    def test_language_uses_prose_not_region_or_model_names(self):
        self.assertTrue(is_english('The model is available for developers and researchers. ' * 20))
        self.assertFalse(is_english('OpenAI GPT Claude Gemini 모델의 성능과 평가 조건을 원문에서 확인할 수 있습니다. ' * 20))
        self.assertFalse(is_english('新しい人工知能モデルの性能と評価条件を確認してください。 ' * 20))
        self.assertFalse(is_english('GPT Claude Gemini Transformer Python CUDA TensorFlow ' * 20))
