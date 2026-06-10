import { ref } from 'vue'
import { streamRun, sendCommand } from './sseClient'
import type { AgentEvent, PlanStep, Estimate } from './events'
import type { RawIntent } from '../patterns/types'

/**
 * The agent–UI loop as an explicit state machine (spec layer 4). Phases gate
 * which surface the UI shows and keep a generative interface deterministic:
 *
 *   idle → planning → awaiting_plan → executing ⇄ awaiting_approval → done
 *                                          ↘ error / cancelled
 *
 * (A lightweight follow-up run skips planning: planning → executing directly.)
 */
export type RunPhase =
  | 'idle'
  | 'planning'
  | 'awaiting_plan'
  | 'executing'
  | 'awaiting_approval'
  | 'done'
  | 'error'
  | 'cancelled'

export interface ToolCall {
  callId: string
  agent: string
  tool: string
  args?: Record<string, unknown>
  result?: string
  status: 'running' | 'done'
}

/** A specialist sub-agent the orchestrator delegated this step to. */
export interface Delegation {
  agent: string
  returned: boolean
  toolCalls: ToolCall[]
}

export interface RunStep {
  id: string
  title: string
  status: 'running' | 'done' | 'skipped'
  intents: RawIntent[]
  /** Present when the orchestrator handed this step to a specialist agent. */
  delegation?: Delegation
}

export interface ApprovalGate {
  stepId: string
  title: string
  message: string
  estimate?: Estimate
}

export type TraceKind = 'run' | 'plan' | 'step' | 'delegation' | 'tool' | 'ui' | 'approval' | 'error'

/** One observability log line: a timestamped event with optional span duration. */
export interface TraceEntry {
  seq: number
  /** ms since the run's first event. */
  elapsedMs: number
  kind: TraceKind
  actor: string
  label: string
  /** For *_finished events: wall-clock duration of the matched span. */
  durationMs?: number
  /** Raw payload (inputs/outputs) for the expandable detail view. */
  detail?: unknown
}

function fmtArgs(args?: Record<string, unknown>): string {
  if (!args) return ''
  return Object.entries(args)
    .map(([k, v]) => `${k}=${typeof v === 'string' ? `"${v}"` : String(v)}`)
    .join(', ')
}

