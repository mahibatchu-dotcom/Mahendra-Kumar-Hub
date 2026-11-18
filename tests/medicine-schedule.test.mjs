import assert from "node:assert/strict";
import { test } from "node:test";
import { planMedicineSchedule } from "../medicine-schedule.js";

const daily = {
  frequency: "daily", interval: 1, days: [], times: ["08:00", "21:00"],
  startDate: "2026-11-18", endDate: "2026-11-20", timeZone: "Asia/Kolkata"
};

test("plans each daily time from its first future occurrence in the prescribed time zone", () => {
  const [morning, night] = planMedicineSchedule(daily, new Date("2026-11-18T07:00:00Z"));
  assert.deepEqual(morning.start, { dateTime: "2026-11-19T08:00:00", timeZone: "Asia/Kolkata" });
  assert.deepEqual(night.start, { dateTime: "2026-11-18T21:00:00", timeZone: "Asia/Kolkata" });
  assert.deepEqual(morning.recurrence, ["RRULE:FREQ=DAILY;INTERVAL=1;COUNT=2"]);
  assert.deepEqual(night.recurrence, ["RRULE:FREQ=DAILY;INTERVAL=1;COUNT=3"]);
});

test("keeps the days and interval of a weekly course across a daylight saving change", () => {
  const schedule = {
    frequency: "weekly", interval: 2, days: ["MO", "FR"], times: ["08:15"],
    startDate: "2026-10-29", endDate: "2026-11-16", timeZone: "America/New_York"
  };
  const [event] = planMedicineSchedule(schedule, new Date("2026-10-28T15:00:00Z"));
  assert.deepEqual(event.start, { dateTime: "2026-10-30T08:15:00", timeZone: "America/New_York" });
  assert.deepEqual(event.recurrence, ["RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,FR;WKST=MO;COUNT=3"]);
});

test("anchors an every-other-day schedule to its original start", () => {
  const [event] = planMedicineSchedule({ ...daily, endDate: "", interval: 2, times: ["08:00"] }, new Date("2026-11-19T07:00:00Z"));
  assert.equal(event.start.dateTime, "2026-11-20T08:00:00");
  assert.deepEqual(event.recurrence, ["RRULE:FREQ=DAILY;INTERVAL=2"]);
});

test("creates a one-date event that can cross midnight without repeating", () => {
  const [event] = planMedicineSchedule({ ...daily, frequency: "once", times: ["23:58"] }, new Date("2026-11-18T07:00:00Z"));
  assert.equal(event.start.dateTime, "2026-11-18T23:58:00");
  assert.equal(event.end.dateTime, "2026-11-19T00:03:00");
  assert.deepEqual(event.recurrence, []);
});

test("rejects incomplete, conflicting and expired schedules", () => {
  const now = new Date("2026-11-18T07:00:00Z");
  assert.throws(() => planMedicineSchedule({ ...daily, times: ["08:00", "08:00"] }, now), /appear once/);
  assert.throws(() => planMedicineSchedule({ ...daily, frequency: "weekly" }, now), /days of the week/);
  assert.throws(() => planMedicineSchedule({ ...daily, endDate: "2026-11-01" }, now), /end date/);
  assert.throws(() => planMedicineSchedule({ ...daily, startDate: "2026-11-31" }, now), /valid date/);
  assert.throws(() => planMedicineSchedule({ ...daily, timeZone: "+05:30" }, now), /valid time zone/);
  assert.throws(() => planMedicineSchedule({ ...daily, frequency: "once", times: ["08:00"] }, now), /upcoming reminder/);
});
