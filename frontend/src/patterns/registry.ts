import { z } from 'zod'
import { definePattern, type PatternDef, type RawIntent, type ResolveResult } from './types'
import StatTile from './components/StatTile.vue'
import SummaryCard from './components/SummaryCard.vue'
import EmbeddingScatter from './components/EmbeddingScatter.vue'

/* ──────────────────────────────────────────────────────────────────────────
 * Pattern definitions = the constrained generation surface.
 * Each entry is ONE thing the agent is allowed to ask the UI to render.
 * The schema is the contract; props that don't match are rejected, not rendered.
 * (These same schemas can later be emitted as the agent's tool/output schema so
 * the model is steered toward valid intents in the first place.)
 * ──────────────────────────────────────────────────────────────────────── */

const statTile = definePattern({
  name: 'stat_tile',
  kind: 'generated',
  description: 'A single headline metric with optional delta.',
  schema: z.object({
    label: z.string().min(1),
    value: z.union([z.string(), z.number()]),
    delta: z.number().optional(),
    format: z.enum(['number', 'percent', 'currency']).default('number'),
  }),
  component: StatTile,
})

const summaryCard = definePattern({
  name: 'summary_card',
  kind: 'generated',
  description: 'A titled card with prose and optional bullet findings.',
  schema: z.object({
    title: z.string().min(1),
    body: z.string(),
    bullets: z.array(z.string()).max(8).optional(),
    tone: z.enum(['neutral', 'positive', 'warning']).default('neutral'),
  }),
  component: SummaryCard,
})

const embeddingScatter = definePattern({
  name: 'embedding_scatter',
  kind: 'hero',
  description: '2D embedding scatter (PCA/UMAP) with selection. Hand-crafted hero.',
  schema: z.object({
    // A handle to a server-side point set — never the raw points over the wire.
    pointsRef: z.string().min(1),
    colorBy: z.string().default('cluster'),
    pointCount: z.number().int().positive().optional(),
  }),
  component: EmbeddingScatter,
})

/** The registry. Add a pattern here and it becomes renderable; nothing else can. */
export const PATTERNS = [statTile, summaryCard, embeddingScatter] as const

const byName = new Map<string, PatternDef>(PATTERNS.map((p) => [p.name, p]))

export function listPatterns(): readonly PatternDef[] {
  return PATTERNS
}

/**
 * Resolve + validate a raw agent intent. This is the single gate every
 * generated UI passes through before anything reaches the DOM.
 */
export function resolveIntent(intent: RawIntent): ResolveResult {
  const pattern = byName.get(intent.component)
  if (!pattern) {
    return {
      ok: false,
      reason: 'unknown_component',
      message: `No registered pattern named "${intent.component}".`,
      intent,
    }
  }

  const parsed = pattern.schema.safeParse(intent.props ?? {})
  if (!parsed.success) {
    const message = parsed.error.issues
      .map((i) => `${i.path.join('.') || '(root)'}: ${i.message}`)
      .join('; ')
    return { ok: false, reason: 'invalid_props', message, intent }
  }

  return {
    ok: true,
    pattern,
    component: pattern.component,
    props: parsed.data as Record<string, unknown>,
  }
}
