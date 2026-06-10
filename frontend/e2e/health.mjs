// Postmortem remediation #6: assert the backend's IDENTITY (build version) before
// driving the app, so a green-looking run can't be reported against a stale server.
//
// The incident (see ../../POSTMORTEM.md): a stale instance squatting :8787 answered
// the verify run, which asserted behavior but never asked "which build are you?".
// This closes that gap — call assertBackendHealth() first; if the backend is
// unreachable, returns non-200, or can't name its build, we abort BEFORE the app
// is driven instead of trusting whatever happens to be on the port.
//
// /health is served at the ROOT of :8787 (not under the Vite /api proxy), on the
// same port a stray server would squat — so we hit it directly. Set EXPECT_VERSION
// to pin an exact build and turn a stale-server run into a hard failure.

const BACKEND = process.env.BACKEND_URL || 'http://localhost:8787'

export async function assertBackendHealth(out) {
  let res
  try {
    res = await fetch(`${BACKEND}/health`)
  } catch (err) {
    throw new Error(
      `backend /health unreachable at ${BACKEND} — is a server running on :8787? ` +
        `(${err instanceof Error ? err.message : err})`,
    )
  }
  if (!res.ok) throw new Error(`backend /health returned ${res.status} at ${BACKEND}`)

  let health
  try {
    health = await res.json()
  } catch {
    throw new Error(`backend /health did not return JSON at ${BACKEND} — wrong server on the port?`)
  }
  if (!health || typeof health.version !== 'string' || !health.version) {
    throw new Error(`backend /health has no version — can't identify the build (got ${JSON.stringify(health)})`)
  }

  const want = process.env.EXPECT_VERSION
  if (want && health.version !== want) {
    throw new Error(`backend build mismatch: /health says ${health.version}, expected ${want} (stale server?)`)
  }

  out.health = health
  // stderr so it never pollutes the JSON summary the scripts print to stdout.
  console.error(`✓ backend build ${health.version} on ${BACKEND} (activeRuns=${health.activeRuns ?? '?'})`)
  return health
}
