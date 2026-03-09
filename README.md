# Social Media Platform (Connectify)

Full-stack Django social media project with:
- Authentication (login/register/logout)
- Home feed, likes, comments, saves, follows
- Stories, reels, post creation
- Search and notifications
- Chat/messages with media support
- Profile, activity, saved posts, help pages

## Tech Stack
- Python 3.10+
- Django 5.x
- SQLite (default)
- HTML/CSS/Bootstrap + JavaScript

## Project Structure
- `Hamzify/` Django project root
- `Hamzify/manage.py` Django entrypoint
- `Hamzify/social/` main app logic
- `Hamzify/templates/` frontend templates
- `Hamzify/static/` static assets

## Local Setup
1. Open terminal in `Hamzify_Project`.
2. Create and activate virtual environment:
   - Windows PowerShell:
     ```powershell
     python -m venv venv
     .\venv\Scripts\Activate.ps1
     ```
3. Install dependencies:
   ```powershell
   pip install -r Hamzify/requirements.txt
   ```
4. Run migrations:
   ```powershell
   cd Hamzify
   python manage.py migrate
   ```
5. Start server:
   ```powershell
   python manage.py runserver
   ```
6. Open:
   - `http://127.0.0.1:8000/`

## Useful Commands
- Django checks:
  ```powershell
  python manage.py check
  ```
- Create superuser:
  ```powershell
  python manage.py createsuperuser
  ```

## Notes
- `db.sqlite3`, `media/`, and `venv/` are excluded from git.
- If UI changes do not appear, do hard refresh: `Ctrl + F5`.
