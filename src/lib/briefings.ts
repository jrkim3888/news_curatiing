import { z } from 'zod';
import { readMonths } from './storage';
const raw = { version: 1, editions: readMonths('briefings') };

const stamp = z.iso.datetime({ offset: true });
const id = z.string().regex(/^[a-f0-9]{20}$/);
const text = z.string().min(1);
const factSchema = z.object({
  text,
  article_ids: z.array(id).min(1),
  links: z
    .array(
      z
        .object({
          text: z.string().min(2).max(30),
          article_id: id,
        })
        .strict(),
    )
    .max(3)
    .optional(),
});
const issue = z.object({
  id: z.string().regex(/^issue-[1-5]$/),
  title: text,
  category: text,
  facts: z.array(factSchema).min(1),
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
    takeaway: z.string().min(20).max(120).optional(),
    overview: z.array(z.object({ text, issue_ids: z.array(text).min(1) })),
    overview_revision: z.object({ revised_at: stamp, note: text }).optional(),
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
      if (
        e.articles.length ||
        e.issues.length ||
        e.overview.length ||
        e.takeaway
      )
        fail('Withdrawn content remains');
      return;
    }
    if (
      e.overview_revision &&
      Date.parse(e.overview_revision.revised_at) < Date.parse(e.generated_at)
    )
      fail('Overview revision predates generation');
    if (!e.issues.length || e.issues.length > 5 || !e.overview.length)
      fail('Incomplete edition');
    if (
      ['daily-brief-v3', 'daily-brief-v4'].includes(e.prompt_version) &&
      !e.takeaway
    )
      fail('Missing daily takeaway');
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
      for (const fact of i.facts) {
        const ranges: [number, number][] = [];
        let linkedLength = 0;
        for (const link of fact.links || []) {
          const start = fact.text.indexOf(link.text);
          const end = start + link.text.length;
          if (
            !fact.article_ids.includes(link.article_id) ||
            link.text !== link.text.trim() ||
            start < 0 ||
            fact.text.indexOf(link.text, start + 1) !== -1 ||
            ranges.some(([left, right]) => start < right && end > left)
          )
            fail('Invalid keyword source link');
          ranges.push([start, end]);
          linkedLength += link.text.length;
        }
        if (linkedLength > fact.text.length / 2)
          fail('Most of the fact must remain unlinked');
      }
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
// Render escaped text, resolving URLs only from the edition's source snapshots.
export function splitFact(fact: z.infer<typeof factSchema>) {
  const parts: { text: string; article_id?: string }[] = [];
  let offset = 0;
  const links = [...(fact.links || [])].sort(
    (a, b) => fact.text.indexOf(a.text) - fact.text.indexOf(b.text),
  );
  for (const link of links) {
    const start = fact.text.indexOf(link.text);
    if (start > offset) parts.push({ text: fact.text.slice(offset, start) });
    parts.push(link);
    offset = start + link.text.length;
  }
  if (offset < fact.text.length) parts.push({ text: fact.text.slice(offset) });
  return parts;
}
export const editions = [...history.editions].sort(
  (a, b) =>
    b.window_end.localeCompare(a.window_end) || b.id.localeCompare(a.id),
);
export const latestEdition = editions.find((e) => e.status === 'published');
export const days = [...new Set(editions.map((e) => e.day))].sort().reverse();
const editionByDay = new Map(editions.map((e) => [e.day, e]));
export const forDay = (day: string) =>
  editionByDay.has(day) ? [editionByDay.get(day)!] : [];
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
export const dateTimeLabel = (stamp: string) =>
  new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    month: 'long',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(new Date(stamp));
export const rangeLabel = (e: Edition) =>
  `${dayLabel(new Date(Date.parse(e.window_start) + 9 * 3600000).toISOString().slice(0, 10))} ${timeLabel(e.window_start)} — ${dayLabel(e.day)} ${timeLabel(e.window_end)} KST`;

export const briefingRecord = (e: Edition) => ({
  id: e.id,
  day: e.day,
  headline: e.headline,
  takeaway: e.takeaway || e.overview[0]?.text || '',
  scheduled_at: e.scheduled_at,
  topics: [...new Set(e.issues.map((i) => i.category))].slice(0, 3),
  issue_count: e.issues.length,
  article_count: e.articles.length,
});
export type BriefingRecord = ReturnType<typeof briefingRecord>;
