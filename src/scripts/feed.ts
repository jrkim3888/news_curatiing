import { NEWS_PAGE_SIZE, parsePage } from '../lib/pagination';
import { newsCard, type Source } from './cards';
import { archivePages, ArchiveChangedError } from './archive-pages';
import {
  searchIndex,
  resetIndex,
  renderPagination,
  connectPagination,
  type Filters,
} from './paging';
import type { Article } from '../lib/data';
const storageKey = 'ai-brief:saved:v1',
  viewKey = 'ai-brief:view:v1';
function readSaved(): Set<string> {
  try {
    const v = JSON.parse(localStorage.getItem(storageKey) || '[]');
    return new Set(
      Array.isArray(v)
        ? v.filter((s) => typeof s === 'string' && /^[a-f0-9]{20}$/.test(s))
        : [],
    );
  } catch {
    return new Set();
  }
}
function readView() {
  try {
    return localStorage.getItem(viewKey) === 'list' ? 'list' : 'card';
  } catch {
    return 'card';
  }
}
function validDay(value: string | null) {
  if (!value || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return '';
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) &&
    parsed.toISOString().slice(0, 10) === value
    ? value
    : '';
}
document.querySelectorAll<HTMLElement>('[data-feed]').forEach((feed) => {
  const config: {
    sources: Source[];
    days: string[];
    page: number;
    total: number;
    version: string;
  } = JSON.parse(feed.querySelector('[data-feed-config]')!.textContent!);
  const sources = new Map(config.sources.map((s) => [s.id, s]));
  const loadPage = archivePages(config.version, config.total);
  const query = feed.querySelector<HTMLInputElement>('[data-query]')!;
  const region = feed.querySelector<HTMLSelectElement>('[data-region-filter]')!;
  const summary = feed.querySelector<HTMLSelectElement>(
    '[data-summary-filter]',
  )!;
  const source = feed.querySelector<HTMLSelectElement>('[data-source-filter]')!;
  const from = feed.querySelector<HTMLInputElement>('[data-date-from]')!,
    to = feed.querySelector<HTMLInputElement>('[data-date-to]')!;
  const savedOnly = feed.querySelector<HTMLInputElement>(
    '[data-saved-filter]',
  )!;
  const results = feed.querySelector<HTMLElement>('[data-results]')!,
    nav = feed.querySelector<HTMLElement>('[data-pagination]')!;
  // These cards are already in the HTML. Reuse them on entry and when returning.
  const initialCards = [...results.children];
  const error = feed.querySelector<HTMLElement>('[data-load-error]')!,
    empty = feed.querySelector<HTMLElement>('[data-empty]')!;
  const live = feed.querySelector<HTMLElement>('[data-live]')!,
    count = feed.querySelector('[data-result-count]')!,
    status = feed.querySelector('[data-page-status]')!;
  const tabs = [
    ...feed.querySelectorAll<HTMLButtonElement>('[data-category-filter]'),
  ];
  const viewButtons = [
    ...feed.querySelectorAll<HTMLButtonElement>('button[data-view]'),
  ];
  let category = 'all',
    view = readView(),
    saved = readSaved(),
    page = config.page,
    version = 0,
    pending: AbortController | undefined,
    timer: ReturnType<typeof setTimeout> | undefined;
  function readLocation() {
    const params = new URLSearchParams(location.search);
    query.value = params.get('q') || '';
    category = tabs.some(
      (b) => b.dataset.categoryFilter === params.get('category'),
    )
      ? params.get('category')!
      : 'all';
    for (const [select, key] of [
      [region, 'region'],
      [summary, 'summary'],
      [source, 'source'],
    ] as const)
      select.value = [...select.options].some(
        (o) => o.value === params.get(key),
      )
        ? params.get(key)!
        : 'all';
    from.value = validDay(params.get('from'));
    to.value = validDay(params.get('to'));
    savedOnly.checked = params.get('saved') === '1';
    view = ['card', 'list'].includes(params.get('view') || '')
      ? params.get('view')!
      : readView();
    page = parsePage(
      params.get('page') ||
        location.pathname.match(/\/page\/(\d+)\//)?.[1] ||
        '1',
    );
  }
  function url(nextPage = page) {
    const params = new URLSearchParams();
    if (query.value) params.set('q', query.value);
    if (category !== 'all') params.set('category', category);
    for (const [select, key] of [
      [region, 'region'],
      [summary, 'summary'],
      [source, 'source'],
    ] as const)
      if (select.value !== 'all') params.set(key, select.value);
    if (from.value) params.set('from', from.value);
    if (to.value) params.set('to', to.value);
    if (savedOnly.checked) params.set('saved', '1');
    params.set('view', view);
    if (nextPage > 1) params.set('page', String(nextPage));
    return `/archive/?${params}`;
  }
  function controls() {
    tabs.forEach((b) =>
      b.setAttribute(
        'aria-pressed',
        String(b.dataset.categoryFilter === category),
      ),
    );
    viewButtons.forEach((b) =>
      b.setAttribute('aria-pressed', String(b.dataset.view === view)),
    );
    results.dataset.view = view;
  }
  function hasFilters() {
    return Boolean(
      query.value.trim() ||
      category !== 'all' ||
      region.value !== 'all' ||
      summary.value !== 'all' ||
      source.value !== 'all' ||
      from.value ||
      to.value ||
      savedOnly.checked,
    );
  }
  function syncBookmarks() {
    results
      .querySelectorAll<HTMLElement>('[data-bookmark]')
      .forEach((button) =>
        button.setAttribute(
          'aria-pressed',
          String(saved.has(button.dataset.bookmark!)),
        ),
      );
  }
  function clampPage(total: number) {
    const clamped = Math.min(
      page,
      Math.max(1, Math.ceil(total / NEWS_PAGE_SIZE)),
    );
    if (clamped !== page) {
      page = clamped;
      history.replaceState(null, '', url());
    }
  }
  function loading() {
    results.setAttribute('aria-busy', 'true');
    results.hidden = true;
    nav.hidden = true;
    count.textContent = '…';
    status.textContent = hasFilters()
      ? '검색 결과를 불러오는 중…'
      : '뉴스를 불러오는 중…';
    live.textContent = status.textContent;
  }
  function show(total: number, focus: boolean) {
    const pages = Math.max(1, Math.ceil(total / NEWS_PAGE_SIZE));
    results.hidden = false;
    count.textContent = String(total);
    empty.hidden = total !== 0;
    status.textContent = total
      ? `${page} / ${pages} 페이지 · ${NEWS_PAGE_SIZE}개씩 보기`
      : '';
    live.textContent = `${total}개의 뉴스. ${page}페이지, ${results.children.length}개 표시 중.`;
    renderPagination(nav, page, pages, url);
    if (focus) {
      results.tabIndex = -1;
      results.focus({ preventScroll: true });
      feed.querySelector('.feed-results')!.scrollIntoView({ block: 'start' });
    }
  }
  async function render(focus = false) {
    const current = ++version;
    pending?.abort();
    pending = undefined;
    results.removeAttribute('aria-busy');
    controls();
    error.hidden = true;
    empty.hidden = true;
    const invalid = Boolean(from.value && to.value && from.value > to.value);
    feed.querySelector<HTMLElement>('#date-filter-error')!.hidden = !invalid;
    to.setAttribute('aria-invalid', String(invalid));
    if (invalid) {
      results.removeAttribute('aria-busy');
      results.replaceChildren();
      nav.hidden = true;
      count.textContent = '0';
      status.textContent = '';
      live.textContent = '날짜 범위를 확인해 주세요.';
      return;
    }
    const filters: Filters = { kind: 'news' };
    if (category !== 'all') filters.category = category;
    for (const [select, key] of [
      [region, 'region'],
      [summary, 'summary'],
      [source, 'source'],
    ] as const)
      if (select.value !== 'all') filters[key] = select.value;
    let noMatches = false;
    if (from.value || to.value) {
      const days = config.days.filter(
        (d) => (!from.value || d >= from.value) && (!to.value || d <= to.value),
      );
      if (!days.length) noMatches = true;
      else filters.date = { any: days };
    }
    if (savedOnly.checked) {
      if (!saved.size) noMatches = true;
      else filters.article_id = { any: [...saved] };
    }
    try {
      if (!hasFilters()) {
        clampPage(config.total);
        if (page === config.page) {
          if (results.firstElementChild !== initialCards[0])
            results.replaceChildren(...initialCards);
          syncBookmarks();
        } else {
          loading();
          const controller = new AbortController();
          pending = controller;
          const timeout = setTimeout(() => controller.abort(), 15000);
          let records: Article[];
          try {
            records = await loadPage(page, controller.signal);
          } finally {
            clearTimeout(timeout);
          }
          if (current !== version) return;
          results.replaceChildren(
            ...records.map((a) => newsCard(a, sources, saved)),
          );
        }
        show(config.total, focus);
        return;
      }
      loading();
      const response = noMatches
        ? { results: [] }
        : await searchIndex(query.value.trim() || null, filters);
      if (current !== version) return;
      const total = response.results.length;
      clampPage(total);
      const records = await Promise.all(
        response.results
          .slice((page - 1) * NEWS_PAGE_SIZE, page * NEWS_PAGE_SIZE)
          .map(
            async (r) => JSON.parse((await r.data()).meta.record) as Article,
          ),
      );
      if (current !== version) return;
      results.replaceChildren(
        ...records.map((a) => newsCard(a, sources, saved)),
      );
      show(total, focus);
    } catch (cause) {
      if (current === version) {
        if (
          cause instanceof ArchiveChangedError &&
          history.state?.archiveReloaded !== config.version
        ) {
          // A deployment removed the old version: obtain fresh HTML/config.
          // Keep a marker through reload so a missing asset cannot cause a loop.
          history.replaceState({ archiveReloaded: config.version }, '', url());
          location.reload();
          return;
        }
        results.hidden = true;
        error.hidden = false;
        nav.hidden = true;
        count.textContent = '—';
        status.textContent = '';
        live.textContent = '목록을 불러오지 못했습니다. 다시 시도해 주세요.';
      }
    } finally {
      if (current === version) results.removeAttribute('aria-busy');
    }
  }
  function change() {
    clearTimeout(timer);
    page = 1;
    history.replaceState(null, '', url());
    void render();
  }
  query.addEventListener('input', () => {
    clearTimeout(timer);
    ++version;
    pending?.abort();
    timer = setTimeout(change, 150);
  });
  [region, summary, source, from, to, savedOnly].forEach((el) =>
    el.addEventListener('change', change),
  );
  tabs.forEach((b) =>
    b.addEventListener('click', () => {
      category = b.dataset.categoryFilter!;
      change();
    }),
  );
  viewButtons.forEach((b) =>
    b.addEventListener('click', () => {
      view = b.dataset.view!;
      try {
        localStorage.setItem(viewKey, view);
      } catch {}
      controls();
      history.replaceState(null, '', url());
    }),
  );
  feed.addEventListener('click', (event) => {
    const button = (event.target as Element).closest<HTMLButtonElement>(
      '[data-bookmark]',
    );
    if (!button) return;
    const id = button.dataset.bookmark!;
    if (saved.has(id)) saved.delete(id);
    else saved.add(id);
    button.setAttribute('aria-pressed', String(saved.has(id)));
    let persisted = true;
    try {
      localStorage.setItem(storageKey, JSON.stringify([...saved]));
    } catch {
      persisted = false;
    }
    if (savedOnly.checked) {
      savedOnly.focus();
      void render();
    }
    if (!persisted)
      live.textContent =
        '브라우저가 저장을 허용하지 않아 이번 화면에서만 유지됩니다.';
  });
  feed.querySelectorAll('[data-reset]').forEach((button) =>
    button.addEventListener('click', () => {
      query.value = from.value = to.value = '';
      region.value = summary.value = source.value = category = 'all';
      savedOnly.checked = false;
      change();
    }),
  );
  connectPagination(nav, (n) => {
    clearTimeout(timer);
    page = n;
    history.pushState(null, '', url());
    void render(true);
  });
  feed.querySelector('[data-retry]')!.addEventListener('click', () => {
    if (hasFilters()) void resetIndex().then(() => render());
    else void render();
  });
  window.addEventListener('popstate', () => {
    clearTimeout(timer);
    readLocation();
    void render();
  });
  window.addEventListener('storage', (event) => {
    if (event.key === storageKey || event.key === null) {
      saved = readSaved();
      if (savedOnly.checked) void render();
      else syncBookmarks();
    }
  });
  readLocation();
  void render();
});
