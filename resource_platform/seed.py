from .database import connect, migrate
from .security import hash_password
from .models import ROLE_PERMISSIONS

def seed(path):
    migrate(path)
    db = connect(path)
    try:
        with db:
            roles = [("employee","Standard employee"),("resource_manager","Scoped resource manager"),("administrator","Administrator"),("super_administrator","System security administrator")]
            for name, desc in roles:
                db.execute("INSERT OR IGNORE INTO roles(name,description) VALUES (?,?)", (name, desc))
            keys = sorted(set().union(*ROLE_PERMISSIONS.values()))
            for key in keys:
                db.execute("INSERT OR IGNORE INTO permissions(key,description) VALUES (?,?)", (key, key))
            for role in db.execute("SELECT id,name FROM roles"):
                for key in ROLE_PERMISSIONS.get(role["name"], set()):
                    permission = db.execute("SELECT id FROM permissions WHERE key=?", (key,)).fetchone()
                    db.execute("INSERT OR IGNORE INTO role_permissions VALUES (?,?)", (role["id"], permission["id"]))
            for name, code in [("Structural Engineering", "STR"), ("Building Physics", "PHY")]:
                db.execute("INSERT OR IGNORE INTO departments(name,code) VALUES (?,?)", (name, code))
            departments = {r["code"]: r["id"] for r in db.execute("SELECT id,code FROM departments")}
            locations = [
                ("Neumünster Main Office", "NMS", "Fictional Street 1, 24534 Neumünster", departments["STR"]),
                ("Braunschweig Office", "BS", "Fictional Street 2, 38100 Braunschweig", departments["STR"]),
                ("Rostock Office", "ROS", "Fictional Street 3, 18055 Rostock", departments["PHY"]),
            ]
            for row in locations:
                db.execute("INSERT OR IGNORE INTO locations(name,code,address,department_id) VALUES (?,?,?,?)", row)
            location_ids = {r["code"]: r["id"] for r in db.execute("SELECT id,code FROM locations")}
            for row in [("Company Vehicle", "VEH", "Fictional vehicle"), ("Measuring Equipment", "MEAS", "Fictional instrument"), ("Meeting Room", "ROOM", "Fictional room")]:
                db.execute("INSERT OR IGNORE INTO resource_types(name,code,description) VALUES (?,?,?)", row)
            users = [
                ("admin@example.test", "Fictional Administrator", "AdminPassphrase-2026!", "administrator", "STR", "NMS"),
                ("manager@example.test", "Fictional Resource Manager One", "ManagerPassphrase-2026!", "resource_manager", "STR", "NMS"),
                ("manager2@example.test", "Fictional Resource Manager Two", "ManagerTwoPassphrase-2026!", "resource_manager", "PHY", "ROS"),
                ("employee@example.test", "Fictional Employee", "EmployeePassphrase-2026!", "employee", "PHY", "ROS"),
            ]
            for email, name, password, role, department_code, location_code in users:
                db.execute("INSERT OR IGNORE INTO users(email,display_name,password_hash,department_id,default_location_id) VALUES (?,?,?,?,?)", (email, name, hash_password(password), departments[department_code], location_ids[location_code]))
                user = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
                role_row = db.execute("SELECT id FROM roles WHERE name=?", (role,)).fetchone()
                db.execute("INSERT OR IGNORE INTO user_roles(user_id,role_id) VALUES (?,?)", (user["id"], role_row["id"]))
            manager_one = db.execute("SELECT id FROM users WHERE email='manager@example.test'").fetchone()["id"]
            manager_two = db.execute("SELECT id FROM users WHERE email='manager2@example.test'").fetchone()["id"]
            types = {r["code"]: r["id"] for r in db.execute("SELECT id,code FROM resource_types")}
            resources = [
                (types["VEH"], "Fictional Pool Car", "Non-production seed vehicle", "SEED-VEH-001", location_ids["NMS"], departments["STR"], manager_one, manager_one, manager_one),
                (types["MEAS"], "Fictional Thermal Camera", "Non-production seed instrument", "SEED-MEAS-001", location_ids["ROS"], departments["PHY"], manager_two, manager_two, manager_two),
            ]
            for row in resources:
                db.execute("INSERT OR IGNORE INTO resources(resource_type_id,name,description,asset_number,current_location_id,responsible_department_id,responsible_manager_id,created_by,updated_by) VALUES (?,?,?,?,?,?,?,?,?)", row)
    finally:
        db.close()

if __name__ == "__main__":
    from .config import Settings
    seed(Settings.from_env().database_path)
    print("Seeded fictional data.")
