"""Migration tool for phone normalization and duplicate merging.

Normalizes phone numbers across contacts, conversations, calls,
campaign_targets, bookings, and bills to 91XXXXXXXXXX.
Merges duplicate contacts and conversations into the earliest record,
repointing any child messages and preserving all non-empty fields.

Usage:
    python backend/migrate_phone_normalization.py --dry-run
    python backend/migrate_phone_normalization.py --apply
"""

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("voiceflow.migration.phone")


def mask_phone(p: str | None) -> str:
    """Mask phone for logs and display: shows only first 2 and last 4 characters."""
    if not p:
        return ""
    if len(p) >= 6:
        return p[:2] + "*" * (len(p) - 6) + p[-4:]
    return "***"


def get_normalized_phone(raw: str) -> str:
    """Standalone normalization helper matching db.normalize_phone."""
    import unicodedata

    if not raw:
        return ""
    d = "".join(str(unicodedata.digit(ch)) for ch in raw if ch.isdigit())
    if not d:
        return raw.strip()
    if d.startswith("0091"):
        d = d[2:]
    elif d.startswith("091") and len(d) == 13:
        d = d[1:]
    elif len(d) == 11 and d.startswith("0"):
        d = d[1:]
    if len(d) == 10:
        d = "91" + d
    return d


