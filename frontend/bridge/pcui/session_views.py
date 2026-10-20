"""Read-only session presentation. Progress is evidence, never an agent liveness claim."""
from __future__ import annotations

import json


def _document(payload) -> dict:
    try:
        doc = json.loads(payload) if payload else None
        return doc if isinstance(doc, dict) else {}
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return {}


# States that wait on the reader rather than the assistant.
NEEDS_READER = {"awaiting", "questions", "needs_changes"}


def session_view(row) -> dict:
    heads = json.loads(row["heads_json"])
    audit = _document(row["audit_payload"])
    checkpoint = _document(row["checkpoint_payload"])
    intake = _document(row["intake_payload"])
    audit_current = row["audit_seq"] >= 0 and row["audit_seq"] > max(
        row["build_seq"], row["checkpoint_seq"], row["decision_seq"], row["intake_seq"], row["intake_answer_seq"])
    # An open intake batch the assistant has not moved past and the reader has not answered here
    # (an answer given in conversation shows up as the assistant's next artifact instead).
    questions_open = row["intake_seq"] >= 0 and row["intake_seq"] > max(
        row["intake_answer_seq"], row["build_seq"], row["checkpoint_seq"], row["audit_seq"]) \
        and intake.get("done") is False and bool(intake.get("batch"))
    if audit_current and audit.get("pass") is True:
        state = "done"
    elif row["checkpoint_seq"] >= 0 and row["decision_seq"] < row["checkpoint_seq"] \
            and checkpoint.get("pause") is True:
        state = "awaiting"
    elif questions_open:
        state = "questions"
    elif audit_current and audit.get("pass") is False:
        state = "needs_changes"
    elif "draft" in heads:
        state = "review"
    else:
        state = "progress" if row["revision"] else "idle"
    view = {"id": row["id"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
            "revision": row["revision"], "heads": heads,
            "meta": _document(row["meta_json"]), "state": state,
            "archived": row["archived_at"] is not None}
    if row["last_kind"]:
        view["lastKind"] = row["last_kind"]
    if row["flag_title"]:
        view["title"] = row["flag_title"]
    return view


# One read joins only the current two status artifacts. No full-artifact or per-session N+1 reads.
SESSION_VIEW_SQL = """
select s.*, a.raw_payload as audit_payload, cp.raw_payload as checkpoint_payload,
       ip.raw_payload as intake_payload, f.title as flag_title, f.archived_at as archived_at,
       coalesce(g.updated_at, s.created_at) as updated_at,
       (select l.kind from session_artifacts l where l.session_id=s.id
        order by l.sequence desc limit 1) as last_kind,
       coalesce(g.audit_seq, -1) as audit_seq, coalesce(g.build_seq, -1) as build_seq,
       coalesce(g.checkpoint_seq, -1) as checkpoint_seq, coalesce(g.decision_seq, -1) as decision_seq,
       coalesce(g.intake_seq, -1) as intake_seq, coalesce(g.intake_answer_seq, -1) as intake_answer_seq
from sessions s
left join (
    select session_id, max(created_at) as updated_at,
           max(case when kind='answer-audit' then sequence else -1 end) as audit_seq,
           max(case when kind in ('context','audit','frame','slate','draft','team','diagnose','tune-result') then sequence else -1 end) as build_seq,
           max(case when kind='checkpoint' then sequence else -1 end) as checkpoint_seq,
           max(case when kind='decision' then sequence else -1 end) as decision_seq,
           max(case when kind='intake' then sequence else -1 end) as intake_seq,
           max(case when kind='intake-answer' then sequence else -1 end) as intake_answer_seq
    from session_artifacts group by session_id
) g on g.session_id=s.id
left join artifacts a on a.hash=json_extract(s.heads_json, '$."answer-audit"')
left join artifacts cp on cp.hash=json_extract(s.heads_json, '$.checkpoint')
left join artifacts ip on ip.hash=json_extract(s.heads_json, '$.intake')
left join session_flags f on f.session_id=s.id
"""

# Counts need the reference, never the potentially large attached team/environment in meta_json.
SESSION_SUMMARY_SQL = SESSION_VIEW_SQL.replace(
    "s.*,", "s.id, s.created_at, s.revision, s.heads_json, '{}' as meta_json, "
    "json_extract(s.meta_json, '$.teamRef.id') as team_ref,")
