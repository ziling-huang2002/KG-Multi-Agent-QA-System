from __future__ import annotations
from dataclasses import dataclass
from typing import Any
import json
import re
from llm_utils import generate_text

@dataclass
class Intent:
    question_type: str
    keywords: list[str]
    aspect: str
    target_unit: str  # 新增：預期答案的單位（如 minutes, NTD, grade points）
    ambiguous: bool = False

class NLUnderstandingAgent:
    """[第1、2角] 強化語義解析：不准只寫 brief summary"""
    def run(self, question: str) -> Intent:
        prompt = f"""You are an advanced NCU Regulation Parser. Deeply analyze the question.
        
        [TASK]
        - Extract ALL relevant entities (e.g., "30 minutes", "student ID").
        - Identify the TARGET UNIT (e.g., is the user asking for Time? Money? Points? Yes/No?).
        - Determine Question Type: FACT_CHECK (for numbers), PENALTY_QUERY (for punishments), YES_NO.

        [CURRENT QUESTION]
        {question}

        Return ONLY a JSON object:
        {{
            "question_type": "FACT_CHECK|PENALTY_QUERY|YES_NO",
            "keywords": ["MUST contain key nouns and numbers from the question"],
            "aspect": "A detailed rephrasing of what needs to be found",
            "target_unit": "minutes|NTD|grade points|permission",
            "ambiguous": false
        }}
        """
        try:
            res = generate_text(prompt)
            match = re.search(r'\{.*\}', res, re.DOTALL)
            data = json.loads(match.group())
            return Intent(
                question_type=data.get("question_type", "FACT_CHECK"),
                keywords=data.get("keywords", []),
                aspect=data.get("aspect", question),
                target_unit=data.get("target_unit", ""),
                ambiguous=data.get("ambiguous", False)
            )
        except:
            return Intent("FACT_CHECK", [], question, "", False)

class SecurityAgent:
    """[安全攔截]"""
    def run(self, question: str, intent: Intent) -> dict[str, str]:
        blocked = ["delete", "drop", "merge", "ignore previous", "bypass"]
        if any(p in question.lower() for p in blocked):
            return {"decision": "REJECT", "reason": "Request rejected by security policy."}
        return {"decision": "ALLOW", "reason": "Safe."}

class QueryPlannerAgent:
    """[第3、4角] 強力檢索：結合數字權重與動態擴展"""
    def run(self, intent: Intent) -> dict[str, Any]:
        # 自動擴展關鍵字，並根據 target_unit 強化
        unit_map = {
            "minutes": ["minutes", "time", "late", "duration", "leave", "early"],
            "NTD": ["fee", "cost", "replacement", "money", "lost"],
            "grade points": ["penalty", "deduction", "points", "zero score", "grade"]
        }
        
        extra_keywords = unit_map.get(intent.target_unit, [])
        all_terms = intent.keywords + extra_keywords
        
        # 提取問題中的數字
        numbers = re.findall(r'\d+', intent.aspect)
        
        filtered = [k.lower().replace("'", "\\'") for k in all_terms if len(k) > 1]
        
        # 生成 Cypher：針對數字與單位進行強力 Boost (^5)
        boosted_parts = []
        for k in list(dict.fromkeys(filtered)):
            weight = "^5" if (k in numbers or k in unit_map) else "^2"
            boosted_parts.append(f"{k}~{weight}")
        
        search_query = " OR ".join(boosted_parts)
        
        cypher = f"""
        CALL db.index.fulltext.queryNodes('article_content_idx', '{search_query}') 
        YIELD node AS a, score
        OPTIONAL MATCH (a)-[:CONTAINS_RULE]->(r:Rule)
        RETURN a.number AS article_num, a.content AS content, r.action AS action, r.result AS result
        ORDER BY score DESC LIMIT 20
        """
        return {"strategy": "heavy_boost", "cypher": cypher}

class QueryExecutionAgent:
    def __init__(self, driver): self.driver = driver
    def run(self, plan: dict[str, Any]) -> dict[str, Any]:
        with self.driver.session() as session:
            res = session.run(plan["cypher"])
            return {"rows": [dict(r) for r in res], "error": None}

class DiagnosisAgent:
    def run(self, execution: dict[str, Any]) -> dict[str, str]:
        if not execution.get("rows"): return {"label": "NO_DATA", "reason": "Empty"}
        return {"label": "SUCCESS", "reason": "OK"}

class QueryRepairAgent:
    """[備援機制] 如果初次失敗，嘗試更模糊的搜尋"""
    def run(self, diagnosis, plan, intent, question=""):
        repaired = dict(plan)
        if diagnosis["label"] == "NO_DATA":
            # 直接暴力搜尋 aspect 中的名詞
            words = [w for w in intent.aspect.split() if len(w) > 4]
            search = " OR ".join([f"{w}~" for w in words[:3]])
            repaired["cypher"] = f"CALL db.index.fulltext.queryNodes('article_content_idx', '{search}') YIELD node AS a RETURN a.number AS article_num, a.content AS content LIMIT 5"
        return repaired

class ExplanationAgent:
    """[第5、6、7角] 終極回答生成：強制邏輯比較、出處必填、嚴禁 Not Specified"""
    def run(self, question, intent, security, diagnosis, rows, repair_attempted):
        if security["decision"] == "REJECT": return "Request rejected by security policy."
        if not rows: return "I searched all NCU regulations but found no mention of this. Please contact the Academic Affairs Office."

        # 整理上下文，顯化邏輯結構
        context = ""
        for r in rows:
            context += f"SOURCE: [Article {r.get('article_num')}]\nCONTENT: {r.get('content')}\n"
            if r.get('action'):
                context += f"LOGIC: IF {r.get('action')} -> THEN {r.get('result')}\n"
            context += "-" * 20 + "\n"

        prompt = f"""You are the NCU Supreme Regulation Expert. You MUST find the answer.

        [STRICT INSTRUCTIONS]
        - QUESTION TYPE: {intent.question_type} (Target: {intent.target_unit})
        - RULE 1: If the user asks for a number (like 30 mins) and the rule says a DIFFERENT number (like 40 mins), you MUST point out the conflict.
        - RULE 2: Citations are MANDATORY. Use [Article X].
        - RULE 3: For YES/NO questions, start with "Yes," or "No,". 
        - RULE 4: If you see "deducted", "points", "fine", or "NTD", this is the PENALTY. Do not ignore it.

        [USER QUESTION]
        {question}

        [RETRIEVED CONTEXT]
        {context}

        FINAL ANSWER:"""
        
        ans = generate_text(prompt).strip()
        return ans if ans.endswith('.') else ans + "."

def build_template_pipeline(driver) -> dict[str, Any]:
    return {
        "nlu": NLUnderstandingAgent(),
        "security": SecurityAgent(),
        "planner": QueryPlannerAgent(),
        "executor": QueryExecutionAgent(driver),
        "diagnosis": DiagnosisAgent(),
        "repair": QueryRepairAgent(),
        "explanation": ExplanationAgent(),
    }