"""Minimal KG builder template for Assignment 4.

Keep this contract unchanged:
- Graph: (Regulation)-[:HAS_ARTICLE]->(Article)-[:CONTAINS_RULE]->(Rule)
- Article: number, content, reg_name, category
- Rule: rule_id, type, action, result, art_ref, reg_name
- Fulltext indexes: article_content_idx, rule_idx
- SQLite file: ncu_regulations.db
"""

import json
import os
import sqlite3
import re
from typing import Any

from dotenv import load_dotenv
from neo4j import GraphDatabase
from llm_utils import generate_text

from llm_loader import load_local_llm, get_tokenizer, get_raw_pipeline


# ========== 1. Initialization ==========
load_dotenv()

URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
AUTH = (
    os.getenv("NEO4J_USER", "neo4j"),
    os.getenv("NEO4J_PASSWORD", "password"),
)


# ========== 2. 規則提取核心 ==========
'''透過 LLM 將非結構化的法規文字變成結構化的 JSON 資料'''
def extract_entities(article_number: str, reg_name: str, content: str) -> dict[str, Any]:
    """TODO(student, required): implement LLM extraction and return {"rules": [...]}"""
    # 這裡建議改用 llm_loader 直接推論，避免 import query_system 產生衝突
    

    load_local_llm() ###我們在這呼叫
    tok = get_tokenizer()
    pipe = get_raw_pipeline()
    
    prompt = f"""You are a specialized legal data extractor. 
    Your goal is to extract rules from university regulations and format them into a SINGLE FLAT JSON object.

    ### RULES FOR OUTPUT:
    1. Output MUST be a valid JSON object starting with {{ "rules": [...] }}.
    2. DO NOT use nested objects within the list.
    3. Ensure all keys and values use DOUBLE QUOTES (").
    4. Ensure there is NO trailing comma after the last element.

    ### EXAMPLE FORMAT:
    {{
      "rules": [
        {{
          "type": "Requirement",
          "action": "Students must bring ID",
          "result": "Permitted to take exam"
        }}
      ]
    }}

    ### CONTENT TO ANALYZE:
    Regulation: {reg_name}
    Article: {article_number}
    Content: {content}
    """
    
    messages = [{"role": "user", "content": prompt}]
    formatted_prompt = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    
    try:
        response_text = pipe(formatted_prompt, max_new_tokens=512)[0]["generated_text"].strip()
        print(f"DEBUG: {response_text}")
        
        # 找尋第一個 { 和最後一個 }
        match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if match:
            json_str = match.group()
            # 自動修正：移除 JSON 結尾多餘的逗號 (常見錯誤)
            json_str = re.sub(r',\s*([\]}])', r'\1', json_str)
            try:
                return json.loads(json_str)
            except:
                # 如果還是失敗，嘗試更激進的修復，或至少記錄下來
                print(f"!!! JSON 嚴重損壞，無法解析: {article_number}")
        return {"rules": []}
    except Exception as e:
        return {"rules": []}


def build_fallback_rules(article_number: str, content: str) -> list[dict[str, str]]:
    """TODO(student, optional): add deterministic fallback rules."""
    """當 LLM 提取失敗時，建立一個包含全文關鍵字的保底規則。"""
    return [{
        "type": "General",
        "action": f"Content related to {article_number}",
        "result": "See full article text for details.",
        "art_ref": article_number
    }]




# SQLite tables used:
# - regulations(reg_id, name, category)
# - articles(reg_id, article_number, content)


