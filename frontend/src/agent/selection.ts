import { reactive, readonly } from 'vue'

/**
 * Shared selection context — the "brushing → agent" bridge from the spec.
 *
 * The embedding explorer is rendered generically through IntentRenderer, so its
 * lasso selection can't ride Vue events up to the agent layer. Instead the hero
 * writes the current selection here and the App/agent layer reads it. This is
 * the live context the agent can act on (e.g. "explain these points").
 */
export interface ClusterCount {
  cluster: number
  count: number
}

export interface SelectionContext {
  /** Which embedding the selection belongs to (the intent's pointsRef). */
  pointsRef: string | null
  count: number
  /** Selected point indices into the embedding's point set. */
  indices: number[]
  /** Per-cluster composition of the selection, descending. */
  clusters: ClusterCount[]
}

const state = reactive<SelectionContext>({
  pointsRef: null,
  count: 0,
  indices: [],
  clusters: [],
})

export function setSelection(next: SelectionContext): void {
  state.pointsRef = next.pointsRef
  state.count = next.count
  state.indices = next.indices
  state.clusters = next.clusters
}

export function clearSelection(): void {
  state.pointsRef = null
  state.count = 0
  state.indices = []
  state.clusters = []
}

/** Read-only view for consumers. Mutate via setSelection / clearSelection. */
export const selection = readonly(state)
