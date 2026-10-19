export const NEWS_PAGE_SIZE = 18;
export const HISTORY_PAGE_SIZE = 12;
export function pageNumbers(current: number, pages: number): (number | null)[] {
  const numbers = new Set([
    1,
    pages,
    current - 2,
    current - 1,
    current,
    current + 1,
    current + 2,
  ]);
  const sorted = [...numbers]
    .filter((n) => n >= 1 && n <= pages)
    .sort((a, b) => a - b);
  const result: (number | null)[] = [];
  for (const n of sorted) {
    const previous = result.at(-1);
    if (typeof previous === 'number' && n - previous > 1) result.push(null);
    result.push(n);
  }
  return result;
}
export const staticPageUrl = (base: string, page: number) =>
  page === 1 ? base : `${base}page/${page}/`;
export function parsePage(value: string | null) {
  return value && /^\d{1,7}$/.test(value) ? Math.max(1, Number(value)) : 1;
}
