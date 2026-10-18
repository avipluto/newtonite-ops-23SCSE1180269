// Small wrapper around fetch() for our JSON API.
// Every request carries the login token; errors become ApiError objects.

const TOKEN_KEY = "ops_token";

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (token) =>
  token ? localStorage.setItem(TOKEN_KEY, token) : localStorage.removeItem(TOKEN_KEY);

export class ApiError extends Error {
  constructor(status, body) {
    super(messageFrom(body) || `Request failed (${status})`);
    this.status = status;
    this.code = body?.code; // e.g. "version_conflict", "already_claimed"
    this.current = body?.current; // on 409: the item as it is now on the server
  }
}

function messageFrom(body) {
  if (!body) return null;
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) {
    // FastAPI validation errors: [{loc: ["body", "title"], msg: "..."}]
    return body.detail.map((d) => `${d.loc.slice(1).join(".")}: ${d.msg}`).join("; ");
  }
  return null;
}

export async function api(path, { method = "GET", body, idempotencyKey } = {}) {
  const headers = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;

  const response = await fetch(`/api${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = response.status === 204 ? null : await response.json().catch(() => null);

  if (!response.ok) {
    if (response.status === 401 && path !== "/auth/login") {
      setToken(null); // token expired or revoked: back to the login page
      window.location.assign("/");
    }
    throw new ApiError(response.status, data);
  }
  return data;
}

// One key per user action. Sending the same key again (double-click, retry)
// makes the server return the first result instead of doing the work twice.
export function newIdempotencyKey() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

// {status: ["open", "closed"], q: "refund"} -> "?status=open&status=closed&q=refund"
export function toQuery(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    const values = Array.isArray(value) ? value : [value];
    for (const v of values) if (v !== undefined && v !== null && v !== "") search.append(key, v);
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}
