import type { Article } from '../lib/data';
import type { BriefingRecord } from '../lib/briefings';
import { kstDay, todayLabel } from '../lib/dates';
export interface Source {
  id: string;
  name: string;
  role: string;
}
const roles: Record<string, string> = {
  official: '공식 발표',
  media: '언론 보도',
  signal: '커뮤니티',
  analysis: '전문가 분석',
  official_research: '공식 연구',
  official_community: '공식·커뮤니티',
};
function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className = '',
  text = '',
) {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = text;
  return node;
}
export function newsCard(
  a: Article,
  sources: Map<string, Source>,
  saved: Set<string>,
) {
  if (
    !/^[a-f0-9]{20}$/.test(a.id) ||
    !a.sources?.length ||
    !sources.has(a.sources[0].source_id)
  )
    throw new Error('Invalid search record');
  const primary = sources.get(a.sources[0].source_id)!;
  const card = el('article', 'news-card');
  card.dataset.card = '';
  card.dataset.id = a.id;
  card.dataset.category = a.category;
  card.dataset.region = a.region;
  card.dataset.summary = a.status;
  card.dataset.date = kstDay(new Date(a.published_at));
  card.dataset.sources = a.sources.map((s) => s.source_id).join(' ');
  const top = el('div', 'card-top');
  top.append(el('span', 'category', a.category));
  const bookmark = el('button', 'bookmark');
  bookmark.type = 'button';
  bookmark.dataset.bookmark = a.id;
  bookmark.setAttribute('aria-label', `${a.title} 저장`);
  bookmark.setAttribute('aria-pressed', String(saved.has(a.id)));
  // Only this fixed icon markup is inserted as HTML; all source/model content uses textContent.
  bookmark.innerHTML =
    '<svg width="18" height="20" viewBox="0 0 20 22" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M5 3h10v16l-5-3-5 3z" /></svg>';
  top.append(bookmark);
  const h3 = el('h3');
  const aLink = el('a', '', a.title);
  aLink.href = `/news/${a.id}/`;
  h3.append(aLink);
  const footer = el('div', 'card-footer');
  const dot = el('span', `source-dot ${primary.role}`);
  dot.setAttribute('aria-hidden', 'true');
  const time = el(
    'time',
    'card-date',
    new Intl.DateTimeFormat('ko-KR', {
      timeZone: 'Asia/Seoul',
      month: 'long',
      day: 'numeric',
    }).format(new Date(a.published_at)),
  );
  time.dateTime = a.published_at;
  footer.append(dot, el('span', '', primary.name), time);
  const tags = el('div', 'card-tags');
  tags.append(
    el('span', '', roles[primary.role] || primary.role),
    el('span', '', a.region === 'kr' ? '국내' : '해외'),
    el(
      'span',
      a.status === 'summarized' ? 'ai-tag' : '',
      a.status === 'summarized' ? 'AI 요약' : '원문 링크',
    ),
  );
  if (a.sources.length > 1)
    tags.append(el('span', '', `출처 ${a.sources.length}`));
  card.append(
    top,
    h3,
    el(
      'p',
      'card-description',
      a.summary || a.excerpt || '원문에서 자세한 내용을 확인하세요.',
    ),
    footer,
    tags,
  );
  return card;
}
export function briefingCard(e: BriefingRecord) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(e.id))
    throw new Error('Invalid briefing record');
  const card = el('li', 'brief-day-card');
  card.dataset.historyDay = e.day;
  const date = el('div', 'brief-day-date');
  const time = el('time', '', todayLabel(new Date(`${e.day}T12:00:00+09:00`)));
  time.dateTime = e.day;
  date.append(time, el('span', '', '06:30 발행 기준 · KST'));
  const body = el('div', 'brief-day-body');
  const link = el('a');
  link.href = `/briefings/${e.id}/`;
  link.append(el('h3', '', e.headline));
  const topics = el('div', 'brief-topic-chips');
  e.topics.forEach((t) => topics.append(el('span', '', t)));
  const footer = el('div', 'brief-day-footer');
  const more = el('a', '', '이날의 브리핑 ↗');
  more.href = link.href;
  more.setAttribute(
    'aria-label',
    `${todayLabel(new Date(`${e.day}T12:00:00+09:00`))} 브리핑 보기`,
  );
  footer.append(
    el('span', '', `이슈 ${e.issue_count}개 · 근거 기사 ${e.article_count}건`),
    more,
  );
  body.append(link, el('p', '', e.takeaway), topics, footer);
  card.append(date, body);
  return card;
}
