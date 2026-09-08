/**
 * Life Alert Fantasy — trade ledger endpoint.
 *
 *   POST /trade   file a trade      (X-League-Password required)
 *   GET  /trades  read the ledger   (X-League-Password required)
 *   GET  /health  liveness, no auth
 *
 * The league password is checked HERE, server-side. The static form's login
 * screen is convenience on top of this; this is the part that actually holds.
 */

const JSON_HEADERS = { "Content-Type": "application/json" };

function cors(env, origin) {
  const allowed = (env.ALLOWED_ORIGINS || "*").split(",").map(s => s.trim());
  const value = allowed.includes("*")
    ? "*"
    : (allowed.includes(origin) ? origin : allowed[0] || "null");
  return {
    "Access-Control-Allow-Origin": value,
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-League-Password",
    "Access-Control-Max-Age": "86400",
    "Vary": "Origin",
  };
}

function reply(body, status, headers) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...JSON_HEADERS, ...headers },
  });
}

/** Constant-time-ish compare so the check doesn't leak length by timing. */
function passwordOk(given, expected) {
  if (typeof given !== "string" || typeof expected !== "string") return false;
  if (given.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < given.length; i++) diff |= given.charCodeAt(i) ^ expected.charCodeAt(i);
  return diff === 0;
}

/** Reject anything that isn't a plausible trade record before it reaches D1. */
function validate(r) {
  if (!r || typeof r !== "object") return "Body must be a trade record.";
  if (!r.side_a || !r.side_b) return "Both sides are required.";
  if (!r.side_a.manager_id || !r.side_b.manager_id) return "Both managers are required.";
  if (r.side_a.manager_id === r.side_b.manager_id) return "A manager cannot trade with themselves.";
  const assets = s => (s.players || []).length + (s.picks || []).length;
  if (assets(r.side_a) + assets(r.side_b) === 0) return "A trade needs at least one player or pick.";
  if (r.conditional) {
    if (!r.condition || !String(r.condition.text || "").trim()) return "A conditional trade needs its condition written out.";
    if (!(r.scenarios || []).some(s => (s.picks || []).length)) return "At least one scenario must move a pick.";
  }
  if (JSON.stringify(r).length > 100000) return "That record is implausibly large.";
  return null;
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const headers = cors(env, origin);
    const url = new URL(request.url);

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers });
    if (url.pathname === "/health") return reply({ ok: true }, 200, headers);

    if (!env.LEAGUE_PASSWORD) {
      return reply({ error: "Ledger isn't configured yet — the commissioner needs to set the league password." }, 503, headers);
    }
    if (!passwordOk(request.headers.get("X-League-Password"), env.LEAGUE_PASSWORD)) {
      return reply({ error: "That's not the league password." }, 401, headers);
    }

    try {
      if (request.method === "POST" && url.pathname === "/trade") {
        const record = await request.json();
        const problem = validate(record);
        if (problem) return reply({ error: problem }, 400, headers);

        const id = crypto.randomUUID();
        const filedAt = new Date().toISOString();   // server clock, not the client's

        await env.LEDGER.prepare(
          `INSERT INTO trades (id, season, filed_at, filed_by, status, conditional,
             side_a_manager_id, side_a_manager, side_b_manager_id, side_b_manager, record_json)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`
        ).bind(
          id,
          Number(record.season) || new Date().getUTCFullYear(),
          filedAt,
          record.filed_by || null,
          record.conditional ? "pending_condition" : "settled",
          record.conditional ? 1 : 0,
          record.side_a.manager_id, record.side_a.manager,
          record.side_b.manager_id, record.side_b.manager,
          JSON.stringify({ ...record, id, filed_at: filedAt })
        ).run();

        return reply({ ok: true, id, filed_at: filedAt }, 201, headers);
      }

      if (request.method === "GET" && url.pathname === "/trades") {
        const season = url.searchParams.get("season");
        const limit = Math.min(Number(url.searchParams.get("limit")) || 100, 500);
        const stmt = season
          ? env.LEDGER.prepare(
              `SELECT record_json, status, resolution_json FROM trades
               WHERE season = ? ORDER BY filed_at DESC LIMIT ?`).bind(Number(season), limit)
          : env.LEDGER.prepare(
              `SELECT record_json, status, resolution_json FROM trades
               ORDER BY filed_at DESC LIMIT ?`).bind(limit);

        const { results } = await stmt.all();
        const trades = (results || []).map(row => ({
          ...JSON.parse(row.record_json),
          status: row.status,
          resolution: row.resolution_json ? JSON.parse(row.resolution_json) : null,
        }));
        return reply({ trades }, 200, headers);
      }

      return reply({ error: "No such endpoint." }, 404, headers);
    } catch (err) {
      // Never echo the raw error to the client; it can carry query internals.
      console.error("ledger error", err && err.stack ? err.stack : err);
      return reply({ error: "The ledger hit an error. Copy your record and send it to Jack." }, 500, headers);
    }
  },
};
