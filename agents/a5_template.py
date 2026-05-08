from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import json
import re
import sys
import os

from llm_utils import generate_text


# 進階版：找到含關鍵字的句子優先保留
def extract_relevant_snippet(content: str, keywords: list, max_len: int = 800) -> str:
    if len(content) <= max_len:
        return content
    
    content_lower = content.lower()
    found_positions = []
    
    for kw in keywords:
        pos = content_lower.find(kw.lower())
        if pos != -1:
            found_positions.append(pos)
    
    if not found_positions:
        return content[:max_len] + "..."
    
    best_pos = min(found_positions)
    start = max(0, best_pos - 100)
    return content[start:start + max_len] + "..."


@dataclass
class Intent:
    question_type: str      # 例如: general, academic, disciplinary, enrollment
    keywords: list[str]
    aspect: str             # 例如: 轉系, 退學, 請假
    ambiguous: bool = False


class NLUnderstandingAgent:
    def run(self, question: str) -> Intent:
        """TODO(student): convert question to structured intent."""
        
        prompt = f"""You are a linguistic parser for NCU university regulations.
        Analyze the CURRENT QUESTION and extract the intent and keywords.

        [CONSTRAINTS]
        - Output MUST be a single, valid JSON object.
        - Keywords should be extracted ONLY from the Current Question provided below.
        - Do not use information from any previous instructions or examples.

        [CURRENT QUESTION]
        {question}

        [OUTPUT JSON FORMAT]
        {{
            "question_type": "The category of the question",
            "keywords": ["list", "of", "relevant", "English", "terms"],
            "aspect": "The core topic of the question",
            "ambiguous": false
        }}
        """

        try:
            res = generate_text(prompt)
            # 偵錯用：看看現在 NLU 到底吐出什麼
            print(f"DEBUG: LLM Original Response: {res}")

            match = re.search(r'\{.*\}', res, re.DOTALL)
            data = json.loads(match.group()) if match else {}
            
            return Intent(
                question_type=data.get("question_type", "general"),
                keywords=data.get("keywords", []),
                aspect=data.get("aspect", "general"),
                ambiguous=data.get("ambiguous", False)
            )
        except Exception as e:
            print(f"NLU Parsing Error: {e}")
            return Intent(question_type="general", keywords=[], aspect="general", ambiguous=False)


class SecurityAgent:
    def run(self, question: str, intent: Intent) -> dict[str, str]:

        blocked_patterns = [
            "delete", "drop", "merge", "create", "set ",
            "bypass", "ignore previous", "dump all",
            # 補充漏網的 unsafe 模式
            "export", "modify", "word-by-word", "every regulation",
            "credentials", "script to", "show me every",
            "return all", "every article",
        ]

        q = question.lower()
        if any(p in q for p in blocked_patterns):
            return {"decision": "REJECT", "reason": "Unsafe query pattern."}
        return {"decision": "ALLOW", "reason": "Passed security check."}


class QueryPlannerAgent:
    def run(self, intent: Intent) -> dict[str, Any]:
        stopwords = {
            'university', 'ncu', 'regulation', 'rule', 'the', 'a', 'an',
            'and', 'or', 'in', 'of', 'for', 'to', 'is', 'are', 'be',
            'student', 'students', 'exam', 'academic', 'settings',
            'process', 'policy', 'procedures', 'conduct', 'discipline'
        }
        
        selective_keywords = [k.replace("'", "\\'") for k in intent.keywords 
                               if k.lower() not in stopwords and len(k) > 3]
        
        if selective_keywords:
            aspect_words = [w.replace("'", "\\'") for w in intent.aspect.split() 
                            if w.lower() not in stopwords and len(w) > 4]
            all_terms = selective_keywords + aspect_words
            all_terms = list(dict.fromkeys(all_terms))
            search_query = " OR ".join([f"{k}~" for k in all_terms])
        else:
            search_query = intent.aspect.split()[0].replace("'", "\\'")
        
        # 判斷是否為考試相關問題，優先搜 Rule 節點
        exam_keywords = {'penalty', 'exam', 'invigilator', 'proctor', 'cheating', 
                         'barred', 'late', 'threaten', 'question paper'}
        is_exam_related = any(k.lower() in exam_keywords for k in intent.keywords)
        
        if is_exam_related:
            # 考試相關：優先搜 Rule 節點的 content
            cypher = f"""
            CALL db.index.fulltext.queryNodes('article_content_idx', '{search_query}') 
            YIELD node AS a, score
            OPTIONAL MATCH (a)-[:CONTAINS_RULE]->(r:Rule)
            WITH a, r, score,
                 CASE WHEN a.number STARTS WITH 'Rule' THEN score * 2 ELSE score END AS weighted_score
            RETURN a.number AS article_num, 
                a.content AS content,
                r.action AS action, 
                r.result AS result
            ORDER BY weighted_score DESC LIMIT 10
            """
        else:
            cypher = f"""
            CALL db.index.fulltext.queryNodes('article_content_idx', '{search_query}') 
            YIELD node AS a, score
            OPTIONAL MATCH (a)-[:CONTAINS_RULE]->(r:Rule)
            RETURN a.number AS article_num, 
                a.content AS content,
                r.action AS action, 
                r.result AS result
            ORDER BY score DESC LIMIT 10
            """
        
        return {
            "strategy": "dynamic_weight_search",
            "cypher": cypher,
            "keywords": intent.keywords,
            "aspect": intent.aspect,
        }

