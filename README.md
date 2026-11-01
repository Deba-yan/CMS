# Conference Management System – Prototype
Flask + SQLite prototype of the SRS (11 functional requirements).

## Run
    pip install -r requirements.txt
    python app.py          # open http://127.0.0.1:5000

Demo logins (password `demo123`): admin@demo.com, author@demo.com, reviewer@demo.com, reviewer2@demo.com, participant@demo.com
Test card: any 16 digits; ending in `0000` = simulated payment failure.

| SRS | Where |
|---|---|
| 1.1 / 1.2 Register / Login | /register, /login |
| 1.3 Conference creation | /conferences (admin) |
| 1.4 Paper submission | /submit (author) |
| 1.5 Paper review | /reviews (reviewer) |
| 1.6 Paper decision (+ reviewer assignment) | /paper/<id> (admin) |
| 1.7 Participant registration | /conferences -> Register |
| 1.8 Payment | /registrations -> Pay now |
| 1.9 Schedule (conflict check) | /schedule |
| 1.10 Notifications | bell icon / admin sends |
| 1.11 Reports | /reports (admin) |
