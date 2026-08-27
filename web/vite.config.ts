import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'
import fs from 'node:fs'
import { spawn } from 'node:child_process'
import { randomUUID } from 'node:crypto'

// The MAROS backend serves its API off the ROOT path space (/lectures,
// /modules, /jobs ...) with no /api prefix, and its CORS_ORIGINS is pinned to
// http://localhost:8000 — so a dev server on :5173 is not an allowed origin.
//
// Rather than change backend config, every API prefix is proxied here. The
// browser only ever talks to its own origin, so CORS never comes into play,
// and the same relative paths work unchanged in production where FastAPI
// serves the built assets itself.
const API_PREFIXES = [
  '/jobs', '/lectures', '/modules', '/quiz', '/quizzes', '/chat',
  '/student', '/reels', '/clipper', '/prep', '/papers',
  '/assignments', '/professor',
]

const BACKEND = process.env.MAROS_API ?? 'http://127.0.0.1:8000'

/**
 * Serves /meals/* in development.
 *
 * The Meal routes exist as a real, self-contained FastAPI router at
 * MAROS/meal_routes.py, but mounting it means editing main.py — which is the
 * user's working file with uncommitted changes in it. Rather than touch it,
 * this middleware serves the SAME shapes off the SAME files, so the frontend
 * is written against the real contract rather than a mock.
 *
 * Nothing here is fabricated: it reads meals/catalogue/*.json and streams the
 * actual rendered MP4s. When meal_routes.py is mounted, delete this plugin and
 * add '/meals' to API_PREFIXES above — the frontend needs no change.
 */
