"""Create a private, synthetic multi-month archive for end-to-end pagination checks.
Run from the repo: python tests/archive_fixture.py .runtime/browser-data
Never copy these synthetic records to data/ or commit them.
"""
import copy
from datetime import datetime, timedelta
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from collector.storage import Archive, month_of
from test_storage import article, edition

root = Path(sys.argv[1]).resolve()
allowed = Path('.runtime').resolve()
if allowed not in root.parents:
    raise SystemExit('Fixture output must be a child of .runtime')
if (root/'catalog.json').exists():
    raise SystemExit('Fixture directory already exists; choose a new path')
source = Archive('data')
news = {m: copy.deepcopy(source.read('news', m)) for m in source.catalog['news']}
editions = {m: copy.deepcopy(source.read('briefings', m)) for m in source.catalog['briefings']}
for i in range(1, 28):
    stamp = f'2026-09-{i:02d}T05:00:00+00:00'
    a = article(f'fixture-{i}', stamp, f'과거월검색표식 ArchiveFixture research {i}')
    news.setdefault(month_of(stamp), []).append(a)
    day = (datetime.fromisoformat(stamp) + timedelta(days=1)).date().isoformat()
    e = edition(a, day)
    e['headline'] = f'과거 브리핑 검색표식 연구 기록 {i}'
    editions.setdefault(day[:7], []).append(e)
store = Archive(root, root.parent/'fixture-runtime')
store.save(news=news, editions=editions, sources=source.sources, updated_at=source.catalog['updated_at'], collection_slot=source.catalog['collection_slot'])
store.validate(); store.close(); source.close()
print('Private fixture: 27 older news articles and daily editions added.')
