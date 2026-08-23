/**
 * Meal generation progress.
 *
 * Polls meals/pipeline.py's status file through the dev server. The run is
 * detached on the server, so leaving this page does not cancel it — the copy
 * says so, because a several-minute job that looks cancellable is worse than
 * one that plainly is not.
 */

import { useEffect } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useGenerationStatus } from '@/api/hooks'
import type { PipelineStage } from '@/api/types'
import {
  ButtonLink, Container, ErrorState, ProgressBar, Spinner,
} from '@/components/ui'
import { cn } from '@/lib/cn'

const STAGES: { key: PipelineStage; label: string; detail: string }[] = [
  { key: 'ingest', label: 'Transcribing the lecture', detail: 'Downloading audio and running speech recognition' },
  { key: 'plan', label: 'Finding the concepts', detail: 'Reading the lecture, designing the series, writing each Meal' },
  { key: 'verify', label: 'Running the code', detail: 'Every snippet is executed for real before it can ship' },
  { key: 'narrate', label: 'Recording the voice', detail: 'Narration, then aligning it word by word' },
  { key: 'render', label: 'Rendering the Meals', detail: 'Drawing each frame and encoding to video' },
]

export default function Generating() {
  const { runId } = useParams()
  const navigate = useNavigate()
  const { data, isError, error, refetch } = useGenerationStatus(runId)

  const done = data?.state === 'done'

  useEffect(() => {
    if (!done) return
    const t = setTimeout(() => navigate('/feed'), 4000)
    return () => clearTimeout(t)
  }, [done, navigate])

  const currentIndex = STAGES.findIndex((s) => s.key === data?.stage)
  const completed = STAGES.filter(
    (s) => data?.stages?.[s.key]?.state === 'done',
  ).length
  const percent = done ? 100 : Math.round((completed / STAGES.length) * 100)

  return (
    <Container>
      <div className="mx-auto max-w-2xl py-12 md:py-20">
        {isError ? (
          <ErrorState
            title="Could not check on this run"
            detail={error instanceof Error ? error.message : undefined}
            onRetry={() => void refetch()}
          />
        ) : data?.state === 'failed' ? (
          <div>
            <p className="eyebrow">Generation failed</p>
            <h1 className="mt-3 font-display text-2xl font-extrabold tracking-[-0.03em] text-ink">
              MAROS could not finish this lecture
            </h1>
            <div className="mt-5">
              <ErrorState
                title={`Stopped during: ${data.stage}`}
                detail={data.error ?? 'No reason was reported.'}
              />
            </div>
            <p className="mt-4 text-xs leading-relaxed text-muted">
              Anything that did finish is kept. Re-running the same lecture
              resumes rather than starting over.
            </p>
            <div className="mt-6 flex gap-3">
              <ButtonLink to="/add" variant="secondary">Try another lecture</ButtonLink>
              <ButtonLink to="/feed" variant="ghost">Go to the feed</ButtonLink>
            </div>
          </div>
        ) : done ? (
          <div className="animate-fade-up">
            <p className="eyebrow">Ready</p>
            <h1 className="mt-3 font-display text-[1.75rem] font-extrabold tracking-[-0.03em] text-ink sm:text-3xl">
              MAROS made {data.meals.length} Meal
              {data.meals.length === 1 ? '' : 's'}
            </h1>
            <ol className="mt-8 space-y-2">
              {data.meals.map((id, i) => (
                <li
                  key={id}
                  className="flex items-center gap-3 rounded border border-line bg-surface px-4 py-3"
                >
                  <span className="font-mono text-micro text-green">
                    {String(i + 1).padStart(2, '0')}
                  </span>
                  <span className="flex-1 truncate font-mono text-xs text-muted2">
                    {id}
                  </span>
                </li>
              ))}
            </ol>
            <div className="mt-8">
              <ButtonLink to="/feed" variant="primary" size="lg">
                Watch them
              </ButtonLink>
            </div>
          </div>
        ) : (
          <div>
            <p className="eyebrow">Making Meals</p>
            <h1 className="mt-3 font-display text-[1.75rem] font-extrabold tracking-[-0.03em] text-ink sm:text-3xl">
              Turning your lecture into Meals
            </h1>
            <p className="mt-3 text-sm text-muted2">
              This takes several minutes. You can close this tab — the run
              continues on the server and the Meals appear in the feed when
              they are ready.
            </p>

            <div className="mt-8">
              <ProgressBar value={percent} />
              <p className="mt-2 font-mono text-micro text-muted">
                {percent}% · STEP {Math.max(completed, 0)} OF {STAGES.length}
              </p>
            </div>

            <ol className="mt-8 space-y-1">
              {STAGES.map((stage, i) => {
                const info = data?.stages?.[stage.key]
                const state =
                  info?.state === 'done'
                    ? 'done'
                    : i === currentIndex || info?.state === 'running'
                      ? 'active'
                      : 'pending'
                return (
                  <li
                    key={stage.key}
                    className={cn(
                      'flex items-start gap-3 rounded px-3 py-3 transition-colors',
                      state === 'active' && 'bg-surface2',
                    )}
                  >
                    <span
                      className={cn(
                        'mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border font-mono text-[9px]',
                        state === 'done' && 'border-green bg-green text-accent-ink',
                        state === 'active' && 'border-green text-green',
                        state === 'pending' && 'border-line2 text-muted',
                      )}
                    >
                      {state === 'done' ? '✓' : ''}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span
                        className={cn(
                          'block text-sm',
                          state === 'pending' ? 'text-muted' : 'text-ink',
                        )}
                      >
                        {stage.label}
                      </span>
                      <span className="mt-0.5 block text-xs text-muted">
                        {stage.detail}
                      </span>
                      {state !== 'pending' && info && (
                        <>
                          {/* The planner reports which sub-step it is on.
                              Without this the longest stage looks frozen. */}
                          {typeof info.step === 'string' && (
                            <span className="mt-1 block text-xs text-green">
                              {info.step}
                              {typeof info.window === 'string' && ` — section ${info.window}`}
                              {typeof info.written === 'string' && ` — ${info.written}`}
                            </span>
                          )}
                          {typeof info.current === 'string' && (
                            <span className="mt-0.5 block truncate text-xs text-muted2">
                              {info.current}
                            </span>
                          )}
                          <span className="mt-1 block font-mono text-micro text-muted2">
                            {Object.entries(info)
                              .filter(([k]) => !['state', 'step', 'current', 'window', 'written'].includes(k))
                              .map(([k, v]) => `${k}=${v}`)
                              .join('  ')}
                          </span>
                        </>
                      )}
                    </span>
                    {state === 'active' && <Spinner className="mt-0.5" />}
                  </li>
                )
              })}
            </ol>

            <p className="mt-6 text-xs text-muted">
              Meanwhile,{' '}
              <Link to="/feed" className="text-green hover:underline">
                watch what is already there
              </Link>
              .
            </p>
          </div>
        )}
      </div>
    </Container>
  )
}
