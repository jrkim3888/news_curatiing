import { HISTORY_PAGE_SIZE, parsePage } from '../lib/pagination';
import { briefingCard } from './cards';
import {
  searchIndex,
  resetIndex,
  renderPagination,
  connectPagination,
} from './paging';
import type { BriefingRecord } from '../lib/briefings';
const month = document.querySelector<HTMLInputElement>('#history-month');
const query = document.querySelector<HTMLInputElement>('#history-query');
if (month && query) {
  const results = document.querySelector<HTMLElement>('#history-results')!;
  const count = document.querySelector('#history-count')!;
  const status = document.querySelector('#history-page-status')!;
  const empty = document.querySelector<HTMLElement>('#history-empty')!;
  const error = document.querySelector<HTMLElement>('#history-error')!;
  const nav = document.querySelector<HTMLElement>('[data-pagination]')!;
  let page = 1,
    version = 0,
    timer: ReturnType<typeof setTimeout>;
  const read = () => {
    const params = new URLSearchParams(location.search);
    month.value = /^\d{4}-(0[1-9]|1[0-2])$/.test(params.get('month') || '')
      ? params.get('month')!
      : '';
    query.value = params.get('q') || '';
    page = parsePage(
      params.get('page') ||
        location.pathname.match(/\/page\/(\d+)\//)?.[1] ||
        '1',
    );
  };
  const url = (n = page) => {
    const params = new URLSearchParams();
    if (month.value) params.set('month', month.value);
    if (query.value) params.set('q', query.value);
    if (n > 1) params.set('page', String(n));
    return `/briefings/${params.size ? `?${params}` : ''}`;
  };
  async function render(focus = false) {
    const current = ++version;
    results.hidden = true;
    results.setAttribute('aria-busy', 'true');
    nav.hidden = true;
    empty.hidden = true;
    error.hidden = true;
    status.textContent = '브리핑을 찾고 있습니다.';
    try {
      const response = await searchIndex(query!.value.trim() || null, {
        kind: 'briefing',
        ...(month!.value ? { month: month!.value } : {}),
      });
      if (current !== version) return;
      const total = response.results.length,
        pages = Math.max(1, Math.ceil(total / HISTORY_PAGE_SIZE));
      if (page > pages) {
        page = pages;
        history.replaceState(null, '', url());
      }
      const records = await Promise.all(
        response.results
          .slice((page - 1) * HISTORY_PAGE_SIZE, page * HISTORY_PAGE_SIZE)
          .map(
            async (r) =>
              JSON.parse((await r.data()).meta.record) as BriefingRecord,
          ),
      );
      if (current !== version) return;
      const list = document.createElement('ol');
      list.className = 'brief-history-list';
      list.append(...records.map(briefingCard));
      results.replaceChildren(list);
      results.hidden = false;
      empty.hidden = total !== 0;
      count.textContent = `${total}일의 브리핑`;
      status.textContent = total
        ? `${total}일의 브리핑 · ${page} / ${pages} 페이지`
        : '';
      renderPagination(nav, page, pages, url);
      if (focus) {
        results.focus({ preventScroll: true });
        results.scrollIntoView({ block: 'start' });
      }
    } catch {
      if (current === version) {
        error.hidden = false;
        status.textContent = '';
        count.textContent = '목록을 불러오지 못했습니다.';
      }
    } finally {
      if (current === version) results.removeAttribute('aria-busy');
    }
  }
  const change = () => {
    clearTimeout(timer);
    page = 1;
    history.replaceState(null, '', url());
    void render();
  };
  month.addEventListener('input', change);
  query.addEventListener('input', () => {
    clearTimeout(timer);
    ++version;
    timer = setTimeout(change, 150);
  });
  document.querySelector('#history-reset')!.addEventListener('click', () => {
    month.value = query.value = '';
    change();
  });
  document
    .querySelector('#history-retry')!
    .addEventListener('click', () => void resetIndex().then(() => render()));
  connectPagination(nav, (n) => {
    clearTimeout(timer);
    page = n;
    history.pushState(null, '', url());
    void render(true);
  });
  window.addEventListener('popstate', () => {
    clearTimeout(timer);
    read();
    void render();
  });
  read();
  void render();
}
