from .base import Repository


class UserRepository(Repository):
    def by_email(self, email):
        q, p = self.backend.prepare("SELECT * FROM users WHERE email = :email", {"email": email})
        return self._fetchone(q, p)

    def by_id(self, user_id):
        q, p = self.backend.prepare("SELECT * FROM users WHERE id = :id", {"id": user_id})
        return self._fetchone(q, p)

    def create(self, email, display_name, status="active", password_hash=None):
        q, p = self.backend.prepare(
            "INSERT INTO users(email, display_name, status, password_hash) "
            "VALUES (:email, :display_name, :status, :password_hash)",
            {"email": email, "display_name": display_name, "status": status, "password_hash": password_hash},
        )
        return self._execute(q, p)

    def roles(self, user_id):
        q, p = self.backend.prepare(
            "SELECT r.* FROM roles r JOIN user_roles ur ON ur.role_id = r.id "
            "WHERE ur.user_id = :user_id", {"user_id": user_id}
        )
        return self._fetchall(q, p)
