import { z } from 'zod'

/**
 * The agent → UI event protocol. A run is a stream of these frames (SSE).
 * Validated at the wire boundary with Zod — same trust-nothing stance as the
 * Pattern Registry. The envelope is checked here; the `intent` payload's PROPS
 * are checked later, at render time, by the registry.
 *
 * The stream is interruptible: `plan_proposed` and `approval_required` are
 * pause points where the server holds and waits for a client decision (sent
 * back over a side-channel POST). This is the human-in-the-loop core.
 */

/** Untrusted UI intent envelope — props stay `unknown` until the registry vets them. */
const rawIntentSchema = z.object({
  component: z.string(),
  props: z.unknown().optional(),
})

/** Cost/time estimate shown at an approval gate. */
export const estimateSchema = z.object({
  rows: z.number().optional(),
  seconds: z.number().optional(),
  cost: z.string().optional(),
})
export type Estimate = z.infer<typeof estimateSchema>

/** One step in the proposed plan. Reorderable / deletable / editable client-side. */
export const planStepSchema = z.object({
  id: z.string(),
  title: z.string(),
  description: z.string().optional(),
  needsApproval: z.boolean().optional(),
  estimate: estimateSchema.optional(),
})
export type PlanStep = z.infer<typeof planStepSchema>

export const agentEventSchema = z.discriminatedUnion('type', [
  z.object({ type: z.literal('run_started'), runId: z.string(), goal: z.string(), traceId: z.string().optional() }),
  z.object({ type: z.literal('plan_proposed'), runId: z.string(), steps: z.array(planStepSchema) }),
  z.object({ type: z.literal('step_started'), stepId: z.string(), title: z.string() }),
  z.object({
    type: z.literal('approval_required'),
    stepId: z.string(),
    title: z.string(),
    message: z.string(),
    estimate: estimateSchema.optional(),
  }),
  z.object({ type: z.literal('ui_intent'), stepId: z.string(), intent: rawIntentSchema }),
  // Multi-agent: orchestrator delegates a step to a specialist sub-agent, which
  // runs attributed tool calls, then returns control.
  z.object({ type: z.literal('delegation_started'), stepId: z.string(), agent: z.string() }),
  z.object({
    type: z.literal('tool_call_started'),
    stepId: z.string(),
    agent: z.string(),
    callId: z.string(),
    tool: z.string(),
    args: z.record(z.string(), z.unknown()).optional(),
  }),
  z.object({ type: z.literal('tool_call_finished'), stepId: z.string(), callId: z.string(), result: z.string() }),
  z.object({ type: z.literal('delegation_finished'), stepId: z.string(), agent: z.string() }),
  z.object({ type: z.literal('step_finished'), stepId: z.string(), skipped: z.boolean().optional() }),
  z.object({ type: z.literal('run_finished'), runId: z.string() }),
  z.object({ type: z.literal('error'), message: z.string() }),
])

export type AgentEvent = z.infer<typeof agentEventSchema>

/** Parse + validate one decoded SSE payload. Returns null for malformed frames. */
export function parseAgentEvent(raw: unknown): AgentEvent | null {
  const result = agentEventSchema.safeParse(raw)
  return result.success ? result.data : null
}
