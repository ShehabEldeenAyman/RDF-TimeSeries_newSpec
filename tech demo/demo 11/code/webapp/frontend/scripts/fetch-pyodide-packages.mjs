// Puts Pyodide (Python compiled to WebAssembly) and the Python packages of the SHACL check
// into public/vendor/pyodide/, so the browser loads all of it from our own server: no CDN.
// The SHACL tab of a user endpoint runs pyshacl there (see src/browser/shaclWorker.js).
//
// Run it in code/webapp/frontend, after npm install:
//   npm run fetch-browser-assets           (this script and the DuckDB one)
//   node scripts/fetch-pyodide-packages.mjs
//
// What it does:
//   1. copies the Pyodide core files from node_modules/pyodide (the npm package "pyodide")
//   2. downloads the pinned wheels (Python packages) and checks each one's sha256
//   3. writes pyodide-lock.json: the list of packages Pyodide may load, with bare file names,
//      so Pyodide looks for them next to the lock file (on our own server)
// A file that is already there and correct is not copied or downloaded again.
// public/vendor/ is in .gitignore: these files are never committed.

import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

// This file lives in code/webapp/frontend/scripts/
const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), '..')
const PYODIDE_PACKAGE = join(FRONTEND, 'node_modules', 'pyodide')
const TARGET = join(FRONTEND, 'public', 'vendor', 'pyodide')

// The wheels below were tested with this Pyodide version (Python 3.14). Another version
// may need other wheels, so the script stops when node_modules has another one.
const PYODIDE_VERSION = '314.0.7'

// The files loadPyodide needs: the loader, the interpreter (JavaScript + WebAssembly)
// and the Python standard library. (pyodide-lock.json is written by this script, see step 3.)
const CORE_FILES = ['pyodide.mjs', 'pyodide.asm.mjs', 'pyodide.asm.wasm', 'python_stdlib.zip']

