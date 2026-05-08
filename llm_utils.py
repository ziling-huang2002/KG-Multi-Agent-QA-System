import os
from llm_loader import load_local_llm, get_raw_pipeline, get_tokenizer

# 確保模型載入
load_local_llm()

def generate_text(prompt: str) -> str:
    pipe = get_raw_pipeline()
    tok = get_tokenizer()
    messages = [{"role": "user", "content": prompt}]
    formatted = tok.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    output = pipe(formatted, max_new_tokens=512)
    
    # 先印出來看結構
    print(f"DEBUG output structure: {output}")

    # return_full_text=False 時，generated_text 直接是字串
    res = output[0]["generated_text"]
    print(f"DEBUG res type: {type(res)}, value: {repr(res)}")

    # 防呆：如果是 list 就取最後一個 content
    if isinstance(res, list):
        res = res[-1].get("content", "")
    
    result = res.strip() if res else ""
    print(f"DEBUG final result: {repr(result)}")
    return result





