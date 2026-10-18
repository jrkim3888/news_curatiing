import { z } from 'zod';
import raw from '../../data/briefings.json';

const stamp = z.iso.datetime({ offset: true });
const id = z.string().regex(/^[a-f0-9]{20}$/);
const text = z.string().min(1);
const issue = z.object({
  id: z.string().regex(/^issue-[1-5]$/),
  title: text,
  category: text,
  facts: z.array(z.object({ text, article_ids: z.array(id).min(1) })).min(1),
  why_it_matters: text,
  article_ids: z.array(id).min(1),
});
const editionSchema = z
  .object({
    id: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
    day: z.string().regex(/^\d{4}-\d{2}-\d{2}$/),
    generated_at: stamp,
    scheduled_at: stamp,
    window_start: stamp,
    window_end: stamp,
    status: z.enum(['published', 'withdrawn']),
    headline: text,
    overview: z.array(z.object({ text, issue_ids: z.array(text).min(1) })),
    issues: z.array(issue),
    articles: z.array(
      z.object({
        id,
        title: text,
        published_at: stamp,
        correction: text.optional(),
        sources: z
          .array(
            z.object({
              source_id: text,
              name: text,
              role: text,
              title: text,
              url: z.url().refine((s) => /^https?:\/\//.test(s)),
              discussion_url: z.url().optional(),
            }),
          )
          .min(1),
      }),
    ),
    reviewed_articles: z.number().int().min(1),
    model: text,
    prompt_version: text,
    input_hash: z.string().regex(/^[a-f0-9]{64}$/),
  })
  .superRefine((e, ctx) => {
    const fail = (message: string) => ctx.addIssue({ code: 'custom', message });
    if (Date.parse(e.window_end) - Date.parse(e.window_start) !== 86400000)
      fail('Expected 24-hour window');
    const day = new Date(Date.parse(e.window_end) + 9 * 3600000)
      .toISOString()
      .slice(0, 10);
    if (
      day !== e.day ||
      e.id !== e.day ||
      Date.parse(e.generated_at) < Date.parse(e.scheduled_at) ||
      Date.parse(e.scheduled_at) - Date.parse(e.window_end) !== 1800000 ||
      new Date(Date.parse(e.window_end) + 9 * 3600000)
        .toISOString()
        .slice(11, 19) !== '06:00:00'
    )
      fail('Invalid edition date');
    if (e.status === 'withdrawn') {
      if (e.articles.length || e.issues.length || e.overview.length)
        fail('Withdrawn content remains');
      return;
    }
    if (!e.issues.length || e.issues.length > 5 || !e.overview.length)
      fail('Incomplete edition');
    const articles = new Set(e.articles.map((a) => a.id));
    const issues = new Set(e.issues.map((i) => i.id));
    if (articles.size !== e.articles.length || issues.size !== e.issues.length)
      fail('Duplicate reference');
    for (const a of e.articles) {
      if (
        Date.parse(a.published_at) < Date.parse(e.window_start) ||
        Date.parse(a.published_at) >= Date.parse(e.window_end)
      )
        fail('Source outside time window');
    }
    for (const i of e.issues) {
      if (i.article_ids.some((a) => !articles.has(a)))
        fail('Unknown issue source');
      if (
        i.facts.some((f) =>
          f.article_ids.some((a) => !i.article_ids.includes(a)),
        )
      )
        fail('Unknown claim source');
    }
    if (e.overview.some((o) => o.issue_ids.some((i) => !issues.has(i))))
      fail('Unknown overview issue');
  });

export const history = z
  .object({ version: z.literal(1), editions: z.array(editionSchema) })
  .parse(raw);
if (new Set(history.editions.map((e) => e.id)).size !== history.editions.length)
  throw new Error('Duplicate edition IDs');
export type Edition = z.infer<typeof editionSchema>;
export const editions = [...history.editions].sort(
  (a, b) =>
    b.window_end.localeCompare(a.window_end) || b.id.localeCompare(a.id),
);
export const latestEdition = editions.find((e) => e.status === 'published');
export const days = [...new Set(editions.map((e) => e.day))].sort().reverse();
export const forDay = (day: string) => editions.filter((e) => e.day === day);
export const representative = (day: string) =>
  forDay(day).find((e) => e.status === 'published') || forDay(day)[0];
export const visibleDays = days.filter(
  (day) => representative(day).status === 'published',
);
export const editionUrl = (e: Edition) => `/briefings/${e.id}/`;
export const dayUrl = (day: string) => `/briefings/${day}/`;
export const dayLabel = (day: string) =>
  new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'short',
  }).format(new Date(`${day}T12:00:00+09:00`));
export const timeLabel = (stamp: string) =>
  new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(stamp));
export const rangeLabel = (e: Edition) =>
  `${dayLabel(new Date(Date.parse(e.window_start) + 9 * 3600000).toISOString().slice(0, 10))} ${timeLabel(e.window_start)} — ${dayLabel(e.day)} ${timeLabel(e.window_end)} KST`;
