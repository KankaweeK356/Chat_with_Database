import streamlit as st
import pandas as pd
import sqlite3
from google import genai
from google.genai import types
import json
import os

# ==========================================
# 1. SETUP & CONFIGURATION
# ==========================================
try:
    gemini_api_key = st.secrets["gemini_api_key"]
    gmn_client = genai.Client(api_key=gemini_api_key)
except Exception:
    st.error("❌ กรุณาตั้งค่า API Key ใน Streamlit Secrets")
    st.stop()

db_name = 'test_database.db'
data_table = 'transactions'
csv_file = 'test_transactions_2026.csv'

@st.cache_resource
def init_database():
    """สร้างฐานข้อมูลอัตโนมัติจากไฟล์ CSV (รันครั้งเดียว)"""
    if os.path.exists(csv_file):
        conn = sqlite3.connect(db_name)
        df_csv = pd.read_csv(csv_file)
        df_csv.to_sql(data_table, conn, if_exists='replace', index=False)
        conn.close()
        return True
    return False

db_ready = init_database()

# สรุป Schema ให้สั้นลงเพื่อประหยัดโควต้า Token
data_dict_text = """
- trx_date: วันที่ (YYYY-MM-DD)
- trx_no: หมายเลขธุรกรรม
- product_code, product_category, product_group, product_type: ข้อมูลสินค้า
- order_qty: จำนวนชิ้น
- unit_price: ราคาต่อหน่วย
- cost: ต้นทุนต่อหน่วย
- net_amount: ยอดขายสุทธิ
- cost_amount: ต้นทุนรวม
- branch_region, branch_province: ภูมิภาคและจังหวัด
"""

# ==========================================
# 2. PROMPT TEMPLATES 
# ==========================================
script_prompt = """
คุณคือระบบแปลงคำถามเป็น SQLite (ส่งคืนแค่ JSON ห้ามมีข้อความอื่น)
หากกรองเดือน/ปี ให้ใช้ `strftime('%Y-%m', trx_date)`
Schema: {data_dict}
Table: {table_name}
Question: {question}

Output format: {{"script": "SELECT ... FROM ..."}}
"""

answer_prompt = """
สรุปคำตอบจากข้อมูลให้สั้น กระชับ และตรงคำถามที่สุด 
(บังคับ: ตัวเลขต้องมีคอมม่าคั่นหลักพัน ทศนิยม 2 ตำแหน่ง และมีหน่วยเสมอ)
Data: {raw_data}
Question: {question}
"""

# ==========================================
# 3. HELPER FUNCTIONS
# ==========================================
def query_to_dataframe(sql_query, database_name):
    try:
        connection = sqlite3.connect(database_name)
        result_df = pd.read_sql_query(sql_query, connection)
        connection.close()
        return result_df
    except Exception as e:
        return f"Database Error: {e}"

def generate_gemini_answer(prompt, is_json=False):
    try:
        config = types.GenerateContentConfig(
            response_mime_type="application/json" if is_json else "text/plain" 
        )
        response = gmn_client.models.generate_content(
            model='gemini-1.5-flash', 
            contents=prompt,
            config=config
        )
        return response.text
    except Exception as e:
        return f"AI Error: {e}"

# ==========================================
# 4. CORE LOGIC
# ==========================================
def generate_summary_answer(user_question):
    # 1. สร้าง SQL
    sql_prompt = script_prompt.format(question=user_question, table_name=data_table, data_dict=data_dict_text)
    sql_json_text = generate_gemini_answer(sql_prompt, is_json=True)
    
    try:
        sql_script = json.loads(sql_json_text)['script']
    except Exception:
        return "❌ ขออภัย ไม่สามารถสร้างเงื่อนไขการค้นหาได้ในขณะนี้"

    # 2. ดึงข้อมูล
    df_result = query_to_dataframe(sql_script, db_name)
    if isinstance(df_result, str): 
        return f"❌ {df_result}"
    if df_result.empty: 
        return "ไม่พบข้อมูลที่ตรงกับคำถามของคุณ"

    # 3. สรุปคำตอบ
    ans_prompt = answer_prompt.format(question=user_question, raw_data=df_result.to_string())
    final_answer = generate_gemini_answer(ans_prompt, is_json=False)
    
    return final_answer

# ==========================================
# 5. USER INTERFACE
# ==========================================
st.set_page_config(page_title="Data Chatbot", page_icon="📊")
st.title('📊 Gemini Data Chatbot')

if not db_ready:
    st.warning("⚠️ กรุณาอัปโหลดไฟล์ CSV เข้าระบบก่อนใช้งาน")
    st.stop()

if "messages" not in st.session_state:
    st.session_state.messages = []

for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

if prompt := st.chat_input("พิมพ์คำถามของคุณ... (เช่น ยอดขายรวมคือเท่าไหร่?)"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
        
    with st.chat_message("assistant"):
        with st.spinner('กำลังหาคำตอบ...'):
            response = generate_summary_answer(prompt)
            st.markdown(response)
            
    st.session_state.messages.append({"role": "assistant", "content": response})
