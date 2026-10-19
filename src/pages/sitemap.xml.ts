import type { APIRoute } from 'astro';
import { articles, xml } from '../lib/data';
import { editions, editionUrl, visibleDays } from '../lib/briefings';
import {
  NEWS_PAGE_SIZE,
  HISTORY_PAGE_SIZE,
  staticPageUrl,
} from '../lib/pagination';
const pages = (base: string, count: number, size: number) =>
  Array.from({ length: Math.max(0, Math.ceil(count / size) - 1) }, (_, i) =>
    staticPageUrl(base, i + 2),
  );
export const GET: APIRoute = ({ site }) =>
  new Response(
    `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${['/', '/archive/', '/briefings/', '/sources/', '/about/', ...pages('/archive/', articles.length, NEWS_PAGE_SIZE), ...pages('/briefings/', visibleDays.length, HISTORY_PAGE_SIZE), ...articles.map((a) => `/news/${a.id}/`), ...editions.filter((e) => e.status === 'published').map(editionUrl)].map((path) => `<url><loc>${xml(new URL(path, site).href)}</loc></url>`).join('')}</urlset>`,
    { headers: { 'Content-Type': 'application/xml' } },
  );
