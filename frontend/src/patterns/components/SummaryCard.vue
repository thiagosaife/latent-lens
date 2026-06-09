<script setup lang="ts">
defineProps<{
  title: string
  body: string
  bullets?: string[]
  tone?: 'neutral' | 'positive' | 'warning'
}>()
</script>

<template>
  <article class="summary-card" :data-tone="tone ?? 'neutral'">
    <h3>{{ title }}</h3>
    <p>{{ body }}</p>
    <ul v-if="bullets?.length">
      <li v-for="(b, i) in bullets" :key="i">{{ b }}</li>
    </ul>
  </article>
</template>

<style scoped>
.summary-card {
  padding: 1.1rem 1.25rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-left: 3px solid var(--accent);
  border-radius: 10px;
  max-width: 560px;
}
.summary-card[data-tone='positive'] { border-left-color: var(--pos); }
.summary-card[data-tone='warning'] { border-left-color: var(--warn); }
h3 {
  margin: 0 0 0.5rem;
  font-size: 1.05rem;
}
p {
  margin: 0;
  color: var(--text-2);
  line-height: 1.5;
}
ul {
  margin: 0.75rem 0 0;
  padding-left: 1.1rem;
  color: var(--muted);
  font-size: 0.9rem;
  line-height: 1.6;
}
</style>
