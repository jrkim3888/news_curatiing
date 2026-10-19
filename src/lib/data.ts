import { z } from 'zod';
import { catalog, sourceData, readMonths } from './storage';
const raw = {
  version: 1,
  updated_at: catalog.updated_at,
  collection_slot: catalog.collection_slot || undefined,
  sources: sourceData.sources,
  articles: readMonths('news'),
};

const url = z.url().refine((v) => /^https?:\/\//.test(v));
const category = z.enum([
  '모델·연구',
  '개발·도구',
  '산업·비즈니스',
  '정책·사회',
]);
const articleSchema = z
  .object({
    id: z.string().regex(/^[a-f0-9]{20}$/),
    url,
    title: z.string().min(1).max(500),
    original_title: z.string(),
    published_at: z.iso.datetime({ offset: true }),
    collected_at: z.iso.datetime({ offset: true }),
    published_precision: z.enum(['day', 'time']).default('time'),
    region: z.enum(['kr', 'global']),
    category,
    status: z.enum(['link', 'summarized']),
    excerpt: z.string().max(180),
    summary: z.string().max(600).optional(),
    why_it_matters: z.string().max(300).optional(),
    correction: z.string().optional(),
    summary_meta: z
      .object({
        model: z.string(),
        prompt_version: z.string(),
        generated_at: z.string(),
        input_hash: z.string(),
        evidence_verified: z.boolean(),
      })
      .optional(),
    sources: z
      .array(
        z.object({
          source_id: z.string(),
          url,
          title: z.string(),
          discussion_url: url.optional(),
        }),
      )
      .min(1),
  })
  .superRefine((a, ctx) => {
    if (
      a.status === 'summarized' &&
      (!a.summary || !a.why_it_matters || !a.summary_meta)
    )
      ctx.addIssue({ code: 'custom', message: 'Incomplete summary' });
  });
export const data = z
  .object({
    version: z.literal(1),
    updated_at: z.string().nullable(),
    collection_slot: z.iso.datetime({ offset: true }).optional(),
    articles: z.array(articleSchema),
    sources: z.array(
      z.object({
        id: z.string(),
        name: z.string(),
        homepage: url,
        region: z.enum(['kr', 'global']),
        role: z.string(),
        enabled: z.boolean(),
      }),
    ),
  })
  .parse(raw);
export type Article = z.infer<typeof articleSchema>;
export const categories = category.options;
export const articles = data.articles.sort(
  (a, b) =>
    b.published_at.localeCompare(a.published_at) || b.id.localeCompare(a.id),
);
if (new Set(articles.map((a) => a.id)).size !== articles.length)
  throw new Error('Duplicate article IDs across months');
export const articlesByCategory = new Map(
  categories.map((category) => [
    category,
    articles.filter((a) => a.category === category),
  ]),
);
export const sourceMap = new Map(data.sources.map((s) => [s.id, s]));
export const roleNames: Record<string, string> = {
  official: '공식 발표',
  media: '언론 보도',
  signal: '커뮤니티',
  analysis: '전문가 분석',
  official_research: '공식 연구',
  official_community: '공식·커뮤니티',
};
export function dateLabel(date: string, time = false) {
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    month: 'long',
    day: 'numeric',
    ...(time ? { hour: '2-digit', minute: '2-digit', hour12: false } : {}),
  }).format(new Date(date));
}
export const xml = (s: string) =>
  s.replace(
    /[<>&"']/g,
    (c) =>
      ({
        '<': '&lt;',
        '>': '&gt;',
        '&': '&amp;',
        '"': '&quot;',
        "'": '&apos;',
      })[c]!,
  );