def plan_and_migrate(db_path: str, dry_run: bool = True) -> dict:
    """Plans or applies phone normalization across the SQLite database."""
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA busy_timeout = 5000;")

    actions = {
        "contacts_updated": [],
        "contacts_merged": [],
        "conversations_updated": [],
        "conversations_merged": [],
        "messages_repointed": [],
        "calls_updated": [],
        "campaign_targets_updated": [],
        "bookings_updated": [],
        "bills_updated": [],
    }

    try:
        if not dry_run:
            conn.execute("BEGIN IMMEDIATE")

        # 1. Contacts
        contact_cols = [c[1] for c in conn.execute("PRAGMA table_info(contacts)").fetchall()]
        contacts = conn.execute(
            f"SELECT {', '.join(contact_cols)} FROM contacts ORDER BY id ASC"
        ).fetchall()
        groups: dict[str, list[sqlite3.Row]] = {}
        for c in contacts:
            norm = get_normalized_phone(c["phone"])
            if not norm or norm.lower() in ("web call", "unknown"):
                continue
            groups.setdefault(norm, []).append(c)

        for norm, rows in groups.items():
            if len(rows) == 1:
                row = rows[0]
                if row["phone"] != norm:
                    actions["contacts_updated"].append({
                        "id": row["id"],
                        "old_phone": mask_phone(row["phone"]),
                        "new_phone": mask_phone(norm),
                    })
                    if not dry_run:
                        conn.execute("UPDATE contacts SET phone = ? WHERE id = ?", (norm, row["id"]))
            else:
                # Earliest ID survives
                survivor = rows[0]
                duplicates = rows[1:]

                # Pick most recent non-empty values for attributes
                recency_key = lambda r: (r["last_seen_at"] if "last_seen_at" in contact_cols and r["last_seen_at"] else "",
                                         r["created_at"] if "created_at" in contact_cols and r["created_at"] else "",
                                         r["id"])
                by_recency = sorted(rows, key=recency_key, reverse=True)

                def pick_attr(col_name: str):
                    if col_name not in contact_cols:
                        return None
                    vals = [r[col_name] for r in by_recency if r[col_name] not in (None, "")]
                    if not vals:
                        return None
                    distinct = list(dict.fromkeys(vals))
                    if len(distinct) > 1:
                        log.warning(
                            "Conflict on contacts.%s for %s: values %s, keeping most recent: '%s'",
                            col_name, mask_phone(norm), distinct, distinct[0]
                        )
                    return distinct[0]

                update_fields = {"phone": norm}
                for cname in ("name", "email", "status"):
                    if cname in contact_cols:
                        update_fields[cname] = pick_attr(cname)

                # Protective flags: if ANY duplicate was opted out or blocked, preserve protection
                for pflag in ("dnd_opt_out", "blocked", "opt_out", "dnd"):
                    if pflag in contact_cols:
                        flag_vals = [r[pflag] for r in rows if r[pflag] is not None]
                        update_fields[pflag] = 1 if (1 in flag_vals or True in flag_vals) else 0

                # Notes: deduplicate and concatenate chronologically
                if "notes" in contact_cols:
                    notes_list = []
                    seen_notes = set()
                    for r in sorted(rows, key=lambda x: (x["created_at"] or "", x["id"])):
                        n = (r["notes"] or "").strip()
                        if n and n not in seen_notes:
                            seen_notes.add(n)
                            dt = (r["created_at"] or "")[:10]
                            notes_list.append(f"[{dt}] {n}" if dt else n)
                    update_fields["notes"] = "\n".join(notes_list) if notes_list else None

                # Tags: union set of tags
                if "tags" in contact_cols:
                    tags_set = set()
                    for r in rows:
                        raw_tags = r["tags"] or ""
                        for tag in raw_tags.split(","):
                            t = tag.strip()
                            if t:
                                tags_set.add(t)
                    update_fields["tags"] = ", ".join(sorted(tags_set)) if tags_set else None

                if "created_at" in contact_cols:
                    all_created = [r["created_at"] for r in rows if r["created_at"]]
                    update_fields["created_at"] = min(all_created) if all_created else survivor["created_at"]
                if "last_seen_at" in contact_cols:
                    all_seen = [r["last_seen_at"] for r in rows if r["last_seen_at"]]
                    update_fields["last_seen_at"] = max(all_seen) if all_seen else survivor["last_seen_at"]

                actions["contacts_merged"].append({
                    "survivor_id": survivor["id"],
                    "merged_into_phone": mask_phone(norm),
                    "duplicate_ids": [d["id"] for d in duplicates],
                    "duplicate_phones": [mask_phone(d["phone"]) for d in duplicates],
                })

                if not dry_run:
                    # 1. Delete duplicates first to satisfy UNIQUE constraint
                    for d in duplicates:
                        conn.execute("DELETE FROM contacts WHERE id = ?", (d["id"],))
                    # 2. Update survivor
                    set_clause = ", ".join(f"{k} = ?" for k in update_fields)
                    conn.execute(
                        f"UPDATE contacts SET {set_clause} WHERE id = ?",
                        [*update_fields.values(), survivor["id"]],
                    )

        # 2. Conversations & child Messages
        conv_cols = [c[1] for c in conn.execute("PRAGMA table_info(conversations)").fetchall()]
        convs = conn.execute(
            f"SELECT {', '.join(conv_cols)} FROM conversations ORDER BY id ASC"
        ).fetchall()
        conv_groups: dict[str, list[sqlite3.Row]] = {}
        for c in convs:
            norm = get_normalized_phone(c["phone"])
            if not norm or norm.lower() in ("web call", "unknown"):
                continue
            conv_groups.setdefault(norm, []).append(c)

        for norm, rows in conv_groups.items():
            if len(rows) == 1:
                row = rows[0]
                if row["phone"] != norm:
                    actions["conversations_updated"].append({
                        "id": row["id"],
                        "old_phone": mask_phone(row["phone"]),
                        "new_phone": mask_phone(norm),
                    })
                    if not dry_run:
                        conn.execute("UPDATE conversations SET phone = ? WHERE id = ?", (norm, row["id"]))
            else:
                survivor = rows[0]
                duplicates = rows[1:]

                rec_key_conv = lambda r: (r["updated_at"] if "updated_at" in conv_cols and r["updated_at"] else "", r["id"])
                by_recency = sorted(rows, key=rec_key_conv, reverse=True)

                def pick_conv_attr(col_name: str):
                    if col_name not in conv_cols:
                        return None
                    vals = [r[col_name] for r in by_recency if r[col_name] not in (None, "")]
                    if not vals:
                        return None
                    distinct = list(dict.fromkeys(vals))
                    if len(distinct) > 1:
                        log.warning(
                            "Conflict on conversations.%s for %s: values %s, keeping most recent: '%s'",
                            col_name, mask_phone(norm), distinct, distinct[0]
                        )
                    return distinct[0]

                update_conv_fields = {"phone": norm}
                if "name" in conv_cols:
                    update_conv_fields["name"] = pick_conv_attr("name")
                if "language" in conv_cols:
                    update_conv_fields["language"] = pick_conv_attr("language") or "Hindi"
                if "auto_reply" in conv_cols:
                    ar_vals = [r["auto_reply"] for r in rows if r["auto_reply"] is not None]
                    update_conv_fields["auto_reply"] = 0 if 0 in ar_vals else 1
                if "agent_id" in conv_cols:
                    update_conv_fields["agent_id"] = pick_conv_attr("agent_id")
                if "assigned" in conv_cols:
                    update_conv_fields["assigned"] = pick_conv_attr("assigned")
                if "goal" in conv_cols:
                    update_conv_fields["goal"] = pick_conv_attr("goal")
                if "updated_at" in conv_cols:
                    all_updated = [r["updated_at"] for r in rows if r["updated_at"]]
                    update_conv_fields["updated_at"] = max(all_updated) if all_updated else survivor["updated_at"]

                for d in duplicates:
                    msg_count = conn.execute(
                        "SELECT COUNT(*) FROM messages WHERE conversation_id = ?", (d["id"],)
                    ).fetchone()[0]
                    if msg_count:
                        actions["messages_repointed"].append({
                            "from_conv_id": d["id"],
                            "to_conv_id": survivor["id"],
                            "count": msg_count,
                        })
                        if not dry_run:
                            conn.execute(
                                "UPDATE messages SET conversation_id = ? WHERE conversation_id = ?",
                                (survivor["id"], d["id"]),
                            )
                    if not dry_run:
                        conn.execute("DELETE FROM conversations WHERE id = ?", (d["id"],))

                actions["conversations_merged"].append({
                    "survivor_id": survivor["id"],
                    "merged_into_phone": mask_phone(norm),
                    "duplicate_ids": [d["id"] for d in duplicates],
                    "duplicate_phones": [mask_phone(d["phone"]) for d in duplicates],
                })
                if not dry_run:
                    set_clause_conv = ", ".join(f"{k} = ?" for k in update_conv_fields)
                    conn.execute(
                        f"UPDATE conversations SET {set_clause_conv} WHERE id = ?",
                        [*update_conv_fields.values(), survivor["id"]],
                    )

        # 3. Calls
        calls = conn.execute("SELECT id, phone FROM calls WHERE phone IS NOT NULL").fetchall()
        for call in calls:
            norm = get_normalized_phone(call["phone"])
            if norm and norm.lower() not in ("web call", "unknown") and norm != call["phone"]:
                actions["calls_updated"].append({
                    "id": call["id"],
                    "old_phone": mask_phone(call["phone"]),
                    "new_phone": mask_phone(norm),
                })
                if not dry_run:
                    conn.execute("UPDATE calls SET phone = ? WHERE id = ?", (norm, call["id"]))

        # 4. Campaign Targets
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "campaign_targets" in tables:
            targets = conn.execute("SELECT id, phone FROM campaign_targets WHERE phone IS NOT NULL").fetchall()
            for t in targets:
                norm = get_normalized_phone(t["phone"])
                if norm and norm.lower() not in ("web call", "unknown") and norm != t["phone"]:
                    actions["campaign_targets_updated"].append({
                        "id": t["id"],
                        "old_phone": mask_phone(t["phone"]),
                        "new_phone": mask_phone(norm),
                    })
                    if not dry_run:
                        conn.execute("UPDATE campaign_targets SET phone = ? WHERE id = ?", (norm, t["id"]))

        # 5. Bookings
        if "bookings" in tables:
            cols = [c[1] for c in conn.execute("PRAGMA table_info(bookings)").fetchall()]
            if "phone" in cols:
                bookings = conn.execute("SELECT id, phone FROM bookings WHERE phone IS NOT NULL").fetchall()
                for b in bookings:
                    norm = get_normalized_phone(b["phone"])
                    if norm and norm.lower() not in ("web call", "unknown") and norm != b["phone"]:
                        actions["bookings_updated"].append({
                            "id": b["id"],
                            "old_phone": mask_phone(b["phone"]),
                            "new_phone": mask_phone(norm),
                        })
                        if not dry_run:
                            conn.execute("UPDATE bookings SET phone = ? WHERE id = ?", (norm, b["id"]))

        # 6. Bills
        if "bills" in tables:
            cols = [c[1] for c in conn.execute("PRAGMA table_info(bills)").fetchall()]
            if "customer_phone" in cols:
                bills = conn.execute("SELECT id, customer_phone FROM bills WHERE customer_phone IS NOT NULL").fetchall()
                for b in bills:
                    norm = get_normalized_phone(b["customer_phone"])
                    if norm and norm.lower() not in ("web call", "unknown") and norm != b["customer_phone"]:
                        actions["bills_updated"].append({
                            "id": b["id"],
                            "old_phone": mask_phone(b["customer_phone"]),
                            "new_phone": mask_phone(norm),
                        })
                        if not dry_run:
                            conn.execute("UPDATE bills SET customer_phone = ? WHERE id = ?", (norm, b["id"]))

        if not dry_run:
            # 7. Add UNIQUE indexes on normalized phone post-merge
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_phone_unique ON contacts(phone);")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_conversations_phone_unique ON conversations(phone);")

            # Check if schema_version exists to stamp version 3
            if "schema_version" in tables:
                ver_row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
                current_ver = ver_row[0] if ver_row and ver_row[0] else 0
                if current_ver < 3:
                    now = datetime.now(timezone.utc).isoformat()
                    conn.execute(
                        "INSERT INTO schema_version (version, applied_at, description) VALUES (3, ?, ?)",
                        (now, "Phone normalization and unique indexes on contacts and conversations"),
                    )

            # Foreign key integrity check before commit
            fk_violations = conn.execute("PRAGMA foreign_key_check;").fetchall()
            if fk_violations:
                conn.execute("ROLLBACK")
                raise RuntimeError(f"Foreign key violations detected before commit: {fk_violations}")

            conn.execute("COMMIT")

        return actions
    except Exception:
        if not dry_run:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
        raise
    finally:
        conn.close()


