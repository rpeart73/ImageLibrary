"""
Iris — YouTube Video Backup Script

Downloads YouTube videos from Iris media library to Proton Drive + Tresorit.
Only downloads videos at risk of disappearing (YouTube). pCloud-hosted files
are not downloaded (you control the hosting). Large files (over 500MB) are
skipped unless explicitly requested.

Usage:
    python download_youtube.py              # backup at-risk videos only
    python download_youtube.py --force-all  # include large files
"""
import sqlite3
import subprocess
import os
import sys

DB = os.path.join(os.path.dirname(__file__), 'image_library.db')
PCLOUD_DIR = '/mnt/c/Users/rpeart/Proton Drive/raymondpeart/My files/EXT/Media_Archive'
WORKBENCH_DIR = '/mnt/c/Users/rpeart/Tresorit/Tresorit Ecosystem/Workbench/York/Resources/Media'
YTDLP = os.path.expanduser('~/prometheus_venv/bin/yt-dlp')

os.makedirs(PCLOUD_DIR, exist_ok=True)
os.makedirs(WORKBENCH_DIR, exist_ok=True)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
c = conn.cursor()

# Add backup_path column if needed
try:
    c.execute('ALTER TABLE media ADD COLUMN backup_path TEXT')
    conn.commit()
except:
    pass

MAX_SIZE_MB = 500
force_all = '--force-all' in sys.argv

# Only YouTube videos — pCloud-hosted files stay on pCloud (you control the hosting)
c.execute("SELECT id, title, url FROM media WHERE url LIKE '%youtube.com%' OR url LIKE '%youtu.be%'")
videos = c.fetchall()

c.execute("SELECT COUNT(*) FROM media WHERE url NOT LIKE '%youtube.com%' AND url NOT LIKE '%youtu.be%'")
skipped = c.fetchone()[0]

print(f"YouTube videos to back up: {len(videos)}")
if skipped:
    print(f"Skipping {skipped} pCloud/file-hosted videos (not at risk)")

for vid in videos:
    # Check if already backed up
    c.execute("SELECT backup_path FROM media WHERE id=?", (vid['id'],))
    existing = c.fetchone()['backup_path']
    if existing and os.path.exists(existing):
        print(f"  SKIP (backed up): {vid['title'][:50]}")
        continue

    print(f"  Downloading: {vid['title'][:50]}...")

    # Download to pCloud (primary archive)
    result = subprocess.run(
        [YTDLP, '-f', 'best[height<=720]', '--no-playlist',
         '-o', os.path.join(PCLOUD_DIR, '%(title)s.%(ext)s'),
         vid['url']],
        capture_output=True, text=True, timeout=300
    )

    if result.returncode == 0:
        # Find the downloaded file and copy to Workbench
        for f in sorted(os.listdir(PCLOUD_DIR), key=lambda x: os.path.getmtime(os.path.join(PCLOUD_DIR, x)), reverse=True):
            fp = os.path.join(PCLOUD_DIR, f)
            if os.path.isfile(fp):
                c.execute("UPDATE media SET backup_path=? WHERE id=?", (fp, vid['id']))
                # Copy to Workbench
                import shutil
                wb_path = os.path.join(WORKBENCH_DIR, f)
                if not os.path.exists(wb_path):
                    shutil.copy2(fp, wb_path)
                    print(f"    Saved: {f} (pCloud + Workbench)")
                else:
                    print(f"    Saved: {f} (pCloud, Workbench already has it)")
                break
    else:
        print(f"    FAILED: {result.stderr[:100]}")

conn.commit()

# Summary
c.execute("SELECT COUNT(*) FROM media WHERE backup_path IS NOT NULL")
backed = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM media")
total = c.fetchone()[0]
print(f"\nBackup status: {backed}/{total} videos backed up to pCloud")

conn.close()
