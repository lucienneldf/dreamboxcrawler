import sqlite3
import os


class DedupManager:
    def __init__(self, db_path="data/collected.db"):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS collected_feeds (
                feed_id TEXT PRIMARY KEY,
                school_sid TEXT,
                collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_school_sid
            ON collected_feeds(school_sid)
        """)
        self.conn.commit()

    def is_collected(self, feed_id):
        row = self.conn.execute(
            "SELECT 1 FROM collected_feeds WHERE feed_id = ?", (str(feed_id),)
        ).fetchone()
        return row is not None

    def get_collected_time(self, feed_id):
        row = self.conn.execute(
            "SELECT collected_at FROM collected_feeds WHERE feed_id = ?", (str(feed_id),)
        ).fetchone()
        return row[0] if row else None

    def filter_new(self, feeds):
        return [f for f in feeds if not self.is_collected(f["feed_id"])]

    def mark_collected(self, feed_ids):
        self.conn.executemany(
            "INSERT OR IGNORE INTO collected_feeds (feed_id, school_sid) VALUES (?, ?)",
            [(str(fid["feed_id"]), str(fid["school_sid"])) for fid in feed_ids],
        )
        self.conn.commit()

    def get_stats(self):
        total = self.conn.execute("SELECT COUNT(*) FROM collected_feeds").fetchone()[0]
        today = self.conn.execute(
            "SELECT COUNT(*) FROM collected_feeds WHERE date(collected_at) = date('now')"
        ).fetchone()[0]
        schools = self.conn.execute(
            "SELECT COUNT(DISTINCT school_sid) FROM collected_feeds"
        ).fetchone()[0]
        return {
            "total_collected": total,
            "today_collected": today,
            "school_count": schools,
            "high_value": 0,
        }

    def get_count(self):
        """获取去重记录总数"""
        return self.conn.execute("SELECT COUNT(*) FROM collected_feeds").fetchone()[0]

    def reset(self):
        self.conn.execute("DELETE FROM collected_feeds")
        self.conn.commit()

    def close(self):
        self.conn.close()
