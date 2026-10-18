import { test, expect } from '@playwright/test';
import history from '../../data/briefings.json' with { type: 'json' };
import type { Edition } from '../../src/lib/briefings';

const editions = (history.editions as Edition[]).filter(
  (e) => e.status === 'published',
);

test('history is reachable from home and distinguishes news from briefings', async ({
  page,
}) => {
  await page.goto('/');
  await page.getByRole('link', { name: '히스토리', exact: true }).click();
  await expect(page).toHaveURL(/\/briefings\/$/);
  await expect(page.getByRole('heading', { level: 1 })).toContainText(
    '날짜별로',
  );
  if (!editions.length) {
    await expect(
      page.getByRole('heading', { name: '첫 브리핑을 기다리고 있습니다.' }),
    ).toBeVisible();
    await expect(page.locator('.brief-day-card')).toHaveCount(0);
  } else {
    const days = new Set(editions.map((e) => e.day));
    await expect(page.locator('.brief-day-card')).toHaveCount(days.size);
    await page.locator('#history-query').fill('no-matching-briefing-12345');
    await expect(page.locator('#history-empty')).toBeVisible();
    await page.locator('#history-reset').click();
    await expect(page.locator('.brief-day-card:visible')).toHaveCount(
      days.size,
    );
    await page.locator('#history-month').fill('2000-01');
    await expect(page.locator('#history-empty')).toBeVisible();
    await page.locator('#history-reset').click();
    await page.locator('.brief-day-body h3').first().click();
    await expect(page).toHaveURL(/\/briefings\/\d{4}-\d{2}-\d{2}\/$/);
    await expect(page.locator('.brief-window')).toContainText('KST');
    await expect(page.locator('.brief-source a').first()).toHaveAttribute(
      'href',
      /^https?:\/\//,
    );
  }
  await page.goto('/archive/');
  await expect(
    page.getByRole('link', { name: /날짜별 종합 브리핑/ }),
  ).toHaveAttribute('href', '/briefings/');
});

test('edition content and citations remain accessible without JavaScript', async ({
  browser,
  request,
}) => {
  const context = await browser.newContext({
    javaScriptEnabled: false,
    baseURL: 'http://127.0.0.1:4322',
  });
  const page = await context.newPage();
  await page.goto('/briefings/');
  if (editions.length) {
    const e = editions[0];
    await page.goto(`/briefings/${e.id}/`);
    await expect(page.locator('.brief-overview h2')).toHaveText(e.headline);
    await expect(page.locator('.brief-issue')).toHaveCount(e.issues.length);
    for (const issue of e.issues) {
      await expect(page.locator(`#${issue.id} h3`)).toHaveText(issue.title);
      for (const fact of issue.facts)
        await expect(page.locator(`#${issue.id} .brief-facts`)).toContainText(
          fact.text,
        );
    }
    const sitemap = await (await request.get('/sitemap.xml')).text();
    expect(sitemap).toContain(`/briefings/${e.id}/`);
    await expect(page.locator('.brief-reading-heading')).toContainText(
      '6시 30분',
    );
    await page.getByText('생성 정보', { exact: true }).click();
    await expect(page.locator('.brief-disclosure')).toContainText('발행 예정');
    await expect(page.locator('.brief-disclosure')).toContainText('실제 생성');
  } else {
    await expect(page.locator('.brief-empty')).toBeVisible();
  }
  await context.close();
});

test('history and briefing mobile layout has no overflow', async ({ page }) => {
  const routes = [
    '/',
    '/briefings/',
    ...(editions.length ? [`/briefings/${editions[0].id}/`] : []),
  ];
  await page.setViewportSize({ width: 390, height: 844 });
  for (const route of routes) {
    await page.goto(route);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      route,
    ).toBe(true);
  }
  await page.goto('/briefings/');
  await page.screenshot({
    path: '.runtime/history-mobile.png',
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({
    path: '.runtime/history-desktop.png',
    fullPage: true,
  });
});
