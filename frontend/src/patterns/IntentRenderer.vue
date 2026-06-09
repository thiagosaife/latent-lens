<script setup lang="ts">
import { computed, type Component } from 'vue'
import { resolveIntent } from './registry'
import type { RawIntent } from './types'
import UnknownIntent from './components/UnknownIntent.vue'

const props = defineProps<{ intent: RawIntent }>()

/**
 * Resolve once, then either render the matched component with its validated
 * props, or fall through to the guardrail surface. The agent's "creativity"
 * lives at the intent level — never at render time.
 */
const view = computed<{ is: Component; bind: Record<string, unknown> }>(() => {
  const r = resolveIntent(props.intent)
  if (r.ok) return { is: r.component, bind: r.props }
  return { is: UnknownIntent, bind: { reason: r.reason, message: r.message, intent: r.intent } }
})
</script>

<template>
  <component :is="view.is" v-bind="view.bind" />
</template>
