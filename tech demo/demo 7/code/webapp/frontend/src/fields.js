// Small helpers for the fields of a source (info.fields), used by several tabs

// What a field does in its source, e.g. "time, key" or "describes Value"
export function roleText(field) {
  const roles = []
  if (field.isTime) roles.push('time')
  if (field.isKey) roles.push('key')
  if (field.describesField) roles.push('describes ' + field.describesField)
  return roles.length > 0 ? roles.join(', ') : '—'
}

// The field to show first: the first measured series (a field with an observed
// property that does not describe another field), else the first field that is not the time
export function firstField(fields) {
  for (const field of fields) {
    if (field.observedProperty && !field.describesField) return field.id
  }
  for (const field of fields) {
    if (!field.isTime) return field.id
  }
  return fields.length > 0 ? fields[0].id : ''
}

// The text for a field in a drop down, e.g. "Value: Electrical conductivity at 20 °C, ..."
export function fieldText(field) {
  if (field.label && field.label !== field.columnName) return field.columnName + ': ' + field.label
  return field.columnName
}
