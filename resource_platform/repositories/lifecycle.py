from .base import Repository


class LifecycleRepository(Repository):
    def active_checkout(self, resource_id):
        q, p = self.backend.prepare(
            "SELECT * FROM checkouts WHERE resource_id = :resource_id AND checked_in_at IS NULL",
            {"resource_id": resource_id}
        )
        return self._fetchone(q, p)

    def checkout(self, resource_id, booking_id, user_id, condition_before, notes=None):
        q, p = self.backend.prepare(
            "INSERT INTO checkouts(resource_id, booking_id, user_id, condition_before, notes) "
            "VALUES (:resource_id, :booking_id, :user_id, :condition_before, :notes)",
            {"resource_id": resource_id, "booking_id": booking_id, "user_id": user_id,
             "condition_before": condition_before, "notes": notes},
        )
        return self._execute(q, p)

    def checkin_for_checkout(self, checkout_id):
        q, p = self.backend.prepare("SELECT * FROM checkins WHERE checkout_id = :checkout_id", {"checkout_id": checkout_id})
        return self._fetchone(q, p)

    def checkin(self, checkout_id, resource_id, booking_id, user_id, condition_after, notes=None, missing_accessories=None, problems=None, damage_reported=False):
        q, p = self.backend.prepare(
            "INSERT INTO checkins(checkout_id, resource_id, booking_id, user_id, condition_after, notes, "
            "missing_accessories, problems, damage_reported) VALUES (:checkout_id, :resource_id, :booking_id, "
            ":user_id, :condition_after, :notes, :missing_accessories, :problems, :damage_reported)",
            {"checkout_id": checkout_id, "resource_id": resource_id, "booking_id": booking_id, "user_id": user_id,
             "condition_after": condition_after, "notes": notes, "missing_accessories": missing_accessories,
             "problems": problems, "damage_reported": damage_reported},
        )
        return self._execute(q, p)
