"""DB 백업: `python -m scripts.backup_db` → DB 옆 backups/ 폴더에 사본을 만든다."""
from app.services.backup import make_backup

if __name__ == "__main__":
    print(make_backup("manual"))
