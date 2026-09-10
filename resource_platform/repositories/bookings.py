from .base import Repository


class BookingRepository(Repository):
    def by_id(self, booking_id):
        q, p = self.backend.prepare("SELECT * FROM bookings WHERE id = :id", {"id": booking_id})
        return self._fetchone(q, p)

    def for_resource(self, resource_id, *, status=None):
        query = "SELECT * FROM bookings WHERE resource_id = :resource_id"
        values = {"resource_id": resource_id}
        if status is not None:
            query += " AND status = :status"
            values["status"] = status
        query += " ORDER BY start_at_utc"
        q, p = self.backend.prepare(query, values)
        return self._fetchall(q, p)

    def create(self, resource_id, owner_user_id, start_at_utc, end_at_utc, purpose, created_by, project_reference=None, destination=None):
        q, p = self.backend.prepare(
            "INSERT INTO bookings(resource_id, owner_user_id, start_at_utc, end_at_utc, purpose, "
            "project_reference, destination, created_by) VALUES (:resource_id, :owner_user_id, :start_at_utc, "
            ":end_at_utc, :purpose, :project_reference, :destination, :created_by)",
            {"resource_id": resource_id, "owner_user_id": owner_user_id, "start_at_utc": start_at_utc,
             "end_at_utc": end_at_utc, "purpose": purpose, "project_reference": project_reference,
             "destination": destination, "created_by": created_by},
        )
        return self._execute(q, p)

    def cancel(self, booking_id, cancelled_at, reason):
        q, p = self.backend.prepare(
            "UPDATE bookings SET status = 'cancelled', cancelled_at = :cancelled_at, "
            "cancellation_reason = :reason WHERE id = :id",
            {"id": booking_id, "cancelled_at": cancelled_at, "reason": reason},
        )
        return self._execute(q, p)
