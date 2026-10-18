const storageKey = 'ai-brief:saved:v1';
const viewKey = 'ai-brief:view:v1';
function readSaved(): Set<string> {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey) || '[]');
    return new Set(
      Array.isArray(value)
        ? value.filter((s): s is string => typeof s === 'string')
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
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) &&
    date.toISOString().slice(0, 10) === value
    ? value
    : '';
}

document.querySelectorAll<HTMLElement>('[data-feed]').forEach((feed) => {
  let saved = readSaved();
  const cards = [...feed.querySelectorAll<HTMLElement>('[data-card]')];
  const query = feed.querySelector<HTMLInputElement>('[data-query]')!;
  const region = feed.querySelector<HTMLSelectElement>('[data-region-filter]')!;
  const summary = feed.querySelector<HTMLSelectElement>(
    '[data-summary-filter]',
  )!;
  const source = feed.querySelector<HTMLSelectElement>('[data-source-filter]')!;
  const from = feed.querySelector<HTMLInputElement>('[data-date-from]')!;
  const to = feed.querySelector<HTMLInputElement>('[data-date-to]')!;
  const savedOnly = feed.querySelector<HTMLInputElement>(
    '[data-saved-filter]',
  )!;
  const more = feed.querySelector<HTMLButtonElement>('[data-more]')!;
  const results = feed.querySelector<HTMLElement>('[data-results]')!;
  const tabs = [
    ...feed.querySelectorAll<HTMLButtonElement>('[data-category-filter]'),
  ];
  const viewButtons = [
    ...feed.querySelectorAll<HTMLButtonElement>('button[data-view]'),
  ];
  const pageSize = matchMedia('(max-width: 720px)').matches ? 9 : 18;
  let category = 'all',
    limit = pageSize,
    view = readView();

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
    ] as const) {
      select.value = [...select.options].some(
        (o) => o.value === params.get(key),
      )
        ? params.get(key)!
        : 'all';
    }
    from.value = validDay(params.get('from'));
    to.value = validDay(params.get('to'));
    savedOnly.checked = params.get('saved') === '1';
    view = ['card', 'list'].includes(params.get('view') || '')
      ? params.get('view')!
      : readView();
  }

  function syncLocation() {
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
    history.replaceState(
      null,
      '',
      `${location.pathname}?${params}${location.hash}`,
    );
  }

  function render() {
    const terms = query.value
      .toLocaleLowerCase()
      .trim()
      .split(/\s+/)
      .filter(Boolean);
    const invalidRange = Boolean(
      from.value && to.value && from.value > to.value,
    );
    feed.querySelector<HTMLElement>('#date-filter-error')!.hidden =
      !invalidRange;
    to.setAttribute('aria-invalid', String(invalidRange));
    let count = 0;
    for (const card of cards) {
      const matches =
        !invalidRange &&
        (category === 'all' || card.dataset.category === category) &&
        (region.value === 'all' || card.dataset.region === region.value) &&
        (summary.value === 'all' || card.dataset.summary === summary.value) &&
        (source.value === 'all' ||
          card.dataset.sources!.split(' ').includes(source.value)) &&
        (!from.value || card.dataset.date! >= from.value) &&
        (!to.value || card.dataset.date! <= to.value) &&
        (!savedOnly.checked || saved.has(card.dataset.id!)) &&
        terms.every((term) => card.dataset.search!.includes(term));
      card.hidden = !matches || count++ >= limit;
      card
        .querySelector('[data-bookmark]')!
        .setAttribute('aria-pressed', String(saved.has(card.dataset.id!)));
    }
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
    feed.querySelector('[data-result-count]')!.textContent = String(count);
    feed.querySelector<HTMLElement>('[data-empty]')!.hidden =
      count !== 0 || invalidRange;
    feed.querySelector('[data-live]')!.textContent = invalidRange
      ? '날짜 범위를 확인해 주세요.'
      : `${count}개의 뉴스. ${Math.min(count, limit)}개 표시 중.`;
    more.hidden = count <= limit;
  }

  function changeFilters() {
    limit = pageSize;
    render();
    syncLocation();
  }
  tabs.forEach((button) =>
    button.addEventListener('click', () => {
      category = button.dataset.categoryFilter!;
      changeFilters();
    }),
  );
  query.addEventListener('input', changeFilters);
  [region, summary, source, from, to, savedOnly].forEach((el) =>
    el.addEventListener('change', changeFilters),
  );
  viewButtons.forEach((button) =>
    button.addEventListener('click', () => {
      view = button.dataset.view!;
      try {
        localStorage.setItem(viewKey, view);
      } catch {
        /* View still works for this visit. */
      }
      render();
      syncLocation();
    }),
  );
  feed
    .querySelectorAll<HTMLButtonElement>('[data-bookmark]')
    .forEach((button) =>
      button.addEventListener('click', () => {
        const id = button.dataset.bookmark!;
        let persisted = true;
        if (saved.has(id)) saved.delete(id);
        else saved.add(id);
        try {
          localStorage.setItem(storageKey, JSON.stringify([...saved]));
        } catch {
          persisted = false;
        }
        render();
        if (savedOnly.checked && !saved.has(id)) savedOnly.focus();
        if (!persisted)
          feed.querySelector('[data-live]')!.textContent =
            '브라우저가 저장을 허용하지 않아 이번 화면에서만 유지됩니다.';
      }),
    );
  feed.querySelectorAll('[data-reset]').forEach((button) =>
    button.addEventListener('click', () => {
      query.value = from.value = to.value = '';
      region.value = summary.value = source.value = category = 'all';
      savedOnly.checked = false;
      changeFilters();
    }),
  );
  more.addEventListener('click', () => {
    limit += pageSize;
    render();
  });
  window.addEventListener('storage', (event) => {
    if (event.key === storageKey || event.key === null) {
      saved = readSaved();
      render();
    }
  });
  window.addEventListener('popstate', () => {
    readLocation();
    limit = pageSize;
    render();
  });
  readLocation();
  render();
});
