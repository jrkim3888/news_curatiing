import { pageNumbers } from '../lib/pagination';
export function renderPagination(
  nav: HTMLElement,
  page: number,
  pages: number,
  url: (page: number) => string,
) {
  nav.replaceChildren();
  nav.hidden = pages <= 1;
  const add = (n: number, label: string, name: string) => {
    const a = document.createElement('a');
    a.href = url(n);
    a.textContent = label;
    a.dataset.page = String(n);
    a.setAttribute('aria-label', name);
    if (n === page) a.setAttribute('aria-current', 'page');
    nav.append(a);
  };
  if (page > 1) add(page - 1, '← 이전', '이전 페이지');
  for (const n of pageNumbers(page, pages)) {
    if (n === null) {
      const gap = document.createElement('span');
      gap.textContent = '…';
      gap.setAttribute('aria-hidden', 'true');
      nav.append(gap);
    } else add(n, String(n), `${n}페이지`);
  }
  if (page < pages) add(page + 1, '다음 →', '다음 페이지');
}
export function connectPagination(
  nav: HTMLElement,
  change: (page: number) => void,
) {
  nav.addEventListener('click', (event) => {
    const a = (event.target as Element).closest<HTMLAnchorElement>(
      'a[data-page]',
    );
    if (
      !a ||
      (event instanceof MouseEvent &&
        (event.button !== 0 ||
          event.ctrlKey ||
          event.metaKey ||
          event.shiftKey ||
          event.altKey))
    )
      return;
    event.preventDefault();
    change(Number(a.dataset.page));
  });
}
export interface SearchRecord {
  url: string;
  meta: Record<string, string>;
  excerpt: string;
}
export interface SearchResult {
  id: string;
  data: () => Promise<SearchRecord>;
}
export interface SearchResponse {
  results: SearchResult[];
}
export type Filters = Record<string, string | { any: string[] }>;
// Pagefind caches rejected fragment requests. A retry must clear that cache first.
export async function resetIndex() {
  try {
    const path = '/pagefind/pagefind.js';
    const module = await import(/* @vite-ignore */ path);
    await bounded(module.destroy());
  } catch {
    location.reload();
  }
}
function bounded<T>(promise: Promise<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(
      () => reject(new Error('Search request timed out')),
      15000,
    );
    promise.then(resolve, reject).finally(() => clearTimeout(timer));
  });
}
export async function searchIndex(
  query: string | null,
  filters: Filters = {},
  sorted = true,
): Promise<SearchResponse> {
  const path = '/pagefind/pagefind.js';
  const module = await bounded(import(/* @vite-ignore */ path));
  const response: SearchResponse = await bounded(
    module.search(query, {
      filters,
      ...(sorted ? { sort: { published: 'desc' } } : {}),
    }),
  );
  return {
    results: response.results.map((result) => ({
      id: result.id,
      data: () => bounded(result.data()),
    })),
  };
}
