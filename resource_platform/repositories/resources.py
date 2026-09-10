from .base import Repository


class ResourceRepository(Repository):
    def by_id(self, resource_id):
        q, p = self.backend.prepare("SELECT * FROM resources WHERE id = :id", {"id": resource_id})
        return self._fetchone(q, p)

    def list(self, *, status=None):
        query = "SELECT * FROM resources"
        values = {}
        if status is not None:
            query += " WHERE status = :status"
            values["status"] = status
        query += " ORDER BY id"
        q, p = self.backend.prepare(query, values)
        return self._fetchall(q, p)

    def create(self, resource_type_id, name, description="", asset_number=None, created_by=None):
        q, p = self.backend.prepare(
            "INSERT INTO resources(resource_type_id, name, description, asset_number, created_by, updated_by) "
            "VALUES (:resource_type_id, :name, :description, :asset_number, :created_by, :updated_by)",
            {"resource_type_id": resource_type_id, "name": name, "description": description,
             "asset_number": asset_number, "created_by": created_by, "updated_by": created_by},
        )
        return self._execute(q, p)

    def update_name(self, resource_id, name, updated_by=None):
        q, p = self.backend.prepare(
            "UPDATE resources SET name = :name, updated_by = :updated_by WHERE id = :id",
            {"id": resource_id, "name": name, "updated_by": updated_by},
        )
        return self._execute(q, p)
