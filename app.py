import streamlit as st
import pandas as pd
import sqlite3
from google import genai
from google.genai import types
import json
import os

# ==========================================
# 1. การตั้งค่าระบบ (Setup & Configuration)
# ==========================================
# ดึง API Key จาก Secrets ของ Streamlit
try:
    gemini_api_key = st.secrets["gemini_api_key"]
    gmn_client = genai.Client(api_key=gemini_api_key)
except Exception:
    st.error("❌ ไม่พบ API Key! กรุณาตั้งค่า gemini_api_key ใน Streamlit Secrets")
    st.stop()

db_name = 'test_database.db'
data_table = 'transactions'
csv_file = 'test_transactions_2026.csv'

# ระบบเตรียมฐานข้อมูลอัตโนมัติ (จะอัปเดตข้อมูลจาก CSV เสมอเมื่อเปิดแอป)
@st.cache_resource
def init_database():
    if os.path.exists(csv_file):
        try:
            conn = sqlite3.connect(db_name)
            df_csv = pd.read_csv(csv_file)
            # เขียนทับตารางเดิมเพื่อให้ข้อมูลเป็นปัจจุบัน
            df_csv.to_sql(data_table, conn, if_exists='replace', index=False)
            conn.close()
            return True
        except Exception as e:
            st.error(f"Error loading CSV: {e}")
            return False
    return False

db_ready = init_database()

# รายละเอียดโครงสร้างข้อมูล (Schema)
data_dict_text = """
- trx_date: วันที่ทำธุรกรรม (รูปแบบ YYYY-MM-DD)
- trx_no: หมายเลขธุรกรรม
- member_code: รหัสสมาชิกของลูกค้า
- branch_code: รหัสสาขา
- branch_region: ภูมิภาคที่สาขาตั้งอยู่
- branch_province: จังหวัดที่สาขาตั้งอยู่
- product_code: รหัสสินค้า
- product_category: หมวดหมู่หลักของสินค้า
- product_group: กลุ่มของสินค้า
- product_type: ประเภทของสินค้า
- order_qty: จำนวนชิ้น/หน่วย ที่ลูกค้าสั่งซื้อ
- unit_price: ราคาขายของสินค้าต่อ 1 หน่วย
- cost: ต้นทุนของสินค้าต่อ 1 หน่วย
- item_discount: ส่วนลดเฉพาะรายการสินค้านั้นๆ
- customer_discount: ส่วนลดจากสิทธิของลูกค้า
- net_amount: ยอดขายสุทธิของรายการนั้น
- cost_amount: ต้นทุนรวมของรายการนั้น
"""

# ==========================================
# 2. PROMPT TEMPLATES (หัวใจสำคัญของการทำงาน)
# ==========================================

# 1. สำหรับแปลงคำถามเป็น SQL
script_prompt = """
### Goal
สร้าง SQLite script ที่สั้นและถูกต้องที่สุดเพื่อตอบคำถามจากข้อมูลที่มี โดยส่งออกเป็น JSON เท่านั้น

### Context
คุณคือ SQLite Master ที่ทำงานในระบบอัตโนมัติ (Strict JSON API) ห้ามตอบเป็นคำพูด ให้ตอบเฉพาะโค้ดที่ใช้งานได้จริง

### Input
- คำถาม: <Question> {question} </Question>
- ชื่อตาราง: <Table_Name> {table_name} </Table_Name>
- คำอธิบายคอลัมน์: <Schema> {data_dict} </Schema>

### Process
1. วิเคราะห์ Query จาก <Question> และ <Schema>
2. หากมีการกรองวันที่ ให้ใช้ฟังก์ชัน strftime('%Y-%m', trx_date) หรือฟังก์ชันที่เหมาะสมของ SQLite เสมอ
3. เขียน SQL ให้กระชับและมุ่งเน้นเฉพาะคำตอบที่ต้องการ

### Output
ตอบกลับในรูปแบบ JSON เท่านั้น:
{{"script": "SELECT ... FROM ..."}}
"""

# 2. สำหรับสรุปคำตอบให้เป็นภาษาคนที่อ่านง่ายและสมบูรณ์
answer_prompt = """
### Goal
สรุปผลลัพธ์จากข้อมูลให้ "สมบูรณ์แบบ" เป็นธรรมชาติ และมีประโยชน์

### Context
คุณคือ Data Analyst มืออาชีพที่ตอบคำถามได้สุภาพ เข้าใจง่าย และให้ข้อมูลที่ชัดเจน

### Input
- คำถามของผู้ใช้: <Question> {question} </Question>
- ข้อมูลดิบจากฐานข้อมูล: <Raw_Data> {raw_data} </Raw_Data>

### Output
กรุณาตอบเป็นภาษาไทย โดยมีโครงสร้างดังนี้:
1. สรุปคำตอบหลักให้ชัดเจน (เช่น ยอดรวมคือ..., รายการที่ขายดีที่สุดคือ...)
2. จัดรูปแบบตัวเลขให้สวยงาม (ใส่คอมม่าคั่นหลักพัน, ทศนิยม 2 ตำแหน่ง, ระบุหน่วยบาท/ชิ้น เสมอ)
3. หากมีข้อมูลที่น่าสนใจหรือข้อสังเกตเพิ่มเติม (Insight) ให้แจ้งให้ผู้ใช้ทราบด้วย
4. หากข้อมูลมีหลายบรรทัด ให้สรุปเป็นลำดับข้อเพื่อให้อ่านง่าย
"""

