from __future__ import annotations
import os
from typing import Any
from dotenv import load_dotenv
from neo4j import GraphDatabase

# 從 a5_template 匯入最新的 Pipeline 建立函式
from agents.a5_template import build_template_pipeline

# 載入環境變數並初始化 Neo4j Driver
load_dotenv()
URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
AUTH = (os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password"))

# 初始化 Driver
driver = GraphDatabase.driver(URI, auth=AUTH)

# 初始化所有 Agents (包含 NLU, Security, Planner, Executor, Diagnosis, Repair, Explanation)
PIPELINE = build_template_pipeline(driver)

"""
Keep output contract for auto_test_a5.py:
{
  "answer": str,
  "safety_decision": "ALLOW"|"REJECT",
  "diagnosis": "SUCCESS"|"QUERY_ERROR"|"SCHEMA_MISMATCH"|"NO_DATA",
  "repair_attempted": bool,
  "repair_changed": bool,
  "explanation": str
}
"""
def answer_question(question: str) -> dict[str, Any]:
    # 取得 Agent 實例
    nlu = PIPELINE["nlu"]
    security_agent = PIPELINE["security"]
    planner = PIPELINE["planner"]
    executor = PIPELINE["executor"]
    diagnosis_agent = PIPELINE["diagnosis"]
    repair_agent = PIPELINE["repair"]
    explanation_agent = PIPELINE["explanation"]

    # 1. 意圖分析 (1-2角) 與 安全檢查
    intent = nlu.run(question)
    security = security_agent.run(question, intent)
    
    if security["decision"] == "REJECT":
        # 安全拒絕時的標準回傳格式
        return {
            "answer": "Request rejected by security policy.",
            "safety_decision": "REJECT",
            "diagnosis": "QUERY_ERROR",
            "repair_attempted": False,
            "repair_changed": False,
            "explanation": "Rejected: Unsafe query pattern."
        }
    
    # 2. 初次查詢 (3-4角)
    plan = planner.run(intent)
    execution = executor.run(plan)
    diagnosis = diagnosis_agent.run(execution)

    # 3. 診斷與修復 (Diagnosis & Repair)
    repair_attempted = False
    repair_changed = False
    
    if diagnosis["label"] in {"QUERY_ERROR", "SCHEMA_MISMATCH", "NO_DATA"}:
        repair_attempted = True
        repaired_plan = repair_agent.run(diagnosis, plan, intent, question)
        
        # 檢查 Cypher 是否有變動
        if repaired_plan.get("cypher") != plan.get("cypher"):
            repair_changed = True
            # 執行修復後的查詢
            execution = executor.run(repaired_plan)
            diagnosis = diagnosis_agent.run(execution)

    # 4. 最終答案生成 (5-7角：包含語義對齊與邏輯審核)
    rows = execution.get("rows", [])
    explanation = explanation_agent.run(
        question, 
        intent, 
        security, 
        diagnosis, 
        rows, 
        repair_attempted
    )

    # 5. 回傳符合助教要求格式的字典
    return {
        "answer": explanation,
        "safety_decision": "ALLOW",
        "diagnosis": diagnosis["label"],
        "repair_attempted": repair_attempted,
        "repair_changed": repair_changed,
        "explanation": explanation
    }

def run_multiagent_qa(question: str) -> dict[str, Any]:
    return answer_question(question)

if __name__ == "__main__":
    try:
        print("=== NCU Regulation QA System Ready ===")
        while True:
            q = input("\nQuestion (type exit): ").strip()
            if not q or q.lower() in {"exit", "quit"}:
                break
            
            result = answer_question(q)
            
            print("-" * 50)
    finally:
        driver.close()