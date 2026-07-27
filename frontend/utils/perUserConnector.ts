// Frontend mirror of backend `is_per_user_connector` / `PER_USER_TOKEN_TYPES`
// (app/schemas/data_source_registry.py). Per-user-token connectors let each
// member sign in with their own Microsoft account; with
// HYBRID_PER_USER_TABLE_SELECT they manage their own active-table set + private
// training. EXPLICIT two-type set — must stay in sync with the backend. A shared
// connector must never be treated as per-user, so keep this list deliberate.
export const PER_USER_TOKEN_TYPES = ['fabric_user', 'powerbi_user'] as const

export function isPerUserConnector(dsOrType: any): boolean {
  const t = typeof dsOrType === 'string' ? dsOrType : dsOrType?.type
  return (PER_USER_TOKEN_TYPES as readonly string[]).includes(t)
}
