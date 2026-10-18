import { test, expect } from '@playwright/test';
import news from '../../data/news.json' with { type: 'json' };
import history from '../../data/briefings.json' with { type: 'json' };
import { kstDay, recentWeek } from '../../src/lib/dates';

test.use({ timezoneId: 'America/Los_Angeles' });

test('header date follows Korea midnight and home shows only the recent week', async ({
  page,
}) => {
  await page.clock.install({ time: new Date('2026-10-19T14:59:30Z') });
  await page.goto('/');
  await expect(page.locator('[data-today]')).toHaveAttribute(
    'datetime',
    '2026-10-19',
  );
  await expect(page.locator('[data-today]')).toContainText('19일');
  await page.clock.runFor(60000);
  await expect(page.locator('[data-today]')).toHaveAttribute(
    'datetime',
    '2026-10-20',
  );
  await expect(page.locator('[data-week-range]')).toContainText(
    '2026.10.14 — 2026.10.20',
  );
  await expect(page.locator('[data-feed]')).toHaveCount(0);
  await expect(
    page.getByRole('heading', { name: '지금, AI에서 일어나는 일' }),
  ).toHaveCount(0);
  // An already open static page drops expired cards without another build.
  await page.clock.setSystemTime(new Date('2030-01-01T00:00:00Z'));
  await page.evaluate(() =>
    document.dispatchEvent(new Event('visibilitychange')),
  );
  await expect(page.locator('[data-week-day]:visible')).toHaveCount(0);
  await expect(page.locator('[data-week-empty]')).toBeVisible();
});

test('home week cards link to real editions and the full history stays available', async ({
  page,
}) => {
  const week = recentWeek();
  const expected = history.editions.filter(
    (e) => e.status === 'published' && e.day >= week.start && e.day <= week.end,
  );
  await page.goto('/');
  await expect(page.locator('[data-week-day]:visible')).toHaveCount(
    expected.length,
  );
  for (const e of expected) {
    const card = page.locator(`[data-week-day="${e.day}"]`);
    await expect(card.locator('h3')).toHaveText(e.headline);
    await expect(card.locator('.brief-day-body > a')).toHaveAttribute(
      'href',
      `/briefings/${e.id}/`,
    );
  }
  await page
    .locator('.brief-home-history')
    .screenshot({ path: '.runtime/week-desktop.png' });
  await page.getByRole('link', { name: '히스토리 전체 보기' }).click();
  await expect(page.locator('[data-history-day]')).toHaveCount(
    history.editions.filter((e) => e.status === 'published').length,
  );
});

test('archive combines dates, sources, category, search and bookmarks across views', async ({
  page,
}) => {
  const article = news.articles.find((a) => a.sources.length > 1)!;
  expect(article).toBeTruthy();
  const source = article.sources[1].source_id;
  const day = kstDay(new Date(article.published_at));
  const keyword = article.title.split(/\s+/)[0];
  await page.goto('/archive/');
  await page.getByRole('searchbox').fill(keyword);
  await page.locator('[data-source-filter]').selectOption(source);
  await page.getByLabel('시작일', { exact: true }).fill(day);
  await page.getByLabel('종료일', { exact: true }).fill(day);
  await page
    .getByRole('button', { name: article.category, exact: true })
    .click();
  const matching = news.articles.filter(
    (a) =>
      a.sources.some((s) => s.source_id === source) &&
      kstDay(new Date(a.published_at)) === day &&
      a.category === article.category &&
      `${a.title} ${a.original_title} ${a.summary || a.excerpt}`
        .toLocaleLowerCase()
        .includes(keyword.toLocaleLowerCase()),
  );
  await expect(page.locator('[data-result-count]')).toHaveText(
    String(matching.length),
  );
  await page.locator(`[data-bookmark="${article.id}"]`).click();
  await page.getByLabel('북마크만', { exact: true }).check();
  await expect(page.locator('[data-card]:visible')).toHaveCount(1);
  await page.getByRole('button', { name: '리스트', exact: true }).click();
  await expect(page.locator('[data-results]')).toHaveAttribute(
    'data-view',
    'list',
  );
  await expect(page.locator('[data-card]:visible')).toHaveAttribute(
    'data-id',
    article.id,
  );
  const url = new URL(page.url());
  expect(url.searchParams.get('source')).toBe(source);
  expect(url.searchParams.get('from')).toBe(day);
  expect(url.searchParams.get('saved')).toBe('1');
  await page.reload();
  await expect(page.locator('[data-results]')).toHaveAttribute(
    'data-view',
    'list',
  );
  await expect(page.getByRole('searchbox')).toHaveValue(keyword);
  await expect(page.locator('[data-card]:visible')).toHaveCount(1);
  await page.locator(`[data-bookmark="${article.id}"]`).click();
  await expect(page.locator('[data-empty]')).toBeVisible();
  await expect(page.getByLabel('북마크만', { exact: true })).toBeFocused();
  await page.getByRole('button', { name: '필터 초기화' }).click();
  await expect(page.locator('[data-result-count]')).toHaveText(
    String(news.articles.length),
  );
  await expect(page.locator('[data-card]:visible')).toHaveCount(18);
  await page.getByRole('button', { name: '뉴스 더 보기' }).click();
  await expect(page.locator('[data-card]:visible')).toHaveCount(36);
  await page.getByRole('button', { name: '카드', exact: true }).click();
  await expect(page.locator('[data-card]:visible')).toHaveCount(36);
  await page.getByRole('button', { name: '리스트', exact: true }).click();
  await page.locator('[data-card]:visible h3 a').first().click();
  await page.goBack();
  await expect(page.locator('[data-results]')).toHaveAttribute(
    'data-view',
    'list',
  );
  await page.goto('/archive/');
  await expect(
    page.getByRole('button', { name: '리스트', exact: true }),
  ).toHaveAttribute('aria-pressed', 'true');
});

