import type { APIRoute } from 'astro';
import { articles } from '../../../../lib/data';
import { archivePages, archiveVersion } from '../../../../lib/archive';
import { NEWS_PAGE_SIZE } from '../../../../lib/pagination';

export function getStaticPaths() {
  return Array.from({ length: archivePages }, (_, index) => ({
    params: { version: archiveVersion, page: String(index + 1) },
    props: { page: index + 1 },
  }));
}

export const GET: APIRoute = ({ props }) => {
  const page = props.page as number;
  return new Response(
    JSON.stringify({
      version: archiveVersion,
      page,
      total: articles.length,
      articles: articles.slice(
        (page - 1) * NEWS_PAGE_SIZE,
        page * NEWS_PAGE_SIZE,
      ),
    }),
    { headers: { 'Content-Type': 'application/json; charset=utf-8' } },
  );
};
