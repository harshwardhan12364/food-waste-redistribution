from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional


class ValidationError(ValueError):
    """Raised when a donation or request contains invalid user data."""


class InvalidTransition(ValueError):
    """Raised when an operation is not valid for the current request status."""


class StorageError(RuntimeError):
    """Raised when persistent storage cannot be read or initialized safely."""


@dataclass(frozen=True)
class FoodListing:
    id: int
    donor: str
    food: str
    category: str
    meals: int
    location: str
    expires_at: datetime
    description: str = ""
    pickup_instructions: str = ""
    created_at: datetime = datetime.min.replace(tzinfo=timezone.utc)
    allocated_meals: int = 0
    status: str = "Available"

    @property
    def available_meals(self) -> int:
        return max(0, self.meals - self.allocated_meals)

    @property
    def hours_left(self) -> float:
        return max(0.0, (self.expires_at - datetime.now(timezone.utc)).total_seconds() / 3600)


@dataclass(frozen=True)
class FoodRequest:
    id: int
    organization: str
    category: str
    meals: int
    location: str
    created_at: datetime
    status: str = "Submitted"
    allocation_id: Optional[int] = None
    donation_id: Optional[int] = None
    donor: Optional[str] = None
    food: Optional[str] = None
    pickup_instructions: str = ""
    rejection_reason: Optional[str] = None


@dataclass(frozen=True)
class AppUser:
    id: int
    name: str
    email: str
    created_at: datetime


