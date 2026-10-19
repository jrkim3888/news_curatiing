import { test, expect } from '@playwright/test';
import { history } from '../../src/lib/briefings';
import { timeLabel, type Edition } from '../../src/lib/briefings';
import { kstDay } from '../../src/lib/dates';

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
    await expect(page.locator('.brief-day-card')).toHaveCount(
      Math.min(12, days.size),
    );
    await page.locator('#history-query').fill('no-matching-briefing-12345');
    await expect(page.locator('#history-empty')).toBeVisible();
    await page.locator('#history-reset').click();
    await expect(page.locator('.brief-day-card:visible')).toHaveCount(
      Math.min(12, days.size),
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
    await expect(page.locator('.brief-publication time')).toHaveAttribute(
      'datetime',
      e.generated_at,
    );
    await expect(page.locator('.brief-publication time')).toHaveText(
      `발행 · ${kstDay(new Date(e.generated_at)).replaceAll('-', '.')} ${timeLabel(e.generated_at)} KST`,
    );
    if (e.takeaway) {
      await expect(page.locator('.brief-takeaway p')).toHaveText(e.takeaway);
      await expect(page.locator('.brief-takeaway > span')).toHaveText(
        '이날의 한 줄',
      );
    }
    await expect(page.locator('.brief-overview-copy > p')).toHaveCount(
      e.overview.length,
    );
    for (const paragraph of e.overview)
      await expect(page.locator('.brief-overview-copy')).toContainText(
        paragraph.text,
      );
    await expect(page.locator('.brief-issue')).toHaveCount(e.issues.length);
    for (const issue of e.issues) {
      await expect(page.locator(`#${issue.id} h3`)).toHaveText(issue.title);
      await expect(
        page.locator(`#${issue.id} .brief-issue-meta time`),
      ).toHaveAttribute('datetime', e.day);
      await expect(
        page.locator(`#${issue.id} .brief-issue-meta time`),
      ).toHaveText(`${e.day.replaceAll('-', '.')} 브리핑`);
      for (const [index, fact] of issue.facts.entries()) {
        const paragraph = page
          .locator(`#${issue.id} .brief-facts > p`)
          .nth(index);
        await expect(paragraph).toHaveText(fact.text);
        const links = paragraph.getByRole('link');
        const annotations = [...(fact.links || [])].sort(
          (a, b) => fact.text.indexOf(a.text) - fact.text.indexOf(b.text),
        );
        await expect(links).toHaveCount(annotations.length);
        expect(
          annotations.reduce((sum, link) => sum + link.text.length, 0),
        ).toBeLessThanOrEqual(fact.text.length / 2);
        for (const [sourceIndex, annotation] of annotations.entries()) {
          const source = e.articles.find((a) => a.id === annotation.article_id)!
            .sources[0];
          await expect(links.nth(sourceIndex)).toHaveAttribute(
            'href',
            source.url,
          );
          await expect(links.nth(sourceIndex)).toHaveAttribute(
            'target',
            '_blank',
          );
          await expect(links.nth(sourceIndex)).toHaveText(annotation.text);
          await expect(links.nth(sourceIndex)).toHaveAccessibleName(
            `${annotation.text} — ${source.name} 원문 (새 탭)`,
          );
        }
      }
      for (const id of issue.article_ids) {
        const article = e.articles.find((a) => a.id === id)!;
        await expect(
          page.locator(`#source-${issue.id}-${id} time`),
        ).toHaveAttribute('datetime', article.published_at);
        await expect(page.locator(`#source-${issue.id}-${id} time`)).toHaveText(
          `원문 발행 · ${kstDay(new Date(article.published_at)).replaceAll('-', '.')} KST`,
        );
        await expect(
          page.locator(`#source-${issue.id}-${id} > a`).first(),
        ).toHaveAttribute('href', article.sources[0].url);
      }
    }
    await expect(page.locator('.brief-facts a[href^="#"]')).toHaveCount(0);
    await expect(
      page.locator('.brief-edition a').filter({ hasText: /^\[\d+\]$/ }),
    ).toHaveCount(0);
    if (e.overview_revision)
      await expect(page.locator('.brief-revision')).toContainText(
        e.overview_revision.note,
      );
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
  if (editions.length) {
    await page.goto('/');
    await expect(page.locator('.brief-publication time')).toHaveAttribute(
      'datetime',
      editions[0].generated_at,
    );
    if (editions[0].takeaway)
      await expect(page.locator('.brief-takeaway > span')).toHaveText(
        '오늘의 한 줄',
      );
    await page
      .locator('.brief-overview')
      .screenshot({ path: '.runtime/overview-mobile.png' });
    await page
      .locator('.brief-issue')
      .last()
      .screenshot({ path: '.runtime/source-mobile.png' });
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
  if (editions.length) {
    await page.goto('/');
    await page
      .locator('.brief-overview')
      .screenshot({ path: '.runtime/overview-desktop.png' });
    await page
      .locator('.brief-issue')
      .last()
      .screenshot({ path: '.runtime/source-desktop.png' });
  }
});
