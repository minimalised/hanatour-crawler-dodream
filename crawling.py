import os
import json
import asyncio
import hashlib
import datetime
import re
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from playwright.async_api import async_playwright
from openai import AsyncOpenAI

# 1. OpenAI 비동기 클라이언트 초기화
openai_client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", "YOUR_LOCAL_API_KEY"))

async def generate_naver_titles_llm(data):
    """
    GPT-4o-mini를 활용하여 4가지 콘셉트별로 3개씩, 총 12개의 마케팅 최적화 상품명을 생성합니다.
    """
    if data['departure_airport'] != "없음":
        departure_context = f"- 지정 출발공항: {data['departure_airport']} (반드시 상품명 맨 앞에 '{data['departure_airport']}' 형식으로 고정 배치할 것)"
    else:
        departure_context = "- 지정 출발공항: 없음 (★주의: 상품명 맨 앞에 '[기본출발]', '[기본출발지]', '[출발지없음]' 등 어떠한 출발 관련 문구도 절대 넣지 말고, 곧바로 '지역명'부터 시작할 것)"

    prompt = f"""
당신은 네이버 쇼핑 검색 최적화(SEO) 및 소비자 심리를 꿰뚫는 초일류 퍼포먼스 마케팅 카피라이팅 전문가입니다.
제공된 여행 상품 데이터를 바탕으로, 가이드라인을 완벽히 준수하는 4가지 서로 다른 마케팅 콘셉트의 상품명을 각각 3개씩(총 12개) 생성하세요.

[입력 데이터]
- 원본 상품명: {data['full_title']}  # 🌟 [교정] 특화 키워드(#NO쇼핑, #실속 등)를 GPT가 인지하도록 원본 상품명 항목 추가
- 기준 상품명: {data['pure_title']}
- 여행 지역: {data['region']}
- 기간: {data['duration']}
{departure_context}
- 핵심 설명: {data['description']}
- 추출 키워드: {data['hashtags']}

[❌ 전 콘셉트 공통 절대 금지 가이드라인]
1. 글자 수: 모든 상품명은 공백 포함 최소 30자 ~ 최대 45자 사이로 구성한다. (50자 절대 초과 금지)
2. 중복 제거: 단일 상품명 내부에서 동일한 단어(ex: 방콕, 여행, 패키지 등)가 2회 이상 중복 나열되는 것을 절대 금지한다.
3. 정제성: '신상품', '세이브', '특가', '대박', '★' 같은 홍보성 문구나 특수문자는 절대 포함하지 않는다.
4. 출발지 조건 규칙: [지정 출발공항]이 '없음'일 경우 '기본출발' 등을 임의로 조작하지 말고 무조건 곧바로 지역명/브랜드명으로 시작한다.
5. 🌟 [신규] 결과물 간 상호 중복 엄금: 생성되는 12개의 상품명은 조사나 어순만 바꾼 수준이 아니라 완전히 다른 키워드 조합을 가져야 한다.

[🎯 콘셉트별 상세 생성 규칙]
■ 콘셉트 A (정석 SEO형 - 3개): 감성적 수식어를 배제하고, 검색량이 높은 실용적 핵심 키워드 위주의 명사 나열 조합. (3개 간 키워드 순서를 다르게 분산할 것)
■ 콘셉트 B (타겟/상황형 - 3개): 소비자가 떠나는 이유와 타겟을 전면 강조. (ex: 부모님 효도, 아이동반, 여름휴가 등 타겟 키워드를 각각 다르게 융합)
■ 콘셉트 C (혜택/USP형 - 3개): 소비자가 직관적으로 이득을 느끼는 프리미엄 혜택 명사화 강조. (ex: 5성호텔, 자유시간, 전일정식사 등 각각 다르게 융합)
■ 콘셉트 D (감성/트렌디형 - 3개): 인스타/릴스 감성의 카피라이팅 가미. (ex: 요즘뜨는, 인생샷, 감성숙소 등 감성 단어가 겹치지 않게 분산)
"""
    
    json_schema_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "naver_twelve_titles_schema",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "A_1": {"type": "string"}, "A_2": {"type": "string"}, "A_3": {"type": "string"},
                    "B_1": {"type": "string"}, "B_2": {"type": "string"}, "B_3": {"type": "string"},
                    "C_1": {"type": "string"}, "C_2": {"type": "string"}, "C_3": {"type": "string"},
                    "D_1": {"type": "string"}, "D_2": {"type": "string"}, "D_3": {"type": "string"}
                },
                "required": [
                    "A_1", "A_2", "A_3", "B_1", "B_2", "B_3", 
                    "C_1", "C_2", "C_3", "D_1", "D_2", "D_3"
                ],
                "additionalProperties": False
            }
        }
    }

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that outputs compliant JSON based on the provided schema."},
                {"role": "user", "content": prompt}
            ],
            response_format=json_schema_format,
            temperature=0.4,
            seed=42
        )
        
        res_json = json.loads(response.choices[0].message.content)
        
        # 12개 결과물을 안전하게 리스트로 파싱
        titles_list = [
            res_json.get(f"{concepts}_{i}", "").strip() 
            for concepts in ['A', 'B', 'C', 'D'] 
            for i in [1, 2, 3]
        ]
        
        # 중복 체크 메커니즘 작동
        unique_titles = set(titles_list)
        if len(unique_titles) < 12:
            print(f"⚠️ [경고] LLM 결과물 중 중복 상품명 발생! (고유 개수: {len(unique_titles)}/12개)")
            
        return tuple(titles_list)

    except Exception as e:
        print(f"❌ LLM 12개 상품명 생성 중 에러 발생: {e}")
        err_t = f"[Error] {data['pure_title']}"
        return tuple([err_t] * 12)  # 구조 깨짐 방지를 위해 12개 튜플로 반환


