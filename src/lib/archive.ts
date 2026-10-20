import { createHash } from 'node:crypto';
import { articles, sourceMap } from './data';
import { catalog } from './storage';
import { NEWS_PAGE_SIZE } from './pagination';

// Build-only: an open tab must not combine pages from different collections.
export const archiveVersion = createHash('sha256')
  .update(
    JSON.stringify({
      format: 1,
      pageSize: NEWS_PAGE_SIZE,
      news: Object.entries(catalog.news).sort(([a], [b]) => a.localeCompare(b)),
      sources: [...sourceMap.values()],
    }),
  )
  .digest('hex')
  .slice(0, 20);
export const archivePages = Math.max(
  1,
  Math.ceil(articles.length / NEWS_PAGE_SIZE),
);
