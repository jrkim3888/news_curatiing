// Calendar dates throughout the site use Korea time, regardless of the reader's zone.
export function kstDay(date = new Date()) {
  return new Date(date.getTime() + 9 * 3600000).toISOString().slice(0, 10);
}

export function todayLabel(date = new Date()) {
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'short',
  }).format(date);
}

export function recentWeek(date = new Date()) {
  return {
    start: kstDay(new Date(date.getTime() - 6 * 86400000)),
    end: kstDay(date),
  };
}

export function weekLabel(range: ReturnType<typeof recentWeek>) {
  return `최근 7일 · ${range.start.replaceAll('-', '.')} — ${range.end.replaceAll('-', '.')} · KST`;
}
