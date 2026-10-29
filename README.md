# FoodShare

FoodShare is a local food-rescue app for listing surplus meals and matching eligible donations with nearby organizations. It includes account registration and login, a donor/request workflow, SQLite persistence, and request status tracking.

## Run locally

```powershell
& ".venv-1\Scripts\python.exe" -m pip install -r requirements.txt
& ".venv-1\Scripts\python.exe" -m streamlit run app.py
```

Open the local URL printed by Streamlit, create an account, and then use the dashboard. If you are not using the included `.venv-1` environment, replace its Python path with the Python executable for your environment. Run the automated tests with:

```powershell
& ".venv-1\Scripts\python.exe" -m unittest -v
```

The app stores user accounts, donations, requests, allocations, and activity in `foodshare.db` beside `app.py`. SQLite preserves these records across reruns and restarts. Keep this file to preserve data and back it up before moving or cleaning the project.

New accounts require a valid email address and a password of at least eight characters containing letters and numbers. Passwords are stored as salted PBKDF2 hashes, not plaintext.

## Workflow

1. On **Donor Page**, enter donor, food, category, positive meal quantity, neighborhood, local expiry date/time, details, and pickup instructions.
2. On **Find Food**, browse available donor listings and submit an organization request.
3. A request reserves food only when category, unallocated quantity, unexpired time, and normalized neighborhood all match. Eligible donations are selected by earliest expiry, then creation time and ID.
4. A matched request can be confirmed, cancelled (which releases its reservation), and marked collected after pickup. Unmatched requests are rejected immediately and are not retried when later donations are added.
5. **View Matches** shows persisted request and collection statuses.

## Prototype limitations

Login is implemented, but records are still shared between accounts and there is no role-specific authorization, email verification, password reset, or notification system. Treat this as a local demonstration, not a production service. A public deployment needs per-user/organization data access rules, secure session and deployment configuration, shared production storage, and operational backups. Expiry date/time input uses the computer's local timezone and is stored in UTC.
