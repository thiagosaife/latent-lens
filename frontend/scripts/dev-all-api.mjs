// One-command dev against the REAL backend: runs the FastAPI app (uvicorn) and
// the Vite dev server together. The backend is a drop-in for the Node mock — it
// serves the same /api on :8787, so the Vite proxy is unchanged. No deps.
//
// Prereq: backend venv created (see backend/README.md):
//   python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt

import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const frontend = resolve(here, '..')
const backend = resolve(here, '../../backend')
const py = join(backend, '.venv', 'bin', 'python')
const reset = '\x1b[0m'

if (!existsSync(py)) {
  console.error(`[dev:api] backend venv not found at ${py}`)
  console.error('[dev:api] create it: python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt')
  process.exit(1)
}

const targets = [
  { name: 'api', cmd: py, args: ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8787'], cwd: backend, color: '\x1b[32m' },
  { name: 'vite', cmd: 'npm', args: ['run', 'dev'], cwd: frontend, color: '\x1b[35m' },
]

const children = targets.map(({ name, cmd, args, cwd, color }) => {
  const child = spawn(cmd, args, { cwd, stdio: ['ignore', 'pipe', 'pipe'] })
  const tag = `${color}[${name}]${reset} `
  const pipe = (src, dst) =>
    src.on('data', (buf) => {
      for (const line of buf.toString().split('\n')) if (line.length) dst.write(tag + line + '\n')
    })
  pipe(child.stdout, process.stdout)
  pipe(child.stderr, process.stderr)
  child.on('exit', (code) => {
    console.log(`${tag}exited (code ${code ?? 0})`)
    shutdown()
  })
  return child
})

let down = false
function shutdown() {
  if (down) return
  down = true
  for (const child of children) {
    try {
      child.kill('SIGTERM')
    } catch {
      // already gone
    }
  }
  setTimeout(() => process.exit(0), 150)
}
process.on('SIGINT', shutdown)
process.on('SIGTERM', shutdown)
