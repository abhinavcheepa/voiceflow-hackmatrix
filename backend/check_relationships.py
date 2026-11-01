import sqlite3

def run_scan():
    con = sqlite3.connect("backend/voiceflow.db")
    con.execute("PRAGMA foreign_keys = ON;")
    
    # 1. PRAGMA foreign_key_check
    fk_violations = con.execute("PRAGMA foreign_key_check;").fetchall()
    print("=== PRAGMA foreign_key_check OUTPUT ===")
    print(fk_violations)
    
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
    print("\n=== DECLARED FOREIGN KEYS (DB-ENFORCED) ===")
    declared_relations = []
    for t in tables:
        fks = con.execute(f"PRAGMA foreign_key_list({t})").fetchall()
        for fk in fks:
            declared_relations.append((t, fk[3], fk[2], fk[4]))
            print(f"DECLARED: {t}.{fk[3]} -> {fk[2]}.{fk[4]} (on_update={fk[5]}, on_delete={fk[6]})")
            
    print("\n=== ORPHAN COUNTS: DECLARED RELATIONSHIPS ===")
    for child_t, child_c, parent_t, parent_c in declared_relations:
        query = f"SELECT COUNT(*) FROM {child_t} WHERE {child_c} IS NOT NULL AND {child_c} NOT IN (SELECT {parent_c} FROM {parent_t})"
        count = con.execute(query).fetchone()[0]
        print(f"Declared: {child_t}.{child_c} -> {parent_t}.{parent_c} | Orphans: {count}")

    print("\n=== ORPHAN COUNTS: LOGICAL / CODE-ENFORCED RELATIONSHIPS ===")
    logical_relations = [
        ("conversations", "agent_id", "agents", "id"),
        ("calls", "agent_id", "agents", "id"),
        ("bills", "agent_id", "agents", "id"),
        ("bookings", "agent_id", "agents", "id"),
        ("contacts", "agent_id", "agents", "id"),
    ]
    for child_t, child_c, parent_t, parent_c in logical_relations:
        # Check if columns exist
        c_cols = [r[1] for r in con.execute(f"PRAGMA table_info({child_t})").fetchall()]
        p_cols = [r[1] for r in con.execute(f"PRAGMA table_info({parent_t})").fetchall()]
        if child_c in c_cols and parent_c in p_cols:
            query = f"SELECT COUNT(*) FROM {child_t} WHERE {child_c} IS NOT NULL AND {child_c} != '' AND {child_c} NOT IN (SELECT {parent_c} FROM {parent_t})"
            count = con.execute(query).fetchone()[0]
            print(f"Logical: {child_t}.{child_c} -> {parent_t}.{parent_c} | Orphans: {count}")
        else:
            print(f"Logical: {child_t}.{child_c} -> {parent_t}.{parent_c} | (Columns not present)")

if __name__ == "__main__":
    run_scan()
