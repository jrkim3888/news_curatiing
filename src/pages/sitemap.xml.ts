import type { APIRoute } from 'astro';
import { articles, xml } from '../lib/data';
import { editions, editionUrl } from '../lib/briefings';
export const GET: APIRoute = ({ site }) =>
  new Response(
    `<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${['/', '/archive/', '/briefings/', '/sources/', '/about/', ...articles.map((a) => `/news/${a.id}/`), ...editions.filter((e) => e.status === 'published').map(editionUrl)].map((path) => `<url><loc>${xml(new URL(path, site).href)}</loc></url>`).join('')}</urlset>`,
    { headers: { 'Content-Type': 'application/xml' } },
  );
