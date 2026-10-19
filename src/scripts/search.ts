import { NEWS_PAGE_SIZE, parsePage } from '../lib/pagination';
import {
  searchIndex,
  resetIndex,
  renderPagination,
  connectPagination,
} from './paging';
const form = document.querySelector<HTMLFormElement>('#search-form')!;
const input = document.querySelector<HTMLInputElement>('#global-search')!;
const status = document.querySelector('#search-status')!;
const results = document.querySelector<HTMLElement>('#search-results')!;
const nav = document.querySelector<HTMLElement>('[data-pagination]')!;
const retry = document.querySelector<HTMLButtonElement>('#search-retry')!;
let page = 1,
  version = 0;
const read = () => {
  const params = new URLSearchParams(location.search);
  input.value = params.get('q') || '';
  page = parsePage(params.get('page') || '1');
};
const url = (n = page) => {
  const params = new URLSearchParams({ q: input.value.trim() });
  if (n > 1) params.set('page', String(n));
  return `/search/?${params}`;
};
async function search(focus = false) {
  const current = ++version;
  results.replaceChildren();
  nav.hidden = true;
  retry.hidden = true;
  const q = input.value.trim();
  if (!q) {
    status.textContent = '검색어를 입력하세요.';
    return;
  }
  status.textContent = '검색 중…';
  try {
    const response = await searchIndex(q, {}, false);
    if (current !== version) return;
    const total = response.results.length,
      pages = Math.max(1, Math.ceil(total / NEWS_PAGE_SIZE));
    if (page > pages) {
      page = pages;
      history.replaceState(null, '', url());
    }
    const records = await Promise.all(
      response.results
        .slice((page - 1) * NEWS_PAGE_SIZE, page * NEWS_PAGE_SIZE)
        .map((r) => r.data()),
    );
    if (current !== version) return;
    for (const r of records) {
      const li = document.createElement('li'),
        a = document.createElement('a'),
        p = document.createElement('p');
      a.href = r.url;
      a.textContent = r.meta.title;
      const doc = new DOMParser().parseFromString(r.excerpt, 'text/html');
      p.textContent = doc.body.textContent;
      li.append(a, p);
      results.append(li);
    }
    status.textContent = total
      ? `${total}개 검색 결과 · ${page} / ${pages} 페이지`
      : '검색 결과가 없습니다. 다른 키워드를 입력해보세요.';
    renderPagination(nav, page, pages, url);
    if (focus) {
      results.focus({ preventScroll: true });
      results.scrollIntoView({ block: 'start' });
    }
  } catch {
    if (current === version) {
      status.textContent =
        '검색 인덱스를 불러올 수 없습니다. 잠시 뒤 다시 시도하세요.';
      retry.hidden = false;
    }
  }
}
form.addEventListener('submit', (event) => {
  event.preventDefault();
  page = 1;
  history.replaceState(null, '', url());
  void search();
});
connectPagination(nav, (n) => {
  page = n;
  history.pushState(null, '', url());
  void search(true);
});
retry.addEventListener('click', () => void resetIndex().then(() => search()));
window.addEventListener('popstate', () => {
  read();
  void search();
});
read();
void search();
