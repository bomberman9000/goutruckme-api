
# AI Moderation Contract

This document defines the JSON contract returned by AI moderation endpoints.

## Common fields (all AI moderation responses)

All responses MUST include:

- `risk_level`: string
  - For complaints: `low_risk` | `medium_risk` | `high_risk` | `critical`
  - For forum: `low_risk` | `medium_risk` | `high_risk`
- `risk_score`: integer (0–100)
- `issues`: array of strings (may be empty)
- `recommendations`: array of strings (may be empty)
- `auto_action`: string (see per-entity mapping below)
- `model`: string (e.g. `gpt-4.1-mini`)
- `version`: string (e.g. `ai_jurist_v1`)
- `analyzed_at`: ISO-8601 UTC timestamp ending with `Z`
- `source`: string
  - `auto` | `manual` | `view` | `admin`

No additional required fields are assumed unless explicitly stated below.

---

## Complaints (Pretensions)

### Endpoints

- `GET /api/complaints/{complaint_id}/ai-analysis`
- `POST /api/complaints/{complaint_id}/ai-analysis/run`

### Required response fields

The response MUST contain the **Common fields**.

### `auto_action` values (complaints)

- `auto_confirm`
- `auto_penalty`
- `send_to_admin`
- `none`

### Notes

- `needs_moderation` / `can_publish` are **not part of the complaint contract** and should be omitted or set to `null` if present.

---

## Forum

### Endpoints (planned)

- `GET /api/forum/{post_id}/ai-analysis`
- `POST /api/forum/{post_id}/ai-analysis/run`

### Required response fields

The response MUST contain the **Common fields**.

### `auto_action` values (forum)

- `block`
- `send_to_admin`
- `publish`
- `none`

### Publishing flags (forum only)

Forum moderation responses SHOULD also include:

- `needs_moderation`: boolean
- `can_publish`: boolean

Mapping guidance:

- If `auto_action` is `block` or `send_to_admin` → `needs_moderation = true`, `can_publish = false`
- If `auto_action` is `publish` → `needs_moderation = false`, `can_publish = true`
- If `auto_action` is `none` → both flags depend on upstream business rules (default to `false/false` unless specified)

---

## Example (complaint)

```json
{
  "risk_level": "high_risk",
  "risk_score": 62,
  "issues": ["No proof attached", "Aggressive language"],
  "recommendations": ["Request documents", "Escalate to admin"],
  "auto_action": "send_to_admin",
  "model": "gpt-4.1-mini",
  "version": "ai_jurist_v1",
  "analyzed_at": "2026-02-05T12:34:56Z",
  "source": "manual"
}
```
