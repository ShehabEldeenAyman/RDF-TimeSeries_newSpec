// python.js: Python's (and rdflib's) way of writing, rounding, adding, reading and comparing
// values, in JavaScript. You can skip this file to follow what the app does.
//
// Why it exists: for your own endpoint the browser computes the answers the server computes for
// a bundled one, and they must be the server's answers character for character (the same JSON,
// so a test can compare the two as text). The server's answers come from Python, and JavaScript
// does some small things differently. Each function here copies Python's way:
//   pyFloatRepr        Python writes the float 1605.0 and 1e-05; JavaScript writes 1605 and 0.00001
//   pyRound            round(0.0625, 3) is 0.062 in Python (half way: the even one);
//                      Number((0.0625).toFixed(3)) is 0.063 in JavaScript
//   pySum              Python's sum() corrects its rounding errors: sum([0.1] * 10) is 1.0;
//                      adding 0.1 ten times in a JavaScript loop gives 0.9999999999999999
//   pyInt              int(' 1_000 ') is 1000 in Python, Number(' 1_000 ') is NaN; and
//                      Number('') is 0, where int('') is an error
//   pyRepr             Python writes a text in a message as 'abc' or "it's", with escapes like
//                      \t and \x00; JSON.stringify writes "abc" and \u0000
//   compareCodePoints  Python sorts texts by code point: 'ｚ' (U+FF5A) before '😀' (U+1F600);
//                      JavaScript's sort compares UTF-16 units and puts '😀' first
//   messageText,       rdflib writes a term in an error message as xsd:double, <https://...> or
//   termTextWith       "abc"^^xsd:integer, with its own prefixes (xsd:, rdfs:, sosa:, ...) even
//                      when the document does not declare them; n3 has no such function
//
// It ports parts of Python itself and of rdflib (namespace.split_uri, Term.n3), no module of the
// backend. Used by the other files of the engine: values.js, sourceReading.js, dataReading.js,
// queryHelpers.js, query.js, arrowValues.js, duck.js and observationsWorker.js.

import { XSD } from '../tss.js'

// ---------------------------------------------------------------------------
// Numbers: how Python writes, rounds, adds and reads them
// ---------------------------------------------------------------------------

// A float as Python writes it (repr): 4797.72, 1605.0, 1e+16, 1e-05, nan, inf, -0.0.
// JavaScript finds the same shortest digits, but writes them in another way (1605, 1e-5).
export function pyFloatRepr(x) {
  if (Number.isNaN(x)) return 'nan'
  if (x === Infinity) return 'inf'
  if (x === -Infinity) return '-inf'
  if (x === 0) return Object.is(x, -0) ? '-0.0' : '0.0'
  // The shortest digits that give x back, and where the decimal point goes (decpt: after so many digits)
  const text = String(Math.abs(x)) // e.g. '4797.72', '1e+21', '1.5e-7', '0.0001'
  let mantissa = text
  let exponent = 0
  const ePosition = text.indexOf('e')
  if (ePosition >= 0) {
    mantissa = text.slice(0, ePosition)
    exponent = Number(text.slice(ePosition + 1))
  }
  const parts = mantissa.split('.')
  let digits = parts[0] + (parts.length > 1 ? parts[1] : '')
  let decpt = parts[0].length + exponent
  while (digits.length > 1 && digits[0] === '0') {
    digits = digits.slice(1)
    decpt -= 1
  }
  digits = digits.replace(/0+$/, '')
  if (digits === '') digits = '0'

  let written
  if (decpt <= -4 || decpt > 16) {
    // Python's exponent form: one digit, maybe a fraction, and at least two exponent digits
    const power = decpt - 1
    written = digits[0] + (digits.length > 1 ? '.' + digits.slice(1) : '') +
      'e' + (power < 0 ? '-' : '+') + String(Math.abs(power)).padStart(2, '0')
  } else if (decpt <= 0) {
    written = '0.' + '0'.repeat(-decpt) + digits
  } else if (decpt >= digits.length) {
    written = digits + '0'.repeat(decpt - digits.length) + '.0'
  } else {
    written = digits.slice(0, decpt) + '.' + digits.slice(decpt)
  }
  return (x < 0 ? '-' : '') + written
}