def print_summary(actions: dict, dry_run: bool):
    prefix = "[DRY-RUN]" if dry_run else "[APPLIED]"
    print(f"\n{prefix} --- Phone Normalization Summary ---")
    print(f"Contacts to update format: {len(actions['contacts_updated'])}")
    for a in actions["contacts_updated"]:
        print(f"  • Contact ID {a['id']}: {a['old_phone']} -> {a['new_phone']}")

    print(f"Contacts to merge (duplicates): {len(actions['contacts_merged'])}")
    for a in actions["contacts_merged"]:
        print(f"  • Merge duplicates {a['duplicate_ids']} ({a['duplicate_phones']}) into Survivor {a['survivor_id']} ({a['merged_into_phone']})")

    print(f"Conversations to update format: {len(actions['conversations_updated'])}")
    for a in actions["conversations_updated"]:
        print(f"  • Conversation ID {a['id']}: {a['old_phone']} -> {a['new_phone']}")

    print(f"Conversations to merge (duplicates): {len(actions['conversations_merged'])}")
    for a in actions["conversations_merged"]:
        print(f"  • Merge duplicates {a['duplicate_ids']} into Survivor {a['survivor_id']} ({a['merged_into_phone']})")

    print(f"Messages to repoint: {len(actions['messages_repointed'])}")
    for a in actions["messages_repointed"]:
        print(f"  • Repointed {a['count']} messages from Conv {a['from_conv_id']} to Conv {a['to_conv_id']}")

    print(f"Calls to update: {len(actions['calls_updated'])}")
    print(f"Campaign targets to update: {len(actions['campaign_targets_updated'])}")
    print(f"Bookings to update: {len(actions['bookings_updated'])}")
    print(f"Bills to update: {len(actions['bills_updated'])}")
    print(f"{prefix} --- End Summary ---\n")


def main():
    parser = argparse.ArgumentParser(description="Phone normalization and duplicate merge tool")
    parser.add_argument("--db", default="backend/voiceflow.db", help="Path to SQLite DB")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default is dry-run)")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Perform dry-run without writing")
    args = parser.parse_args()

    dry_run = not args.apply
    db_path = str(Path(args.db).resolve())
    print(f"Checking database: {db_path} (mode: {'DRY-RUN' if dry_run else 'APPLY'})")
    actions = plan_and_migrate(db_path, dry_run=dry_run)
    print_summary(actions, dry_run=dry_run)


if __name__ == "__main__":
    main()
