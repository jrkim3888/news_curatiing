import type { APIRoute } from 'astro';
import { articles, data, xml } from '../lib/data';
export const GET: APIRoute = ({ site }) =>
  new Response(
    `<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>AI 브리프</title><link>${site}</link><description>국내외 AI 뉴스와 맥락</description><language>ko</language>${data.updated_at ? `<lastBuildDate>${new Date(data.updated_at).toUTCString()}</lastBuildDate>` : ''}${articles
      .slice(0, 50)
      .map(
        (a) =>
          `<item><title>${xml(a.title)}</title><link>${new URL(`/news/${a.id}/`, site)}</link><guid isPermaLink="true">${new URL(`/news/${a.id}/`, site)}</guid><pubDate>${new Date(a.published_at).toUTCString()}</pubDate><description>${xml(a.summary || a.excerpt)}</description></item>`,
      )
      .join('')}</channel></rss>`,
    { headers: { 'Content-Type': 'application/rss+xml; charset=utf-8' } },
  );
