import argparse
from contextlib import redirect_stdout
from datetime import datetime, timedelta
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collector.__main__ import ROOT, run
from collector.adapters import parse_rss, parse_anthropic, extract_body
from collector.core import UTC, article_id, canonical_url, is_ai_relevant, merge_candidates, parse_date, validate_summary, validate_dataset
from collector.llm import SummaryError, summarize
from collector.network import ensure_allowed, Robots


class SourceTests(unittest.TestCase):
    def test_rss_korean_naive_time_and_query_id(self):
        rss = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>News</title><item><title>AI announcement</title><link>https://example.com/news?idxno=42&amp;utm_source=rss</link><pubDate>2026-10-16 14:33:18</pubDate><description><![CDATA[<p>Source &amp; context</p>]]></description></item></channel></rss>'''
        items = parse_rss(rss, {"timezone": "Asia/Seoul"})
        self.assertEqual(items[0]["published_at"], "2026-10-16T05:33:18+00:00")
        self.assertEqual(items[0]["url"], "https://example.com/news?idxno=42")
        self.assertEqual(items[0]["body"], "Source & context")

    def test_atom_body_is_used_without_loading_app_shell(self):
        atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"><title>Test</title><entry><title>AI</title><link href="https://example.com/ai"/><updated>2026-10-15T15:00:00+09:00</updated><content type="html">&lt;p&gt;Full article body&lt;/p&gt;</content></entry></feed>'''
        item = parse_rss(atom, {"timezone": "UTC"})[0]
        self.assertEqual(item["body"], "Full article body")
        self.assertEqual(item["published_at"], "2026-10-15T06:00:00+00:00")

    def test_anthropic_official_listing_adapter_and_layout_failure(self):
        html = b'<a href="/news/test"><time>Oct 15, 2026</time><span class="PublicationList__title">New announcement</span></a>'
        item = parse_anthropic(html, {"homepage": "https://www.anthropic.com/news"})[0]
        self.assertEqual(item["title"], "New announcement")
        self.assertEqual(item["published_at"], "2026-10-15T00:00:00+00:00")
        with self.assertRaises(ValueError):
            parse_anthropic(b'<html>new app shell</html>', {"homepage": "https://www.anthropic.com/news"})

    def test_paywall_and_non_article_content_are_not_extracted(self):
        self.assertEqual(extract_body(b'<script type="application/ld+json">{"isAccessibleForFree":false}</script><article>Hidden paid text</article>'), "")
        self.assertEqual(extract_body(b'<nav>Menu</nav><footer>Copyright</footer>'), "")

    def test_article_body_selector_takes_priority_over_header_article(self):
        html = b'<article>Header</article><div id="article-view-content-div"><p>Actual source article</p></div>'
        self.assertEqual(extract_body(html), 'Actual source article')

    def test_invalid_dates_and_unsafe_urls(self):
        self.assertIsNone(parse_date("tomorrow afternoon"))
        for url in ('javascript:alert(1)', 'file:///etc/passwd', 'https://name:secret@example.com/a'):
            with self.assertRaises(ValueError):
                canonical_url(url)
        for url in ('https://127.0.0.1/a', 'https://example.com:8443/a', 'http://example.com/a', 'https://example.com.evil/a'):
            with self.assertRaises(ValueError):
                ensure_allowed(url, {'example.com'})
        self.assertNotEqual(article_id('https://example.com/?idxno=1'), article_id('https://example.com/?idxno=2'))

    @patch('collector.network.request', return_value=b'User-agent: *\nAllow: /\nUser-agent: GPTBot\nDisallow: /\n')
    def test_explicit_ai_crawler_exclusion_is_respected(self, _request):
        self.assertFalse(Robots().allows('https://example.com/article', {'example.com'}))


def fixture_article(url, title='AI model launch with a sufficiently descriptive title', source='openai'):
    return {'id': article_id(url), 'url': url, 'title': title, 'original_title': title, 'published_at': '2026-10-15T00:00:00+00:00',
            'collected_at': '2026-10-16T00:00:00+00:00', 'category': '모델·연구', 'region': 'global', 'status': 'link', 'excerpt': '',
            'sources': [{'source_id': source, 'title': title, 'url': url}]}