async def process_single_product(item, target_region, target_airport, current_url, existing_titles_dict, runtime_titles_dict):
    """
    용민님의 원래 정상 코드의 '엘리먼트 추출 가이드라인과 순서'를 100% 원형 복구했습니다.
    """
    try:
        main_info = await item.query_selector(":scope > .inr.right")
        img_check = await item.query_selector(":scope > .inr.img")
        
        if not main_info or not img_check:
            return None

        # 1. 원래 정상 작동하던 순수 타이틀 파싱 방식 복구
        title_el = await main_info.query_selector(".item_title")
        full_title = (await title_el.inner_text()).strip() if title_el else "제목 없음"

        # 2. 원래 정상 작동하던 가격 필터 방식 복구
        price_el = await main_info.query_selector(".price")
        price_raw = await price_el.inner_text() if price_el else "0"
        price = "".join(filter(str.isdigit, price_raw))

        # ID 고유화
        unique_str = f"{full_title}_{price}"
        product_id = hashlib.md5(unique_str.encode()).hexdigest()[:8]

        # 3. 정제 상품명 및 해시태그 수집 원형 복구
        pure_title_body = re.sub(r'\[.*?\]', '', full_title).strip()
        if "#" in pure_title_body:
            parts = pure_title_body.split("#")
            pure_title = parts[0].strip()
            title_hashtags = sorted([p.strip() for p in parts[1:] if p.strip()])
        else:
            pure_title = pure_title_body
            title_hashtags = []

        hash_span_els = await main_info.query_selector_all(".hash_group span")
        ui_hashtags = [(await h.inner_text()).replace("#", "").strip() for h in hash_span_els]
        all_hashtags = sorted(list(set(title_hashtags + ui_hashtags)))

        desc_el = await main_info.query_selector(".item_text.stit")
        product_desc = (await desc_el.inner_text()).strip() if desc_el else ""

        duration_el = await main_info.query_selector("span.icn.cal")
        duration_text = (await duration_el.inner_text()).strip() if duration_el else ""
        duration = duration_text.replace("여행기간", "").strip()

        # 4. 이미지 bg_alpha 방어 코드 완벽 복구
        img_url = ""
        img_el = await img_check.query_selector("img")
        if img_el:
            data_src = await img_el.get_attribute("data-src")
            src = await img_el.get_attribute("src")
            potential_url = data_src if data_src else src
            
            if potential_url and "bg_alpha" not in potential_url:
                img_url = potential_url.strip()
            else:
                all_imgs = await img_check.query_selector_all("img")
                for im in all_imgs:
                    i_src = await im.get_attribute("src")
                    i_data = await im.get_attribute("data-src")
                    target = i_data if i_data else i_src
                    if target and "bg_alpha" not in target:
                        img_url = target.strip()
                        break

        if img_url and img_url.startswith("//"): 
            img_url = "https:" + img_url

        # 💡 [교정] 복사 붙여넣기 오작동을 막기 위해 캐시 비교 기준을 pure_title에서 full_title(원본명)로 변경
        if product_id in existing_titles_dict:
            titles = existing_titles_dict[product_id]
        elif full_title in runtime_titles_dict:  # 🌟 pure_title ➡️ full_title 로 교정
            titles = runtime_titles_dict[full_title]
            print(f"♻️ [비용 절감] 동일 회차 내 원본형 상품명 캐시 재사용: {full_title}")
        else:
            print(f"✨ [신규 상품 발견] LLM 12대 타이틀 통합 최초 생성: {full_title}")
            ai_input_data = {
                "pure_title": pure_title,
                "full_title": full_title,  # 🌟 GPT에 원본명을 전달할 수 있도록 적재 데이터 추가
                "region": target_region,          
                "departure_airport": target_airport, 
                "duration": duration,
                "description": product_desc,
                "hashtags": ", ".join(all_hashtags)
            }
            titles = await generate_naver_titles_llm(ai_input_data)
            runtime_titles_dict[full_title] = titles  # 🌟 캐시 키값 매핑 교정

        return {
            "ID": product_id,
            "원본상품명": full_title,
            "정제상품명": pure_title,
            "가격": int(price) if price else 0,
            "URL": current_url,
            "이미지URL": img_url,
            "지정지역": target_region,
            "출발공항": target_airport,
            "A_정석_1": titles[0], "A_정석_2": titles[1], "A_정석_3": titles[2],
            "B_타겟_1": titles[3], "B_타겟_2": titles[4], "B_타겟_3": titles[5],
            "C_혜택_1": titles[6], "C_혜택_2": titles[7], "C_혜택_3": titles[8],
            "D_감성_1": titles[9], "D_감성_2": titles[10], "D_감성_3": titles[11]
        }
    except Exception as e:
        print(f"⚠️ 개별 상품 추출 중 오류 패스: {e}")
        return None


