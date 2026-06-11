// Browser check for dataset upload (#1): connect a CSV, confirm the run
// analyzes IT (profiling reflects the file's real row count), not the synthetic
// default. Drives headed Chrome against the running app (npm run dev:api).
//
//   node e2e/verify-upload.mjs            # headed
//   HEADLESS=1 node e2e/verify-upload.mjs

import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { assertBackendHealth } from './health.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const SHOTS = join(HERE, 'shots')
mkdirSync(SHOTS, { recursive: true })
const URL = process.env.APP_URL || 'http://localhost:5173'
const CSV = process.env.CSV || '/tmp/sample.csv' // 2000-row sample from the backend test

const out = { steps: {}, logs: [] }
const browser = await chromium.launch({ channel: 'chrome', headless: process.env.HEADLESS === '1' })
const page = await browser.newPage({ viewport: { width: 1280, height: 920 }, deviceScaleFactor: 2 })
page.on('pageerror', (e) => out.logs.push(`[pageerror] ${e.message}`))

try {
  // 0) Identity before behavior: prove which backend build answers :8787 before
  //    driving the app, or abort loudly (POSTMORTEM #6).
  await assertBackendHealth(out)

  await page.goto(URL, { waitUntil: 'domcontentloaded' })

  // 1) initial synthetic run shows its plan
  await page.waitForSelector('.plan-item', { timeout: 10000 })

  // 2) connect a CSV via the (hidden) file input → schema PREVIEW (no run yet)
  await page.locator('.dataset input[type=file]').setInputFiles(CSV)
  await page.waitForSelector('.dataset .preview', { timeout: 10000 })
  out.steps.previewMeta = (await page.locator('.preview .pv-meta').innerText()).replace(/\s+/g, ' ').trim()
  out.steps.previewColumns = await page.locator('.preview .cols li').count()
  out.steps.previewDelimiter = await page.locator('.preview .pv-meta .tag strong').first().innerText()
  await page.locator('.dataset').scrollIntoViewIfNeeded()
  await page.screenshot({ path: join(SHOTS, '8-upload-preview.png') })

  // 2b) confirm: "Run analysis" connects the dataset (chip) and starts the run
  await page.getByRole('button', { name: /Run analysis/ }).click()
  await page.waitForSelector('.dataset .chip', { timeout: 10000 })
  out.steps.chip = (await page.locator('.dataset .chip').innerText()).replace(/\s+/g, ' ').trim()

  // 3) the run analyzes the uploaded data → fresh plan
  await page.waitForTimeout(1200) // let the restart settle (cancel → new plan_proposed)
  await page.waitForSelector('.plan-item', { timeout: 10000 })
  out.steps.planStepCount = await page.locator('.plan-item').count()

  // 4) approve → profiling should reflect the CSV's real row count (2000)
  await page.getByRole('button', { name: /Approve & run/ }).click()
  await page.waitForSelector('.stat-tile', { timeout: 12000 })
  await page.waitForTimeout(2000)
  out.steps.rowsProfiled = await page.locator('.stat-tile', { hasText: 'Rows profiled' }).locator('.value').innerText()
  out.steps.profilingBody = (await page.locator('.summary-card', { hasText: 'Profiling complete' }).innerText()).replace(/\s+/g, ' ').trim()
  await page.screenshot({ path: join(SHOTS, '9-upload-profiled.png') })

  out.steps.usedUpload = out.steps.rowsProfiled.replace(/[^0-9]/g, '') === '2000'
  out.ok = true
} catch (err) {
  out.ok = false
  out.error = err instanceof Error ? err.message : String(err)
  await page.screenshot({ path: join(SHOTS, 'upload-error.png') }).catch(() => {})
} finally {
  console.log(JSON.stringify(out, null, 2))
  await browser.close()
}
