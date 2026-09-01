// web/scripts/build-meals-index.mjs
//
// Generates web/public/meals-data/index.json — a static snapshot of what
// GET /meals returns in dev (see web/vite.config.ts's mealsDevServer).
// Run this once after copying catalogue/*.json + out/*.mp4 (+ optional
// build/*.timing.json) into web/public/meals-data/.
//
// Usage (from web/):  node scripts/build-meals-index.mjs

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const DATA = path.resolve(__dirname, '..', 'public', 'meals-data')
const VIDEOS = path.join(DATA, 'videos')
const TIMING = path.join(DATA, 'timing')
const SERIES_ORDER = path.resolve(__dirname, '..', '..', 'meals', 'series_order.json')

function duration(id) {
  const p = path.join(TIMING, `${id}.timing.json`)
  if (!fs.existsSync(p)) return null
  try {
    return JSON.parse(fs.readFileSync(p, 'utf8')).duration ?? null
  } catch {
    return null
  }
}

function summary(meal) {
  const hasTiming = fs.existsSync(path.join(TIMING, `${meal.id}.timing.json`))
  return {
    id: meal.id,
    series: meal.series ?? null,
    generated_at: 0,
    title: meal.title,
    concept: meal.concept,
    objective: meal.objective,
    difficulty: meal.difficulty ?? null,
    prerequisites: meal.prerequisites ?? [],
    next_concepts: meal.next_concepts ?? [],
    practice: meal.practice ?? null,
    video_url: `/meals-data/videos/${meal.id}.mp4`,
    timing_url: hasTiming ? `/meals-data/timing/${meal.id}.timing.json` : null,
    duration_sec: duration(meal.id),
  }
}

const meals = fs
  .readdirSync(DATA)
  .filter((f) => f.endsWith('.json'))
  .sort()
  .map((f) => {
    try {
      return JSON.parse(fs.readFileSync(path.join(DATA, f), 'utf8')).meal
    } catch {
      return null
    }
  })
  .filter((m) => m && fs.existsSync(path.join(VIDEOS, `${m.id}.mp4`)))
  .map(summary)

let pinned = []
try {
  if (fs.existsSync(SERIES_ORDER)) {
    pinned = JSON.parse(fs.readFileSync(SERIES_ORDER, 'utf8')).order ?? []
  }
} catch {
  /* malformed order file falls back to encounter order */
}

// Unlisted series need a stable group position too, or their meals interleave
// by filename instead of staying together. Give each series first seen (in
// current array order) the next slot after the pinned ones.
const seriesFirstSeen = []
const seen = new Set()
for (const m of meals) {
  const t = m.series?.title ?? ''
  if (!seen.has(t)) {
    seen.add(t)
    seriesFirstSeen.push(t)
  }
}
const groupRank = (title) => {
  const p = pinned.indexOf(title)
  if (p !== -1) return p
  return pinned.length + seriesFirstSeen.indexOf(title)
}
meals.sort((a, b) => {
  const ta = a.series?.title ?? ''
  const tb = b.series?.title ?? ''
  if (ta !== tb) return groupRank(ta) - groupRank(tb)
  return (a.series?.order ?? 0) - (b.series?.order ?? 0)
})

fs.writeFileSync(path.join(DATA, 'index.json'), JSON.stringify({ meals }, null, 2))
console.log(`Wrote ${meals.length} meals to ${path.join(DATA, 'index.json')}`)
