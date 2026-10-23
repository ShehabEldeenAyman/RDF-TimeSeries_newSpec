// sections.js: what the series inspector (Inspector.jsx) shows about a field, grouped by meaning.
//
// Each property is named by its full IRI, and says where the inspector looks for its value:
//   on: 'field'   on the field itself (e.g. its tss:datatype)
//   on: 'source'  on the field's source, so it holds for every field of that source (e.g. tss:from)
// When the value is in neither place, the inspector says "not stated".
// To show one more property, add a line here; Inspector.jsx reads it from the store.

import {
  SOSA,
  RDF_TYPE,
  RDFS_LABEL,
  RDFS_COMMENT,
  TSS_FORMAT,
  TSS_COLUMN_NAME,
  TSS_COLUMN_INDEX,
  TSS_FROM,
  TSS_UNTIL,
  TSS_DATATYPE,
  TSS_LANGUAGE,
  TSS_REQUIRED,
  TSS_MINIMUM,
  TSS_MAXIMUM,
  TSS_OBSERVED_PROPERTY,
  TSS_UNIT,
  TSS_SENSOR,
  TSS_FEATURE_OF_INTEREST,
  TSS_PRODUCED_BY,
  TSS_MUTABLE,
  TSS_DESCRIBES_FIELD,
  TSS_HAS_FIELD,
  TSS_POINTS,
} from './tss'

// The sections of the field panel, in the order the inspector shows them.
// (Inspector.jsx adds a few lines to "Data binding": the data file or the embedded
// points first, the time column and the key last.)
export const FIELD_SECTIONS = [
  {
    title: 'Data binding',
    explanation: 'Where the values are: the data, and the column that holds this field.',
    properties: [
      { name: 'format', iri: TSS_FORMAT, on: 'source' },
      { name: 'column', iri: TSS_COLUMN_NAME, on: 'field' },
      { name: 'column index', iri: TSS_COLUMN_INDEX, on: 'field' },
    ],
  },
  {
    title: 'Time window',
    explanation: 'The stretch of time the source covers: from is included, until is not.',
    properties: [
      { name: 'from', iri: TSS_FROM, on: 'source' },
      { name: 'until', iri: TSS_UNTIL, on: 'source' },
    ],
  },
  {
    title: 'Values',
    explanation: 'How to read each value of this field, and which values are allowed.',
    properties: [
      { name: 'datatype', iri: TSS_DATATYPE, on: 'field' },
      { name: 'language', iri: TSS_LANGUAGE, on: 'field' },
      { name: 'required', iri: TSS_REQUIRED, on: 'field' },
      { name: 'minimum', iri: TSS_MINIMUM, on: 'field' },
      { name: 'maximum', iri: TSS_MAXIMUM, on: 'field' },
    ],
  },
  {
    title: 'What is measured',
    explanation: 'What each value of this field is an observation of.',
    properties: [
      { name: 'observes', iri: TSS_OBSERVED_PROPERTY, on: 'field' },
      { name: 'unit', iri: TSS_UNIT, on: 'field' },
      { name: 'made by', iri: TSS_SENSOR, on: 'field' },
      { name: 'feature', iri: TSS_FEATURE_OF_INTEREST, on: 'field' },
    ],
  },
  {
    title: 'Provenance',
    explanation: 'Who produced the data, and whether it can still change.',
    properties: [
      { name: 'produced by', iri: TSS_PRODUCED_BY, on: 'source' },
      { name: 'mutable', iri: TSS_MUTABLE, on: 'source' },
    ],
  },
]

// Properties of a field that the inspector shows in their own place (the heading, the
// comments under it, the "Describes" row), so "Also stated" leaves them out
export const SHOWN_ELSEWHERE = [RDF_TYPE, RDFS_LABEL, RDFS_COMMENT, TSS_DESCRIBES_FIELD]

// Properties of the source that its panel leaves out: the type and the label are its
// heading, its fields are listed under it, and the points can be very long
export const SOURCE_PANEL_SKIPS = [RDF_TYPE, RDFS_LABEL, TSS_HAS_FIELD, TSS_POINTS]

// IRIs that start like this open a real web page, so the inspector links to them
// ('http://qudt.org/' covers all the QUDT namespaces: schema, unit, quantitykind, ...)
export const LINKABLE_VOCABULARIES = [
  { start: 'http://qudt.org/', name: 'QUDT' },
  { start: SOSA, name: 'SOSA' },
]