# ==========================================
# 3. ฟังก์ชันการทำงาน (Helper Functions)
# ==========================================
def query_to_dataframe(sql_query, database_name):
    """รัน SQL และส่งคืนค่าเป็น DataFrame"""
    try:
        connection = sqlite3.connect(database_name)
        result_df = pd.read_sql_query(sql_query, connection)
        connection.close()
        return result_df
    except Exception as e:
        return f"Database Error: {e}"

def generate_gemini_answer(prompt, is_json=False):
    """ส่งคำสั่งไปให้ Gemini ประมวลผล"""
    try:
        config = types.GenerateContentConfig(
            response_mime_type="application/json" if is_json else "text/plain" 
        )
        # ใช้รุ่นที่เสถียรและเร็วที่สุด
        response = gmn_client.models.generate_content(
            model='gemini-2.0-flash', 
            contents=prompt,
            config=config
        )
        return response.text
    except Exception as e:
        return f"AI Error: {e}"

# ==========================================
# 4. ตรรกะหลัก (Core Logic)
# ==========================================
def generate_summary_answer(user_question):
    # 1. สร้าง SQL จากคำถาม
    sql_prompt_input = script_prompt.format(
        question=user_question, 
        table_name=data_table, 
        data_dict=data_dict_text
    )
    sql_json_text = generate_gemini_answer(sql_prompt_input, is_json=True)
    
    try:
        # ดึงเอา SQL Script ออกมา
        sql_script = json.loads(sql_json_text)['script']
    except Exception:
        return f"❌ AI ไม่สามารถสร้าง SQL ได้: {sql_json_text}"

    # 2. ดึงข้อมูลจากฐานข้อมูล
    df_result = query_to_dataframe(sql_script, db_name)
    
    if isinstance(df_result, str): # กรณีเกิด Error จากฐานข้อมูล
        return f"❌ {df_result}"
    
    if df_result.empty:
        return "🔍 ขออภัยครับ ไม่พบข้อมูลที่ตรงกับเงื่อนไขคำถามของคุณ"

    # 3. สรุปคำตอบสุดท้าย
    final_prompt_input = answer_prompt.format(
        question=user_question, 
        raw_data=df_result.to_string()
    )
    final_answer = generate_gemini_answer(final_prompt_input, is_json=False)
    
    return {"text": final_answer, "data": df_result, "sql": sql_script}

# ==========================================
# 5. ส่วนแสดงผล (User Interface - Streamlit)
# ==========================================
st.set_page_config(page_title="Data Analyst AI", page_icon="📈", layout="wide")
st.title('📊 Gemini Data Analyst')
st.markdown("ถามคำถามเกี่ยวกับข้อมูลการขายได้ทันที (เช่น ยอดขายรายจังหวัด, สินค้าขายดีรายเดือน)")

# ตรวจสอบความพร้อมของฐานข้อมูล
if not db_ready:
    st.warning(f"⚠️ กรุณาอัปโหลดไฟล์ `{csv_file}` ขึ้นไปที่ GitHub เพื่อเริ่มใช้งาน")
    st.stop()

# สร้างระบบแชท
if "messages" not in st.session_state:
    st.session_state.messages = []

# แสดงประวัติการคุย
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# รับคำถามจากผู้ใช้
if prompt := st.chat_input("พิมพ์คำถามของคุณที่นี่..."):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
        
    with st.chat_message("assistant"):
        with st.spinner('กำลังวิเคราะห์และหาคำตอบ...'):
            result = generate_summary_answer(prompt)
            
            if isinstance(result, dict):
                # แสดงคำตอบภาษาคน
                st.markdown(result["text"])
                
                # แสดงข้อมูลเสริม (UX ที่ดีคือการโชว์ข้อมูลอ้างอิง)
                with st.expander("ดูข้อมูลอ้างอิงและ SQL"):
                    st.write("SQL Query ที่ใช้:")
                    st.code(result["sql"], language="sql")
                    st.write("ตารางข้อมูล:")
                    st.dataframe(result["data"])
                
                response_to_save = result["text"]
            else:
                st.markdown(result)
                response_to_save = result
            
    # เก็บคำตอบลงประวัติ
    st.session_state.messages.append({"role": "assistant", "content": response_to_save})
