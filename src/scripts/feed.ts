const storageKey = 'ai-brief:saved:v1';
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
let saved = readSaved();
document.querySelectorAll<HTMLElement>('[data-feed]').forEach((feed) => {
  const cards = [...feed.querySelectorAll<HTMLElement>('[data-card]')];
  const query = feed.querySelector<HTMLInputElement>('[data-query]')!;
  const region = feed.querySelector<HTMLSelectElement>('[data-region-filter]')!;
  const summary = feed.querySelector<HTMLSelectElement>(
    '[data-summary-filter]',
  )!;
  const more = feed.querySelector<HTMLButtonElement>('[data-more]')!;
  const pageSize = matchMedia('(max-width: 720px)').matches ? 9 : 18;
  let category = 'all',
    limit = pageSize;
  if (new URLSearchParams(location.search).get('saved') === '1')
    summary.value = 'saved';
  function render() {
    const terms = query.value
      .toLocaleLowerCase()
      .trim()
      .split(/\s+/)
      .filter(Boolean);
    let count = 0;
    for (const card of cards) {
      const matches =
        (category === 'all' || card.dataset.category === category) &&
        (region.value === 'all' || card.dataset.region === region.value) &&
        (summary.value === 'all' ||
          (summary.value === 'saved'
            ? saved.has(card.dataset.id!)
            : card.dataset.summary === summary.value)) &&
        terms.every((t) => card.dataset.search!.includes(t));
      card.hidden = !matches || count++ >= limit;
      const button = card.querySelector<HTMLButtonElement>('[data-bookmark]')!;
      button.setAttribute('aria-pressed', String(saved.has(card.dataset.id!)));
    }
    feed.querySelector('[data-result-count]')!.textContent = String(count);
    feed.querySelector<HTMLElement>('[data-empty]')!.hidden = count !== 0;
    feed.querySelector('[data-live]')!.textContent =
      `${count}개의 뉴스. ${Math.min(count, limit)}개 표시 중.`;
    more.hidden = count <= limit;
  }
  feed
    .querySelectorAll<HTMLButtonElement>('[data-category-filter]')
    .forEach((button) =>
      button.addEventListener('click', () => {
        category = button.dataset.categoryFilter!;
        limit = pageSize;
        feed
          .querySelectorAll('[data-category-filter]')
          .forEach((b) => b.setAttribute('aria-pressed', String(b === button)));
        render();
      }),
    );
  [query, region, summary].forEach((el) =>
    el.addEventListener(el === query ? 'input' : 'change', () => {
      limit = pageSize;
      render();
    }),
  );
  feed
    .querySelectorAll<HTMLButtonElement>('[data-bookmark]')
    .forEach((button) =>
      button.addEventListener('click', () => {
        const id = button.dataset.bookmark!;
        if (saved.has(id)) saved.delete(id);
        else saved.add(id);
        try {
          localStorage.setItem(storageKey, JSON.stringify([...saved]));
        } catch {
          feed.querySelector('[data-live]')!.textContent =
            '브라우저가 저장을 허용하지 않아 이번 화면에서만 유지됩니다.';
        }
        render();
      }),
    );
  feed.querySelector('[data-reset]')!.addEventListener('click', () => {
    query.value = '';
    region.value = summary.value = 'all';
    category = 'all';
    limit = pageSize;
    feed
      .querySelectorAll<HTMLElement>('[data-category-filter]')
      .forEach((b) =>
        b.setAttribute(
          'aria-pressed',
          String(b.dataset.categoryFilter === 'all'),
        ),
      );
    render();
  });
  more.addEventListener('click', () => {
    limit += pageSize;
    render();
  });
  window.addEventListener('storage', () => {
    saved = readSaved();
    render();
  });
  render();
});
