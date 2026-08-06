"""
SCP - Viet Nam | Self-Correcting Pipeline
Copyright (c) 2026 SCP Vietnam Project. All Rights Reserved.

This software is protected by Vietnamese Intellectual Property Law and
international treaties. Unauthorized commercial use is prohibited.

Patent Pending:
  - WHY Engine (Verifikation Plan Generator)
  - Recursive Why (Trust Tracing to Axiomatic Sources)
  - MetaFalsifier (Verification Plan Falsification)
  - ProofGraph (Dynamic Proof Dependency Graph)

License: See LICENSE file
Contact: scp-vietnam@example.com
"""

#!/usr/bin/env python3
"""
SCP V41 — Knowledge Restructure.

Vấn đề: knowledge table lẫn entity (fact) với question (history).
  entity = "tính 99 * 81"  ← đây là QUESTION, không phải entity
  entity = "giá bitcoin hiện tại là bao nhiêu?"  ← QUESTION

Fix:
  1. Clean knowledge table — xóa rows có entity là question
  2. Extract proper entity từ question
  3. Move question → error_history (already there)
  4. knowledge chỉ chứa facts: entity="bitcoin", attribute="price_usd"
"""
import re
import sys

sys.path.insert(0, '.')

import sqlite3

DB_PATH = 'data/v13.db'

def is_question(entity: str) -> bool:
    """Check if entity looks like a question rather than a fact."""
    if not entity:
        return False
    e = entity.lower().strip()
    # Questions contain Vietnamese/English question patterns
    question_patterns = [
        '?', 'là gì', 'là bao nhiêu', 'bằng bao nhiêu', 'hiện tại',
        'tính ', 'calculate', 'ai là', 'who is', 'thủ đô của',
        'nhiệt độ', 'khối lượng phân tử', 'giá ', 'chuyển đổi',
    ]
    for pattern in question_patterns:
        if pattern in e:
            return True
    # Math expressions like "+ 78" or "* 6" are not proper entities
    if re.match(r'^[+\-*/]\s*\d+', e):
        return True
    if re.match(r'^tính\s+\d', e):
        return True
    # Very long entities (>50 chars) are likely questions
    if len(e) > 50:
        return True
    return False


def extract_proper_entity(entity: str, attribute: str) -> str:
    """Extract proper entity from a question-like entity."""
    e = entity.lower().strip()

    # Math: "tính 99 * 81" → "99*81" (expression, not entity)
    m = re.search(r'(?:tính|calculate)\s+(.+)', e)
    if m:
        return m.group(1).strip().rstrip('?')

    # Chemistry: "khối lượng phân tử caffeine" → "caffeine"
    m = re.search(r'khối\s*lượng\s*phân\s*tử\s+(\w+)', e)
    if m:
        return m.group(1)

    # Crypto: "giá bitcoin hiện tại" → "bitcoin"
    m = re.search(r'giá\s+(\w+)', e)
    if m:
        return m.group(1)

    # Geography: "thủ đô của việt nam" → "việt_nam"
    m = re.search(r'thủ\s*đô.*?của\s+(.+?)\s*(?:là|$)', e)
    if m:
        return m.group(1).strip().replace(' ', '_')

    # Weather: "nhiệt độ tại hà nội" → "hà_nội"
    m = re.search(r'nhiệt\s*độ.*?(?:tại|ở)\s+(.+?)\s*(?:là|$)', e)
    if m:
        return m.group(1).strip().replace(' ', '_')

    # If can't extract, return original
    return entity


def restructure_knowledge():
    """Clean knowledge table — separate facts from questions."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Get all rows
    rows = conn.execute('SELECT id, entity, attribute, value, source FROM knowledge').fetchall()

    cleaned = 0
    updated = 0
    deleted = 0

    for row in rows:
        entity = row['entity']
        attr = row['attribute']

        if is_question(entity):
            # Try to extract proper entity
            proper = extract_proper_entity(entity, attr)

            if proper and proper != entity and not is_question(proper):
                # Update with proper entity
                conn.execute(
                    'UPDATE knowledge SET entity = ? WHERE id = ?',
                    (proper.lower(), row['id'])
                )
                updated += 1
            else:
                # Can't extract — delete (question shouldn't be in knowledge)
                conn.execute('DELETE FROM knowledge WHERE id = ?', (row['id'],))
                deleted += 1
        else:
            cleaned += 1

    conn.commit()

    # Report
    total = conn.execute('SELECT COUNT(*) FROM knowledge').fetchone()[0]
    unique = conn.execute('SELECT COUNT(DISTINCT entity) FROM knowledge').fetchone()[0]

    print('Knowledge restructure complete:')
    print(f'  Already clean: {cleaned}')
    print(f'  Updated (extracted entity): {updated}')
    print(f'  Deleted (unparseable questions): {deleted}')
    print(f'  Remaining: {total} rows, {unique} unique entities')

    # Show sample
    print('\nSample clean knowledge:')
    rows = conn.execute('SELECT entity, attribute, value, source FROM knowledge LIMIT 15').fetchall()
    for r in rows:
        print(f'  entity={r["entity"][:30]:30s} attr={r["attribute"]:20s} value={str(r["value"])[:20]}')

    conn.close()


if __name__ == '__main__':
    restructure_knowledge()
