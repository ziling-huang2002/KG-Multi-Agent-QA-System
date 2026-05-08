from __future__ import annotations
import os
from typing import Any
from dotenv import load_dotenv
from neo4j import GraphDatabase

# 從你剛剛建好的檔案匯入
from agents.a5_template import build_template_pipeline
from llm_utils import generate_text


# 載入環境變數並初始化 Neo4j Driver
load_dotenv()
URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
AUTH = (os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "password"))
driver = GraphDatabase.driver(URI, auth=AUTH)

# 初始化所有 Agents
# 記得要在 build_template_pipeline 裡接收並傳遞 driver 給 Executor
PIPELINE = build_template_pipeline(driver)


def answer_question(question: str) -> dict[str, Any]:
    """
    Student template entry.
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
    # 取得各個 Agent 實例
    nlu = PIPELINE["nlu"]
    security_agent = PIPELINE["security"]
    planner = PIPELINE["planner"]
    executor = PIPELINE["executor"]
    diagnosis_agent = PIPELINE["diagnosis"]
    repair_agent = PIPELINE["repair"]
    explanation_agent = PIPELINE["explanation"]

    # 初始化所有合約要求的變數，確保「保底」
    answer = ""
    explanation = ""
    repair_attempted = False
    repair_changed = False
    safety_decision = "ALLOW"

    # 1. 意圖分析與安全檢查
    intent = nlu.run(question)
    # [DEBUG] 印出 NLU 提取的關鍵字
    print(f"\n🔍 [NLU 分析] 關鍵字: {intent.keywords}, 主題: {intent.aspect}")
    
    security = security_agent.run(question, intent)
    if security["decision"] == "REJECT":
        diagnosis_label = "QUERY_ERROR"
        safety_decision = "REJECT"
        answer = "Request rejected by security policy."
        # 安全拒絕時，rows 傳空陣列
        explanation = explanation_agent.run(question, intent, security, {"label": diagnosis_label}, [], False)
        return {
            "answer": answer,
            "safety_decision": safety_decision,
            "diagnosis": diagnosis_label,
            "repair_attempted": False,
            "repair_changed": False,
            "explanation": explanation,
        }
    
    # 2. 第一次查詢嘗試
    plan = planner.run(intent)
    # [DEBUG] 印出第一次生成的 Cypher
    print(f"📡 [第一次查詢] 執行 Cypher:\n{plan.get('cypher')}")
    
    execution = executor.run(plan)
    diagnosis = diagnosis_agent.run(execution)
    print(f"📊 [初次診斷] 結果: {diagnosis['label']}")


    # 3. 診斷與修復 (Diagnosis & Repair)
    # 如果初次查詢失敗或沒資料，嘗試修復 (你可以在這裡決定 NO_DATA 是否也要修復)
    if diagnosis["label"] in {"QUERY_ERROR", "SCHEMA_MISMATCH", "NO_DATA"}:
        repair_attempted = True
        print(f"🛠️ [啟動修復] 因為診斷為: {diagnosis['label']}，正在嘗試修復...")
        
        repaired_plan = repair_agent.run(diagnosis, plan, intent)
        repair_changed = repaired_plan.get("cypher") != plan.get("cypher")
        
        # 檢查 Cypher 是否真的有變動
        if repair_changed:
            print(f"🔄 [修復成功] 新的 Cypher:\n{repaired_plan.get('cypher')}")

        # 第二次查詢嘗試
        execution = executor.run(repaired_plan)
        diagnosis = diagnosis_agent.run(execution)
        print(f"📊 [修復後診斷] 結果: {diagnosis['label']}")

    # 4. 最終結果處理
    rows = execution.get("rows", [])
    # [DEBUG] 印出到底撈到了幾筆資料
    print(f"📦 [資料檢索] 共撈到 {len(rows)} 筆資料")
    if len(rows) > 0:
        print(f"📝 [資料樣本] 第一筆資料: {rows[0]}")
    
    explanation = explanation_agent.run(
        question, 
        intent, 
        security, 
        diagnosis, 
        rows, 
        repair_attempted
    )
    answer = explanation

    # 5. 產出符合助教要求的輸出合約 (Output Contract)
    return {
        "answer": answer,
        "safety_decision": "ALLOW",
        "diagnosis": diagnosis["label"],
        "repair_attempted": repair_attempted,
        "repair_changed": repair_changed,
        "explanation": explanation,
    }


def run_multiagent_qa(question: str) -> dict[str, Any]:
    return answer_question(question)


if __name__ == "__main__":
    try:
        while True:
            q = input("Question (type exit): ").strip()
            if not q or q.lower() in {"exit", "quit"}:
                break
            result = answer_question(q)
            
            print("-" * 30)
            print(f"回答: {result['answer']}")
            print(f"診斷結果: {result['diagnosis']} (修復嘗試: {result['repair_attempted']})")
            print("-" * 30)
    finally:
        driver.close()