export function useAgentRun() {
  const phase = ref<RunPhase>('idle')
  const goal = ref('')
  const runId = ref<string | null>(null)
  const plan = ref<PlanStep[]>([])
  const gate = ref<ApprovalGate | null>(null)
  const steps = ref<RunStep[]>([])
  const errorMessage = ref<string | null>(null)
  const traceId = ref<string | null>(null)
  const trace = ref<TraceEntry[]>([])

  let controller: AbortController | null = null
  let traceSeq = 0
  let traceStart = 0
  // Open spans keyed by `step:`/`tool:`/`deleg:` id, for duration matching.
  const openSpans = new Map<string, { t: number; title?: string; actor?: string }>()

  function apply(event: AgentEvent): void {
    switch (event.type) {
      case 'run_started':
        runId.value = event.runId
        goal.value = event.goal
        traceId.value = event.traceId ?? null
        phase.value = 'planning'
        break
      case 'plan_proposed':
        plan.value = event.steps
        phase.value = 'awaiting_plan'
        break
      case 'step_started':
        steps.value.push({ id: event.stepId, title: event.title, status: 'running', intents: [] })
        // planning → executing for the no-plan follow-up path; otherwise no-op.
        if (phase.value === 'planning') phase.value = 'executing'
        break
      case 'approval_required':
        gate.value = { stepId: event.stepId, title: event.title, message: event.message, estimate: event.estimate }
        phase.value = 'awaiting_approval'
        break
      case 'ui_intent': {
        const step = steps.value.find((s) => s.id === event.stepId)
        if (step) step.intents.push(event.intent)
        break
      }
      case 'delegation_started': {
        const step = steps.value.find((s) => s.id === event.stepId)
        if (step) step.delegation = { agent: event.agent, returned: false, toolCalls: [] }
        break
      }
      case 'tool_call_started': {
        const step = steps.value.find((s) => s.id === event.stepId)
        step?.delegation?.toolCalls.push({
          callId: event.callId,
          agent: event.agent,
          tool: event.tool,
          args: event.args,
          status: 'running',
        })
        break
      }
      case 'tool_call_finished': {
        const step = steps.value.find((s) => s.id === event.stepId)
        const call = step?.delegation?.toolCalls.find((c) => c.callId === event.callId)
        if (call) {
          call.result = event.result
          call.status = 'done'
        }
        break
      }
      case 'delegation_finished': {
        const step = steps.value.find((s) => s.id === event.stepId)
        if (step?.delegation) step.delegation.returned = true
        break
      }
      case 'step_finished': {
        const step = steps.value.find((s) => s.id === event.stepId)
        if (step) step.status = event.skipped ? 'skipped' : 'done'
        break
      }
      case 'run_finished':
        if (phase.value !== 'error' && phase.value !== 'cancelled') phase.value = 'done'
        break
      case 'error':
        errorMessage.value = event.message
        phase.value = 'error'
        break
    }
  }

  /**
   * Observability: fold each event into a timestamped trace line, matching
   * start/finish events into spans with durations. Runs AFTER apply() so it can
   * read the reduced state. This is the data a Langfuse-style backend would log.
   */
  function record(event: AgentEvent): void {
    const now = Date.now()
    if (trace.value.length === 0) traceStart = now
    const elapsedMs = now - traceStart
    const line = (e: Omit<TraceEntry, 'seq' | 'elapsedMs'>) =>
      trace.value.push({ seq: traceSeq++, elapsedMs, ...e })

    switch (event.type) {
      case 'run_started':
        line({ kind: 'run', actor: 'orchestrator', label: `run started · "${event.goal}"`, detail: { runId: event.runId, traceId: event.traceId } })
        break
      case 'plan_proposed':
        line({ kind: 'plan', actor: 'orchestrator', label: `plan proposed · ${event.steps.length} steps`, detail: event.steps })
        break
      case 'step_started':
        openSpans.set(`step:${event.stepId}`, { t: now, title: event.title })
        line({ kind: 'step', actor: 'orchestrator', label: `▶ ${event.title}` })
        break
      case 'delegation_started':
        openSpans.set(`deleg:${event.stepId}`, { t: now })
        line({ kind: 'delegation', actor: event.agent, label: `delegate → ${event.agent}` })
        break
      case 'tool_call_started':
        openSpans.set(`tool:${event.callId}`, { t: now, title: event.tool, actor: event.agent })
        line({ kind: 'tool', actor: event.agent, label: `${event.tool}(${fmtArgs(event.args)})`, detail: event.args })
        break
      case 'tool_call_finished': {
        const open = openSpans.get(`tool:${event.callId}`)
        openSpans.delete(`tool:${event.callId}`)
        line({ kind: 'tool', actor: open?.actor ?? 'agent', label: `✓ ${open?.title ?? 'tool'} → ${event.result}`, durationMs: open && now - open.t, detail: { result: event.result } })
        break
      }
      case 'delegation_finished': {
        const open = openSpans.get(`deleg:${event.stepId}`)
        openSpans.delete(`deleg:${event.stepId}`)
        line({ kind: 'delegation', actor: event.agent, label: `↩ ${event.agent} returned`, durationMs: open && now - open.t })
        break
      }
      case 'approval_required':
        line({ kind: 'approval', actor: 'orchestrator', label: `⏸ approval required · ${event.title}`, detail: event.estimate })
        break
      case 'ui_intent':
        line({ kind: 'ui', actor: 'orchestrator', label: `render ‹${event.intent.component}›`, detail: event.intent })
        break
      case 'step_finished': {
        const open = openSpans.get(`step:${event.stepId}`)
        openSpans.delete(`step:${event.stepId}`)
        line({ kind: 'step', actor: 'orchestrator', label: `${event.skipped ? '⊘ skipped' : '✓'} ${open?.title ?? event.stepId}`, durationMs: open && now - open.t })
        break
      }
      case 'run_finished':
        line({ kind: 'run', actor: 'orchestrator', label: 'run finished', durationMs: elapsedMs })
        break
      case 'error':
        line({ kind: 'error', actor: 'system', label: `error · ${event.message}` })
        break
    }
  }

  async function start(nextGoal: string, datasetId?: string, selection?: unknown): Promise<void> {
    if (phase.value === 'planning' || phase.value === 'awaiting_plan' || phase.value === 'executing' || phase.value === 'awaiting_approval') {
      stop()
    }
    steps.value = []
    plan.value = []
    gate.value = null
    errorMessage.value = null
    runId.value = null
    goal.value = nextGoal
    phase.value = 'planning'
    trace.value = []
    traceId.value = null
    openSpans.clear()
    traceSeq = 0
    traceStart = 0

    controller = new AbortController()
    await streamRun(
      nextGoal,
      {
        onEvent: (event) => {
          apply(event)
          record(event)
        },
        onError: (err) => {
          errorMessage.value = err instanceof Error ? err.message : String(err)
          phase.value = 'error'
        },
      },
      { signal: controller.signal, datasetId, selection },
    )
    controller = null
    // Stream closed without an explicit terminal event → treat as done.
    // (assertion defeats the literal narrowing TS carries past the await)
    const ended = phase.value as RunPhase
    if (ended === 'executing' || ended === 'planning') phase.value = 'done'
  }

  /** Approve the (possibly edited) plan and begin execution. */
  function approvePlan(editedSteps: PlanStep[]): void {
    if (!runId.value || phase.value !== 'awaiting_plan') return
    plan.value = editedSteps
    phase.value = 'executing'
    void sendCommand(`/api/runs/${runId.value}/plan`, {
      steps: editedSteps.map((s) => ({ id: s.id, title: s.title })),
    })
  }

  /** Resolve the current approval gate. */
  function decide(decision: 'approve' | 'skip' | 'cancel'): void {
    const current = gate.value
    if (!runId.value || !current) return
    gate.value = null
    if (decision === 'cancel') {
      cancel()
      return
    }
    phase.value = 'executing'
    void sendCommand(`/api/runs/${runId.value}/decision`, { stepId: current.stepId, decision })
  }

  function stop(): void {
    // Intentional stop (vs. a network drop, which the transport auto-resumes):
    // tell the server to tear the decoupled run down, then abort our read.
    if (runId.value) void sendCommand(`/api/runs/${runId.value}/cancel`, {})
    controller?.abort()
    controller = null
  }

  function cancel(): void {
    stop()
    gate.value = null
    phase.value = 'cancelled'
  }

  return { phase, goal, runId, traceId, trace, plan, gate, steps, errorMessage, start, approvePlan, decide, cancel }
}
