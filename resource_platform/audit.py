import hashlib, json, secrets

def audit(db, actor_user_id, action, entity_type, entity_id=None, before=None, after=None, reason=None, request_id=None, ip=None):
    ip_hash = hashlib.sha256(ip.encode()).hexdigest() if ip else None
    db.execute("INSERT INTO audit_logs(actor_user_id,action,entity_type,entity_id,request_id,ip_address_hash,before_data,after_data,reason) VALUES (?,?,?,?,?,?,?,?,?)", (actor_user_id, action, entity_type, entity_id, request_id or secrets.token_hex(16), ip_hash, json.dumps(before, sort_keys=True) if before is not None else None, json.dumps(after, sort_keys=True) if after is not None else None, reason))
