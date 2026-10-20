// What a field does in its source, e.g. "time, key" or "describes Value".
// Used by the Overview and the series inspector.
export function roleText(field) {
  const roles = []
  if (field.isTime) roles.push('time')
  if (field.isKey) roles.push('key')
  if (field.describesField) roles.push('describes ' + field.describesField)
  return roles.length > 0 ? roles.join(', ') : '—'
}
