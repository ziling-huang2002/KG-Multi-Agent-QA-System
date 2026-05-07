# KG-Multi-Agent-QA-System

## Architecture Diagram

```mermaid
graph TD
    %% 定義節點
    Start([開始: 學生提問]) --> NLU[NL Understanding Agent<br/>語意與實體提取]
    NLU --> Security{Security Agent<br/>安全檢查}
    
    %% 安全檢查分支
    Security -- REJECT --> RejectAns[Explanation Agent<br/>生成安全拒絕回覆]
    RejectAns --> End([結束])
    
    %% 正常查詢分支
    Security -- ALLOW --> Planner[Query Planner Agent<br/>生成 Cypher 語法]
    Planner --> Executor[Query Execution Agent<br/>執行 Neo4j 查詢]
    Executor --> Diagnosis{Diagnosis Agent<br/>結果診斷}
    
    %% 診斷分支
    Diagnosis -- SUCCESS --> Explain[Explanation Agent<br/>整合資料並生成回答]
    Diagnosis -- NO_DATA / QUERY_ERROR --> Repair[Query Repair Agent<br/>自動修復與策略調整]
    
    %% 修復循環 (限一次)
    Repair --> Executor2[Query Execution Agent<br/>第二次執行]
    Executor2 --> Diagnosis2{最終診斷}
    
    Diagnosis2 -- SUCCESS --> Explain
    Diagnosis2 -- FAILURE --> NoDataExplain[Explanation Agent<br/>生成無資料/錯誤說明]
    
    Explain --> End
    NoDataExplain --> End

    %% 樣式設定
    style Security fill:#f9f,stroke:#333,stroke-width:2px
    style Diagnosis fill:#bbf,stroke:#333,stroke-width:2px
    style Repair fill:#ff9,stroke:#333,stroke-width:2px
    style Start fill:#dfd
    style End fill:#dfd
```
---
### 3. 架構圖重點說明
*   **Security First**：所有問題在進入資料庫前，都會經過 Security Agent 掃描，確保符合 **Read-only** 規範並過濾不當內容。
*   **Two-Stage Retrieval**：
    *   第一層：透過 NLU 提取的關鍵字進行精確搜尋。
    *   第二層：若初次失敗，**Repair Agent** 會將策略轉向模糊搜尋或語意擴張（例如從「轉系」擴大到「學則」內容）。
*   **Self-Healing Loop**：系統具備自我修復能力，能夠識別 Cypher 語法錯誤或空結果，並在用戶無感的情況下完成修復。
