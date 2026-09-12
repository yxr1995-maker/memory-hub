"""Shared experience API. Source text is data, never execution instructions."""
from .contracts import AccessContext, ExperienceError, ALLOWED_SOURCE_KINDS, validate_payload, require, check_artifacts
from .store import initialize, record as _record
from .retrieval import recall as recall_for_decision, read as read_evidence


def record_episode(db_path,ctx,payload,*,idempotency_key):
    if type(payload) is dict and payload.get("event_kind")=="feedback":
        from .revisions import feedback
        return feedback(db_path,ctx,payload,idempotency_key)
    validate_payload(payload)
    require(ctx.may_access(payload['collection_id']),'collection outside authorized scope','ACCESS_DENIED')
    require(ctx.may_write_source_kind(payload['source_kind']),'untrusted source elevation','ACCESS_DENIED')
    # An agent can report a claim; only a trusted validator can attest success.
    if ctx.role!='owner':
        require(payload['outcome'].get('verification') in (None,'unknown'),'verification requires trusted evidence','ACCESS_DENIED')
    check_artifacts(payload,ctx)
    return _record(db_path,ctx,payload,idempotency_key)

from .revisions import revise_understanding, revoke

__all__ = ["AccessContext", "ExperienceError", "initialize", "record_episode", "recall_for_decision", "read_evidence", "revise_understanding", "revoke"]