# ========== 3. 圖譜構建主程序(執行 ETL（提取、轉換、載入）流程) ==========
def build_graph() -> None:
    """Build KG from SQLite into Neo4j using the fixed assignment schema."""
    sql_conn = sqlite3.connect("ncu_regulations.db")
    cursor = sql_conn.cursor()
    driver = GraphDatabase.driver(URI, auth=AUTH)

    # Optional: warm up local LLM
    load_local_llm()

    with driver.session() as session:
        # Fixed strategy: clear existing graph data before rebuilding.
        session.run("MATCH (n) DETACH DELETE n")

        # 1) Read regulations and create Regulation nodes.
        cursor.execute("SELECT reg_id, name, category FROM regulations")
        regulations = cursor.fetchall()
        reg_map: dict[int, tuple[str, str]] = {}

        for reg_id, name, category in regulations:
            reg_map[reg_id] = (name, category)
            session.run(
                "MERGE (r:Regulation {id:$rid}) SET r.name=$name, r.category=$cat",
                rid=reg_id,
                name=name,
                cat=category,
            )

        # 2) Read articles and create Article + HAS_ARTICLE.
        cursor.execute("SELECT reg_id, article_number, content FROM articles")
        articles = cursor.fetchall()

        for reg_id, article_number, content in articles:
            reg_name, reg_category = reg_map.get(reg_id, ("Unknown", "Unknown"))
            session.run(
                """
                MATCH (r:Regulation {id: $rid})
                CREATE (a:Article {
                    number:   $num,
                    content:  $content,
                    reg_name: $reg_name,
                    category: $reg_category
                })
                MERGE (r)-[:HAS_ARTICLE]->(a)
                """,
                rid=reg_id,
                num=article_number,
                content=content,
                reg_name=reg_name,
                reg_category=reg_category,
            )

        # 3) Create full-text index on Article content.
        session.run(
            """
            CREATE FULLTEXT INDEX article_content_idx IF NOT EXISTS
            FOR (a:Article) ON EACH [a.content]
            """
        )

        rule_counter = 0

        # TODO(student, required):
        # 3) 處理規則提取與連結
        # === 修改開始：遍歷所有文章並建立規則與連線 ===
        for reg_id, article_number, content in articles:
            reg_name, reg_category = reg_map.get(reg_id, ("Unknown", "Unknown"))
            
            # 1. 呼叫 LLM 提取規則
            extracted = extract_entities(article_number, reg_name, content)

            # 2. 【修正位置】在這裡處理 rules 並加入保底邏輯
            rules = extracted.get("rules", [])

            # 如果 LLM 沒抓到東西 (extracted 為空或回傳空 rules)，執行保底
            if not rules:
                print(f"Applying fallback for Art {article_number}")
                rules = build_fallback_rules(article_number, content)

            # 3. 接下來才開始遍歷 rules 建立節點
            if rules and isinstance(rules, list):
                for r_data in rules:
                    # 跳過內容不完整的規則 (Action 或 Result 為空)
                    if not r_data.get("action") or not r_data.get("result"):
                        continue
                        
                    rule_counter += 1
                    # 這裡的 unique_id 可以加上 counter 確保唯一性
                    unique_id = f"RULE_{reg_id}_{article_number.replace(' ', '')}_{rule_counter}"
                    
                    session.run(
                        """
                        MATCH (a:Article {number: $num, reg_name: $reg_name})
                        CREATE (r:Rule {
                            rule_id: $rid,
                            type:    $type,
                            action:  $action,
                            result:  $result,
                            art_ref: $num,
                            reg_name: $reg_name
                        })
                        MERGE (a)-[:CONTAINS_RULE]->(r)
                        """,
                        num=article_number,
                        reg_name=reg_name,
                        rid=unique_id,
                        type=r_data.get("type", "Requirement"),
                        action=r_data.get("action"),
                        result=r_data.get("result")
                    )
            else:
                # 選項：如果沒有規則，你可以在 Article 節點打一個標籤，表示已處理但無規則
                pass
        # 4) Create full-text index on Rule fields.
        session.run(
            """
            CREATE FULLTEXT INDEX rule_idx IF NOT EXISTS
            FOR (r:Rule) ON EACH [r.action, r.result]
            """
        )

        # 5) Coverage audit (provided scaffold).
        coverage = session.run(
            """
            MATCH (a:Article)
            OPTIONAL MATCH (a)-[:CONTAINS_RULE]->(r:Rule)
            WITH a, count(r) AS rule_count
            RETURN count(a) AS total_articles,
                   sum(CASE WHEN rule_count > 0 THEN 1 ELSE 0 END) AS covered_articles,
                   sum(CASE WHEN rule_count = 0 THEN 1 ELSE 0 END) AS uncovered_articles
            """
        ).single()

        total_articles = int((coverage or {}).get("total_articles", 0) or 0)
        covered_articles = int((coverage or {}).get("covered_articles", 0) or 0)
        uncovered_articles = int((coverage or {}).get("uncovered_articles", 0) or 0)

        print(
            f"[Coverage] covered={covered_articles}/{total_articles}, "
            f"uncovered={uncovered_articles}"
        )

    driver.close()
    sql_conn.close()


if __name__ == "__main__":
    build_graph()
