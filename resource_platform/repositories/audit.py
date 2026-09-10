from .base import Repository
class AuditRepository(Repository):
    def record(self, actor_user_id, action, entity_type, entity_id=None, request_id="repository", reason=None):
        q,p=self.backend.prepare("INSERT INTO audit_logs(actor_user_id,action,entity_type,entity_id,request_id,reason) VALUES (:actor_user_id,:action,:entity_type,:entity_id,:request_id,:reason)", {"actor_user_id":actor_user_id,"action":action,"entity_type":entity_type,"entity_id":entity_id,"request_id":request_id,"reason":reason}); return self._execute(q,p)