async def run_crawler():
    print("🌐 구글 API 인증 및 스프레드시트 연결 중...")
    scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
    
    json_raw = os.environ.get("GOOGLE_JSON_RAW")
    
    try:
        if json_raw:
            service_account_info = json.loads(json_raw)
            creds = Credentials.from_service_account_info(service_account_info, scopes=scopes)
        else:
            creds = Credentials.from_service_account_file('secrets.json', scopes=scopes)
            
        gc = gspread.authorize(creds)
    except Exception as auth_error:
        print(f"❌ 구글 API 인증 실패: {auth_error}")
        return

    # ------------------ SOURCE LOAD ------------------
    source_spreadsheet_id = os.environ.get("SOURCE_SPREADSHEET_ID")
    if not source_spreadsheet_id:
        print("❌ SOURCE_SPREADSHEET_ID 환경 변수가 없습니다.")
        return
        
    try:
        source_doc = gc.open_by_key(source_spreadsheet_id)
        source_sheet = source_doc.worksheet("상품리스트")
        
        all_rows = source_sheet.get_all_values()
        data_rows = all_rows[1:]
        
        target_tasks = []
        for row in data_rows:
            if len(row) >= 1 and row[0].startswith("http"):
                url = row[0].strip()
                region = row[1].strip() if len(row) > 1 and row[1].strip() else "지역명 미상"
                airport = row[2].strip() if len(row) > 2 and row[2].strip() else "없음"
                
                target_tasks.append({
                    "url": url,
                    "sheet_region": region,
                    "sheet_airport": airport
                })
                
        print(f"✅ 총 {len(target_tasks)}개의 유효 타겟 상품 라인을 확보했습니다.")
    except Exception as e:
        print(f"❌ URL 리스트 가공 중 에러 발생: {e}")
        return

    # ------------------ TARGET 기존 캐시 LOAD ------------------
    target_spreadsheet_id = os.environ.get("TARGET_SPREADSHEET_ID")
    if not target_spreadsheet_id:
        print("❌ TARGET_SPREADSHEET_ID 환경 변수가 설정되지 않았습니다.")
        return

    worksheet_name = "github"
    existing_titles_dict = {}
    
    try:
        target_doc = gc.open_by_key(target_spreadsheet_id)
        github_sheet = target_doc.worksheet(worksheet_name)
        existing_data = github_sheet.get_all_records()
        
        for r in existing_data:
            if r.get("ID"):
                existing_titles_dict[str(r["ID"])] = (
                    r.get("A_정석_1", ""), r.get("A_정석_2", ""), r.get("A_정석_3", ""),
                    r.get("B_타겟_1", ""), r.get("B_타겟_2", ""), r.get("B_타겟_3", ""),
                    r.get("C_혜택_1", ""), r.get("C_혜택_2", ""), r.get("C_혜택_3", ""),
                    r.get("D_감성_1", ""), r.get("D_감성_2", ""), r.get("D_감성_3", "")
                )
        print(f"✅ 기수집된 기존 12대 옵션 상품 데이터 {len(existing_titles_dict)}개를 캐싱했습니다.")
    except Exception as cache_error:
        print(f"⚠️ 기존 시트 로드 실패. 원인: {cache_error}")

    runtime_titles_dict = {}

    # ------------------ CRAWLING RUN ------------------
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={'width': 1280, 'height': 1024},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        all_products = []

        for task in target_tasks:
            current_url = task["url"]
            target_region = task["sheet_region"]
            target_airport = task["sheet_airport"]
            
            try:
                print(f"🔄 {target_region} (출발: {target_airport}) 페이지 로딩 중...")
                await page.goto(current_url, wait_until="domcontentloaded", timeout=30000)
                
                try:
                    await page.wait_for_selector(".option_wrap.result .count em", timeout=10000)
                except Exception:
                    pass

                # 원래 작동하던 스마트 스크롤 제어 로직 완전 복구
                total_count = 20  
                try:
                    count_element = await page.query_selector(".option_wrap.result .count em")
                    if count_element:
                        count_text = (await count_element.inner_text()).strip()
                        if count_text.isdigit():
                            total_count = int(count_text)
                            print(f"   ↳ 🎯 총 상품 수 동기화 성공: [{total_count}개]")
                except Exception as e:
                    print(f"   ⚠️ 총 상품 수 추출 실패: {e}")

                needed_scrolls = (total_count - 1) // 20 if total_count > 20 else 0
                
                if needed_scrolls > 0:
                    print(f"   ↳ ⏳ 전수 노출을 위해 {needed_scrolls}번 스마트 스크롤 작동.")
                    for scroll_step in range(1, needed_scrolls + 1):
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        await asyncio.sleep(2.0)
                        
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight - 300)")
                        await asyncio.sleep(0.3)
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        
                        current_items = await page.query_selector_all(".prod_list_wrap ul.type > li")
                        if len(current_items) >= total_count:
                            break

                await asyncio.sleep(1.0)

                # 원래 301개씩 긁어오던 그 시점의 선택자 완벽 복구
                final_items = await page.query_selector_all(".prod_list_wrap ul.type > li")
                print(f"📦 최종 수집된 타겟 엘리먼트 총 {len(final_items)}개! 조건부 병렬 처리를 시작합니다.")
                
                tasks = [
                    process_single_product(item, target_region, target_airport, current_url, existing_titles_dict, runtime_titles_dict) 
                    for item in final_items
                ]
                
                batch_results = await asyncio.gather(*tasks)
                
                for res in batch_results:
                    if res is not None:
                        all_products.append(res)

                print(f"✅ {target_region} (출발지: {target_airport}) 완료 ({len(all_products)}개 전수 적재 대기)")
                await asyncio.sleep(1)

            except Exception as e:
                print(f"❌ {current_url} 접속 에러: {e}")
                continue

        # ------------------ 구글 시트 마스터 적재부 ------------------
        if all_products:
            print("\n🚀 마스터 Raw 데이터 스프레드시트 업데이트 시작...")
            try:
                df = pd.DataFrame(all_products)
                column_order = [
                    "ID", "원본상품명", "정제상품명", "가격", "URL", "이미지URL", "지정지역", "출발공항",
                    "A_정석_1", "A_정석_2", "A_정석_3",
                    "B_타겟_1", "B_타겟_2", "B_타겟_3",
                    "C_혜택_1", "C_혜택_2", "C_혜택_3",
                    "D_감성_1", "D_감성_2", "D_감성_3"
                ]
                df = df[column_order]
                data_to_upload = [df.columns.values.tolist()] + df.values.tolist()

                sheet = target_doc.worksheet(worksheet_name)
                sheet.clear()  
                sheet.update(values=data_to_upload, range_name='A1')
                print(f"🎯 [성공] 마스터 Raw 시트 [{target_doc.title}]에 12개 옵션 데이터가 축적되었습니다.")

            except Exception as e:
                print(f"❌ 구글 시트 결과 적재 에러: {e}")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run_crawler())