test('date range boundaries are inclusive in KST and invalid ranges can be reset', async ({
  page,
}) => {
  const article = news.articles.find(
    (a) => kstDay(new Date(a.published_at)) !== a.published_at.slice(0, 10),
  )!;
  expect(article).toBeTruthy();
  const day = kstDay(new Date(article.published_at));
  const expected = news.articles.filter(
    (a) => kstDay(new Date(a.published_at)) === day,
  );
  await page.goto(`/archive/?from=${day}&to=${day}&view=list`);
  await expect(page.locator('[data-result-count]')).toHaveText(
    String(expected.length),
  );
  const dates = await page
    .locator('[data-card]:visible')
    .evaluateAll((cards) => cards.map((c) => c.getAttribute('data-date')));
  expect(dates.every((d) => d === day)).toBe(true);
  await page.getByLabel('시작일', { exact: true }).fill('2030-01-01');
  await expect(page.locator('#date-filter-error')).toBeVisible();
  await expect(page.getByLabel('종료일', { exact: true })).toHaveAttribute(
    'aria-invalid',
    'true',
  );
  await expect(page.locator('[data-card]:visible')).toHaveCount(0);
  await page.getByRole('button', { name: '필터 초기화' }).click();
  await expect(page.locator('#date-filter-error')).toBeHidden();
  await expect(page.locator('[data-result-count]')).toHaveText(
    String(news.articles.length),
  );
  await page.goto(
    '/archive/?from=2026-02-30&source=unknown&category=unknown&view=unknown',
  );
  await expect(page.getByLabel('시작일', { exact: true })).toHaveValue('');
  await expect(page.locator('[data-result-count]')).toHaveText(
    String(news.articles.length),
  );
});

test('archive layouts fit narrow and wide screens, and storage denial does not break filtering', async ({
  page,
}) => {
  await page.addInitScript(() => {
    Storage.prototype.getItem = () => {
      throw new DOMException('Storage denied', 'SecurityError');
    };
    Storage.prototype.setItem = () => {
      throw new DOMException('Storage denied', 'SecurityError');
    };
  });
  await page.goto('/archive/');
  await page.locator('[data-card]:visible [data-bookmark]').first().click();
  await expect(page.locator('[data-live]')).toContainText('이번 화면에서만');
  await page.getByLabel('북마크만', { exact: true }).check();
  await expect(page.locator('[data-card]:visible')).toHaveCount(1);
  await page.getByLabel('북마크만', { exact: true }).uncheck();
  for (const width of [320, 390, 768, 1024, 1440]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const mode of ['카드', '리스트']) {
      await page.getByRole('button', { name: mode, exact: true }).click();
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        `${width}px ${mode}`,
      ).toBe(true);
      if ([390, 1440].includes(width)) {
        await page.evaluate(() => scrollTo(0, 0));
        await page.screenshot({
          path: `.runtime/archive-${width}-${mode === '카드' ? 'card' : 'list'}.png`,
        });
      }
    }
  }
});
