import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { createHash } from 'node:crypto';
import { z } from 'zod';

const root = resolve(process.env.AI_BRIEF_DATA_DIR || 'data');
const month = z.string().regex(/^\d{4}-(0[1-9]|1[0-2])$/);
const shard = z.object({
  count: z.number().int().nonnegative(),
  sha256: z.string().regex(/^[a-f0-9]{64}$/),
  days: z.array(z.string().regex(/^\d{4}-\d{2}-\d{2}$/)).optional(),
});
export const catalog = z
  .object({
    version: z.literal(2),
    updated_at: z.string().nullable(),
    collection_slot: z.string().nullable(),
    news: z.record(month, shard),
    briefings: z.record(month, shard),
  })
  .parse(JSON.parse(readFileSync(resolve(root, 'catalog.json'), 'utf8')));
export const sourceData = z
  .object({ version: z.literal(1), sources: z.array(z.unknown()) })
  .parse(JSON.parse(readFileSync(resolve(root, 'sources.json'), 'utf8')));
export function readMonths(kind: 'news' | 'briefings') {
  const listed = Object.keys(catalog[kind]).sort().reverse();
  const files = (
    existsSync(resolve(root, kind)) ? readdirSync(resolve(root, kind)) : []
  )
    .filter((name) => name.endsWith('.json'))
    .map((name) => name.slice(0, -5));
  if (files.length !== listed.length || files.some((m) => !listed.includes(m)))
    throw new Error(`Unlisted or missing ${kind} shard`);
  return listed.flatMap((m) => {
    const bytes = readFileSync(resolve(root, kind, `${m}.json`));
    if (
      createHash('sha256').update(bytes).digest('hex') !==
      catalog[kind][m].sha256
    )
      throw new Error(`Archive checksum mismatch: ${kind}/${m}`);
    const value = JSON.parse(bytes.toString());
    const records = value[kind === 'news' ? 'articles' : 'editions'];
    if (
      value.version !== 1 ||
      value.month !== m ||
      !Array.isArray(records) ||
      records.length !== catalog[kind][m].count
    )
      throw new Error(`Invalid ${kind}/${m} shard`);
    for (const record of records) {
      const actual =
        kind === 'news'
          ? new Date(Date.parse(record.published_at) + 9 * 3600000)
              .toISOString()
              .slice(0, 7)
          : record.day.slice(0, 7);
      if (actual !== m) throw new Error('Record in wrong archive month');
    }
    if (kind === 'news') {
      const actualDays = [
        ...new Set(
          records.map((record) =>
            new Date(Date.parse(record.published_at) + 9 * 3600000)
              .toISOString()
              .slice(0, 10),
          ),
        ),
      ].sort();
      if (JSON.stringify(actualDays) !== JSON.stringify(catalog.news[m].days))
        throw new Error(`Incorrect archive day index: ${m}`);
    }
    return records;
  });
}