class CurationTests(unittest.TestCase):
    def test_generated_image_credit_does_not_make_local_event_ai_news(self):
        self.assertFalse(is_ai_relevant('광양 채용박람회', 'AI 생성 영상광양시에서 구인기업과 구직자가 참여했다.'))
        self.assertTrue(is_ai_relevant('오픈AI, 챗GPT 업데이트', '새로운 음성 기능을 발표했다.'))
        self.assertTrue(is_ai_relevant('AI가 만든 개발 도구', '새로운 API 기능.'))

    def test_merge_preserves_published_ids_and_summaries(self):
        old = fixture_article('https://example.com/a')
        old['summary'] = 'Already generated'
        new = fixture_article('https://example.com/b', source='google_ai')
        merged = merge_candidates([old], [new])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]['id'], old['id'])
        self.assertEqual(merged[0]['summary'], 'Already generated')
        self.assertEqual(len(merged[0]['sources']), 2)
        self.assertEqual(merge_candidates(merged, [new]), merged)

    def test_different_model_versions_are_not_fuzzy_merged(self):
        a = fixture_article('https://example.com/a', 'An announcement of AI model version 4')
        b = fixture_article('https://example.com/b', 'An announcement of AI model version 5')
        self.assertEqual(len(merge_candidates([a], [b])), 2)

    def test_summary_rejects_missing_grounding_and_extra_fields(self):
        result = {'title': '새로운 모델에 대한 발표', 'summary': '회사가 새로운 언어 모델을 발표했습니다. 발표문에는 개발자를 위한 기능 변경이 설명되어 있습니다.',
                  'why_it_matters': '개발자는 기존 도구와의 호환성을 확인할 필요가 있습니다.', 'category': '모델·연구', 'evidence': ['A new model is available.']}
        self.assertEqual(validate_summary(result, 'Today: A new model is available.'), result)
        with self.assertRaises(ValueError):
            validate_summary(result, 'Unrelated source text')
        with self.assertRaises(ValueError):
            validate_summary({**result, 'url': 'https://invented.example'}, 'A new model is available.')

    @patch('collector.llm.request')
    def test_openrouter_request_and_response_contract(self, request):
        body = 'A new model is available. ' * 40
        result = {'title': '새로운 모델에 대한 발표', 'summary': '회사가 새로운 언어 모델을 발표했습니다. 발표문에는 새로운 모델을 이용할 수 있다는 내용이 포함되어 있습니다.',
                  'why_it_matters': '개발자는 새 모델의 실제 적용 가능성을 검토할 수 있습니다.', 'category': '모델·연구', 'evidence': ['A new model is available.']}
        request.return_value = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(result)}}]}).encode()
        value, meta = summarize(fixture_article('https://example.com/a'), body, 'test-only-not-a-key', 'test/model')
        sent = json.loads(request.call_args.kwargs['payload'])
        self.assertEqual(sent['model'], 'test/model')
        self.assertEqual(sent['max_tokens'], 1600)
        self.assertNotIn('reasoning', sent)
        self.assertTrue(sent['provider']['require_parameters'])
        self.assertEqual(value['title'], result['title'])
        self.assertTrue(meta['evidence_verified'])
        # A real GLM response spent 1,598/1,600 tokens on reasoning and returned
        # an empty content. It must not be mistaken for a successful summary.
        request.return_value = b'{"choices":[{"finish_reason":"length","message":{"content":""}}],"usage":{"completion_tokens":1600,"completion_tokens_details":{"reasoning_tokens":1598}}}'
        with self.assertRaisesRegex(SummaryError, 'output_token_limit'):
            summarize(fixture_article('https://example.com/a'), body, 'test-only-not-a-key', 'test/model')

    @patch('collector.llm.request')
    def test_glm_mandatory_reasoning_preserves_budget_for_visible_summary(self, request):
        result = {'title': '새로운 모델에 대한 발표', 'summary': '회사가 새로운 언어 모델을 발표했습니다. 발표문에는 새로운 모델을 이용할 수 있다는 내용이 포함되어 있습니다.',
                  'why_it_matters': '개발자는 새 모델의 실제 적용 가능성을 검토할 수 있습니다.', 'category': '모델·연구', 'evidence': ['A new model is available.']}
        request.return_value = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(result)}}]}).encode()
        value, meta = summarize(fixture_article('https://example.com/a'), 'A new model is available. ' * 40, 'test-only-not-a-key', 'z-ai/glm-5.3-flash')
        sent = json.loads(request.call_args.kwargs['payload'])
        self.assertEqual(sent['model'], 'z-ai/glm-5.3-flash')
        self.assertEqual(sent['reasoning'], {'effort': 'low'})
        self.assertGreater(sent['max_tokens'], 1600)
        self.assertEqual(value['title'], result['title'])
        self.assertEqual(meta['prompt_version'], 'ko-brief-v3')


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.args = argparse.Namespace(data_dir=self.temp.name, runtime_dir=str(Path(self.temp.name) / 'runtime'), days=3, no_llm=True, dry_run=False)
        self.path = Path(self.temp.name) / 'news.json'

    def fetch(self, source):
        now = datetime.now(UTC)
        entries = [{'url': f'https://example.com/{source["id"]}', 'title': f'AI announcement from {source["id"]}',
                    'published_at': (now - timedelta(hours=1)).replace(minute=0, second=0, microsecond=0).isoformat(), 'body': 'A source text about AI. ' * 50}]
        entries.append({**entries[0], 'url': 'https://example.com/future', 'published_at': (now + timedelta(days=1)).isoformat()})
        entries.append({**entries[0], 'url': 'https://example.com/old', 'published_at': (now - timedelta(days=100)).isoformat()})
        return entries, ''

    def quiet_run(self, **kwargs):
        with redirect_stdout(io.StringIO()):
            return run(self.args, fetch_source=kwargs.pop('fetch_source', self.fetch), **kwargs)

    def test_repeat_run_is_idempotent_and_future_old_items_filtered(self):
        self.quiet_run()
        before = self.path.read_bytes()
        result = self.quiet_run()
        self.assertEqual(result['result'], 'unchanged')
        self.assertEqual(self.path.read_bytes(), before)
        data = json.loads(before)
        self.assertEqual(len(data['articles']), 14)
        validate_dataset(data)

    def test_all_source_failure_preserves_previous_bytes(self):
        self.quiet_run()
        before = self.path.read_bytes()
        with self.assertRaises(RuntimeError):
            self.quiet_run(fetch_source=lambda _: (_ for _ in ()).throw(RuntimeError('offline')))
        self.assertEqual(self.path.read_bytes(), before)

    def test_partial_failure_keeps_old_articles(self):
        self.quiet_run()
        def partial(source):
            if source['id'] == 'openai':
                raise RuntimeError('offline')
            return self.fetch(source)
        self.quiet_run(fetch_source=partial)
        self.assertEqual(len(json.loads(self.path.read_text())['articles']), 14)

    @patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test-only-not-a-key', 'OPENROUTER_MODEL': 'test/model', 'MAX_SUMMARIES_PER_RUN': '1'})
    def test_paid_call_cap_and_failure_cooldown(self):
        self.args.no_llm = False
        self.args.summarize_articles = True
        calls = []
        def failed(article, *_):
            calls.append(article['id'])
            raise RuntimeError('provider failure')
        # Here only article summaries are exercised; the briefing pipeline has its own tests.
        with patch('collector.briefings.prepare_inputs', return_value=[]):
            self.quiet_run(generate=failed)
        self.assertEqual(len(calls), 1)
        first_report = json.loads((Path(self.args.runtime_dir) / 'last-run.json').read_text())
        self.assertEqual(first_report['summary_errors'], [{'article_id': calls[0], 'reason': 'RuntimeError'}])
        with patch('collector.briefings.prepare_inputs', return_value=[]):
            self.quiet_run(generate=failed)
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0], calls[1])
        self.assertTrue(all(a['status'] == 'link' for a in json.loads(self.path.read_text())['articles']))

    def test_dry_run_does_not_write_public_data_or_call_llm(self):
        self.args.dry_run = True
        self.args.no_llm = False
        self.quiet_run(generate=lambda *_: self.fail('dry run must not call model'))
        self.assertFalse(self.path.exists())


if __name__ == '__main__':
    unittest.main()
