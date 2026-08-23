/**
 * The code pane and the terminal.
 *
 * Uses Motion Canvas's built-in Code node, which ships CodeMirror/Lezer
 * highlighting plus typing and diff animation. Per the renderer decision,
 * Monaco is NOT used inside videos — it lives in the web app's practice panel
 * where a learner actually edits. This keeps one scene graph and one visual
 * language in the video pipeline.
 */

import { Code, LezerHighlighter, Rect, Txt, Layout } from '@motion-canvas/2d'
import { createRef, type Reference } from '@motion-canvas/core'
import { parser } from '@lezer/python'
import { theme, CONTENT_WIDTH } from '../theme'

/** Python grammar, shared by every code pane so parsing is set up once. */
export const pythonHighlighter = new LezerHighlighter(parser)

/** Three-dot window chrome is deliberately omitted — it imitates a desktop
 *  app without teaching anything. The filename carries the context instead. */
export function codePane(
  filename: string,
  codeRef: Reference<Code>,
  initial = '',
  opts: { compact?: boolean; sizeFor?: string } = {},
) {
  // Compact is for when the snippet shares the frame with a terminal: the
  // code is being referred to rather than read line by line, so it gives up
  // room to the output it produced.
  const padding = opts.compact ? 26 : 36

  // Fit the longest line to the pane rather than using a fixed size. Real
  // snippets contain lines like
  //   user_choice = input('Enter rock, paper, or scissors: ').lower()
  // which ran off the right edge at any size readable for short lines.
  // DM Mono advances ~0.6em per character.
  // A typing pane starts empty and fills in, so size it against the code it
  // will END UP holding — otherwise it is laid out for one blank line and the
  // text runs off the edge as soon as it appears.
  const longest = (opts.sizeFor ?? initial)
    .split('\n')
    .reduce((n, line) => Math.max(n, line.length), 1)
  const available = CONTENT_WIDTH - padding * 2
  const ideal = opts.compact ? 32 : 40
  const fitted = Math.floor(available / (longest * 0.6))
  const fontSize = Math.max(Math.min(ideal, fitted), 18)
  const lineHeight = Math.round(fontSize * 1.45)
  return (
    <Rect
      layout
      direction="column"
      radius={20}
      fill={theme.surface}
      stroke={theme.line}
      lineWidth={2}
      width={CONTENT_WIDTH}
      clip
    >
      <Rect
        layout
        padding={[20, 32]}
        gap={16}
        alignItems="center"
        fill="rgba(255,255,255,0.03)"
      >
        <Txt
          text={filename}
          fontFamily={theme.fontMono}
          fontSize={26}
          fill={theme.muted}
        />
      </Rect>
      <Rect layout padding={padding} width={CONTENT_WIDTH}>
        <Code
          ref={codeRef}
          highlighter={pythonHighlighter}
          fontFamily={theme.fontMono}
          fontSize={fontSize}
          lineHeight={lineHeight}
          code={initial}
        />
      </Rect>
    </Rect>
  )
}

/**
 * Terminal pane.
 *
 * Output shown here is the RECORDED result of a real run — see the Meal's
 * `execution` block, which the verifier populates by actually executing the
 * code. Nothing is typed in by hand.
 *
 * A real TTY echoes what the user types; a captured pipe does not. So stdin is
 * stored separately in the Meal and interleaved here, rather than being faked
 * into stdout.
 */
export function terminalPane(
  command: string,
  bodyRef: Reference<Txt>,
  stdin: string[] = [],
) {
  return (
    <Rect
      layout
      direction="column"
      radius={20}
      fill="#060606"
      stroke={theme.line}
      lineWidth={2}
      width={CONTENT_WIDTH}
      clip
    >
      <Rect
        layout
        padding={[20, 32]}
        alignItems="center"
        fill="rgba(255,255,255,0.03)"
      >
        <Txt
          text={stdin.length ? `TERMINAL — TYPED: ${stdin.join(', ')}` : 'TERMINAL'}
          fontFamily={theme.fontMono}
          fontSize={24}
          letterSpacing={3}
          fill={theme.muted}
        />
      </Rect>
      <Layout layout direction="column" padding={36} gap={10} alignItems="start">
        <Txt
          text={`$ ${command}`}
          fontFamily={theme.fontMono}
          fontSize={36}
          fill={theme.green}
        />
        <Txt
          ref={bodyRef}
          text=""
          fontFamily={theme.fontMono}
          fontSize={36}
          lineHeight={54}
          fill={theme.ink}
          textWrap
          width={CONTENT_WIDTH - 72}
        />
      </Layout>
    </Rect>
  )
}

export { createRef }
