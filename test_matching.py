import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from matching import (
    InvalidTransition,
    RedistributionSystem,
    StorageError,
    ValidationError,
)


class MatchingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "foodshare.db"
        self.now = datetime(2030, 5, 1, 12, 0, tzinfo=timezone.utc)
        self.system = self.make_system()

    def tearDown(self):
        self.temp_dir.cleanup()

    def make_system(self):
        return RedistributionSystem(self.database, clock=lambda: self.now)

    def add_food(self, meals=20, category="prepared", location="Central", expires=3, **kwargs):
        return self.system.add_food(
            "Canteen A", "Lunch bowls", category, meals, location,
            self.now + timedelta(hours=expires), **kwargs
        )

    def add_request(self, meals=10, category="prepared", location="Central", **kwargs):
        return self.system.add_request("Organization B", category, meals, location, **kwargs)

    def set_stored_expiry(self, listing_id, expiry):
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "UPDATE donations SET expires_at = ? WHERE id = ?",
                (expiry.isoformat(), listing_id),
            )
            connection.commit()
        finally:
            connection.close()

    def test_matching_category_quantity_expiry_and_location_reserves_requested_amount(self):
        listing = self.add_food(meals=20)
        request = self.add_request(meals=12)
        persisted_listing = self.system.get_food(listing.id)

        self.assertEqual(request.status, "Matched")
        self.assertEqual(listing.meals, 20)
        self.assertEqual(persisted_listing.allocated_meals, 12)
        self.assertEqual(persisted_listing.available_meals, 8)
        self.assertEqual(request.donation_id, listing.id)

    def test_different_category_is_rejected(self):
        self.add_food(category="produce")
        request = self.add_request(category="prepared")
        self.assertEqual(request.status, "Rejected")
        self.assertIsNone(request.donation_id)

    def test_insufficient_quantity_is_rejected(self):
        self.add_food(meals=9)
        request = self.add_request(meals=10)
        self.assertEqual(request.status, "Rejected")
        self.assertEqual(self.system.available_food[0].available_meals, 9)

    def test_expired_donation_is_not_matched(self):
        listing = self.add_food(expires=1)
        self.set_stored_expiry(listing.id, self.now - timedelta(seconds=1))
        request = self.add_request()
        self.assertEqual(request.status, "Rejected")
        self.assertEqual(self.system.available_food, [])

    def test_expiry_equal_to_now_is_not_eligible(self):
        listing = self.add_food(expires=1)
        self.set_stored_expiry(listing.id, self.now)
        request = self.add_request()
        self.assertEqual(request.status, "Rejected")

    def test_different_neighborhood_is_rejected(self):
        self.add_food(location="Pune")
        request = self.add_request(location="Mumbai")
        self.assertEqual(request.status, "Rejected")

    def test_neighborhood_matching_ignores_capitalization(self):
        self.add_food(location="Pune")
        request = self.add_request(location="pUnE")
        self.assertEqual(request.status, "Matched")

    def test_neighborhood_matching_ignores_outer_spaces(self):
        self.add_food(location="  Pune  ")
        request = self.add_request(location="Pune ")
        self.assertEqual(request.status, "Matched")

    def test_neighborhood_matching_uses_unicode_casefold(self):
        self.add_food(location="Straße")
        request = self.add_request(location="STRASSE")
        self.assertEqual(request.status, "Matched")

    def test_earliest_expiring_listing_wins_with_deterministic_tie_break(self):
        later = self.add_food(meals=20, expires=5)
        earlier = self.add_food(meals=20, expires=1)
        tie = self.add_food(meals=20, expires=1)
        request = self.add_request(meals=15)
        self.assertEqual(request.donation_id, earlier.id)
        self.assertEqual(self.system.get_food(tie.id).available_meals, 20)
        self.assertEqual(self.system.get_food(later.id).available_meals, 20)

    def test_fully_allocated_donation_is_not_available(self):
        listing = self.add_food(meals=10)
        self.add_request(meals=10)
        self.assertEqual(self.system.get_food(listing.id).status, "Fully Allocated")
        self.assertNotIn(listing.id, [food.id for food in self.system.available_food])

    def test_partially_allocated_donation_remains_available(self):
        listing = self.add_food(meals=10)
        self.add_request(meals=4)
        result = self.system.get_food(listing.id)
        self.assertEqual(result.status, "Partially Allocated")
        self.assertEqual(result.available_meals, 6)
        self.assertIn(listing.id, [food.id for food in self.system.available_food])

    def test_competing_requests_cannot_overallocate(self):
        listing = self.add_food(meals=10)
        first = self.add_request(meals=7)
        second = self.add_request(meals=7)
        self.assertEqual(first.status, "Matched")
        self.assertEqual(second.status, "Rejected")
        self.assertEqual(self.system.get_food(listing.id).allocated_meals, 7)
        self.assertGreaterEqual(self.system.get_food(listing.id).available_meals, 0)

    def test_invalid_quantities_and_missing_required_fields_are_rejected(self):
        for quantity in (0, -1, 1.5, True):
            with self.subTest(quantity=quantity), self.assertRaises(ValidationError):
                self.add_food(meals=quantity)
            with self.subTest(request_quantity=quantity), self.assertRaises(ValidationError):
                self.add_request(meals=quantity)
        with self.assertRaises(ValidationError):
            self.system.add_food(" ", "Soup", "prepared", 3, "Central", self.now + timedelta(hours=1))
        with self.assertRaises(ValidationError):
            self.system.add_food("Donor", "Soup", "prepared", 3, " ", self.now + timedelta(hours=1))
        with self.assertRaises(ValidationError):
            self.system.add_request(" ", "prepared", 3, "Central")
        with self.assertRaises(ValidationError):
            self.system.add_request("NGO", "prepared", 3, " ")

    def test_expiry_must_be_aware_and_in_future(self):
        with self.assertRaises(ValidationError):
            self.system.add_food("Donor", "Soup", "prepared", 3, "Central", self.now.replace(tzinfo=None))
        with self.assertRaises(ValidationError):
            self.system.add_food("Donor", "Soup", "prepared", 3, "Central", self.now - timedelta(seconds=1))

    def test_idempotency_keys_prevent_duplicate_donations_and_requests(self):
        donation_a = self.add_food(idempotency_key="donation-click-1")
        donation_b = self.add_food(idempotency_key="donation-click-1")
        self.assertEqual(donation_a.id, donation_b.id)
        self.assertEqual(len(self.system.all_food), 1)

        request_a = self.add_request(idempotency_key="request-click-1")
        request_b = self.add_request(idempotency_key="request-click-1")
        self.assertEqual(request_a.id, request_b.id)
        self.assertEqual(len(self.system.requests), 1)
        self.assertEqual(self.system.get_food(donation_a.id).allocated_meals, 10)

    def test_database_survives_service_restart_without_duplicate_records(self):
        donation = self.add_food(idempotency_key="stable-donation")
        request = self.add_request(idempotency_key="stable-request")
        restarted = self.make_system()
        repeated_donation = restarted.add_food(
            "Canteen A", "Lunch bowls", "prepared", 20, "Central",
            self.now + timedelta(hours=3), idempotency_key="stable-donation"
        )
        repeated_request = restarted.add_request(
            "Organization B", "prepared", 10, "Central", idempotency_key="stable-request"
        )
        self.assertEqual(repeated_donation.id, donation.id)
        self.assertEqual(repeated_request.id, request.id)
        self.assertEqual(len(restarted.all_food), 1)
        self.assertEqual(len(restarted.requests), 1)
        self.assertEqual(restarted.get_request(request.id).status, "Matched")

    def test_confirm_complete_lifecycle_and_repeated_transition_rejected(self):
        self.add_food()
        request = self.add_request()
        with self.assertRaises(InvalidTransition):
            self.system.complete_request(request.id)
        confirmed = self.system.confirm_request(request.id)
        self.assertEqual(confirmed.status, "Confirmed")
        with self.assertRaises(InvalidTransition):
            self.system.confirm_request(request.id)
        completed = self.system.complete_request(request.id)
        self.assertEqual(completed.status, "Completed")
        with self.assertRaises(InvalidTransition):
            self.system.complete_request(request.id)
        with self.assertRaises(InvalidTransition):
            self.system.cancel_request(request.id)

    def test_cancellation_releases_reservation_and_cannot_be_repeated(self):
        listing = self.add_food(meals=12)
        request = self.add_request(meals=8)
        cancelled = self.system.cancel_request(request.id)
        self.assertEqual(cancelled.status, "Cancelled")
        self.assertEqual(self.system.get_food(listing.id).available_meals, 12)
        with self.assertRaises(InvalidTransition):
            self.system.cancel_request(request.id)

    def test_cancelled_request_does_not_consume_food_for_later_request(self):
        listing = self.add_food(meals=12)
        cancelled = self.add_request(meals=8)
        self.system.cancel_request(cancelled.id)
        next_request = self.add_request(meals=12)
        self.assertEqual(self.system.get_request(cancelled.id).status, "Cancelled")
        self.assertEqual(next_request.status, "Matched")
        self.assertEqual(self.system.get_food(listing.id).status, "Fully Allocated")
        self.assertEqual(self.system.get_request(cancelled.id).status, "Cancelled")

    def test_empty_board_and_no_match_are_clear(self):
        self.assertEqual(self.system.available_food, [])
        self.assertEqual(self.system.requests, [])
        self.assertEqual(self.system.eligible_food("prepared", 1, "Central"), [])
        request = self.add_request()
        self.assertEqual(request.status, "Rejected")
        self.assertEqual(request.rejection_reason and "Central" in request.rejection_reason, True)

    def test_malformed_database_file_raises_storage_error(self):
        bad_database = Path(self.temp_dir.name) / "broken.db"
        bad_database.write_bytes(b"not a sqlite database")
        with self.assertRaises(StorageError):
            RedistributionSystem(bad_database)

    def test_malformed_persisted_timestamp_raises_storage_error(self):
        listing = self.add_food()
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("UPDATE donations SET expires_at = 'not-a-date' WHERE id = ?", (listing.id,))
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(StorageError):
            _ = self.system.all_food

    def test_concurrent_requests_only_allocate_available_quantity(self):
        listing = self.add_food(meals=10)
        second_service = self.make_system()

        def request_from(service, suffix):
            return service.add_request(f"NGO {suffix}", "prepared", 7, "Central").status

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(lambda args: request_from(*args), [(self.system, "A"), (second_service, "B")]))
        self.assertCountEqual(statuses, ["Matched", "Rejected"])
        self.assertLessEqual(self.system.get_food(listing.id).allocated_meals, listing.meals)

    def test_unique_ids_and_extra_listing_fields_are_persisted(self):
        first = self.system.add_food(
            "Donor", "Soup", "prepared", 5, "West", self.now + timedelta(hours=2),
            description="Vegetarian", pickup_instructions="Use side entrance"
        )
        second = self.add_food()
        self.assertNotEqual(first.id, second.id)
        self.assertEqual(first.description, "Vegetarian")
        self.assertEqual(first.pickup_instructions, "Use side entrance")

    def test_user_registration_and_login(self):
        user = self.system.register_user("alice@example.com", "Password123!", "Alice")
        self.assertEqual(user.email, "alice@example.com")
        self.assertEqual(user.name, "Alice")

        authenticated = self.system.authenticate_user("alice@example.com", "Password123!")
        self.assertEqual(authenticated.email, "alice@example.com")
        self.assertFalse(hasattr(authenticated, "password_hash"))
        self.assertFalse(hasattr(authenticated, "salt"))

        with self.assertRaises(ValidationError):
            self.system.register_user("alice@example.com", "Password123!", "Alice")

        with self.assertRaises(ValidationError):
            self.system.authenticate_user("alice@example.com", "WrongPassword!")

        with self.assertRaises(ValidationError):
            self.system.authenticate_user("missing@example.com", "Password123!")


if __name__ == "__main__":
    unittest.main()
