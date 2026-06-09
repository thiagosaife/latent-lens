/**
 * Attaches a bearer token to API requests when one is configured via
 * `VITE_API_TOKEN`. Off by default (no header) so local dev works against an
 * unauthenticated backend; set the env var to talk to a hardened deployment.
 */
export function authHeaders(): Record<string, string> {
  const token = import.meta.env.VITE_API_TOKEN
  return token ? { Authorization: `Bearer ${token}` } : {}
}
