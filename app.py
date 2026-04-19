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
# ดึง API Key จาก Secrets ของ Streamlit
try:
    gemini_api_key = st.secrets["gemini_api_key"]
    gmn_client = genai.Client(api_key=gemini_api_key)
except Exception:
    st.error("❌ ไม่พบ Gemini API Key ใน Secrets! กรุณาตรวจสอบการตั้งค่า")
    st.stop()

db_name = 'test_database.db'
data_table = 'transactions'
csv_file = 'test_transactions_2026.csv'

# ระบบสร้างฐานข้อมูลอัตโนมัติจาก CSV (รันเฉพาะครั้งแรก)
@st.cache_resource
def init_database():
    if os.path.exists(csv_file):
        conn = sqlite3.connect(db_name)
        # นำข้อมูลเข้าแบบทับไฟล์เดิมเพื่อให้ข้อมูลเป็นปัจจุบันเสมอ
        df_csv = pd.read_csv(csv_file)
        df_csv.to_sql(data_table, conn, if_exists='replace', index=False)
        conn.close()
        return True
    return False

db_ready = init_database()

# รายละเอียดคอลัมน์เพื่อให้ AI เข้าใจตาราง (Schema Context)
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
# 2. PROMPT TEMPLATES (ปรับปรุงให้สมบูรณ์ขึ้น)
# ==========================================

# Prompt สำหรับแปลงภาษาคนเป็น SQL
script_prompt = """
### Goal
สร้าง SQLite script เพื่อดึงข้อมูลมาตอบคำถาม โดยส่งออกเป็น JSON เท่านั้น

### Context
คุณคือ SQLite Master ห้ามตอบเป็นคำพูด ให้ตอบเฉพาะโค้ดที่ถูกต้องและรันได้จริง 100%

### Input
- คำถาม: <Question> {question} </Question>
- ชื่อตาราง: <Table_Name> {table_name} </Table_Name>
- โครงสร้างคอลัมน์: <Schema> {data_dict} </Schema>

### Process
1. วิเคราะห์คำถามและ Schema
2. หากมีการเปรียบเทียบเดือน/ปี ให้ใช้คำสั่ง strftime('%Y-%m', trx_date) เสมอ
3. เขียน SQL ให้กระชับ มุ่งเน้นข้อมูลที่ตอบโจทย์คำถามได้ครบถ้วน

### Output
{{"script": "SELECT ... FROM ..."}}
"""

# Prompt สำหรับสรุปคำตอบให้สมบูรณ์ (Insightful Answer)
answer_prompt = """
### Goal
สรุปผลลัพธ์จากข้อมูลให้ "สมบูรณ์แบบ" เป็นธรรมชาติ และมีประโยชน์ต่อการตัดสินใจ

### Context
คุณคือ Data Analyst มืออาชีพที่ตอบคำถามได้สุภาพ เข้าใจง่าย และมีความน่าเชื่อถือ

### Input
- คำถามของผู้ใช้: <Question> {question} </Question>
- ข้อมูลที่ดึงได้จากฐานข้อมูล: <Raw_Data> {raw_data} </Raw_Data>

### Process
1. ตรวจสอบว่าข้อมูลใน Raw_Data มีคำตอบสำหรับคำถามหรือไม่
2. จัดรูปแบบตัวเลขให้สวยงาม (ใส่คอมม่า, ทศนิยม 2 ตำแหน่ง, หน่วยบาท/ชิ้น)
3. หากมีหลายประเด็น ให้ใช้ Bullet points

### Output
กรุณาตอบเป็นภาษาไทย โดยมีโครงสร้างดังนี้:
1. สรุปคำตอบหลัก (เช่น ยอดรวมคือ..., รายการที่ขายดีที่สุดคือ...)
2. (ถ้ามีประโยชน์) เพิ่มเติมข้อสังเกตหรือ Insight จากข้อมูล เช่น แนวโน้ม หรือสาเหตุ
3. หากคำตอบมีหลายรายการ ให้แสดงเป็นลำดับข้อเพื่อให้อ่านง่าย
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
            model='gemini-2.0-flash-lite', # ใช้รุ่นที่ประมวลผลเร็วและแม่นยำ
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
    input_for_sql = script_prompt.format(
        question=user_question, 
        table_name=data_table, 
        data_dict=data_dict_text
    )
    sql_json_text = generate_gemini_answer(input_for_sql, is_json=True)
    
    try:
        sql_script = json.loads(sql_json_text)['script']
    except:
        return "❌ ขออภัย ระบบไม่สามารถสร้างคำสั่งข้อมูลได้ในขณะนี้"

    # 2. ดึงข้อมูล
    df_result = query_to_dataframe(sql_script, db_name)
    if isinstance(df_result, str): return df_result 
    if df_result.empty: return "🔍 ไม่พบข้อมูลที่ตรงกับเงื่อนไขคำถามของคุณครับ"

    # 3. สรุปคำตอบ
    input_for_answer = answer_prompt.format(
        question=user_question, 
        raw_data=df_result.to_string()
    )
    final_text = generate_gemini_answer(input_for_answer, is_json=False)
    
    return {"text": final_text, "data": df_result}

# ==========================================
# 5. USER INTERFACE
# ==========================================
st.set_page_config(page_title="Data Insights AI", page_icon="📊")
st.title('📊 Gemini Data Analyst')
st.markdown("ถามคำถามเกี่ยวกับข้อมูลการขายจากฐานข้อมูลของคุณได้เลย")

if not db_ready:
    st.warning(f"⚠️ ไม่พบไฟล์ `{csv_file}` บนระบบ กรุณาอัปโหลดไฟล์ขึ้น GitHub เพื่อเริ่มใช้งาน")
    st.stop()

if "messages" not in st.session_state:
    st.session_state.messages = []

# แสดงประวัติแชท
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])

# ส่วนรับคำถาม
if prompt := st.chat_input("ตัวอย่าง: ยอดขายรวมของเดือน Jan 2026 เป็นเท่าไหร่?"):
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
        
    with st.chat_message("assistant"):
        with st.spinner('กำลังวิเคราะห์ฐานข้อมูล...'):
            result = generate_summary_answer(prompt)
            
            if isinstance(result, dict):
                st.markdown(result["text"])
                # แสดงตารางข้อมูลประกอบเพื่อความโปร่งใส (UX ที่ดี)
                with st.expander("ดูตารางข้อมูลอ้างอิง"):
                    st.dataframe(result["data"])
                response_text = result["text"]
            else:
                st.markdown(result)
                response_text = result
            
    st.session_state.messages.append({"role": "assistant", "content": response_text})
