from .base import Repository


class QrTokenRepository(Repository):
    def active_for_resource(self, resource_id):
        q, p = self.backend.prepare(
            "SELECT * FROM qr_tokens WHERE resource_id = :resource_id "
            "AND invalidated_at IS NULL ORDER BY id DESC", {"resource_id": resource_id}
        )
        return self._fetchone(q, p)

    def by_hash(self, token_hash):
        q, p = self.backend.prepare("SELECT * FROM qr_tokens WHERE token_hash = :token_hash", {"token_hash": token_hash})
        return self._fetchone(q, p)

    def create(self, resource_id, token_hash, created_by=None):
        q, p = self.backend.prepare(
            "INSERT INTO qr_tokens(resource_id, token_hash, created_by) "
            "VALUES (:resource_id, :token_hash, :created_by)",
            {"resource_id": resource_id, "token_hash": token_hash, "created_by": created_by},
        )
        return self._execute(q, p)

    def invalidate(self, resource_id, invalidated_at, invalidated_by=None):
        q, p = self.backend.prepare(
            "UPDATE qr_tokens SET invalidated_at = :invalidated_at, invalidated_by = :invalidated_by "
            "WHERE resource_id = :resource_id AND invalidated_at IS NULL",
            {"resource_id": resource_id, "invalidated_at": invalidated_at, "invalidated_by": invalidated_by},
        )
        return self._execute(q, p)
