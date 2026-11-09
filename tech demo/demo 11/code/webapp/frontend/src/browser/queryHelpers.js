// queryHelpers.js: the numbers the query form shows next to the readings of a field: the summary
// (count, mean, median, min, max, first and last time), the gaps in the data, and the readings
// per hour or per day. Each reading is { moment, value } (a moment: microseconds since 1970).
//
// It is a port of code/webapp/backend/query_helpers.py (summarize, text_summary, find_gaps,
// resample_readings, duration_text). Its parse_time, parse_number and time_text are in values.js,
// because other files need them too. Used by query.js (querySeries, GET /query).

import { pyRound, pySum } from './python.js'
import { timeText } from './values.js'

// A number of seconds as text people can read, e.g. 2 d 3 h 15 min (query_helpers.duration_text)
function durationText(seconds) {
  // Python's int(seconds // 60). For dividing by 60, Math.floor(seconds / 60) gives the same
  // whole number for every float: a division rounded up to a whole number would need a
  // seconds value closer to a multiple of 60 than a float can be.
  let minutes = Math.floor(seconds / 60)
  const days = Math.floor(minutes / (24 * 60))
  const hours = Math.floor(minutes / 60) % 24
  minutes = minutes % 60
  const parts = []
  if (days > 0) parts.push(`${days} d`)
  if (hours > 0) parts.push(`${hours} h`)
  if (minutes > 0 || parts.length === 0) parts.push(`${minutes} min`)
  return parts.join(' ')
}

// Count, mean, median, min and max of the readings, and the first and last time with a value
// (query_helpers.summarize). Each reading is { moment, value }.
export function summarize(readings) {
  let count = 0
  let empty = 0
  let total = 0
  let lowest = null
  let highest = null
  const values = [] // every value, for the median
  let firstTime = null
  let lastTime = null
  for (const reading of readings) {
    const value = reading.value
    if (value === null) {
      empty += 1
      continue
    }
    if (typeof value !== 'number') continue
    count += 1
    total += value // one by one, like the Python loop
    values.push(value)
    if (lowest === null || value < lowest.value) lowest = reading
    if (highest === null || value > highest.value) highest = reading
    if (firstTime === null || reading.moment < firstTime) firstTime = reading.moment
    if (lastTime === null || reading.moment > lastTime) lastTime = reading.moment
  }

  const summary = {
    count: count, empty: empty, mean: null, median: null, min: null, max: null, firstTime: null, lastTime: null,
  }
  if (count > 0) {
    summary.mean = pyRound(total / count, 3)
    // The middle value; with an even number of values, the mean of the middle two (statistics.median)
    values.sort((a, b) => a - b)
    const middle = Math.floor(values.length / 2)
    summary.median = values.length % 2 === 1 ? values[middle] : (values[middle - 1] + values[middle]) / 2
    summary.min = { value: lowest.value, time: timeText(lowest.moment) }
    summary.max = { value: highest.value, time: timeText(highest.moment) }
    summary.firstTime = timeText(firstTime)
    summary.lastTime = timeText(lastTime)
  }
  return summary
}

// For a field that holds text: how many readings have a value, how many are empty, and the
// first and last time with a value (query_helpers.text_summary)
export function textSummary(readings) {
  const summary = {
    count: 0, empty: 0, mean: null, median: null, min: null, max: null, firstTime: null, lastTime: null,
  }
  let firstTime = null
  let lastTime = null
  for (const reading of readings) {
    if (reading.value === null) {
      summary.empty += 1
      continue
    }
    summary.count += 1
    if (firstTime === null || reading.moment < firstTime) firstTime = reading.moment
    if (lastTime === null || reading.moment > lastTime) lastTime = reading.moment
  }
  if (firstTime !== null) {
    summary.firstTime = timeText(firstTime)
    summary.lastTime = timeText(lastTime)
  }
  return summary
}

// Gaps: where the time between two readings is longer than the step that occurs most often
// (query_helpers.find_gaps). moments: in time order.
export function findGaps(moments) {
  if (moments.length < 3) return { step: null, count: 0, missing: null, longest: [] }

  // 1. How often each step occurs, in seconds (a Map keeps the order in which steps first occur)
  const steps = new Map()
  for (let i = 1; i < moments.length; i++) {
    const seconds = (moments[i] - moments[i - 1]) / 1000000
    steps.set(seconds, (steps.get(seconds) || 0) + 1)
  }
  let usual = null
  for (const [seconds, howOften] of steps) {
    if (usual === null || howOften > steps.get(usual)) usual = seconds
  }

  // 2. Every step longer than the usual one is a gap
  const gaps = []
  let missing = 0
  for (let i = 1; i < moments.length; i++) {
    const seconds = (moments[i] - moments[i - 1]) / 1000000
    if (seconds > usual) {
      gaps.push({ seconds: seconds, before: moments[i - 1], after: moments[i] })
      missing += seconds - usual
    }
  }
  // Longest first (and, for the same length, the later one first, like Python's reversed tuple sort)
  gaps.sort((a, b) => b.seconds - a.seconds || b.before - a.before || b.after - a.after)

  const longest = []
  for (const gap of gaps.slice(0, 5)) {
    longest.push({ from: timeText(gap.before), until: timeText(gap.after), length: durationText(gap.seconds) })
  }
  return { step: durationText(usual), count: gaps.length, missing: durationText(missing), longest: longest }
}

// The readings in groups per hour or per day, each group one value: the mean (rounded to 3
// decimals), the min or the max of its values (query_helpers.resample_readings)
export function resampleReadings(readings, period, aggregate) {
  const size = period === 'hour' ? 3600 * 1000000 : 24 * 3600 * 1000000
  const groups = new Map() // the start of the hour (or day) -> its values; a Map keeps the order
  for (const reading of readings) {
    const start = reading.moment - (((reading.moment % size) + size) % size)
    if (!groups.has(start)) groups.set(start, [])
    if (reading.value !== null) groups.get(start).push(reading.value)
  }

  const rows = []
  for (const [start, values] of groups) {
    let value
    if (values.length === 0) value = null
    else if (aggregate === 'min') value = values.reduce((low, x) => (x < low ? x : low))
    else if (aggregate === 'max') value = values.reduce((high, x) => (x > high ? x : high))
    else value = pyRound(pySum(values) / values.length, 3)
    rows.push({ time: timeText(start), value: value, count: values.length })
  }
  return rows
}
