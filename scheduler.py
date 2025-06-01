import os
import base64
import json
from datetime import datetime, timedelta
from apscheduler.schedulers.blocking import BlockingScheduler
import firebase_admin
from firebase_admin import credentials, firestore

# ----------------------------------
# 🔐 Initialize Firebase
# ----------------------------------
def init_firebase():
    cred_dict = json.loads(base64.b64decode(os.environ["FIREBASE_CREDENTIALS_BASE64"]).decode())
    cred = credentials.Certificate(cred_dict)
    firebase_admin.initialize_app(cred)

init_firebase()
db = firestore.client()

# ----------------------------------
# 🔔 System Alert Creator
# ----------------------------------
def create_system_alert(agency_id, title, message, severity="medium", category="generic", site_id=None):
    alert = {
        "agencyId": agency_id,
        "title": title,
        "message": message,
        "severity": severity,
        "category": category,
        "siteId": site_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "read": False
    }
    db.collection("systemAlerts").add(alert)

# ----------------------------------
# 🔁 Auto Clock-Out
# ----------------------------------
def auto_clockout_expired_shifts():
    now = datetime.utcnow()
    settings = db.collection("agencySettings").stream()
    enabled_agencies = {s.id for s in settings if s.to_dict().get("autoClockOut", False)}

    records = db.collection("attendance").where("clockOut", "==", None).stream()
    for doc in records:
        att = doc.to_dict()
        if att["agencyId"] not in enabled_agencies or not att.get("shiftId"):
            continue
        shift_doc = db.collection("shifts").document(att["shiftId"]).get()
        if not shift_doc.exists:
            continue
        shift = shift_doc.to_dict()
        shift_end = datetime.fromisoformat(shift["shiftEnd"].replace("Z", "+00:00"))

        if now > shift_end:
            hours = round((now - datetime.fromisoformat(att["clockIn"].replace("Z", "+00:00"))).total_seconds() / 3600, 2)
            db.collection("attendance").document(doc.id).update({
                "clockOut": now.isoformat() + "Z",
                "hoursWorked": hours,
                "updatedAt": now.isoformat() + "Z"
            })
            db.collection("shifts").document(att["shiftId"]).update({
                "status": "completed",
                "updatedAt": now.isoformat() + "Z"
            })
            create_system_alert(
                att["agencyId"],
                "Auto Clock-Out Executed",
                f"Employee {att['userId']} auto clocked out at shift end.",
                category="auto_clockout"
            )

# ----------------------------------
# 🔕 Inactivity Reminders
# ----------------------------------
def send_activity_reminders():
    now = datetime.utcnow()
    settings = db.collection("agencySettings").stream()

    for s in settings:
        agency_id = s.id
        config = s.to_dict()
        freq = config.get("activityReportFrequency", "30min")
        if freq == "OFF":
            continue

        interval = {"30min": 30, "1hr": 60, "2hr": 120}.get(freq, 30)
        employees = db.collection("employees").where("agencyId", "==", agency_id).stream()

        for emp in employees:
            e = emp.to_dict()
            last = e.get("lastKnownLocation", {}).get("updatedAt")
            if not last:
                continue
            last_seen = datetime.fromisoformat(last.replace("Z", "+00:00"))
            if (now - last_seen).total_seconds() > interval * 60:
                create_system_alert(
                    agency_id,
                    "Employee Inactivity",
                    f"{e.get('name', 'An employee')} inactive for {freq}.",
                    category="inactivity"
                )

# ----------------------------------
# 🧾 License Expiry Reminders
# ----------------------------------
def send_license_reminders():
    now = datetime.utcnow()
    settings = db.collection("agencySettings").stream()

    for s in settings:
        agency_id = s.id
        reminder = s.to_dict().get("licenseExpiryReminder", "1week")
        days_map = {"1week": 7, "2weeks": 14, "1month": 30}
        days = days_map.get(reminder, 7)

        licenses = db.collection("licenses").stream()
        for lic in licenses:
            l = lic.to_dict()
            if l.get("agencyId") != agency_id or not l.get("expiryDate"):
                continue
            expiry = datetime.fromisoformat(l["expiryDate"].replace("Z", "+00:00"))
            if 0 <= (expiry - now).days == days:
                create_system_alert(
                    agency_id,
                    "License Expiry Reminder",
                    f"Employee {l['employeeId']}'s license expires in {days} days.",
                    category="license"
                )

# ----------------------------------
# ⏱ APScheduler Setup
# ----------------------------------
if __name__ == "__main__":
    scheduler = BlockingScheduler()
    scheduler.add_job(auto_clockout_expired_shifts, "interval", minutes=15)
    scheduler.add_job(send_activity_reminders, "interval", minutes=15)
    scheduler.add_job(send_license_reminders, "cron", hour=7)
    print("✅ SecureFront Scheduler started...")
    scheduler.start()
