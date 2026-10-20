import { test, expect } from '@playwright/test';
import { articles } from '../../src/lib/data';
import { NEWS_PAGE_SIZE } from '../../src/lib/pagination';

test('static archive entry keeps its cards and saved view without search requests', async ({
  page,
}) => {
  const searchRequests: string[] = [];
  const pageRequests: string[] = [];
  await page.route('**/pagefind/**', (route) => {
    searchRequests.push(route.request().url());
    return route.abort();
  });
  page.on('request', (request) => {
    if (request.url().includes('/archive/data/'))
      pageRequests.push(request.url());
  });
  await page.addInitScript(
    (ids) => {
      localStorage.setItem('ai-brief:saved:v1', JSON.stringify(ids));
      localStorage.setItem('ai-brief:view:v1', 'list');
    },
    [articles[0].id, articles[NEWS_PAGE_SIZE].id],
  );
  for (const path of ['/archive/', '/archive/page/2/']) {
    await page.goto(path);
    // A remembered view and bookmark prove the script initialized, not just SSR.
    await expect(page.locator('[data-results]')).toHaveAttribute(
      'data-view',
      'list',
    );
    await expect(page.locator('[data-card]:visible')).toHaveCount(
      NEWS_PAGE_SIZE,
    );
    await expect(page.locator('[data-bookmark]').first()).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect(page.locator('[data-load-error]')).toBeHidden();
  }
  expect(searchRequests).toEqual([]);
  expect(pageRequests).toEqual([]);
});

test('unfiltered pagination reads one bounded file and reuses recently visited pages', async ({
  page,
}) => {
  const requests: string[] = [];
  page.on('request', (request) => {
    if (/\/(archive\/data|pagefind)\//.test(new URL(request.url()).pathname))
      requests.push(request.url());
  });
  await page.goto('/archive/');
  const response = page.waitForResponse(/\/archive\/data\/[a-f0-9]+\/2\.json$/);
  await page.getByRole('link', { name: '다음 페이지', exact: true }).click();
  const data = await (await response).json();
  expect(data.articles.map((a: { id: string }) => a.id)).toEqual(
    articles.slice(NEWS_PAGE_SIZE, NEWS_PAGE_SIZE * 2).map((a) => a.id),
  );
  await expect(page.locator('[data-page-status]')).toContainText('2 /');
  await page.locator('[data-bookmark]').first().click();
  await page.getByRole('link', { name: '이전 페이지', exact: true }).click();
  await expect(page.locator('[data-page-status]')).toContainText('1 /');
  await page.getByRole('link', { name: '다음 페이지', exact: true }).click();
  await expect(page.locator('[data-page-status]')).toContainText('2 /');
  await expect(page.locator('[data-bookmark]').first()).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  expect(requests).toHaveLength(1);
});

test('a failed page file can be retried without loading the search engine', async ({
  page,
}) => {
  const searchRequests: string[] = [];
  await page.route('**/pagefind/**', (route) => {
    searchRequests.push(route.request().url());
    return route.abort();
  });
  await page.route('**/archive/data/**/2.json', (route) => route.abort());
  await page.goto('/archive/');
  await page.getByRole('link', { name: '다음 페이지', exact: true }).click();
  await expect(page.locator('[data-load-error]')).toBeVisible();
  await expect(page.locator('[data-empty]')).toBeHidden();
  await page.unroute('**/archive/data/**/2.json');
  await page.locator('[data-retry]').click();
  await expect(page.locator('[data-page-status]')).toContainText('2 /');
  await expect(page.locator('[data-card]:visible')).toHaveCount(NEWS_PAGE_SIZE);
  expect(searchRequests).toEqual([]);
});

test('a page from an obsolete deployment reloads the current archive', async ({
  page,
}) => {
  // Serve stale configuration just once, as if this tab predated a collection.
  await page.route(
    '**/archive/',
    async (route) => {
      const response = await route.fetch();
      const html = (await response.text()).replace(
        /("version":")[a-f0-9]{20}("})/,
        '$1obsolete$2',
      );
      expect(html).toContain('"version":"obsolete"');
      await route.fulfill({ response, body: html });
    },
    { times: 1 },
  );
  await page.route('**/archive/data/obsolete/**', (route) =>
    route.fulfill({ status: 404 }),
  );
  await page.goto('/archive/');
  const refreshed = page.waitForEvent(
    'framenavigated',
    (frame) => frame === page.mainFrame(),
  );
  await page.getByRole('link', { name: '다음 페이지', exact: true }).click();
  await refreshed;
  await expect(page.locator('[data-page-status]')).toContainText('2 /');
  await expect(page.locator('[data-card]:visible').first()).toHaveAttribute(
    'data-id',
    articles[NEWS_PAGE_SIZE].id,
  );
  await expect(page.locator('[data-load-error]')).toBeHidden();
});

