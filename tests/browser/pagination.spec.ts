import { test, expect } from '@playwright/test';
import { articles } from '../../src/lib/data';
import { visibleDays } from '../../src/lib/briefings';
import { NEWS_PAGE_SIZE, HISTORY_PAGE_SIZE } from '../../src/lib/pagination';

const cards = '[data-card]';
test('all archive pages are bounded and contain every article exactly once', async ({
  page,
  request,
}) => {
  const html = await (await request.get('/archive/')).text();
  expect((html.match(/data-card(\s|>)/g) || []).length).toBe(NEWS_PAGE_SIZE);
  await page.goto('/archive/');
  const ids: string[] = [];
  const pages = Math.ceil(articles.length / NEWS_PAGE_SIZE);
  for (let n = 1; n <= pages; n++) {
    await expect(page.locator('[data-page-status]')).toContainText(
      `${n} / ${pages} 페이지`,
    );
    const batch = await page
      .locator(cards)
      .evaluateAll((nodes) =>
        nodes.map((node) => node.getAttribute('data-id')!),
      );
    expect(batch.length).toBeLessThanOrEqual(NEWS_PAGE_SIZE);
    ids.push(...batch);
    if (n < pages)
      await page
        .getByRole('link', { name: '다음 페이지', exact: true })
        .click();
  }
  expect(ids.length).toBe(articles.length);
  expect(new Set(ids).size).toBe(articles.length);
  expect([...ids].sort()).toEqual(articles.map((a) => a.id).sort());
  await page.reload();
  await expect(page.locator('[data-page-status]')).toContainText(
    `${pages} / ${pages} 페이지`,
  );
  await page.goBack();
  await expect(page.locator('[data-page-status]')).toContainText(
    `${pages - 1} / ${pages} 페이지`,
  );
});

test('filtered pagination preserves URL state, bookmarks, and valid page bounds', async ({
  page,
}) => {
  const selected = articles.filter((a) => a.category === '모델·연구');
  await page.addInitScript(
    (ids) => localStorage.setItem('ai-brief:saved:v1', JSON.stringify(ids)),
    selected.map((a) => a.id),
  );
  await page.goto(
    `/archive/?category=${encodeURIComponent('모델·연구')}&saved=1&view=list`,
  );
  await expect(page.locator('[data-result-count]')).toHaveText(
    String(selected.length),
  );
  await page.getByRole('link', { name: '다음 페이지', exact: true }).click();
  await expect(page.locator('[data-page-status]')).toContainText('2 /');
  const params = new URL(page.url()).searchParams;
  expect(params.get('category')).toBe('모델·연구');
  expect(params.get('saved')).toBe('1');
  expect(params.get('view')).toBe('list');
  expect(params.get('page')).toBe('2');
  await page.reload();
  await expect(page.locator('[data-page-status]')).toContainText('2 /');
  await expect(page.locator('[data-bookmark][aria-pressed=true]')).toHaveCount(
    NEWS_PAGE_SIZE,
  );
  await page.goto('/archive/?page=9999999&view=list');
  await expect(page.locator('[data-page-status]')).toContainText(
    `${Math.ceil(articles.length / NEWS_PAGE_SIZE)} /`,
  );
  expect(new URL(page.url()).searchParams.get('page')).toBe(
    String(Math.ceil(articles.length / NEWS_PAGE_SIZE)),
  );
});

test('search pagination continues beyond the old 30-result cap', async ({
  page,
}) => {
  await page.goto('/search/?q=AI');
  await expect(page.locator('#search-status')).toContainText('검색 결과');
  const total = Number(
    (await page.locator('#search-status').innerText()).match(/^\d+/)![0],
  );
  expect(total).toBeGreaterThan(36);
  const first = await page
    .locator('#search-results a')
    .evaluateAll((nodes) => nodes.map((a) => a.getAttribute('href')));
  await page.getByRole('link', { name: '다음 페이지', exact: true }).click();
  await expect(page.locator('#search-status')).toContainText('2 /');
  await page.getByRole('link', { name: '다음 페이지', exact: true }).click();
  await expect(page.locator('#search-status')).toContainText('3 /');
  await expect(page.locator('#search-results li')).toHaveCount(
    Math.min(18, total - 36),
  );
  const third = await page
    .locator('#search-results a')
    .evaluateAll((nodes) => nodes.map((a) => a.getAttribute('href')));
  expect(third.some((id) => first.includes(id))).toBe(false);
  await page.reload();
  await expect(page.locator('#search-status')).toContainText('3 /');
});