// Python's round(x, digits) for a float: the nearest number with that many decimals, and at
// exactly half way the even one (round(0.25, 1) is 0.2; JavaScript's toFixed would give 0.3)
export function pyRound(x, digits) {
  if (!Number.isFinite(x) || Math.abs(x) >= 1e21) return x
  // x lies exactly half way between two candidates when x * 2 * 10^digits is an odd whole number.
  // A float is a fraction with a power of 2 below, so that happens exactly when x * 2^(digits+1)
  // is an odd whole number m; then x * 2 * 10^digits is m * 5^digits (BigInt: no digit is lost).
  const scaled = x * 2 ** (digits + 1)
  if (Number.isInteger(scaled) && scaled % 2 !== 0) {
    const doubled = BigInt(scaled) * 5n ** BigInt(digits) // x * 2 * 10^digits
    const lower = (doubled - 1n) / 2n // the two candidates, in units of 10^-digits
    const upper = (doubled + 1n) / 2n
    const even = lower % 2n === 0n ? lower : upper
    return Number(even.toString() + 'e-' + digits)
  }
  // Otherwise toFixed gives the correctly rounded decimal text
  return Number(x.toFixed(digits))
}

// Python's sum() of floats (since Python 3.12): Neumaier's compensated sum, which loses
// less precision than adding one by one. The backend's hourly/daily means use it.
export function pySum(values) {
  if (values.length === 0) return 0
  let total = 0 + values[0]
  let compensation = 0
  for (let i = 1; i < values.length; i++) {
    const x = values[i]
    const next = total + x
    if (Math.abs(total) >= Math.abs(x)) compensation += (total - next) + x
    else compensation += (x - next) + total
    total = next
  }
  if (compensation !== 0 && Number.isFinite(compensation)) total += compensation
  return total
}

// A whole number as Python's int() reads a text (spaces around it and _ between digits are fine),
// or null. (Bigger than 2^53 loses precision here; the backend keeps it exact.)
export function pyInt(text) {
  if (!/^\s*[+-]?[0-9](?:_?[0-9])*\s*$/.test(text)) return null
  return Number(text.trim().replaceAll('_', ''))
}

// ---------------------------------------------------------------------------
// Texts: how Python writes and sorts them
// ---------------------------------------------------------------------------

// Characters Python's repr writes as an escape: control characters and other "not printable" ones
const NOT_PRINTABLE = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}\p{Zs}]/u

// A text as Python's repr writes it: 'abc', "it's", 'tab\there'
export function pyRepr(text) {
  const quote = text.includes("'") && !text.includes('"') ? '"' : "'"
  let written = quote
  for (const character of text) {
    if (character === '\\') written += '\\\\'
    else if (character === quote) written += '\\' + quote
    else if (character === '\t') written += '\\t'
    else if (character === '\n') written += '\\n'
    else if (character === '\r') written += '\\r'
    else if (character !== ' ' && NOT_PRINTABLE.test(character)) {
      const code = character.codePointAt(0)
      if (code < 0x100) written += '\\x' + code.toString(16).padStart(2, '0')
      else if (code < 0x10000) written += '\\u' + code.toString(16).padStart(4, '0')
      else written += '\\U' + code.toString(16).padStart(8, '0')
    } else written += character
  }
  return written + quote
}

// Python compares texts by their characters' code points (JavaScript's < compares UTF-16 units)
export function compareCodePoints(a, b) {
  const charactersA = Array.from(a)
  const charactersB = Array.from(b)
  const length = Math.min(charactersA.length, charactersB.length)
  for (let i = 0; i < length; i++) {
    const difference = charactersA[i].codePointAt(0) - charactersB[i].codePointAt(0)
    if (difference !== 0) return difference
  }
  return charactersA.length - charactersB.length
}

// ---------------------------------------------------------------------------
// RDF terms: how rdflib writes them in messages
// ---------------------------------------------------------------------------