test('reset restores the static list while slow search requests finish', async ({
  page,
}) => {
  let release!: () => void;
  let started!: () => void;
  const blocked = new Promise<void>((resolve) => {
    release = resolve;
  });
  const requested = new Promise<void>((resolve) => {
    started = resolve;
  });
  await page.route('**/pagefind/**/*.pf_fragment', async (route) => {
    started();
    await blocked;
    await route.continue();
  });
  await page.goto('/archive/');
  await page.getByRole('button', { name: '모델·연구', exact: true }).click();
  await requested;
  await expect(page.locator('[data-page-status]')).toHaveText(
    '검색 결과를 불러오는 중…',
  );
  await page.getByRole('button', { name: '필터 초기화' }).click();
  await expect(page.locator('[data-card]:visible')).toHaveCount(NEWS_PAGE_SIZE);
  await expect(page.locator('[data-result-count]')).toHaveText(
    String(articles.length),
  );
  release();
  await page.waitForLoadState('networkidle');
  expect(
    await page
      .locator('[data-card]')
      .evaluateAll((nodes) =>
        nodes.map((node) => node.getAttribute('data-id')),
      ),
  ).toEqual(articles.slice(0, NEWS_PAGE_SIZE).map((a) => a.id));
});

test('persistently missing page files stop after one refresh', async ({
  page,
}) => {
  let requests = 0;
  await page.route('**/archive/data/**/2.json', (route) => {
    requests++;
    return route.fulfill({ status: 404 });
  });
  await page.goto('/archive/');
  await page.getByRole('link', { name: '다음 페이지', exact: true }).click();
  await expect(page.locator('[data-load-error]')).toBeVisible();
  expect(requests).toBe(2);
  await page.getByRole('button', { name: '필터 초기화' }).click();
  await expect(page.locator('[data-card]:visible')).toHaveCount(NEWS_PAGE_SIZE);
});

test('reset cancels a slow page request and keeps the initial cards', async ({
  page,
}) => {
  let release!: () => void;
  let started!: () => void;
  let finished!: () => void;
  const blocked = new Promise<void>((resolve) => {
    release = resolve;
  });
  const requested = new Promise<void>((resolve) => {
    started = resolve;
  });
  const completed = new Promise<void>((resolve) => {
    finished = resolve;
  });
  await page.route('**/archive/data/**/2.json', async (route) => {
    const response = await route.fetch();
    started();
    await blocked;
    await route.fulfill({ response });
    finished();
  });
  await page.goto('/archive/');
  await page.getByRole('link', { name: '다음 페이지', exact: true }).click();
  await requested;
  await expect(page.locator('[data-page-status]')).toHaveText(
    '뉴스를 불러오는 중…',
  );
  await page.getByRole('button', { name: '필터 초기화' }).click();
  await expect(page.locator('[data-card]:visible')).toHaveCount(NEWS_PAGE_SIZE);
  release();
  await completed;
  await expect(page.locator('[data-page-status]')).toContainText('1 /');
  await expect(page.locator('[data-card]').first()).toHaveAttribute(
    'data-id',
    articles[0].id,
  );
});