// The Python packages for the SHACL check: pyshacl and what it needs, the same versions as
// the backend. Pure Python wheels: rdflib, pyshacl, owlrl and prettytable come from PyPI;
// wcwidth, packaging and pyparsing come from the Pyodide CDN, because Pyodide ships its own
// builds of those three (their sha256 differs from PyPI's).
// depends: what Pyodide loads first; imports: the Python module names each package gives.
const PYODIDE_CDN = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`
const PACKAGES = [
  {
    name: 'pyshacl',
    version: '0.40.1',
    url: 'https://files.pythonhosted.org/packages/03/90/7f35a79db93032ef20db5b740062b54afba32a2c2475a6f0a43c141a69de/pyshacl-0.40.1-py3-none-any.whl',
    sha256: '27dd58c8ddfa103303b4a8c40b2c666332ffc912dbcd3137f7adc7b7bc5e6bda',
    imports: ['pyshacl'],
    depends: ['owlrl', 'packaging', 'prettytable', 'rdflib'],
  },
  {
    name: 'rdflib',
    version: '7.6.0',
    url: 'https://files.pythonhosted.org/packages/10/c2/6604a71269e0c1bd75656d5a001432d16f2cc5b8c057140ec797155c295e/rdflib-7.6.0-py3-none-any.whl',
    sha256: '30c0a3ebf4c0e09215f066be7246794b6492e054e782d7ac2a34c9f70a15e0dd',
    imports: ['rdflib'],
    depends: ['pyparsing'],
  },
  {
    name: 'owlrl',
    version: '7.6.2',
    url: 'https://files.pythonhosted.org/packages/4a/5e/314be7440bf28dbd47f85321a7434c5b74179a762228487d6493c01bddce/owlrl-7.6.2-py3-none-any.whl',
    sha256: '83347bf7f133979e87b2b18695d51d25510b99cec3f6919b5df05d4fbf058ae0',
    imports: ['owlrl'],
    depends: ['rdflib'],
  },
  {
    name: 'prettytable',
    version: '3.18.0',
    url: 'https://files.pythonhosted.org/packages/fe/be/2e6798ace5cc036f5d05d36b7b2fd85346f1a708c87060890b070d0ec607/prettytable-3.18.0-py3-none-any.whl',
    sha256: 'b3346e0e6f79180833aebaac088ae926340586cf6d7d991b9eb125b65f72313a',
    imports: ['prettytable'],
    depends: ['wcwidth'],
  },
  {
    name: 'wcwidth',
    version: '0.6.0',
    url: PYODIDE_CDN + 'wcwidth-0.6.0-py3-none-any.whl',
    sha256: 'ae04c5081c1920e605e9d8b7dffc368a35d8de9b698468819d52e4994a883b76',
    imports: ['wcwidth'],
    depends: [],
  },
  {
    name: 'packaging',
    version: '26.1',
    url: PYODIDE_CDN + 'packaging-26.1-py3-none-any.whl',
    sha256: '565acbbea54da30348b6d68b6a54373e6f777987db9f1db6966b4b3a5060d303',
    imports: ['packaging'],
    depends: [],
  },
  {
    name: 'pyparsing',
    version: '3.3.2',
    url: PYODIDE_CDN + 'pyparsing-3.3.2-py3-none-any.whl',
    sha256: 'f0dd8225b5f8e945980b400bd465b4b90cb0498a2eede0d9dbea98372f6110c5',
    imports: ['pyparsing'],
    depends: [],
  },
]

// The sha256 of some bytes, as hex text (how PyPI and the lock file write it)
function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex')
}

// The sha256 of a file, or null when the file is not there
function fileSha256(path) {
  if (!existsSync(path)) return null
  return sha256(readFileSync(path))
}

// The last part of a URL: 'https://.../rdflib-7.6.0-py3-none-any.whl' -> 'rdflib-7.6.0-py3-none-any.whl'
function fileName(url) {
  return url.split('/').pop()
}

// Write a file in two steps (first a .part file, then rename it), so a broken run
// never leaves half a file that looks finished
function writeWhole(path, bytes) {
  writeFileSync(path + '.part', bytes)
  renameSync(path + '.part', path)
}

// 1. The Pyodide core files, copied from node_modules/pyodide
function copyCoreFiles() {
  const packageJson = JSON.parse(readFileSync(join(PYODIDE_PACKAGE, 'package.json'), 'utf8'))
  if (packageJson.version !== PYODIDE_VERSION) {
    throw new Error(`node_modules/pyodide is version ${packageJson.version}, but the wheels here are for ` +
      `${PYODIDE_VERSION}. Run "npm install" (package.json pins pyodide ${PYODIDE_VERSION}).`)
  }
  for (const name of CORE_FILES) {
    const source = join(PYODIDE_PACKAGE, name)
    const target = join(TARGET, name)
    const bytes = readFileSync(source)
    if (fileSha256(target) === sha256(bytes)) {
      console.log(`  ok        ${name}`)
      continue
    }
    writeWhole(target, bytes)
    console.log(`  copied    ${name} (${bytes.length} bytes)`)
  }
}

// 2. One wheel: download it unless the right file is there, and check its sha256.
// Gives a Promise that resolves when the file is there.
function fetchWheel(pkg) {
  const name = fileName(pkg.url)
  const target = join(TARGET, name)
  if (fileSha256(target) === pkg.sha256) {
    console.log(`  ok        ${name}`)
    return Promise.resolve()
  }
  return fetch(pkg.url)
    .then((response) => {
      if (!response.ok) throw new Error(`${pkg.url} answered ${response.status} ${response.statusText}`)
      return response.arrayBuffer()
    })
    .then((buffer) => {
      const bytes = Buffer.from(buffer)
      const found = sha256(bytes)
      // A different file than the one we tested: never use it
      if (found !== pkg.sha256) {
        throw new Error(`${name}: sha256 is ${found}, expected ${pkg.sha256}. The file was not saved.`)
      }
      writeWhole(target, bytes)
      console.log(`  download  ${name} (${bytes.length} bytes, sha256 ok)`)
    })
}

// 3. The lock file: which packages Pyodide may load, their wheel files (bare names: next to
// the lock file) and sha256 (Pyodide checks it again in the browser). The "info" part (Python
// version, platform) is the one of the Pyodide release itself.
function writeLockFile() {
  const pyodideLock = JSON.parse(readFileSync(join(PYODIDE_PACKAGE, 'pyodide-lock.json'), 'utf8'))
  const lock = { info: pyodideLock.info, packages: {} }
  for (const pkg of PACKAGES) {
    lock.packages[pkg.name] = {
      name: pkg.name,
      version: pkg.version,
      file_name: fileName(pkg.url),
      install_dir: 'site',
      sha256: pkg.sha256,
      package_type: 'package',
      imports: pkg.imports,
      depends: pkg.depends,
      unvendored_tests: false,
    }
  }
  const text = JSON.stringify(lock, null, 2) + '\n'
  const target = join(TARGET, 'pyodide-lock.json')
  if (existsSync(target) && readFileSync(target, 'utf8') === text) {
    console.log('  ok        pyodide-lock.json')
    return
  }
  writeWhole(target, text)
  console.log('  written   pyodide-lock.json')
}

// One download at a time, so the log stays readable (7 small files)
function fetchAllWheels() {
  let chain = Promise.resolve()
  for (const pkg of PACKAGES) {
    chain = chain.then(() => fetchWheel(pkg))
  }
  return chain
}

console.log(`Pyodide ${PYODIDE_VERSION} and the SHACL packages -> ${TARGET}`)
mkdirSync(TARGET, { recursive: true })
Promise.resolve()
  .then(() => copyCoreFiles())
  .then(() => fetchAllWheels())
  .then(() => writeLockFile())
  .then(() => console.log('Done.'))
  .catch((error) => {
    console.error(`Failed: ${error.message}`)
    process.exitCode = 1
  })
