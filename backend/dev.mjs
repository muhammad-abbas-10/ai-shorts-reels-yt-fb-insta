import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const python = process.platform === 'win32'
  ? join(projectRoot, '.venv', 'Scripts', 'python.exe')
  : join(projectRoot, '.venv', 'bin', 'python')

if (!existsSync(python)) {
  console.error('Python environment missing. Create .venv in the project root and install the [web] package.')
  process.exit(1)
}

const server = spawn(python, [
  '-m', 'uvicorn', 'backend.main:app',
  '--reload', '--reload-dir', 'backend', '--reload-dir', 'shorts',
  '--host', '127.0.0.1', '--port', '8000',
], { cwd: projectRoot, stdio: 'inherit' })

server.on('error', (error) => {
  console.error(`Could not start the backend: ${error.message}`)
  process.exitCode = 1
})
server.on('exit', (code, signal) => {
  process.exitCode = code ?? (signal ? 1 : 0)
})
