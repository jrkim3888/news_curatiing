import { test, expect } from '@playwright/test';
import news from '../../data/news.json' with { type: 'json' };

test('AI summary filter and generated article retain source and model attribution', async ({
  page,
}) => {
  const summarized = news.articles.filter((a) => a.status === 'summarized');
  await page.goto('/archive/');
  await page.locator('[data-summary-filter]').selectOption('summarized');
  if (summarized.length === 0) {
    await expect(page.locator('[data-empty]')).toBeVisible();
    await expect(page.locator('[data-card]:visible')).toHaveCount(0);
    return;
  }
  await expect(page.locator('[data-result-count]')).toHaveText(
    String(summarized.length),
  );
  await expect(
    page.locator('[data-card]:visible:not([data-summary="summarized"])'),
  ).toHaveCount(0);
  const article = summarized[0];
  await page.goto(`/news/${article.id}/`);
  await expect(page.locator('h1')).toHaveText(article.title);
  await expect(page.locator('.summary-block p').first()).toHaveText(
    article.summary!,
  );
  await expect(page.locator('.why-block p')).toHaveText(
    article.why_it_matters!,
  );
  await expect(page.locator('.article-fineprint')).toContainText(
    article.summary_meta!.model,
  );
  await expect(page.locator('.article-sources a').first()).toHaveAttribute(
    'href',
    article.sources[0].url,
  );
  await expect(page.locator('.article-body > .disclosure')).toContainText(
    'AI가 작성',
  );
  await page.screenshot({
    path: '.runtime/summary-desktop.png',
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: '.runtime/summary-mobile.png',
    fullPage: true,
  });
});

test('filters, empty state, saved articles and persistence', async ({
  page,
}) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toContainText(
    'AI의 다음을',
  );
  await expect(page.locator('[data-card]:visible')).toHaveCount(18);
  await page.locator('[data-card]:visible [data-bookmark]').first().click();
  await page.reload();
  await expect(page.locator('[data-bookmark][aria-pressed=true]')).toHaveCount(
    1,
  );
  await page.goto('/archive/?saved=1');
  await expect(page.locator('[data-card]:visible')).toHaveCount(1);
  await page.locator('[data-summary-filter]').selectOption('all');
  await page.locator('[data-region-filter]').selectOption('kr');
  expect(await page.locator('[data-card]:visible').count()).toBeGreaterThan(0);
  expect(
    await page.locator('[data-card][data-region=global]:visible').count(),
  ).toBe(0);
  await page.getByRole('button', { name: '개발·도구', exact: true }).click();
  expect(
    await page
      .locator('[data-card]:visible:not([data-category="개발·도구"])')
      .count(),
  ).toBe(0);
  await page.getByRole('searchbox').fill('unfindable-example-12345');
  await expect(page.locator('[data-empty]')).toBeVisible();
  await page.getByRole('button', { name: '전체 뉴스 보기' }).click();
  await expect(page.locator('[data-card]:visible')).toHaveCount(18);
  await page.getByRole('button', { name: '뉴스 더 보기' }).click();
  await expect(page.locator('[data-card]:visible')).toHaveCount(36);
  expect(errors).toEqual([]);
});

test('Korean and English search index returns real article links', async ({
  page,
}) => {
  for (const query of ['모델', 'OpenAI']) {
    await page.goto(`/search/?q=${encodeURIComponent(query)}`);
    await expect(page.locator('#search-results li').first()).toBeVisible();
    await expect(page.locator('#search-status')).toContainText('검색 결과');
    const link = page.locator('#search-results a').first();
    const title = await link.textContent();
    await link.click();
    await expect(page.locator('h1')).toHaveText(title!);
    await expect(
      page.getByRole('heading', { name: '출처에서 더 읽기' }),
    ).toBeVisible();
  }
});

test('mobile layout, navigation and screenshots', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({ path: '.runtime/mobile.png', fullPage: true });
  await page.getByRole('link', { name: '출처', exact: true }).click();
  await expect(page.locator('.source-card')).toHaveCount(14);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/');
  await page.screenshot({ path: '.runtime/desktop.png', fullPage: true });
});

test('articles render without JavaScript and public feeds are available', async ({
  browser,
  request,
}) => {
  const context = await browser.newContext({
    javaScriptEnabled: false,
    baseURL: 'http://127.0.0.1:4322',
  });
  const page = await context.newPage();
  await page.goto('/');
  await page.locator('.news-card h3 a').first().click();
  await expect(page.locator('h1')).not.toBeEmpty();
  await expect(page.locator('.article-sources a').first()).toHaveAttribute(
    'href',
    /^https?:\/\//,
  );
  await context.close();
  expect((await request.get('/rss.xml')).status()).toBe(200);
  expect(await (await request.get('/sitemap.xml')).text()).toContain('/news/');
  expect((await request.get('/does-not-exist/')).status()).toBe(404);
});