function mealsDevServer(): Plugin {
  const MEALS = path.resolve(__dirname, '..', 'meals')
  const CATALOGUE = path.join(MEALS, 'catalogue')
  const BUILD = path.join(MEALS, 'build')
  const OUT = path.join(MEALS, 'out')

  const readMeal = (id: string) => {
    const p = path.join(CATALOGUE, `${id}.json`)
    if (!fs.existsSync(p)) return null
    return JSON.parse(fs.readFileSync(p, 'utf8')).meal
  }

  const duration = (id: string) => {
    const p = path.join(BUILD, `${id}.timing.json`)
    if (!fs.existsSync(p)) return null
    try {
      return JSON.parse(fs.readFileSync(p, 'utf8')).duration
    } catch {
      return null
    }
  }

  const summary = (meal: Record<string, unknown>, mtime = 0) => ({
    id: meal.id,
    series: meal.series ?? null,
    generated_at: mtime,
    title: meal.title,
    concept: meal.concept,
    objective: meal.objective,
    difficulty: meal.difficulty ?? null,
    prerequisites: meal.prerequisites ?? [],
    next_concepts: meal.next_concepts ?? [],
    practice: meal.practice ?? null,
    video_url: `/meals/${meal.id}/video`,
    timing_url: `/meals/${meal.id}/timing`,
    duration_sec: duration(String(meal.id)),
  })

  const sendJson = (res: import('node:http').ServerResponse, body: unknown) => {
    res.setHeader('Content-Type', 'application/json')
    res.end(JSON.stringify(body))
  }

  /** Range-aware so <video> can seek, exactly as FileResponse does. */
  const sendFile = (
    req: import('node:http').IncomingMessage,
    res: import('node:http').ServerResponse,
    file: string,
    type: string,
  ) => {
    const { size } = fs.statSync(file)
    const range = req.headers.range
    res.setHeader('Content-Type', type)
    res.setHeader('Accept-Ranges', 'bytes')

    if (range) {
      const [rawStart, rawEnd] = range.replace('bytes=', '').split('-')
      const start = Number(rawStart)
      const end = rawEnd ? Number(rawEnd) : size - 1
      res.statusCode = 206
      res.setHeader('Content-Range', `bytes ${start}-${end}/${size}`)
      res.setHeader('Content-Length', end - start + 1)
      fs.createReadStream(file, { start, end }).pipe(res)
      return
    }

    res.setHeader('Content-Length', size)
    fs.createReadStream(file).pipe(res)
  }

  const VENV_PYTHON = path.resolve(__dirname, '..', 'venv', 'bin', 'python')

  const readBody = (req: import('node:http').IncomingMessage) =>
    new Promise<string>((resolve) => {
      let body = ''
      req.on('data', (c) => (body += c))
      req.on('end', () => resolve(body))
    })

  /**
   * Starts the full pipeline: ingest -> plan -> verify -> narrate -> render.
   *
   * Detached, because a 9-minute lecture takes several minutes to become
   * Meals and no browser request should be held open for that. The child
   * writes progress to meals/build/pipeline_{runId}.json, which the status
   * route below serves, so the UI polls a file rather than a socket.
   */
  const startPipeline = (body: { url?: string; job_id?: string }) => {
    const runId = randomUUID().slice(0, 8)
    const args = ['-u', 'pipeline.py', '--run-id', runId]
    if (body.url) args.push('--url', body.url)
    else if (body.job_id) args.push('--job', body.job_id)
    else return null

    const log = fs.openSync(path.join(BUILD, `pipeline_${runId}.log`), 'a')
    const child = spawn(VENV_PYTHON, args, {
      cwd: MEALS,
      detached: true,
      stdio: ['ignore', log, log],
    })
    child.unref()
    return runId
  }

  return {
    name: 'maros-meals-dev',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = (req.url ?? '').split('?')[0]
        if (!url.startsWith('/meals')) return next()

        const rest = url.slice('/meals'.length).replace(/^\//, '')

        // ── POST /meals/generate  { url } | { job_id } ──────────────────
        if (rest === 'generate' && req.method === 'POST') {
          void readBody(req).then((raw) => {
            let parsed: { url?: string; job_id?: string } = {}
            try {
              parsed = JSON.parse(raw || '{}')
            } catch {
              res.statusCode = 400
              return sendJson(res, { detail: 'Body must be JSON.' })
            }
            const runId = startPipeline(parsed)
            if (!runId) {
              res.statusCode = 400
              return sendJson(res, { detail: 'Provide a url or a job_id.' })
            }
            res.statusCode = 202
            sendJson(res, { run_id: runId })
          })
          return
        }

        // ── GET /meals/generate/{runId} ────────────────────────────────
        if (rest.startsWith('generate/')) {
          const runId = rest.slice('generate/'.length)
          const file = path.join(BUILD, `pipeline_${runId}.json`)
          if (!fs.existsSync(file)) {
            // The child has been spawned but has not written its first status
            // yet. That is starting, not missing.
            return sendJson(res, {
              run_id: runId, state: 'starting', stage: 'ingest',
              stages: {}, meals: [], error: null,
            })
          }
          try {
            const status = JSON.parse(fs.readFileSync(file, 'utf8'))

            // A run that was killed or crashed never wrote a terminal state,
            // so the file still says "running" and the UI would poll it
            // forever. The recorded pid settles it: if the process is gone
            // and the state is not terminal, the run died.
            const active = status.state === 'running' || status.state === 'starting'
            if (active) {
              let dead = false

              if (typeof status.pid === 'number') {
                try {
                  process.kill(status.pid, 0)
                } catch {
                  dead = true
                }
              } else {
                // Runs started before the pid was recorded, and any case where
                // the pid was reused. The pipeline writes its status after
                // every step; the longest gap is one Meal's narrate-and-render,
                // so silence well past that means it is not coming back.
                const idleMs = Date.now() - fs.statSync(file).mtimeMs
                dead = idleMs > 10 * 60 * 1000
              }

              if (dead) {
                status.state = 'failed'
                status.error =
                  status.error ||
                  `The run stopped during "${status.stage}". Anything already ` +
                  'finished was kept — starting it again resumes from there.'
              }
            }

            return sendJson(res, status)
          } catch {
            return sendJson(res, {
              run_id: runId, state: 'starting', stage: 'ingest',
              stages: {}, meals: [], error: null,
            })
          }
        }

        if (rest === '') {
          if (!fs.existsSync(CATALOGUE)) return sendJson(res, { meals: [] })
          const meals = fs
            .readdirSync(CATALOGUE)
            .filter((f) => f.endsWith('.json'))
            .sort()
            .map((f) => {
              try {
                return JSON.parse(fs.readFileSync(path.join(CATALOGUE, f), 'utf8')).meal
              } catch {
                return null
              }
            })
            // Only Meals that have actually been rendered are listed: the
            // feed's contract is that everything in it is watchable.
            .filter((m) => m && fs.existsSync(path.join(OUT, `${m.id}.mp4`)))
            .map((m) => summary(m, fs.statSync(path.join(OUT, `${m.id}.mp4`)).mtimeMs))

          // Pinned course order, if one is configured. Recency alone is not
          // enough: rendering a single new Meal could reorder the whole feed,
          // which is not something a demo should have to survive.
          let pinned: string[] = []
          try {
            const cfg = path.join(MEALS, 'series_order.json')
            if (fs.existsSync(cfg)) {
              pinned = JSON.parse(fs.readFileSync(cfg, 'utf8')).order ?? []
            }
          } catch {
            /* a malformed order file falls back to recency */
          }
          const rank = (title: string) => {
            const i = pinned.indexOf(title)
            return i === -1 ? Number.MAX_SAFE_INTEGER : i
          }

          // Group by SERIES, pinned first then newest, ordered within the course.
          //
          // Filename order interleaved two courses — game Meal 1, OOP Meal 1,
          // game Meal 2 — which is unlearnable. Alphabetical series order
          // fixed that but buried the course you just made behind ten Meals
          // of an older one, so series are ranked by recency instead.
          const newest = new Map<string, number>()
          for (const m of meals) {
            const title = ((m.series as { title?: string }) ?? {}).title ?? ''
            newest.set(title, Math.max(newest.get(title) ?? 0, m.generated_at))
          }
          meals.sort((a, b) => {
            const sa = (a.series as { title?: string; order?: number }) ?? {}
            const sb = (b.series as { title?: string; order?: number }) ?? {}
            const ta = sa.title ?? ''
            const tb = sb.title ?? ''
            if (ta !== tb) {
              const ra = rank(ta)
              const rb = rank(tb)
              if (ra !== rb) return ra - rb
              return (newest.get(tb) ?? 0) - (newest.get(ta) ?? 0)
            }
            return (sa.order ?? 0) - (sb.order ?? 0)
          })

          return sendJson(res, { meals })
        }

        const [id, kind] = rest.split('/')

        if (kind === 'video') {
          const file = path.join(OUT, `${id}.mp4`)
          if (!fs.existsSync(file)) {
            res.statusCode = 404
            return sendJson(res, { detail: 'Meal video not rendered yet.' })
          }
          return sendFile(req, res, file, 'video/mp4')
        }

        if (kind === 'audio') {
          const file = path.join(BUILD, `${id}.mp3`)
          if (!fs.existsSync(file)) {
            res.statusCode = 404
            return sendJson(res, { detail: 'Meal narration not found.' })
          }
          return sendFile(req, res, file, 'audio/mpeg')
        }

        if (kind === 'timing') {
          const file = path.join(BUILD, `${id}.timing.json`)
          if (!fs.existsSync(file)) {
            res.statusCode = 404
            return sendJson(res, { detail: 'Timing sidecar not found.' })
          }
          return sendJson(res, JSON.parse(fs.readFileSync(file, 'utf8')))
        }

        if (!kind) {
          const meal = readMeal(id)
          if (!meal) {
            res.statusCode = 404
            return sendJson(res, { detail: `Meal ${id} not found.` })
          }
          return sendJson(res, meal)
        }

        return next()
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), mealsDevServer()],
  resolve: {
    alias: { '@': path.resolve(__dirname, 'src') },
  },
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      API_PREFIXES.map((p) => [p, { target: BACKEND, changeOrigin: true }]),
    ),
  },
  build: {
    outDir: 'dist',
  },
})
