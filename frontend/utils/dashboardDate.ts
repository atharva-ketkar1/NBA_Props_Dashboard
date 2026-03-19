const DASHBOARD_TIME_ZONE = 'America/New_York';
const DASHBOARD_ROLLOVER_HOUR = 7;

const etDateTimeFormatter = new Intl.DateTimeFormat('en-US', {
  timeZone: DASHBOARD_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  hour12: false,
});

function getEtParts(referenceDate: Date = new Date()) {
  const parts = etDateTimeFormatter.formatToParts(referenceDate);

  return {
    year: parts.find((part) => part.type === 'year')?.value ?? '1970',
    month: parts.find((part) => part.type === 'month')?.value ?? '01',
    day: parts.find((part) => part.type === 'day')?.value ?? '01',
    hour: parts.find((part) => part.type === 'hour')?.value ?? '00',
  };
}

function shiftIsoDate(isoDate: string, days: number) {
  const [year, month, day] = isoDate.split('-').map(Number);
  const shifted = new Date(Date.UTC(year, month - 1, day));
  shifted.setUTCDate(shifted.getUTCDate() + days);

  return [
    shifted.getUTCFullYear(),
    String(shifted.getUTCMonth() + 1).padStart(2, '0'),
    String(shifted.getUTCDate()).padStart(2, '0'),
  ].join('-');
}

export function getDashboardDate(referenceDate: Date = new Date()) {
  const { year, month, day, hour } = getEtParts(referenceDate);
  const currentEtDate = `${year}-${month}-${day}`;

  return Number(hour) < DASHBOARD_ROLLOVER_HOUR
    ? shiftIsoDate(currentEtDate, -1)
    : currentEtDate;
}

export function getDashboardScheduleDates(referenceDate: Date = new Date()) {
  const dashboardDate = getDashboardDate(referenceDate);
  return [dashboardDate, shiftIsoDate(dashboardDate, 1)] as const;
}