class RedistributionSystem:
    """SQLite-backed food board with transactional reservations and request lifecycle."""

    CATEGORIES = {"prepared", "produce", "bakery", "dairy", "other"}
    ACTIVE_ALLOCATION_STATUSES = ("Reserved", "Confirmed", "Completed")

    def __init__(
        self,
        database_path: str | Path = "foodshare.db",
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.database_path = str(database_path)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._initialize_database()

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValidationError("The system clock must return a timezone-aware datetime.")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _stamp(value: datetime) -> str:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValidationError("Expiry must include a timezone.")
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds")

    @staticmethod
    def _parse_stamp(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise StorageError(f"Stored timestamp is malformed: {value!r}.") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise StorageError("Stored timestamps must include a timezone.")
        return parsed.astimezone(timezone.utc)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize_database(self) -> None:
        try:
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL CHECK(length(trim(name)) > 0),
                        email TEXT NOT NULL UNIQUE CHECK(length(trim(email)) > 0),
                        password_hash TEXT NOT NULL,
                        salt TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS donations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        donor TEXT NOT NULL CHECK(length(trim(donor)) > 0),
                        food TEXT NOT NULL CHECK(length(trim(food)) > 0),
                        category TEXT NOT NULL CHECK(category IN ('prepared','produce','bakery','dairy','other')),
                        meals INTEGER NOT NULL CHECK(meals > 0),
                        location TEXT NOT NULL CHECK(length(trim(location)) > 0),
                        expires_at TEXT NOT NULL,
                        description TEXT NOT NULL DEFAULT '',
                        pickup_instructions TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        idempotency_key TEXT UNIQUE
                    );
                    CREATE TABLE IF NOT EXISTS requests (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        organization TEXT NOT NULL CHECK(length(trim(organization)) > 0),
                        category TEXT NOT NULL CHECK(category IN ('prepared','produce','bakery','dairy','other')),
                        meals INTEGER NOT NULL CHECK(meals > 0),
                        location TEXT NOT NULL CHECK(length(trim(location)) > 0),
                        created_at TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN ('Submitted','Matched','Confirmed','Completed','Cancelled','Rejected')),
                        rejection_reason TEXT,
                        idempotency_key TEXT UNIQUE
                    );
                    CREATE TABLE IF NOT EXISTS allocations (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        donation_id INTEGER NOT NULL REFERENCES donations(id),
                        request_id INTEGER NOT NULL UNIQUE REFERENCES requests(id),
                        meals INTEGER NOT NULL CHECK(meals > 0),
                        status TEXT NOT NULL CHECK(status IN ('Reserved','Confirmed','Completed','Released')),
                        created_at TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        message TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_donations_match
                        ON donations(category, location, expires_at);
                    CREATE INDEX IF NOT EXISTS idx_requests_status
                        ON requests(status, created_at, id);
                    CREATE INDEX IF NOT EXISTS idx_allocations_donation
                        ON allocations(donation_id, status);
                    """
                )
                connection.execute("PRAGMA user_version = 1")
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise StorageError(f"Food database integrity check failed: {integrity}")
        except StorageError:
            raise
        except (sqlite3.DatabaseError, OSError) as exc:
            raise StorageError(f"Could not initialize food database '{self.database_path}': {exc}") from exc

    @staticmethod
    def _validate_common(name: str, food: str, category: str, meals: int, location: str) -> tuple[str, str, str, int, str]:
        if not all(isinstance(value, str) for value in (name, food, category, location)):
            raise ValidationError("Names, food details, category, and location must be text.")
        donor_or_org = name.strip()
        food_name = food.strip()
        category_value = category.strip().casefold()
        location_value = location.strip()
        if not donor_or_org:
            raise ValidationError("Donor or organization name is required.")
        if not food_name:
            raise ValidationError("Food name or request description is required.")
        if not location_value:
            raise ValidationError("Neighborhood or pickup location is required.")
        if category_value not in RedistributionSystem.CATEGORIES:
            raise ValidationError("Choose a valid food category.")
        if isinstance(meals, bool) or not isinstance(meals, int) or meals <= 0:
            raise ValidationError("Meal quantity must be a positive whole number.")
        return donor_or_org, food_name, category_value, meals, location_value

    def add_food(
        self,
        donor: str,
        food: str,
        category: str,
        meals: int,
        location: str,
        expires_at: datetime,
        description: str = "",
        pickup_instructions: str = "",
        idempotency_key: Optional[str] = None,
    ) -> FoodListing:
        donor, food, category, meals, location = self._validate_common(donor, food, category, meals, location)
        now = self._now()
        if not isinstance(expires_at, datetime):
            raise ValidationError("Expiry must be a valid date and time.")
        expiry = self._stamp(expires_at)
        if expires_at.astimezone(timezone.utc) <= now:
            raise ValidationError("Expiry date and time must be in the future.")
        key = idempotency_key or uuid.uuid4().hex
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute("SELECT id FROM donations WHERE idempotency_key = ?", (key,)).fetchone()
                if existing:
                    connection.commit()
                    return self.get_food(existing["id"])
                cursor = connection.execute(
                    """INSERT INTO donations
                    (donor, food, category, meals, location, expires_at, description, pickup_instructions, created_at, idempotency_key)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (donor, food, category, meals, location, expiry, description.strip(), pickup_instructions.strip(), self._stamp(now), key),
                )
                donation_id = cursor.lastrowid
                self._log(connection, f"{donor} listed {meals} meals of {food}", now)
                connection.commit()
                return self.get_food(donation_id)
        except sqlite3.DatabaseError as exc:
            raise StorageError(f"Could not save donation: {exc}") from exc

    def add_request(
        self,
        organization: str,
        category: str,
        meals: int,
        location: str,
        idempotency_key: Optional[str] = None,
    ) -> FoodRequest:
        organization, _, category, meals, location = self._validate_common(
            organization, "Food request", category, meals, location
        )
        now = self._now()
        key = idempotency_key or uuid.uuid4().hex
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute("SELECT id FROM requests WHERE idempotency_key = ?", (key,)).fetchone()
                if existing:
                    connection.commit()
                    return self.get_request(existing["id"])
                cursor = connection.execute(
                    """INSERT INTO requests
                    (organization, category, meals, location, created_at, status, idempotency_key)
                    VALUES (?, ?, ?, ?, ?, 'Submitted', ?)""",
                    (organization, category, meals, location, self._stamp(now), key),
                )
                request_id = cursor.lastrowid
                self._log(connection, f"{organization} requested {meals} {category} meals in {location}", now)
                donation = self._find_match(connection, category, meals, location, now)
                if donation is None:
                    reason = f"No unexpired {category} donation with at least {meals} meals is available in {location}."
                    connection.execute(
                        "UPDATE requests SET status = 'Rejected', rejection_reason = ? WHERE id = ?",
                        (reason, request_id),
                    )
                    self._log(connection, f"{organization}'s request was rejected: {reason}", now)
                else:
                    connection.execute(
                        "INSERT INTO allocations(donation_id, request_id, meals, status, created_at) VALUES (?, ?, ?, 'Reserved', ?)",
                        (donation["id"], request_id, meals, self._stamp(now)),
                    )
                    connection.execute("UPDATE requests SET status = 'Matched' WHERE id = ?", (request_id,))
                    self._log(connection, f"{organization} matched with {donation['donor']} ({meals} meals)", now)
                connection.commit()
                return self.get_request(request_id)
        except sqlite3.DatabaseError as exc:
            raise StorageError(f"Could not save food request: {exc}") from exc

    @staticmethod
    def _hash_password(password: str, salt: str) -> str:
        if not isinstance(password, str):
            raise ValidationError("Password must be text.")
        if not password:
            raise ValidationError("Password is required.")
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000).hex()

    @staticmethod
    def _validate_user_details(name: str, email: str, password: str) -> tuple[str, str, str]:
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("Full name is required.")
        if not isinstance(email, str) or not email.strip():
            raise ValidationError("Email address is required.")
        cleaned_email = email.strip().casefold()
        if "@" not in cleaned_email or "." not in cleaned_email.split("@")[-1]:
            raise ValidationError("Enter a valid email address.")
        if not isinstance(password, str) or len(password) < 8:
            raise ValidationError("Password must be at least 8 characters long.")
        if not any(char.isalpha() for char in password) or not any(char.isdigit() for char in password):
            raise ValidationError("Password must include letters and numbers.")
        return name.strip(), cleaned_email, password

    def register_user(self, email: str, password: str, name: str) -> AppUser:
        cleaned_name, cleaned_email, password = self._validate_user_details(name, email, password)
        salt = secrets.token_hex(16)
        password_hash = self._hash_password(password, salt)
        now = self._stamp(self._now())
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute("SELECT id FROM users WHERE email = ?", (cleaned_email,)).fetchone()
                if existing is not None:
                    raise ValidationError("An account with that email already exists.")
                cursor = connection.execute(
                    "INSERT INTO users (name, email, password_hash, salt, created_at) VALUES (?, ?, ?, ?, ?)",
                    (cleaned_name, cleaned_email, password_hash, salt, now),
                )
                user_id = cursor.lastrowid
                connection.commit()
                return self.get_user_by_email(cleaned_email)
        except sqlite3.DatabaseError as exc:
            raise StorageError(f"Could not register user: {exc}") from exc

    def authenticate_user(self, email: str, password: str) -> AppUser:
        if not isinstance(email, str) or not email.strip():
            raise ValidationError("Email address is required.")
        cleaned_email = email.strip().casefold()
        if not isinstance(password, str) or not password:
            raise ValidationError("Password is required.")
        try:
            with self._connect() as connection:
                row = connection.execute("SELECT * FROM users WHERE email = ?", (cleaned_email,)).fetchone()
                if row is None:
                    raise ValidationError("No account was found for that email.")
                expected_hash = self._hash_password(password, row["salt"])
                if not hmac.compare_digest(expected_hash, row["password_hash"]):
                    raise ValidationError("Invalid email or password.")
                return self._user_from_row(row)
        except sqlite3.DatabaseError as exc:
            raise StorageError(f"Could not authenticate user: {exc}") from exc

    @staticmethod
    def _user_from_row(row: sqlite3.Row) -> AppUser:
        return AppUser(
            id=row["id"],
            name=row["name"],
            email=row["email"],
            created_at=RedistributionSystem._parse_stamp(row["created_at"]),
        )

    def get_user_by_email(self, email: str) -> AppUser:
        cleaned_email = email.strip().casefold()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, name, email, created_at FROM users WHERE email = ?", (cleaned_email,)
            ).fetchone()
            if row is None:
                raise ValidationError(f"No account was found for {cleaned_email}.")
            return self._user_from_row(row)

    def _find_match(
        self, connection: sqlite3.Connection, category: str, meals: int, location: str, now: datetime
    ) -> Optional[sqlite3.Row]:
        rows = connection.execute(
            """SELECT d.*, d.meals - COALESCE(SUM(CASE WHEN a.status IN ('Reserved','Confirmed','Completed') THEN a.meals ELSE 0 END), 0) AS remaining
            FROM donations d LEFT JOIN allocations a ON a.donation_id = d.id
            WHERE d.category = ? AND d.expires_at > ?
            GROUP BY d.id
            HAVING remaining >= ?
            ORDER BY d.expires_at ASC, d.created_at ASC, d.id ASC""",
            (category, self._stamp(now), meals),
        ).fetchall()
        normalized_location = location.strip().casefold()
        return next((row for row in rows if row["location"].strip().casefold() == normalized_location), None)

    def find_match(self, request: FoodRequest) -> Optional[FoodListing]:
        try:
            with self._connect() as connection:
                row = self._find_match(connection, request.category, request.meals, request.location, self._now())
                return self._food_from_row(row) if row else None
        except sqlite3.DatabaseError as exc:
            raise StorageError(f"Could not search donations: {exc}") from exc

    def eligible_food(self, category: str, meals: int, location: str) -> list[FoodListing]:
        if meals <= 0:
            return []
        try:
            with self._connect() as connection:
                now = self._now()
                rows = connection.execute(
                    """SELECT d.*, d.meals - COALESCE(SUM(CASE WHEN a.status IN ('Reserved','Confirmed','Completed') THEN a.meals ELSE 0 END), 0) AS remaining
                    FROM donations d LEFT JOIN allocations a ON a.donation_id = d.id
                    WHERE d.category = ? AND d.expires_at > ?
                    GROUP BY d.id HAVING remaining >= ?
                    ORDER BY d.expires_at ASC, d.created_at ASC, d.id ASC""",
                    (category.strip().casefold(), self._stamp(now), meals),
                ).fetchall()
                normalized_location = location.strip().casefold()
                return [self._food_from_row(row) for row in rows if row["location"].strip().casefold() == normalized_location]
        except sqlite3.DatabaseError as exc:
            raise StorageError(f"Could not search donations: {exc}") from exc

    def confirm_request(self, request_id: int) -> FoodRequest:
        return self._transition_request(request_id, "Matched", "Confirmed", "Confirmed")

    def complete_request(self, request_id: int) -> FoodRequest:
        return self._transition_request(request_id, "Confirmed", "Completed", "Completed")

    def cancel_request(self, request_id: int) -> FoodRequest:
        now = self._now()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                request = connection.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
                if request is None:
                    raise InvalidTransition("Food request does not exist.")
                if request["status"] not in ("Matched", "Confirmed", "Submitted"):
                    raise InvalidTransition(f"A {request['status'].lower()} request cannot be cancelled.")
                connection.execute(
                    "UPDATE allocations SET status = 'Released' WHERE request_id = ? AND status IN ('Reserved','Confirmed')",
                    (request_id,),
                )
                connection.execute("UPDATE requests SET status = 'Cancelled' WHERE id = ?", (request_id,))
                self._log(connection, f"{request['organization']}'s request was cancelled and its reservation released", now)
                connection.commit()
                return self.get_request(request_id)
        except sqlite3.DatabaseError as exc:
            raise StorageError(f"Could not cancel request: {exc}") from exc

    def _transition_request(self, request_id: int, source: str, target: str, allocation_status: str) -> FoodRequest:
        now = self._now()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                request = connection.execute("SELECT * FROM requests WHERE id = ?", (request_id,)).fetchone()
                if request is None or request["status"] != source:
                    actual = request["status"] if request else "missing"
                    raise InvalidTransition(f"Cannot change request from {actual} to {target}.")
                allocation = connection.execute(
                    "SELECT id FROM allocations WHERE request_id = ? AND status = ?",
                    (request_id, "Reserved" if source == "Matched" else "Confirmed"),
                ).fetchone()
                if allocation is None:
                    raise InvalidTransition("This request has no active reservation for that transition.")
                connection.execute("UPDATE requests SET status = ? WHERE id = ?", (target, request_id))
                connection.execute(
                    "UPDATE allocations SET status = ? WHERE request_id = ? AND status IN ('Reserved','Confirmed')",
                    (allocation_status, request_id),
                )
                self._log(connection, f"{request['organization']}'s request is now {target.lower()}", now)
                connection.commit()
                return self.get_request(request_id)
        except sqlite3.DatabaseError as exc:
            raise StorageError(f"Could not update request: {exc}") from exc

    def _log(self, connection: sqlite3.Connection, message: str, now: datetime) -> None:
        connection.execute("INSERT INTO history(message, created_at) VALUES (?, ?)", (message, self._stamp(now)))

    def _food_from_row(self, row: sqlite3.Row) -> FoodListing:
        total = row["meals"]
        allocated = row["meals"] - row["remaining"] if "remaining" in row.keys() else 0
        expiry = RedistributionSystem._parse_stamp(row["expires_at"])
        now = self._now()
        status = "Expired" if expiry <= now else "Fully Allocated" if total - allocated == 0 else "Partially Allocated" if allocated else "Available"
        return FoodListing(
            id=row["id"], donor=row["donor"], food=row["food"], category=row["category"], meals=total,
            location=row["location"], expires_at=expiry, description=row["description"],
            pickup_instructions=row["pickup_instructions"], created_at=RedistributionSystem._parse_stamp(row["created_at"]),
            allocated_meals=allocated, status=status,
        )

    @staticmethod
    def _request_from_row(row: sqlite3.Row) -> FoodRequest:
        return FoodRequest(
            id=row["id"], organization=row["organization"], category=row["category"], meals=row["meals"],
            location=row["location"], created_at=RedistributionSystem._parse_stamp(row["created_at"]),
            status=row["status"], allocation_id=row["allocation_id"], donation_id=row["donation_id"],
            donor=row["donor"], food=row["food"], pickup_instructions=row["pickup_instructions"] or "",
            rejection_reason=row["rejection_reason"],
        )

    def get_food(self, donation_id: int) -> FoodListing:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT d.*, d.meals - COALESCE(SUM(CASE WHEN a.status IN ('Reserved','Confirmed','Completed') THEN a.meals ELSE 0 END), 0) AS remaining
                FROM donations d LEFT JOIN allocations a ON a.donation_id = d.id WHERE d.id = ? GROUP BY d.id""",
                (donation_id,),
            ).fetchone()
        if row is None:
            raise StorageError(f"Donation {donation_id} was not found.")
        return self._food_from_row(row)

    def get_request(self, request_id: int) -> FoodRequest:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT r.*, a.id AS allocation_id, a.donation_id, d.donor, d.food, d.pickup_instructions
                FROM requests r LEFT JOIN allocations a ON a.request_id = r.id LEFT JOIN donations d ON d.id = a.donation_id
                WHERE r.id = ?""",
                (request_id,),
            ).fetchone()
        if row is None:
            raise StorageError(f"Food request {request_id} was not found.")
        return self._request_from_row(row)

    @property
    def available_food(self) -> list[FoodListing]:
        try:
            with self._connect() as connection:
                now = self._stamp(self._now())
                rows = connection.execute(
                    """SELECT d.*, d.meals - COALESCE(SUM(CASE WHEN a.status IN ('Reserved','Confirmed','Completed') THEN a.meals ELSE 0 END), 0) AS remaining
                    FROM donations d LEFT JOIN allocations a ON a.donation_id = d.id
                    WHERE d.expires_at > ? GROUP BY d.id HAVING remaining > 0
                    ORDER BY d.expires_at ASC, d.created_at ASC, d.id ASC""",
                    (now,),
                ).fetchall()
                return [self._food_from_row(row) for row in rows]
        except sqlite3.DatabaseError as exc:
            raise StorageError(f"Could not load donations: {exc}") from exc

    @property
    def all_food(self) -> list[FoodListing]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """SELECT d.*, d.meals - COALESCE(SUM(CASE WHEN a.status IN ('Reserved','Confirmed','Completed') THEN a.meals ELSE 0 END), 0) AS remaining
                    FROM donations d LEFT JOIN allocations a ON a.donation_id = d.id
                    GROUP BY d.id ORDER BY d.created_at DESC, d.id DESC"""
                ).fetchall()
                return [self._food_from_row(row) for row in rows]
        except sqlite3.DatabaseError as exc:
            raise StorageError(f"Could not load donor listings: {exc}") from exc

    @property
    def pending_requests(self) -> list[FoodRequest]:
        return self._requests_by_status(("Submitted",))

    @property
    def rejected_requests(self) -> list[FoodRequest]:
        return self._requests_by_status(("Rejected",))

    @property
    def requests(self) -> list[FoodRequest]:
        return self._requests_by_status(("Submitted", "Matched", "Confirmed", "Completed", "Cancelled", "Rejected"))

    def _requests_by_status(self, statuses: tuple[str, ...]) -> list[FoodRequest]:
        placeholders = ",".join("?" for _ in statuses)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    f"""SELECT r.*, a.id AS allocation_id, a.donation_id, d.donor, d.food, d.pickup_instructions
                    FROM requests r LEFT JOIN allocations a ON a.request_id = r.id LEFT JOIN donations d ON d.id = a.donation_id
                    WHERE r.status IN ({placeholders}) ORDER BY r.created_at DESC, r.id DESC""",
                    statuses,
                ).fetchall()
                return [self._request_from_row(row) for row in rows]
        except sqlite3.DatabaseError as exc:
            raise StorageError(f"Could not load requests: {exc}") from exc

    @property
    def history(self) -> list[str]:
        try:
            with self._connect() as connection:
                return [row["message"] for row in connection.execute("SELECT message FROM history ORDER BY id")]
        except sqlite3.DatabaseError as exc:
            raise StorageError(f"Could not load activity history: {exc}") from exc

    def undo_last_operation(self) -> Optional[str]:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT id, message FROM history ORDER BY id DESC LIMIT 1").fetchone()
                if row is None:
                    connection.commit()
                    return None
                connection.execute("DELETE FROM history WHERE id = ?", (row["id"],))
                connection.commit()
                return row["message"]
        except sqlite3.DatabaseError as exc:
            raise StorageError(f"Could not update activity history: {exc}") from exc

    def is_empty(self) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT NOT EXISTS(SELECT 1 FROM donations) AND NOT EXISTS(SELECT 1 FROM requests)").fetchone()[0] == 1
