const DAY = 86_400_000;
const WEEKDAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"];
const weekday = day => ((day + 3) % 7 + 7) % 7;
const dateString = day => new Date(day * DAY).toISOString().slice(0, 10);

function dayNumber(value) {
  const day = Date.parse(value + "T00:00:00Z") / DAY;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(day) || dateString(day) !== value) {
    throw new Error("Enter a valid date.");
  }
  return day;
}

function timeInZone(now, timeZone) {
  let parts;
  try {
    if (!/^[A-Za-z][A-Za-z0-9_+/-]*$/.test(timeZone)) throw new Error();
    parts = new Intl.DateTimeFormat("en-CA", {
      timeZone, year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23"
    }).formatToParts(now);
  } catch {
    throw new Error("Enter a valid time zone, such as Asia/Kolkata.");
  }
  const values = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return { date: `${values.year}-${values.month}-${values.day}`, time: `${values.hour}:${values.minute}` };
}

// DTSTART is the first future occurrence for each time of day. COUNT keeps the
// end date inclusive in the prescription's time zone, including across DST.
export function planMedicineSchedule(schedule, now = new Date()) {
  const { frequency, interval, days, times, startDate, endDate, timeZone } = schedule;
  if (!["once", "daily", "weekly"].includes(frequency)) throw new Error("Pick the prescribed frequency.");
  if (!Number.isInteger(interval) || interval < 1 || interval > 365) throw new Error("Enter a repeat interval between 1 and 365.");
  if (!times.length || times.some(time => !/^([01]\d|2[0-3]):[0-5]\d$/.test(time))) throw new Error("Enter each prescribed time.");
  if (new Set(times).size !== times.length) throw new Error("Each time should appear once.");
  const start = dayNumber(startDate);
  const end = frequency === "once" ? start : endDate ? dayNumber(endDate) : Infinity;
  if (end < start) throw new Error("The end date must be on or after the start date.");
  if (!timeZone) throw new Error("Enter the time zone for this prescription.");
  const today = timeInZone(now, timeZone);
  const todayDay = dayNumber(today.date);
  const selectedDays = WEEKDAYS.map((code, i) => days.includes(code) ? i : -1).filter(i => i >= 0);
  if (frequency === "weekly" && !selectedDays.length) throw new Error("Select the prescribed days of the week.");

  const occurrences = [];
  for (const time of [...times].sort()) {
    const earliest = Math.max(start, todayDay + Number(time <= today.time));
    let first = start, rule = "", count = 1;
    if (frequency === "once") {
      if (first < earliest) continue;
    } else if (frequency === "daily") {
      first = start + Math.ceil((earliest - start) / interval) * interval;
      count = Math.floor((end - first) / interval) + 1;
      rule = `FREQ=DAILY;INTERVAL=${interval}`;
    } else {
      const cycle = 7 * interval, startWeek = start - weekday(start);
      let week = startWeek + Math.floor((earliest - startWeek) / cycle) * cycle;
      const nextDay = selectedDays.find(day => week + day >= earliest);
      first = nextDay === undefined ? week + cycle + selectedDays[0] : week + nextDay;
      const offsets = selectedDays.map(day => (day - weekday(first) + cycle) % cycle);
      count = offsets.reduce((total, offset) => total + Math.max(0, Math.floor((end - first - offset) / cycle) + 1), 0);
      rule = `FREQ=WEEKLY;INTERVAL=${interval};BYDAY=${selectedDays.map(i => WEEKDAYS[i]).join(",")};WKST=MO`;
    }
    if (first > end || count < 1) continue;
    const startAt = dateString(first) + "T" + time + ":00";
    const endAt = new Date(Date.parse(startAt + "Z") + 5 * 60_000).toISOString().slice(0, 19);
    occurrences.push({
      start: { dateTime: startAt, timeZone },
      end: { dateTime: endAt, timeZone },
      recurrence: count > 1 ? [`RRULE:${rule}${Number.isFinite(count) ? ";COUNT=" + count : ""}`] : []
    });
  }
  if (!occurrences.length) throw new Error("Choose dates and times with at least one upcoming reminder.");
  return occurrences;
}
