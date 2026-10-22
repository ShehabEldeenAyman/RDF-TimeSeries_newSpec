// fields.js: small helpers about the fields of a source, read from the metadata store.
// Several tabs use them (Overview, Series inspector, Query, Files).
//
// A field is an RDF term, usually a NamedNode such as ex:grootdok-ec20-field.
// What a field does in its source is said by the SOURCE, not by the field:
//   ex:grootdok-source  tss:timeField  ex:grootdok-time-field .   -> the time field
//   ex:grootdok-source  tss:key        ex:grootdok-time-field .   -> (part of) the key
// A field can also say that it holds extra information about another field:
//   ex:grootdok-quality-code-field  tss:describesField  ex:grootdok-ec20-field .
// So every helper here asks the store, and compares fields by their full IRI.
//
// Usage (rdf is the prop every tab gets from App: { store, prefixes, text, url, source }):
//   import { firstField, fieldText, roleText, findField } from './fields'
//
//   const first = firstField(rdf.store, rdf.source)            // a NamedNode, or null
//   fieldText(rdf.store, first, rdf.prefixes)                  // 'Value: Electrical conductivity ...'
//   roleText(rdf.store, rdf.source, first, rdf.prefixes)       // '—', 'time, key' or 'describes Value'
//   findField(rdf.store, rdf.source, 'https://example.org/ex#grootdok-ec20-field')  // the field, or null

import {
  all,
  has,
  oneText,
  readFields,
  shortName,
  RDFS_LABEL,
  TSS_COLUMN_NAME,
  TSS_DESCRIBES_FIELD,
  TSS_KEY,
  TSS_OBSERVED_PROPERTY,
  TSS_TIME_FIELD,
} from './tss'

// Is this field the time field of the source (source tss:timeField field)?
export function isTimeField(store, source, field) {
  return has(store, source, TSS_TIME_FIELD, field)
}

// Is this field (part of) the key of the source (source tss:key field)?
export function isKeyField(store, source, field) {
  return has(store, source, TSS_KEY, field)
}

// The name of a field for people: its column name (tss:columnName), or its short IRI
// when it has none, e.g. 'Value' or 'ex:grootdok-ec20-field'
export function fieldName(store, field, prefixes) {
  const columnName = oneText(store, field, TSS_COLUMN_NAME)
  if (columnName !== '') return columnName
  return shortName(field, prefixes)
}

// The text for a field in a drop down: its name, and its label when that says more,
// e.g. 'Value: Electrical conductivity at 20 °C, Kanaal Gent-Terneuzen Grootdok (BE)'
export function fieldText(store, field, prefixes) {
  const name = fieldName(store, field, prefixes)
  const label = oneText(store, field, RDFS_LABEL)
  if (label !== '' && label !== name) return name + ': ' + label
  return name
}

// What a field does in its source, e.g. 'time, key' or 'describes Value', or '—'
export function roleText(store, source, field, prefixes) {
  const roles = []
  if (isTimeField(store, source, field)) roles.push('time')
  if (isKeyField(store, source, field)) roles.push('key')
  // A field may describe more than one other field
  for (const described of all(store, field, TSS_DESCRIBES_FIELD)) {
    roles.push('describes ' + fieldName(store, described, prefixes))
  }
  return roles.length > 0 ? roles.join(', ') : '—'
}

// The field to show first: the first measured series (a field with a tss:observedProperty
// that does not describe another field), else the first field that is not the time field,
// else the first field. Gives the field (a term), or null when the source has no fields.
export function firstField(store, source) {
  const fields = readFields(store, source)
  for (const field of fields) {
    // has(..., null): "with any value"
    const measured = has(store, field, TSS_OBSERVED_PROPERTY, null)
    const describes = has(store, field, TSS_DESCRIBES_FIELD, null)
    if (measured && !describes) return field
  }
  for (const field of fields) {
    if (!isTimeField(store, source, field)) return field
  }
  return fields.length > 0 ? fields[0] : null
}

// The field of the source with this full IRI, or null. The backend names fields by
// their IRI in its JSON answers (e.g. the columns of /rows): this finds the field again.
export function findField(store, source, iri) {
  for (const field of readFields(store, source)) {
    if (field.value === iri) return field
  }
  return null
}