class QueryExecutionAgent:
    def __init__(self, driver):
        self.driver = driver

    def run(self, plan: dict[str, Any]) -> dict[str, Any]:
        """TODO(student): execute Neo4j read-only query and return rows/error."""
        cypher = plan.get("cypher")
        if not cypher:
            return {"rows": [], "error": "No Cypher generated"}
        
        try:
            with self.driver.session() as session:
                result = session.run(cypher)
                rows = [dict(record) for record in result]
                return {"rows": rows, "error": None}
        except Exception as e:
            return {"rows": [], "error": str(e)}

class DiagnosisAgent:
    def run(self, execution: dict[str, Any]) -> dict[str, str]:
        if execution.get("error"):
            return {"label": "QUERY_ERROR", "reason": str(execution["error"])}
        if not execution.get("rows") or len(execution["rows"]) == 0:
            return {"label": "NO_DATA", "reason": "No matching rule in KG."}
        return {"label": "SUCCESS", "reason": "Query succeeded."}


class QueryRepairAgent:
    def run(self, diagnosis: dict[str, str], original_plan: dict[str, Any], intent: Intent) -> dict[str, Any]:
        repaired = dict(original_plan)

        # 策略：如果找不到資料，嘗試更模糊的搜尋 (Broad Search)
        if diagnosis["label"] == "NO_DATA":
            # 取 aspect 的第一個核心單字，例如 "Student" 或 "EasyCard"
            first_word = intent.aspect.split()[0].replace("'", "\\'")
            repaired["cypher"] = f"""
            MATCH (a:Article) 
            WHERE a.content CONTAINS '{first_word}'
            OPTIONAL MATCH (a)-[:CONTAINS_RULE]->(r:Rule)
            RETURN a.number AS article_num, 
                   COALESCE(r.action, a.content) AS action, 
                   COALESCE(r.result, 'See regulations.') AS result
            LIMIT 5
            """
            repaired["strategy"] = "single_word_fallback"
                
        # 策略：如果是語法錯誤，回傳一個簡單的保底查詢
        elif diagnosis["label"] == "QUERY_ERROR":
            # 既然語法錯了，就回傳一個最簡單、絕對不會錯的模糊查詢
            aspect_safe = intent.aspect.replace("'", "\\'")
            repaired["cypher"] = f"MATCH (a:Article) WHERE a.content CONTAINS '{aspect_safe}' MATCH (a)-[:CONTAINS_RULE]->(r:Rule) RETURN a.number, r.action, r.result LIMIT 5"
            repaired["strategy"] = "emergency_fallback"
            
        return repaired


class ExplanationAgent:
    def run(self, question, intent, security, diagnosis, rows, repair_attempted):
        if security["decision"] == "REJECT":
            return "Rejected: Unsafe query."
        if not rows:
            return "No relevant regulations found."

        # 只取前3筆，避免 context 太長
        context_parts = []
        for r in rows[:3]:
            content = r.get('content', '') or ''
            # 優先保留含關鍵字的段落
            content = extract_relevant_snippet(content, intent.keywords, max_len=600)
            context_parts.append(content)
        
        context = "\n\n".join(context_parts)

        prompt = f"""Read the content and answer the question with a short phrase.
        If there are multiple consequences, include all of them separated by "and".

        Content: {context}

        Question: {question}

        Short answer (use numbers like 20, 60, 200):"""

        raw_answer = generate_text(prompt)
        
        # 後處理1：去掉開頭多餘的 Yes/No
        yes_no_question_words = ["can i", "is it", "are students", "is a student", "am i"]
        is_yes_no_question = any(q in question.lower() for q in yes_no_question_words)
        if not is_yes_no_question:
            raw_answer = re.sub(r'^(Yes|No)[.,]\s*', '', raw_answer, flags=re.IGNORECASE)

        # 後處理2：結尾補句號
        raw_answer = raw_answer.strip()
        if raw_answer and not raw_answer.endswith('.'):
            raw_answer += '.'

        return raw_answer
        

def build_template_pipeline(driver) -> dict[str, Any]:
    """Factory for student use in query_system_multiagent_template.py."""
    return {
        "nlu": NLUnderstandingAgent(),
        "security": SecurityAgent(),
        "planner": QueryPlannerAgent(),
        "executor": QueryExecutionAgent(driver),
        "diagnosis": DiagnosisAgent(),
        "repair": QueryRepairAgent(),
        "explanation": ExplanationAgent(),
    }

