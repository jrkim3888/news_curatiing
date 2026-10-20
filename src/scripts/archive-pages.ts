import type { Article } from '../lib/data';
import { NEWS_PAGE_SIZE } from '../lib/pagination';

export class ArchiveChangedError extends Error {}

export function archivePages(version: string, total: number) {
  // Keep only a few pages in memory, regardless of the size of the archive.
  const cache = new Map<number, Article[]>();
  return async (page: number, signal: AbortSignal): Promise<Article[]> => {
    const cached = cache.get(page);
    if (cached) {
      cache.delete(page);
      cache.set(page, cached);
      return cached;
    }
    const response = await fetch(`/archive/data/${version}/${page}.json`, {
      signal,
    });
    if (response.status === 404) throw new ArchiveChangedError();
    if (!response.ok) throw new Error('Archive page request failed');
    const data = await response.json();
    const expected = Math.min(
      NEWS_PAGE_SIZE,
      total - (page - 1) * NEWS_PAGE_SIZE,
    );
    if (
      data.version !== version ||
      data.page !== page ||
      data.total !== total ||
      !Array.isArray(data.articles) ||
      data.articles.length !== expected ||
      new Set(data.articles.map((a: Article) => a.id)).size !== expected
    )
      throw new Error('Invalid archive page');
    if (signal.aborted) throw new Error('Archive request cancelled');
    cache.set(page, data.articles);
    if (cache.size > 3) cache.delete(cache.keys().next().value!);
    return data.articles;
  };
}
