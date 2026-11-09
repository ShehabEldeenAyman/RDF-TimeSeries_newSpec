// fetch-duckdb-extensions.mjs: download the DuckDB extensions the browser engine needs
// (src/browser/duck.js), so the app serves them itself instead of the browser fetching them
// from extensions.duckdb.org while a user's data is open.
//
//   npm run fetch-browser-assets      (runs this script, then the Pyodide one)
//   node scripts/fetch-duckdb-extensions.mjs
//
// What it does:
//   1. ask the installed @duckdb/duckdb-wasm which DuckDB version is inside it (SELECT version()),
//      because an extension only loads into the exact DuckDB version it was built for;
//   2. download icu (time zones, date_trunc on timestamps with a time zone), parquet and json
//      (the JSON functions, and json_serialize_sql, which duck.js uses to check that a query is
//      one SELECT; the backend's DuckDB has all three built in), for the two kinds of
//      WebAssembly the browser may pick (wasm_eh and wasm_mvp), into
//      public/vendor/duckdb-extensions/<version>/<platform>/<name>.duckdb_extension.wasm
//      (the folder layout DuckDB asks for: see custom_extension_repository in duck.js);
//   3. check every file against the sha256 pinned below. A file that is already there and has
//      the right sha256 is not downloaded again; a download with another sha256 is refused.
// The files are signed by DuckDB and served unchanged. public/vendor/ is in .gitignore.

import { createRequire } from 'node:module'
import { createHash } from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

// The frontend folder (this script is in frontend/scripts/)
const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const TARGET = path.join(FRONTEND, 'public', 'vendor', 'duckdb-extensions')
const SOURCE = 'https://extensions.duckdb.org'

// The sha256 of each file, as downloaded from extensions.duckdb.org on 2026-10-25.
// A new version of @duckdb/duckdb-wasm embeds another DuckDB version: then add its files here.
const PINNED = {
  'v1.5.4': {
    wasm_eh: {
      icu: 'ecdbbb29331f72103b8eb227db5792f186b191a4f7a359db8dda9919b0a736de',
      parquet: '4845705bbd69fc9ad52878d96a505c73cae4a6c509822079cc2413e5eb437f95',
      json: '993b19f7929cc305b2529c548f2842e8e7a5b112d1c88f31c84798b51901ca16',
    },
    wasm_mvp: {
      icu: 'e12652f2953fbf0183c5354443c8e7547ec620958baa9ad303db23f46bdd6eac',
      parquet: 'b64c255a7f7d06cc234535b2f0ecab345fda91bffff5509d3179004bc13aa19a',
      json: '15a89d3fd0fa3449c0d8981e6cb1ca6be8df4bcaf0dfdd6660c17bcc3bdf0af8',
    },
  },
}

// 1. The DuckDB version inside the installed duckdb-wasm package. The package itself does not
// say it in package.json, so we start its Node build (the same WebAssembly) and ask.
async function duckdbVersion() {
  const require = createRequire(import.meta.url)
  const duckdb = require('@duckdb/duckdb-wasm/dist/duckdb-node-blocking.cjs')
  const dist = path.dirname(require.resolve('@duckdb/duckdb-wasm/dist/duckdb-node-blocking.cjs'))
  const bundles = {
    mvp: { mainModule: path.join(dist, 'duckdb-mvp.wasm'), mainWorker: path.join(dist, 'duckdb-node-mvp.worker.cjs') },
    eh: { mainModule: path.join(dist, 'duckdb-eh.wasm'), mainWorker: path.join(dist, 'duckdb-node-eh.worker.cjs') },
  }
  const db = await duckdb.createDuckDB(bundles, new duckdb.VoidLogger(), duckdb.NODE_RUNTIME)
  await db.instantiate()
  db.open({})
  const connection = db.connect()
  const version = connection.query('SELECT version() AS version').getChild('version').get(0)
  connection.close()
  return version // e.g. 'v1.5.4'
}

// The sha256 of some bytes, as hex text
function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex')
}

// 2. + 3. One file: keep it when it is there and correct, otherwise download and check it
async function fetchOne(version, platform, name, expected) {
  const fileName = `${name}.duckdb_extension.wasm`
  const folder = path.join(TARGET, version, platform)
  const target = path.join(folder, fileName)
  const shown = `${version}/${platform}/${fileName}`

  if (fs.existsSync(target) && sha256(fs.readFileSync(target)) === expected) {
    console.log(`ok          ${shown} (already there)`)
    return
  }

  const url = `${SOURCE}/${version}/${platform}/${fileName}`
  const response = await fetch(url)
  if (!response.ok) throw new Error(`Could not download ${url}: ${response.status} ${response.statusText}`)
  const bytes = Buffer.from(await response.arrayBuffer())
  const found = sha256(bytes)
  if (found !== expected) {
    throw new Error(`${url} has sha256 ${found}, but ${expected} is pinned. The file was not saved.`)
  }

  // Write next to the target first and then rename, so a broken download never stays behind
  fs.mkdirSync(folder, { recursive: true })
  fs.writeFileSync(target + '.part', bytes)
  fs.renameSync(target + '.part', target)
  console.log(`downloaded  ${shown} (${(bytes.length / 1e6).toFixed(1)} MB)`)
}

async function main() {
  const version = await duckdbVersion()
  console.log(`@duckdb/duckdb-wasm embeds DuckDB ${version}`)
  const pins = PINNED[version]
  if (!pins) {
    throw new Error(`No sha256 pinned for DuckDB ${version} in scripts/fetch-duckdb-extensions.mjs. ` +
      'Download its extensions once, check them, and add their sha256 to PINNED.')
  }
  for (const platform of Object.keys(pins)) {
    for (const name of Object.keys(pins[platform])) {
      await fetchOne(version, platform, name, pins[platform][name])
    }
  }
}

main().catch((error) => {
  console.error(`fetch-duckdb-extensions: ${error.message}`)
  process.exit(1)
})