test('search load failure is distinct from empty results and can be retried', async ({
  page,
}) => {
  await page.route('**/pagefind/**/*.pf_fragment', (route) => route.abort());
  await page.goto('/archive/');
  await expect(page.locator('[data-load-error]')).toBeVisible();
  await expect(page.locator('[data-empty]')).toBeHidden();
  await expect(page.locator('[data-results]')).toBeHidden();
  await page.unroute('**/pagefind/**/*.pf_fragment');
  await page.locator('[data-retry]').click();
  await expect(page.locator('[data-card]:visible')).toHaveCount(NEWS_PAGE_SIZE);
  await expect(page.locator('[data-load-error]')).toBeHidden();
});

test('static pages are traversable without JavaScript', async ({ browser }) => {
  const context = await browser.newContext({
    javaScriptEnabled: false,
    baseURL: 'http://127.0.0.1:4322',
  });
  const page = await context.newPage();
  await page.goto('/archive/');
  const first = await page.locator(cards).first().getAttribute('data-id');
  await page.getByRole('link', { name: '다음 페이지', exact: true }).click();
  await expect(page).toHaveURL(/\/archive\/page\/2\//);
  expect(await page.locator(cards).first().getAttribute('data-id')).not.toBe(
    first,
  );
  await expect(page.locator(cards)).toHaveCount(NEWS_PAGE_SIZE);
  if (visibleDays.length > HISTORY_PAGE_SIZE) {
    await page.goto('/briefings/');
    await page.getByRole('link', { name: '다음 페이지', exact: true }).click();
    await expect(page).toHaveURL(/\/briefings\/page\/2\//);
    await expect(page.locator('[data-history-day]')).toHaveCount(
      Math.min(HISTORY_PAGE_SIZE, visibleDays.length - HISTORY_PAGE_SIZE),
    );
  }
  await context.close();
});

test('older monthly files participate in archive, history and global search', async ({
  page,
}) => {
  test.skip(
    !process.env.AI_BRIEF_DATA_DIR,
    'Run against the private multi-month fixture',
  );
  await page.goto(
    '/archive/?q=ArchiveFixture&from=2026-09-01&to=2026-09-30&source=openai',
  );
  await expect(page.locator('[data-result-count]')).toHaveText('27');
  await page.getByRole('link', { name: '다음 페이지', exact: true }).click();
  await expect(page.locator('[data-card]:visible')).toHaveCount(9);
  const bookmark = page.locator('[data-bookmark]').first();
  const id = await bookmark.getAttribute('data-bookmark');
  await bookmark.click();
  await page.getByLabel('북마크만', { exact: true }).check();
  await expect(page.locator('[data-card]:visible')).toHaveCount(1);
  await expect(page.locator('[data-card]:visible')).toHaveAttribute(
    'data-id',
    id!,
  );
  await page.reload();
  await expect(page.locator('[data-card]:visible')).toHaveCount(1);
  await page.goto('/briefings/?month=2026-09&q=검색표식');
  await expect(page.locator('#history-count')).toHaveText('27일의 브리핑');
  await expect(page.locator('[data-history-day]')).toHaveCount(
    HISTORY_PAGE_SIZE,
  );
  await page.getByRole('link', { name: '다음 페이지', exact: true }).click();
  await expect(page.locator('#history-page-status')).toContainText('2 / 3');
  await page.reload();
  await expect(page.locator('#history-page-status')).toContainText('2 / 3');
  await page.getByRole('link', { name: '다음 페이지', exact: true }).click();
  await expect(page.locator('[data-history-day]')).toHaveCount(3);
  await page.goto('/search/?q=과거월검색표식');
  await expect(page.locator('#search-status')).toContainText('27개 검색 결과');
});

test('history and global search recover from failed fragment requests', async ({
  page,
}) => {
  for (const item of [
    {
      url: '/briefings/',
      error: '#history-error',
      retry: '#history-retry',
      result: '[data-history-day]',
    },
    {
      url: '/search/?q=AI',
      error: '#search-retry',
      retry: '#search-retry',
      result: '#search-results li',
    },
  ]) {
    await page.route('**/pagefind/**/*.pf_fragment', (route) => route.abort());
    await page.goto(item.url);
    await expect(page.locator(item.error)).toBeVisible();
    await page.unroute('**/pagefind/**/*.pf_fragment');
    await page.locator(item.retry).click();
    await expect(page.locator(item.result).first()).toBeVisible();
    await expect(page.locator(item.error)).toBeHidden();
  }
});