// The prefixes rdflib knows by itself (Graph() binds them): rdflib's error messages use them
// too (source_reading.message_text), next to the prefixes of the document
const RDFLIB_PREFIXES = {
  brick: 'https://brickschema.org/schema/Brick#',
  csvw: 'http://www.w3.org/ns/csvw#',
  dc: 'http://purl.org/dc/elements/1.1/',
  dcat: 'http://www.w3.org/ns/dcat#',
  dcmitype: 'http://purl.org/dc/dcmitype/',
  dcterms: 'http://purl.org/dc/terms/',
  dcam: 'http://purl.org/dc/dcam/',
  doap: 'http://usefulinc.com/ns/doap#',
  foaf: 'http://xmlns.com/foaf/0.1/',
  geo: 'http://www.opengis.net/ont/geosparql#',
  odrl: 'http://www.w3.org/ns/odrl/2/',
  org: 'http://www.w3.org/ns/org#',
  prof: 'http://www.w3.org/ns/dx/prof/',
  prov: 'http://www.w3.org/ns/prov#',
  qb: 'http://purl.org/linked-data/cube#',
  schema: 'https://schema.org/',
  sh: 'http://www.w3.org/ns/shacl#',
  skos: 'http://www.w3.org/2004/02/skos/core#',
  sosa: 'http://www.w3.org/ns/sosa/',
  ssn: 'http://www.w3.org/ns/ssn/',
  time: 'http://www.w3.org/2006/time#',
  vann: 'http://purl.org/vocab/vann/',
  void: 'http://rdfs.org/ns/void#',
  wgs: 'https://www.w3.org/2003/01/geo/wgs84_pos#',
  owl: 'http://www.w3.org/2002/07/owl#',
  rdf: 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
  rdfs: 'http://www.w3.org/2000/01/rdf-schema#',
  xsd: 'http://www.w3.org/2001/XMLSchema#',
  xml: 'http://www.w3.org/XML/1998/namespace',
}

// The prefixes rdflib would use for a document: its own defaults, then the document's @prefix lines
export function rdflibPrefixes(prefixes) {
  return Object.assign({}, RDFLIB_PREFIXES, prefixes ? prefixes : {})
}

// Where rdflib splits an IRI into a namespace and a local name (rdflib.namespace.split_uri):
// the local name is the longest run of name characters at the end, from its first letter, digit or _.
// Gives [namespace, local name], or null when it can't split the IRI.
const NAME_CHARACTER = /[\p{L}\p{Nl}\p{Nd}\p{Mn}\p{Mc}\p{Me}]/u
const ALLOWED_NAME_CHARACTERS = ['\u00B7', '\u0387', '-', '.', '_', '%', '(', ')']
const SPLIT_START = /[\p{Ll}\p{Lu}\p{Lo}\p{Lt}\p{Nl}\p{Nd}_]/u
function splitIri(iri) {
  const characters = Array.from(iri)
  for (let i = characters.length - 1; i >= 0; i--) {
    const character = characters[i]
    if (NAME_CHARACTER.test(character) || ALLOWED_NAME_CHARACTERS.includes(character)) continue
    for (let j = i; j < characters.length; j++) {
      if (SPLIT_START.test(characters[j])) {
        if (j === 0) return null
        return [characters.slice(0, j).join(''), characters.slice(j).join('')]
      }
    }
    return null
  }
  return null
}

// An IRI as rdflib writes it with a namespace manager: prefix:local, or <iri>
function rdflibName(iri, prefixTable) {
  const split = splitIri(iri)
  if (split !== null) {
    for (const prefix of Object.keys(prefixTable)) {
      if (prefixTable[prefix] === split[0]) return prefix + ':' + split[1]
    }
  }
  return '<' + iri + '>'
}

// How an RDF term is written in an error message, like rdflib's term.n3(namespace_manager)
// (source_reading.message_text): xsd:double, <https://...>, "abc"^^xsd:integer, "Value"@en
//   prefixes: the document's prefixes (rdf.prefixes); rdflib's own are added
export function messageText(term, prefixes) {
  if (term === null || term === undefined) return '(not stated)'
  return termTextWith(term, rdflibPrefixes(prefixes))
}

// The same, with exactly these prefixes (prefix -> namespace), e.g. the prefixes of the
// converter's preview graph. An empty table writes every IRI in full: <https://...>
export function termTextWith(term, table) {
  if (term.termType === 'NamedNode') return rdflibName(term.value, table)
  if (term.termType === 'BlankNode') return '_:' + term.value
  if (term.termType === 'Literal') {
    const quoted = '"' + term.value.replaceAll('\\', '\\\\').replaceAll('"', '\\"')
      .replaceAll('\n', '\\n').replaceAll('\r', '\\r') + '"'
    if (term.language) return quoted + '@' + term.language
    if (term.datatype && term.datatype.value !== XSD + 'string') return quoted + '^^' + rdflibName(term.datatype.value, table)
    return quoted
  }
  return String(term.value)
}
