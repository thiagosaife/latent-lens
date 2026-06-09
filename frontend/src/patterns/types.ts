import { markRaw, type Component } from 'vue'
import type { ZodType } from 'zod'

/**
 * A raw UI intent as emitted by the agent — UNTRUSTED.
 * `component` selects a pattern by name; `props` are unvalidated until the
 * registry checks them against that pattern's schema. The agent never emits
 * HTML — only this {component, props} envelope.
 */
export interface RawIntent {
  component: string
  props?: unknown
}

/**
 * The hand-craft-vs-generate split from the spec:
 *  - 'hero'      → differentiators we build by hand (embedding explorer, timeline)
 *  - 'generated' → templated utility UI driven from the registry (cards, tiles, tables)
 */
export type PatternKind = 'hero' | 'generated'

/** A vetted pattern: one Vue component bound to a props contract. */
export interface PatternDef<Schema extends ZodType = ZodType> {
  /** Stable id the agent uses to request this pattern, e.g. "embedding_scatter". */
  name: string
  kind: PatternKind
  /** Human-readable contract — surfaced in the registry panel and (later) the agent tool schema. */
  description: string
  /** The props contract. Anything that fails this never reaches the DOM. */
  schema: Schema
  /** The vetted Vue component rendered when an intent matches. */
  component: Component
}

/** Authoring helper: preserves the schema's inferred type and markRaws the component. */
export function definePattern<Schema extends ZodType>(def: PatternDef<Schema>): PatternDef<Schema> {
  // Components are values, not reactive state — keep Vue from proxying them.
  return { ...def, component: markRaw(def.component) }
}

/** Outcome of resolving a raw intent against the registry. */
export type ResolveResult =
  | { ok: true; pattern: PatternDef; component: Component; props: Record<string, unknown> }
  | {
      ok: false
      reason: 'unknown_component' | 'invalid_props'
      message: string
      intent: RawIntent
    }
